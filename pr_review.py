#!/usr/bin/env python3
"""Automated Pull Request Code Review Script.

This script parses a unified PR diff, fetches inline review comments and a
high-level summary from the OpenRouter API, filters comments to target
only changed lines, and submits the review to GitHub.
"""

import json
import os
import sys
from typing import Any
import urllib.error
import urllib.request


def get_modified_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse a unified diff and return a map of files to their modified new line numbers."""
    modified: dict[str, set[int]] = {}
    current_file: str | None = None
    new_line_num: int = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            modified[current_file] = set()
        elif line.startswith("@@"):
            try:
                # Format: @@ -old_start,old_len +new_start,new_len @@ ...
                parts = line.split(" ")
                if len(parts) >= 3:
                    new_info = parts[2]  # e.g., "+new_start,new_len" or "+new_start"
                    if new_info.startswith("+"):
                        new_start = int(new_info[1:].split(",")[0])
                        new_line_num = new_start - 1
            except (ValueError, IndexError):
                # If parsing fails, ignore this hunk's start and skip line counting
                current_file = None
        elif current_file is not None:
            if line.startswith("+") and not line.startswith("+++"):
                new_line_num += 1
                modified[current_file].add(new_line_num)
            elif line.startswith(" "):
                new_line_num += 1
            elif line.startswith("-") and not line.startswith("---"):
                pass  # Deletions do not affect lines in the new file

    return modified


def parse_llm_json(response_text: str) -> dict[str, Any]:
    """Parse JSON output from LLM, stripping markdown block wrappers or extracting the JSON block."""
    response_text = response_text.strip()

    # Try direct parse first
    try:
        result = json.loads(response_text)
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
            result = json.loads(cleaned)
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
            result = json.loads(json_candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON object found in LLM response")


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


def _send_request(url: str, payload: dict[str, Any], headers: dict[str, str]) -> str:
    """Send HTTP request to OpenRouter and handle errors/timeouts.

    Args:
        url: The target OpenRouter API endpoint URL.
        payload: The request body containing model instructions and settings.
        headers: Dict containing headers (Authorization, X-Title, etc.).

    Returns:
        The raw text content returned in the model's chat completion choice.

    Raises:
        ValueError: If the response is empty, malformed, or missing message content.
        urllib.error.HTTPError: If the server returns a non-2xx status code.
    """
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            res_content = res.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
            res_data = json.loads(res_content)
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
            content = choices[0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("OpenRouter returned non-string message content.")
            return content
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = "<could not read HTTP error body>"
        raise ValueError(f"OpenRouter HTTP Error {e.code}: {err_body}") from e


def make_openrouter_request(api_key: str, diff_content: str) -> str:
    """Call OpenRouter API to review the diff content using Gemini 2.0 Flash."""
    url = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
    )
    repo = os.environ.get("REPO", "pr-code-review-action")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": f"https://github.com/{repo}",
        "X-Title": "PR Review Bot",
    }

    system_instruction = (
        "You are an expert, constructive code reviewer. Analyze the provided pull request diff.\n"
        "Generate concrete, helpful code review feedback targeting issues, bugs, and improvements.\n"
        "CRITICAL RULES for inline comments:\n"
        "1. ONLY comment on lines that have actual bugs, logic errors, safety/security concerns, "
        "performance bottlenecks, or critical readability issues that require a change.\n"
        "2. Do NOT comment on lines that are correct, well-written, or look good.\n"
        "3. Do NOT make general observations, positive reinforcement remarks, or explain how the code works.\n"
        "4. If a file or line is fine, do NOT generate any inline comments for it.\n\n"
        "Group your findings into:\n"
        "1. Critical correctness or logic bugs (edge cases, off-by-one errors).\n"
        "2. Security vulnerabilities.\n"
        "3. Clear performance bottlenecks.\n"
        "4. Architectural and readability suggestions.\n\n"
        "Respond ONLY with a JSON object. Do not wrap the JSON object in markdown code block markers. "
        "The JSON response must match this schema exactly:\n"
        "{\n"
        '  "summary": "Overall high-level markdown summary of the review findings, tables of file checks, and final recommendation.",\n'
        '  "comments": [\n'
        "    {\n"
        '      "path": "file/path/here",\n'
        '      "line": 42,\n'
        '      "body": "Markdown string containing the specific feedback for this line. Be constructive."\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    model = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash")
    try:
        max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", "4096"))
    except ValueError:
        max_tokens = 4096

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Please review this diff:\n\n{diff_content}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    print(f"Requesting review from OpenRouter using model: {model} (JSON mode)...")
    try:
        return _send_request(url, payload, headers)
    except Exception as e:
        err_msg = str(e)
        if "HTTP Error 400" in err_msg or "HTTP Error 422" in err_msg:
            print(
                f"Warning: JSON-mode request failed: {e}. Retrying without strict JSON format constraint...",
                file=sys.stderr,
            )
            payload_fallback = payload.copy()
            payload_fallback.pop("response_format", None)
            try:
                return _send_request(url, payload_fallback, headers)
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
    # Load and validate environment variables
    api_key = os.environ.get("OPENROUTER_API_KEY")
    pr_number = os.environ.get("PR_NUMBER")
    repo = os.environ.get("REPO")
    token = os.environ.get("GH_TOKEN")

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

    # Truncate diff if it is too large (> 500KB) to stay within LLM token limits
    is_truncated = len(diff_content) > 500000
    diff_content = truncate_diff(diff_content, 500000)

    print("Parsing modified line numbers from diff...")
    modified_lines = get_modified_lines(diff_content)

    print("Requesting review from OpenRouter...")
    try:
        raw_response = make_openrouter_request(api_key, diff_content)
    except Exception as e:
        print(f"Error: API request failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        review_data = parse_llm_json(raw_response)
        summary = review_data.get("summary", "Automated code review completed.")
        if not isinstance(summary, str):
            summary = str(summary)
        llm_comments = review_data.get("comments", [])
        if not isinstance(llm_comments, list):
            llm_comments = []
    except Exception as e:
        print(
            f"Warning: Failed to parse structured JSON from LLM review: {e}. "
            "Falling back to posting raw response in summary.",
            file=sys.stderr,
        )
        summary = f"### Automated PR Review Summary\n\n{raw_response}"
        llm_comments = []

    if is_truncated:
        summary += (
            "\n\n> [!WARNING]\n> **Note**: The pull request diff was truncated "
            "because it exceeded the size limit (500KB). Some changes or files "
            "might not have been fully reviewed."
        )

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
        except ValueError:
            continue

        # Enforce that the path exists in our diff and the line number is part of the changed chunk
        if path in modified_lines and line_int in modified_lines[path]:
            valid_comments.append(
                {
                    "path": path,
                    "line": line_int,
                    "side": "RIGHT",
                    "body": body,
                }
            )
        else:
            print(f"Skipped comment targeting unchanged line: {path}:{line}")

    print(f"Submitting review to GitHub with {len(valid_comments)} inline comments...")
    submit_github_review(repo, pr_number, token, summary, valid_comments)


if __name__ == "__main__":
    main()
