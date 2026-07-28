#!/usr/bin/env python3
"""Automated Pull Request Code Review Script.

This script parses a unified PR diff, fetches inline review comments and a
high-level summary from the OpenRouter API, filters comments to target
only changed lines, and submits the review to GitHub.
"""

import json
import os
import random
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.request

# Default patterns of files to exclude from code review
DEFAULT_EXCLUDE_PATTERNS = [
    # Lock files
    r"package-lock\.json$",
    r"yarn\.lock$",
    r"pnpm-lock\.yaml$",
    r"Cargo\.lock$",
    r"poetry\.lock$",
    r"mix\.lock$",
    r"go\.sum$",
    r"composer\.lock$",
    r"pdm\.lock$",
    r"Pipfile\.lock$",
    r"bun\.lockb$",
    r"deno\.lock$",
    # Jest snapshots
    r"\.snap$",
    # Minified files
    r"\.min\.js$",
    r"\.min\.css$",
    # Build maps
    r"\.map$",
    # Images/vectors/binaries
    r"\.svg$",
    r"\.png$",
    r"\.jpg$",
    r"\.jpeg$",
    r"\.gif$",
    r"\.ico$",
]

# Pre-compiled regular expressions for efficiency
JSON_CLEAN_RE = re.compile(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])')

MD_HEADER_RE = re.compile(r"^#+\s*")
MD_LIST_RE = re.compile(r"^[-*+]\s+")
MD_NUM_LIST_RE = re.compile(r"^\d+\.\s+")
MD_BOLD_START_RE = re.compile(r"^\*+\s*")
MD_BOLD_END_RE = re.compile(r"\*+\s*$")
MD_BACKTICK_START_RE = re.compile(r"^`\s*")
MD_BACKTICK_END_RE = re.compile(r"`\s*$")


def get_exclude_regexes(
    custom_patterns_str: str | None = None,
) -> list[re.Pattern[str]]:
    """Compile and return the complete list of exclusion regex patterns.

    Allows matching both default and custom-supplied exclusions.
    """
    patterns = list(DEFAULT_EXCLUDE_PATTERNS)
    if custom_patterns_str:
        # Split custom exclusions by comma or newline
        custom_list = [
            p.strip() for p in re.split(r"[,\n]", custom_patterns_str) if p.strip()
        ]
        patterns.extend(custom_list)
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def get_modified_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse a unified diff and return a map of files to their modified new line numbers."""
    modified: dict[str, set[int]] = {}
    current_file: str | None = None
    new_line_num: int = 0
    in_header: bool = True

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_header = True
            current_file = None
        elif line.startswith("@@"):
            in_header = False
            try:
                # Format: @@ -old_start,old_len +new_start,new_len @@ ...
                parts = line.split()
                if len(parts) >= 3:
                    new_info = parts[2]  # e.g., "+new_start,new_len" or "+new_start"
                    if new_info.startswith("+"):
                        new_start = int(new_info[1:].split(",")[0])
                        new_line_num = new_start - 1
            except (ValueError, IndexError):
                # If parsing fails, ignore this hunk's start and skip line counting
                current_file = None
        elif in_header and line.startswith("+++ "):
            path_part = line[4:]
            if path_part.startswith('"') and path_part.endswith('"'):
                path_part = path_part[1:-1]
            if path_part.startswith("b/"):
                current_file = path_part[2:]
                modified[current_file] = set()
            elif path_part == "/dev/null":
                current_file = None
            else:
                current_file = path_part
                modified[current_file] = set()
        elif current_file is not None and not in_header:
            if line.startswith("+") and not line.startswith("+++"):
                new_line_num += 1
                modified[current_file].add(new_line_num)
            elif line.startswith(" "):
                new_line_num += 1
            elif line.startswith("-") and not line.startswith("---"):
                pass  # Deletions do not affect lines in the new file

    return modified


def annotate_diff(diff_text: str) -> str:
    """Annotate a unified diff with new line numbers for modified and context lines."""
    annotated_lines = []
    current_file: str | None = None
    new_line_num: int = 0
    in_header: bool = True

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_header = True
            current_file = None
            annotated_lines.append(line)
        elif line.startswith("@@"):
            in_header = False
            try:
                parts = line.split()
                if len(parts) >= 3:
                    new_info = parts[2]
                    if new_info.startswith("+"):
                        new_start = int(new_info[1:].split(",")[0])
                        new_line_num = new_start - 1
            except (ValueError, IndexError):
                current_file = None
            annotated_lines.append(line)
        elif in_header and line.startswith("+++ "):
            path_part = line[4:]
            if path_part.startswith('"') and path_part.endswith('"'):
                path_part = path_part[1:-1]
            if path_part.startswith("b/"):
                current_file = path_part[2:]
            elif path_part == "/dev/null":
                current_file = None
            else:
                current_file = path_part
            annotated_lines.append(line)
        elif current_file is not None and not in_header:
            if line.startswith("+") and not line.startswith("+++"):
                new_line_num += 1
                annotated_lines.append(f"{new_line_num:5d}: {line}")
            elif line.startswith(" "):
                new_line_num += 1
                annotated_lines.append(f"{new_line_num:5d}: {line}")
            elif line.startswith("-") and not line.startswith("---"):
                annotated_lines.append(f"     : {line}")
            else:
                annotated_lines.append(line)
        else:
            annotated_lines.append(line)

    return "\n".join(annotated_lines)


def filter_diff(diff_text: str, exclude_regexes: list[re.Pattern[str]]) -> str:
    """Filter out lock files, minified files, SVG files, and other non-reviewable files from the unified diff."""
    lines = diff_text.splitlines(keepends=True)
    filtered_lines = []
    current_file_excluded = False

    for line in lines:
        if line.startswith("diff --git "):
            first_line = line.strip()
            # Find the path following "b/"
            b_idx = first_line.find(' "b/')
            if b_idx != -1:
                file_path = first_line[b_idx + 4 :].strip()
                if file_path.endswith('"'):
                    file_path = file_path[:-1]
            else:
                b_idx = first_line.find(" b/")
                if b_idx != -1:
                    words = first_line[b_idx + 3 :].split()
                    file_path = words[0] if words else ""
                else:
                    file_path = ""

            if file_path and any(rx.search(file_path) for rx in exclude_regexes):
                current_file_excluded = True
                print(f"Skipping diff section for excluded file: {file_path}")
            else:
                current_file_excluded = False

        if not current_file_excluded:
            filtered_lines.append(line)

    return "".join(filtered_lines)


def parse_llm_json(response_text: str) -> dict[str, Any]:
    """Parse JSON output from LLM, stripping markdown block wrappers or extracting the JSON block."""
    response_text = response_text.strip()

    def clean_json(text: str) -> str:
        # Replace trailing commas (ignoring those inside strings)
        return JSON_CLEAN_RE.sub(lambda m: m.group(1) or m.group(2), text)

    # Try direct parse first
    try:
        result = json.loads(clean_json(response_text), strict=False)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strip markdown wrappers if present
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
        try:
            result = json.loads(clean_json(cleaned), strict=False)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Extract block between the first '{' and the last '}'
    first_brace = response_text.find("{")
    last_brace = response_text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_candidate = response_text[first_brace : last_brace + 1]
        try:
            result = json.loads(clean_json(json_candidate), strict=False)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON object found in LLM response")


def clean_markdown_line(line: str) -> str:
    """Clean markdown markers (headers, bold, italics, backticks) from a line."""
    line = line.strip()
    # Strip leading '#' characters and whitespace
    line = MD_HEADER_RE.sub("", line)
    # Strip leading markdown list markers (e.g., - , * , + , 1. ) and whitespace
    line = MD_LIST_RE.sub("", line)
    line = MD_NUM_LIST_RE.sub("", line)
    # Strip bold/italic wrappers e.g. **path** or *path*
    line = MD_BOLD_START_RE.sub("", line)
    line = MD_BOLD_END_RE.sub("", line)
    # Strip backticks
    line = MD_BACKTICK_START_RE.sub("", line)
    line = MD_BACKTICK_END_RE.sub("", line)
    return line.strip()


def parse_markdown_comments(
    text: str, modified_lines: dict[str, set[int]]
) -> list[dict[str, Any]]:
    """Attempt to extract file, line, and comment body from free-form markdown text."""
    comments: list[dict[str, Any]] = []
    current_file: str | None = None
    current_comment: dict[str, Any] | None = None

    # Pre-compute lowercase paths, basenames, and patterns for efficient lookups outside the line loop
    path_lookups = []
    for path in modified_lines:
        norm_path = path.lower()
        basename = os.path.basename(path).lower()
        patterns = {
            norm_path,
            basename,
            f"in {norm_path}",
            f"in {basename}",
            f"file: {norm_path}",
            f"file: {basename}",
            f"file {norm_path}",
            f"file {basename}",
            f"review of {norm_path}",
            f"review of {basename}",
            f"review for {norm_path}",
            f"review for {basename}",
            f"review {norm_path}",
            f"review {basename}",
        }
        path_lookups.append((path, norm_path, basename, patterns))

    # Pattern for line numbers
    # Matches:
    # "Line 81: ...", "Lines 42-59: ...", "[Line 81]: ...", "(Line 81) - ..."
    # Case insensitive, optional brackets/parentheses, optional whitespace, colon or hyphen separator
    line_re = re.compile(
        r"^\s*[-*]?\s*[\(\[]?[lL]ines?\s+(\d+)(?:\s*-\s*(\d+))?[\)\]]?\s*[:\-]\s*(.*)$"
    )

    for line in text.splitlines():
        cleaned = clean_markdown_line(line)
        if not cleaned:
            if current_comment:
                current_comment["body"] += "\n"
            continue

        # Check if line indicates a file path from modified_lines
        found_file = False
        cleaned_lower = cleaned.lower()
        cleaned_pat = cleaned_lower.rstrip(":").strip()

        for path, norm_path, basename, patterns in path_lookups:
            if cleaned_pat in patterns or (
                (cleaned_lower.startswith("###") or cleaned_lower.startswith("##"))
                and (norm_path in cleaned_lower or basename in cleaned_lower)
            ):
                current_file = path
                found_file = True
                if current_comment:
                    comments.append(current_comment)
                    current_comment = None
                break

        if found_file:
            continue

        # Check if it's a line comment start
        line_match = line_re.match(line)
        if line_match and current_file:
            if current_comment:
                comments.append(current_comment)
            start_line = int(line_match.group(1))
            end_line_str = line_match.group(2)
            end_line = int(end_line_str) if end_line_str else start_line
            initial_text = line_match.group(3).strip()

            current_comment = {
                "path": current_file,
                "line": end_line,
                "body": initial_text,
            }
            continue

        # If we are inside a comment, append the text
        if current_comment:
            if current_comment["body"]:
                current_comment["body"] += "\n" + line
            else:
                current_comment["body"] = line

    if current_comment:
        comments.append(current_comment)

    # Post-process: clean up trailing/leading newlines and whitespace
    for c in comments:
        c["body"] = c["body"].strip()

    return comments


def truncate_diff(diff_content: str, limit: int = 500000) -> str:
    """Truncate the diff content at the last newline character before the limit."""
    if len(diff_content) > limit:
        print(
            f"PR diff size is over {limit} bytes. Truncating diff for review payload."
        )
        last_newline = diff_content.rfind("\n", 0, limit)
        if last_newline != -1:
            return (
                diff_content[:last_newline]
                + f"\n\n[Diff truncated due to size limit of {limit} bytes]"
            )
        else:
            return (
                diff_content[:limit]
                + f"\n\n[Diff truncated due to size limit of {limit} bytes]"
            )
    return diff_content


def _calculate_backoff(
    attempt: int, base_delay: float, retry_after: str | float | int | None = None
) -> float:
    """Calculate jittered exponential backoff delay, respecting optional retry-after metadata.

    Jittered exponential backoff prevents synchronized retry thundering herds.
    """
    delay = base_delay * (2**attempt) + random.uniform(0.1, 1.0)
    if retry_after is not None:
        try:
            # Respect the provider's suggested wait time if it is longer than our backoff
            delay = max(delay, float(retry_after))
        except (ValueError, TypeError):
            pass
    # Cap the maximum delay to prevent excessively long blocks in automated workflows
    return float(min(delay, 60.0))


def _send_request(url: str, payload: dict[str, Any], headers: dict[str, str]) -> str:
    """Send HTTP request to OpenRouter and handle errors/timeouts.

    Args:
        url: The target OpenRouter API endpoint URL.
        payload: The request body containing model instructions and settings.
        headers: Dict containing headers (Authorization, X-Title, etc.).

    Returns:
        The raw text content returned in the model's chat completion choice.

    Raises:
        ValueError: If the response is empty, malformed, missing message content, or on HTTPError.
        urllib.error.URLError: If connection/DNS resolution fails and retries are exhausted.
        TimeoutError: If request times out and retries are exhausted.
    """
    max_retries = 5
    base_delay = 1.0

    # Serialize and encode payload once outside the retry loop for performance
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                res_content = res.read(10 * 1024 * 1024).decode(
                    "utf-8", errors="replace"
                )
                res_data = json.loads(res_content)
                if isinstance(res_data, dict) and "error" in res_data:
                    err = res_data["error"]
                    err_msg = (
                        str(err.get("message") or "")
                        if isinstance(err, dict)
                        else str(err)
                    )
                    err_code = err.get("code") if isinstance(err, dict) else None

                    # Some API providers return rate-limit info in the JSON response body
                    # with HTTP 200 rather than as an HTTP 429 status code.
                    if (
                        err_code == 429 or "rate limit" in err_msg.lower()
                    ) and attempt < max_retries:
                        retry_after = None
                        if isinstance(err, dict):
                            metadata = err.get("metadata")
                            if isinstance(metadata, dict):
                                retry_after = metadata.get("retry_after_seconds")

                        delay = _calculate_backoff(attempt, base_delay, retry_after)
                        print(
                            f"Rate limited by API error. Retrying in {delay:.2f} seconds...",
                            file=sys.stderr,
                        )
                        time.sleep(delay)
                        continue

                    raise ValueError(f"OpenRouter Error: {err_msg}")

                choices = res_data.get("choices", [])
                if not choices:
                    truncated_res = (
                        res_content[:1000] + "..."
                        if len(res_content) > 1000
                        else res_content
                    )
                    raise ValueError(
                        f"OpenRouter returned empty choices. Full response: {truncated_res}"
                    )
                message = choices[0].get("message", {})
                content = message.get("content")
                if content is None:
                    refusal = message.get("refusal")
                    if refusal:
                        raise ValueError(f"OpenRouter Refusal: {refusal}")
                    raise ValueError(
                        f"OpenRouter returned message with null content. Full response: {res_content}"
                    )
                if not isinstance(content, str):
                    raise ValueError("OpenRouter returned non-string message content.")
                return content
        except urllib.error.HTTPError as e:
            # Read the error body eagerly once to avoid stream exhaustion issues on retry/raise paths
            try:
                err_body = e.read(1024 * 1024).decode("utf-8", errors="replace")
            except Exception:
                err_body = ""

            # Only retry transient errors (429 rate limit or 5xx server errors).
            # Permanent errors (e.g. 400 Bad Request, 401 Unauthorized, 403 Forbidden) fail immediately.
            is_retryable = e.code == 429 or (500 <= e.code < 600)
            if is_retryable and attempt < max_retries:
                retry_after = e.headers.get("Retry-After")

                # Attempt to parse body metadata for specific retry details in case standard header is not populated
                if err_body:
                    try:
                        err_data = json.loads(err_body)
                        if isinstance(err_data, dict):
                            err_info = err_data.get("error")
                            if isinstance(err_info, dict):
                                metadata = err_info.get("metadata")
                                if isinstance(metadata, dict):
                                    body_retry_after = metadata.get(
                                        "retry_after_seconds"
                                    )
                                    if body_retry_after is not None:
                                        retry_after = body_retry_after
                    except Exception:
                        pass

                delay = _calculate_backoff(attempt, base_delay, retry_after)
                print(
                    f"API request failed with status {e.code}. Retrying in {delay:.2f} seconds...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            else:
                if not err_body:
                    err_body = "<could not read HTTP error body>"
                raise ValueError(f"OpenRouter HTTP Error {e.code}: {err_body}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            # Network drops and timeouts are transient issues; retry them
            if attempt < max_retries:
                delay = _calculate_backoff(attempt, base_delay)
                print(
                    f"API request network/timeout error: {e}. Retrying in {delay:.2f} seconds...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            else:
                # Propagate connection errors once max retries are exhausted
                raise

    raise ValueError("API request failed: retries exhausted.")


def make_openrouter_request(
    api_key: str,
    diff_content: str,
    post_summary: bool = True,
    model: str = "openrouter/free",
    base_url: str = "https://openrouter.ai/api/v1/chat/completions",
    max_tokens: int = 4096,
    repo: str = "pr-code-review-action",
) -> str:
    """Call OpenRouter API to review the diff content using the specified model."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": f"https://github.com/{repo}",
        "X-Title": "PR Review Bot",
    }

    script_dir = os.path.dirname(os.path.abspath(__file__))
    instruction_file = (
        "system_instruction.md"
        if post_summary
        else "system_instruction_comments_only.md"
    )
    instruction_path = os.path.join(script_dir, instruction_file)
    try:
        with open(instruction_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception as e:
        print(f"Error reading {instruction_file}: {e}", file=sys.stderr)
        sys.exit(1)

    annotated_diff = annotate_diff(diff_content)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {
                "role": "user",
                "content": (
                    # Explicitly request raw JSON in the prompt as a fallback for models/endpoints
                    # that ignore the response_format API parameter or wrap JSON in markdown blocks.
                    f"Please review this annotated diff:\n\n{annotated_diff}\n\n"
                    "CRITICAL: You must respond ONLY with a valid JSON object matching the schema. "
                    "Do not include any introductory or concluding text, markdown wrappers, "
                    "or any content outside of the JSON object itself."
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    print(f"Requesting review from OpenRouter using model: {model} (JSON mode)...")
    try:
        return _send_request(base_url, payload, headers)
    except Exception as e:
        err_msg = str(e)
        is_json_error = (
            "HTTP Error 400" in err_msg
            or "HTTP Error 422" in err_msg
            or isinstance(e, ValueError)
        )
        if is_json_error:
            print(
                f"Warning: JSON-mode request failed: {e}. Retrying without strict JSON format constraint...",
                file=sys.stderr,
            )
            payload_fallback = payload.copy()
            payload_fallback.pop("response_format", None)
            try:
                return _send_request(base_url, payload_fallback, headers)
            except Exception as fallback_err:
                print(
                    f"Error: Fallback request also failed: {fallback_err}",
                    file=sys.stderr,
                )
                raise fallback_err
        else:
            raise


def submit_github_review(
    repo: str, pr_number: str, token: str, summary: str, comments: list[dict[str, Any]]
) -> dict[str, Any]:
    """Post the review comments and summary to GitHub using the Pull Requests API."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }

    payload = {
        "body": summary,
        "event": "COMMENT",
        "comments": comments,
    }

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            print("Successfully submitted code review to GitHub.")
            result = json.loads(res.read().decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("GitHub API returned non-JSON-object response.")
            return result
    except urllib.error.HTTPError as e:
        print(
            f"GitHub HTTP Error {e.code}: {e.read().decode('utf-8')}", file=sys.stderr
        )
        raise
    except Exception as e:
        print(f"GitHub Error: {e}", file=sys.stderr)
        raise


def main() -> None:
    """Validate environment configurations, process diff content, and submit review."""
    # Load and validate environment variables
    api_key = os.environ.get("OPENROUTER_API_KEY")
    pr_number = os.environ.get("PR_NUMBER")
    repo = os.environ.get("REPO")
    token = os.environ.get("GH_TOKEN")
    post_summary_env = os.environ.get("POST_SUMMARY", "true")
    post_summary = post_summary_env.lower() in ("true", "1", "yes")

    if not api_key:
        print(
            "Error: OPENROUTER_API_KEY environment variable is required.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not pr_number or not repo or not token:
        print(
            "Error: PR_NUMBER, REPO, and GH_TOKEN environment variables are required.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Harden input parameters to prevent URL injection/manipulation
    if not pr_number.isdigit():
        print("Error: PR_NUMBER must be numeric.", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$", repo):
        print("Error: REPO must be in 'owner/repo' format.", file=sys.stderr)
        sys.exit(1)

    # Read diff file
    diff_path = "pr.diff"
    if not os.path.exists(diff_path):
        print(f"Error: PR diff file '{diff_path}' not found.", file=sys.stderr)
        sys.exit(1)

    with open(diff_path, "r", encoding="utf-8") as f:
        diff_content = f.read()

    if not diff_content.strip():
        print("PR diff is empty. Skipping review.")
        sys.exit(0)

    # Retrieve and compile exclusion patterns
    custom_excludes = os.environ.get("EXCLUDE_PATTERNS")
    exclude_regexes = get_exclude_regexes(custom_excludes)

    # Filter out lock files, generated/binary assets, etc. to prevent excessive diff size
    try:
        diff_content = filter_diff(diff_content, exclude_regexes)
    except Exception as e:
        print(
            f"Warning: Failed to filter diff: {e}. Proceeding with unfiltered diff.",
            file=sys.stderr,
        )

    if not diff_content.strip():
        print("PR diff is empty after filtering excluded files. Skipping review.")
        sys.exit(0)

    # Truncate diff if it is too large (> 500KB) to stay within LLM token limits
    is_truncated = len(diff_content) > 500000
    diff_content = truncate_diff(diff_content, 500000)

    print("Parsing modified line numbers from diff...")
    modified_lines = get_modified_lines(diff_content)

    # Retrieve parameterized configuration for OpenRouter API call
    model = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    base_url = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
    )
    try:
        max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", "4096"))
    except ValueError:
        max_tokens = 4096

    print("Requesting review from OpenRouter...")
    raw_response = ""
    json_parsed = False
    review_data = {}

    max_review_attempts = 3
    last_exception = None

    for attempt in range(1, max_review_attempts + 1):
        try:
            raw_response = make_openrouter_request(
                api_key=api_key,
                diff_content=diff_content,
                post_summary=post_summary,
                model=model,
                base_url=base_url,
                max_tokens=max_tokens,
                repo=repo,
            )
            review_data = parse_llm_json(raw_response)
            json_parsed = True
            break
        except Exception as e:
            last_exception = e
            if attempt < max_review_attempts:
                print(
                    f"Warning: Review attempt {attempt} failed (error or malformed JSON): {e}. "
                    "Retrying request...",
                    file=sys.stderr,
                )
                time.sleep(2.0 * attempt)

    if json_parsed:
        summary = review_data.get("summary", "Automated code review completed.")
        if not isinstance(summary, str):
            summary = str(summary)
        llm_comments = review_data.get("comments", [])
        if not isinstance(llm_comments, list):
            llm_comments = []
    else:
        print(
            f"Error: All {max_review_attempts} review attempts failed. Last error: {last_exception}",
            file=sys.stderr,
        )
        if raw_response:
            print(
                "Attempting to parse comments from free-form markdown text of last response...",
                file=sys.stderr,
            )
            print("--- RAW LLM RESPONSE START ---", file=sys.stderr)
            print(raw_response, file=sys.stderr)
            print("--- RAW LLM RESPONSE END ---", file=sys.stderr)
            llm_comments = parse_markdown_comments(raw_response, modified_lines)
            summary = "Automated code review completed (JSON parsing failed, fallback comments parsed)."
        else:
            print(
                "Error: Could not retrieve review response from API.", file=sys.stderr
            )
            sys.exit(1)

    print(f"Filtering {len(llm_comments)} suggested comments against modified lines...")
    valid_comments = []
    for comment in llm_comments:
        if not isinstance(comment, dict):
            continue
        path = comment.get("path")
        line = comment.get("line")
        body = comment.get("body")

        if not isinstance(path, str) or not isinstance(body, str) or line is None:
            continue

        try:
            line_int = int(line)
        except (ValueError, TypeError):
            continue

        # Normalize path: strip a/ or b/ prefixes and leading slashes
        norm_path = path
        if norm_path.startswith("b/"):
            norm_path = norm_path[2:]
        elif norm_path.startswith("a/"):
            norm_path = norm_path[2:]
        norm_path = norm_path.lstrip("/")

        # Enforce that the path exists in our diff and the line number is part of the changed chunk
        if norm_path in modified_lines and line_int in modified_lines[norm_path]:
            valid_comments.append(
                {
                    "path": norm_path,
                    "line": line_int,
                    "side": "RIGHT",
                    "body": body,
                }
            )
        else:
            print(f"Skipped comment targeting unchanged line: {path}:{line}")

    # Build the final review body depending on post_summary and json_parsed
    if not json_parsed:
        final_body = (
            "Automated code review completed.\n\n"
            "> [!WARNING]\n"
            "> The structured code review summary could not be parsed as a JSON object. "
            "For security reasons (to prevent indirect prompt injection vectors), "
            "the raw review feedback is not posted on the pull request thread. "
            "Please check the GitHub Actions workflow execution logs to view the raw feedback."
        )
    elif not post_summary:
        if valid_comments:
            final_body = f"Automated code review completed. Posted {len(valid_comments)} inline comment(s) on specific lines."
        else:
            final_body = "Automated code review completed. No issues found."
    else:
        final_body = summary

    if is_truncated and post_summary:
        final_body += (
            "\n\n> [!WARNING]\n> **Note**: The pull request diff was truncated "
            "because it exceeded the size limit (500KB). Some changes or files "
            "might not have been fully reviewed."
        )

    # Always submit a review so the user receives the status and summary feedback
    print(f"Submitting review to GitHub with {len(valid_comments)} inline comments...")
    submit_github_review(repo, pr_number, token, final_body, valid_comments)

    if not json_parsed:
        print(
            "Error: JSON parsing of LLM response failed. Exiting with failure status.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
