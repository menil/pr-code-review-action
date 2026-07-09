"""Unit tests for the automated code review script."""

from unittest.mock import MagicMock, patch
from pr_review import (
    get_modified_lines,
    parse_llm_json,
    truncate_diff,
    annotate_diff,
    clean_markdown_line,
    parse_markdown_comments,
)


def test_get_modified_lines_single_file() -> None:
    diff_text = """diff --git a/src/main.py b/src/main.py
index 1234567..89abcde 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,6 +10,8 @@ def hello():
  def hello():
      print("hello")
-     print("old")
+     print("new")
+     print("another new")
      return True
"""
    modified = get_modified_lines(diff_text)
    assert "src/main.py" in modified
    # Added lines are 12 and 13 in the new file (10: def, 11: print hello, 12: new, 13: another new)
    assert modified["src/main.py"] == {12, 13}


def test_get_modified_lines_multiple_files() -> None:
    diff_text = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,4 @@
  line1
-line2
+ line2_mod
  line3
+ line4_add
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -5,2 +5,3 @@
  unchanged
+ added_line
"""
    modified = get_modified_lines(diff_text)
    assert "file1.py" in modified
    assert "file2.py" in modified
    # file1.py: lines 2 and 4 are modified/added
    assert modified["file1.py"] == {2, 4}
    # file2.py: line 6 is added (5: unchanged, 6: added_line)
    assert modified["file2.py"] == {6}


def test_get_modified_lines_deleted_file() -> None:
    diff_text = """diff --git a/deleted.py b/deleted.py
deleted file mode 100644
--- a/deleted.py
+++ /dev/null
@@ -1,2 +0,0 @@
-line1
-line2
"""
    modified = get_modified_lines(diff_text)
    # The target file is /dev/null or deleted, shouldn't be counted as a new modified file
    assert "/dev/null" not in modified
    assert "deleted.py" not in modified


def test_parse_llm_json_clean() -> None:
    json_str = '{"summary": "Looks good", "comments": [{"path": "main.py", "line": 5, "body": "nice"}]}'
    result = parse_llm_json(json_str)
    assert result["summary"] == "Looks good"
    assert len(result["comments"]) == 1
    assert result["comments"][0]["line"] == 5


def test_parse_llm_json_wrapped() -> None:
    wrapped_str = """```json
{
  "summary": "wrapped",
  "comments": []
}
```"""
    result = parse_llm_json(wrapped_str)
    assert result["summary"] == "wrapped"
    assert result["comments"] == []


def test_truncate_diff() -> None:
    # Under limit: remains untouched
    assert truncate_diff("hello world", 50) == "hello world"

    # Over limit: truncates at last newline
    long_text = "line1\nline2\nline3\nline4"
    # limit of 15 lands on index 15 which is inside line3 ('line1\nline2\nline3')
    # last newline before 15 is index 11 ('\n' after line2)
    truncated = truncate_diff(long_text, 15)
    assert truncated.startswith("line1\nline2\n\n")
    assert "truncated" in truncated

    # Over limit: no newline before limit, fallback to hard truncation
    no_newline_text = "abcdefghijklmnop"
    truncated_no_nl = truncate_diff(no_newline_text, 5)
    assert truncated_no_nl.startswith("abcde\n\n")
    assert "truncated" in truncated_no_nl


@patch("pr_review.urllib.request.urlopen")
def test_send_request_success(mock_urlopen: MagicMock) -> None:
    import json
    import urllib.request
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = json.dumps(
        {"choices": [{"message": {"content": '{"summary": "OK"}'}}]}
    ).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_res

    content = _send_request(
        url="http://dummy",
        payload={"dummy": "data"},
        headers={"Authorization": "Bearer key"},
    )

    assert content == '{"summary": "OK"}'
    mock_urlopen.assert_called_once()
    args, kwargs = mock_urlopen.call_args
    assert isinstance(args[0], urllib.request.Request)
    assert kwargs.get("timeout") == 30


@patch("pr_review.urllib.request.urlopen")
def test_send_request_empty_choices(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = b'{"choices": []}'
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(ValueError, match="OpenRouter returned empty choices"):
        _send_request("http://dummy", {}, {})


@patch("pr_review.urllib.request.urlopen")
def test_send_request_non_string_content(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = b'{"choices": [{"message": {"content": 123}}]}'
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(
        ValueError, match="OpenRouter returned non-string message content"
    ):
        _send_request("http://dummy", {}, {})


@patch("pr_review.urllib.request.urlopen")
def test_send_request_error_response(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = (
        b'{"error": {"message": "Model not found", "code": 404}}'
    )
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(ValueError, match="OpenRouter Error: Model not found"):
        _send_request("http://dummy", {}, {})


@patch("pr_review.urllib.request.urlopen")
def test_send_request_null_content_refusal(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = b'{"choices": [{"message": {"content": null, "refusal": "I cannot review this"}, "finish_reason": "refusal"}]}'
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(ValueError, match="OpenRouter Refusal: I cannot review this"):
        _send_request("http://dummy", {}, {})


@patch("pr_review.urllib.request.urlopen")
def test_send_request_null_content_no_refusal(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = b'{"choices": [{"message": {"content": null}}]}'
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(
        ValueError, match="OpenRouter returned message with null content"
    ):
        _send_request("http://dummy", {}, {})


@patch("pr_review._send_request")
def test_make_openrouter_request_success(mock_send_request: MagicMock) -> None:
    from pr_review import make_openrouter_request

    mock_send_request.return_value = '{"summary": "Review complete"}'

    result = make_openrouter_request("fake_key", "fake_diff")

    assert result == '{"summary": "Review complete"}'
    mock_send_request.assert_called_once()
    args, kwargs = mock_send_request.call_args
    payload = args[1]
    assert payload["response_format"] == {"type": "json_object"}


@patch("pr_review._send_request")
def test_make_openrouter_request_comments_only(
    mock_send_request: MagicMock,
) -> None:
    from pr_review import make_openrouter_request

    mock_send_request.return_value = '{"comments": []}'

    result = make_openrouter_request("fake_key", "fake_diff", post_summary=False)

    assert result == '{"comments": []}'
    mock_send_request.assert_called_once()
    args, kwargs = mock_send_request.call_args
    payload = args[1]
    assert payload["response_format"] == {"type": "json_object"}
    system_msg = payload["messages"][0]["content"]
    # Check that it uses the simplified instruction file
    assert "comments" in system_msg
    assert "thinking" not in system_msg


@patch("pr_review._send_request")
def test_make_openrouter_request_fallback_success(mock_send_request: MagicMock) -> None:
    from pr_review import make_openrouter_request

    mock_send_request.side_effect = [
        Exception("HTTP Error 400: Bad Request"),
        '{"summary": "Fallback works"}',
    ]

    result = make_openrouter_request("fake_key", "fake_diff")

    assert result == '{"summary": "Fallback works"}'
    assert mock_send_request.call_count == 2

    first_call_payload = mock_send_request.call_args_list[0][0][1]
    assert "response_format" in first_call_payload

    second_call_payload = mock_send_request.call_args_list[1][0][1]
    assert "response_format" not in second_call_payload


@patch("pr_review._send_request")
def test_make_openrouter_request_fallback_failure(mock_send_request: MagicMock) -> None:
    import pytest
    from pr_review import make_openrouter_request

    mock_send_request.side_effect = [
        Exception("HTTP Error 400: Bad Request"),
        Exception("Fallback failed"),
    ]

    with pytest.raises(Exception, match="Fallback failed"):
        make_openrouter_request("fake_key", "fake_diff")
    assert mock_send_request.call_count == 2


@patch("pr_review._send_request")
def test_make_openrouter_request_no_fallback_on_unauthorized(
    mock_send_request: MagicMock,
) -> None:
    import pytest
    from pr_review import make_openrouter_request

    mock_send_request.side_effect = Exception("HTTP Error 401: Unauthorized")

    with pytest.raises(Exception, match="HTTP Error 401: Unauthorized"):
        make_openrouter_request("fake_key", "fake_diff")
    assert mock_send_request.call_count == 1


@patch("pr_review._send_request")
def test_make_openrouter_request_fallback_on_value_error(
    mock_send_request: MagicMock,
) -> None:
    from pr_review import make_openrouter_request

    mock_send_request.side_effect = [
        ValueError("OpenRouter returned message with null content"),
        '{"summary": "Fallback works after value error"}',
    ]

    result = make_openrouter_request("fake_key", "fake_diff")

    assert result == '{"summary": "Fallback works after value error"}'
    assert mock_send_request.call_count == 2


def test_annotate_diff() -> None:
    diff_text = """diff --git a/src/main.py b/src/main.py
index 1234567..89abcde 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,6 +10,8 @@ def hello():
  def hello():
      print("hello")
-     print("old")
+     print("new")
+     print("another new")
      return True
"""
    annotated = annotate_diff(diff_text)
    lines = annotated.splitlines()
    assert "+++ b/src/main.py" in lines
    assert "@@ -10,6 +10,8 @@ def hello():" in lines
    assert any(line.endswith(': -     print("old")') for line in lines)
    assert "   10:   def hello():" in lines
    assert '   11:       print("hello")' in lines
    assert '   12: +     print("new")' in lines
    assert '   13: +     print("another new")' in lines
    assert "   14:       return True" in lines


def test_get_modified_lines_quoted_path() -> None:
    diff_text = """diff --git a/src/my file.py b/src/my file.py
--- a/src/my file.py
+++ "b/src/my file.py"
@@ -1,2 +1,3 @@
  hello
+ world
"""
    modified = get_modified_lines(diff_text)
    assert "src/my file.py" in modified
    assert modified["src/my file.py"] == {2}


def test_parse_llm_json_trailing_commas() -> None:
    json_str = """
    {
        "summary": "OK",
        "comments": [
            {
                "path": "main.py",
                "line": 5,
                "body": "test comma",
            },
        ],
    }
    """
    result = parse_llm_json(json_str)
    assert result["summary"] == "OK"
    assert len(result["comments"]) == 1
    assert result["comments"][0]["line"] == 5


def test_parse_llm_json_unescaped_control_chars() -> None:
    # A JSON string containing a literal (raw) newline inside a string value.
    raw_newline_json = '{"summary": "Line 1\nLine 2", "comments": []}'
    result = parse_llm_json(raw_newline_json)
    assert result["summary"] == "Line 1\nLine 2"


def test_clean_markdown_line() -> None:
    assert clean_markdown_line("### src/app.rs") == "src/app.rs"
    assert clean_markdown_line("In src/app.rs:") == "In src/app.rs:"
    assert clean_markdown_line("**src/app.rs**") == "src/app.rs"
    assert clean_markdown_line("`src/app.rs`") == "src/app.rs"
    assert clean_markdown_line("   ###  `src/app.rs`  ") == "src/app.rs"


def test_parse_markdown_comments() -> None:
    modified_lines = {
        "src/app.rs": {81, 103, 112},
        "src/fs.rs": {42, 43, 44},
    }

    markdown_text = """
    We will go through the passes.

    In app.rs:
     - Line 81: `current_dir: start_path.canonicalize()`
       This is acceptable because...
       It should handle error.

     - Lines 103: self.entries = list_dir()
       This is okay.

    ### Review for fs.rs
    - Line 42-44: split_name_ext
      This is a good helper function.
    """

    comments = parse_markdown_comments(markdown_text, modified_lines)
    assert len(comments) == 3

    c1 = comments[0]
    assert c1["path"] == "src/app.rs"
    assert c1["line"] == 81
    assert "This is acceptable because" in c1["body"]
    assert "It should handle error." in c1["body"]

    c2 = comments[1]
    assert c2["path"] == "src/app.rs"
    assert c2["line"] == 103
    assert c2["body"] == "self.entries = list_dir()\n       This is okay."

    c3 = comments[2]
    assert c3["path"] == "src/fs.rs"
    assert c3["line"] == 44
    assert "This is a good helper function." in c3["body"]


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_retry_on_429_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import urllib.error
    from pr_review import _send_request

    # First attempt raises HTTPError 429
    mock_headers = MagicMock()
    mock_headers.get.return_value = "5"  # Retry-After header

    # We need an HTTPError mock
    err_body = b'{"error": {"message": "Rate limited", "metadata": {"retry_after_seconds": 10}}}'
    fp = MagicMock()
    fp.read.return_value = err_body

    http_err = urllib.error.HTTPError(
        "http://dummy", 429, "Too Many Requests", mock_headers, fp
    )

    # Second attempt succeeds
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = b'{"choices": [{"message": {"content": "Success"}}]}'

    # Set side effect
    mock_urlopen.side_effect = [http_err, mock_res]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()
    # Check that sleep duration was at least the retry_after value
    sleep_args, _ = mock_sleep.call_args
    assert sleep_args[0] >= 10.0


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_retry_on_network_error_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    from pr_review import _send_request
    import urllib.error

    # First attempt raises URLError
    url_err = urllib.error.URLError("DNS resolution failed")

    # Second attempt succeeds
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = (
        b'{"choices": [{"message": {"content": "Success Network"}}]}'
    )

    mock_urlopen.side_effect = [url_err, mock_res]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success Network"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_rate_limit_in_json_body_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    from pr_review import _send_request

    # First attempt returns 200 OK but with error in JSON payload
    mock_res_err = MagicMock()
    mock_res_err.__enter__.return_value = mock_res_err
    mock_res_err.read.return_value = b'{"error": {"message": "Rate limit reached", "code": 429, "metadata": {"retry_after_seconds": 3}}}'

    # Second attempt succeeds
    mock_res_ok = MagicMock()
    mock_res_ok.__enter__.return_value = mock_res_ok
    mock_res_ok.read.return_value = (
        b'{"choices": [{"message": {"content": "Success JSON"}}]}'
    )

    mock_urlopen.side_effect = [mock_res_err, mock_res_ok]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success JSON"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()
    sleep_args, _ = mock_sleep.call_args
    assert sleep_args[0] >= 3.0


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_max_retries_exhausted(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import pytest
    import urllib.error
    from pr_review import _send_request

    # Always raise URLError
    mock_urlopen.side_effect = urllib.error.URLError("Connection timed out")

    with pytest.raises(urllib.error.URLError, match="Connection timed out"):
        _send_request("http://dummy", {}, {})

    # 1 initial attempt + 5 retries = 6 calls total
    assert mock_urlopen.call_count == 6
    assert mock_sleep.call_count == 5


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_stale_error_body_bug(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import pytest
    import urllib.error
    from pr_review import _send_request

    # First attempt raises HTTPError 429 (retryable)
    mock_headers_429 = MagicMock()
    fp_429 = MagicMock()
    fp_429.read.return_value = b"Rate limit details"
    err_429 = urllib.error.HTTPError(
        "http://dummy", 429, "Too Many Requests", mock_headers_429, fp_429
    )

    # Second attempt raises HTTPError 400 (non-retryable)
    mock_headers_400 = MagicMock()
    fp_400 = MagicMock()
    fp_400.read.return_value = b"Bad Request details"
    err_400 = urllib.error.HTTPError(
        "http://dummy", 400, "Bad Request", mock_headers_400, fp_400
    )

    mock_urlopen.side_effect = [err_429, err_400]

    with pytest.raises(ValueError) as excinfo:
        _send_request("http://dummy", {}, {})

    assert "HTTP Error 400: Bad Request details" in str(excinfo.value)


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_retry_on_503_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import urllib.error
    from pr_review import _send_request

    # First attempt raises HTTPError 503 (retryable)
    mock_headers = MagicMock()
    fp = MagicMock()
    fp.read.return_value = b"Service Unavailable"
    http_err_503 = urllib.error.HTTPError(
        "http://dummy", 503, "Service Unavailable", mock_headers, fp
    )

    # Second attempt succeeds
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = (
        b'{"choices": [{"message": {"content": "Success 503"}}]}'
    )

    mock_urlopen.side_effect = [http_err_503, mock_res]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success 503"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_retry_after_header_only_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import urllib.error
    from pr_review import _send_request

    # First attempt raises HTTPError 429 with Retry-After header, no body metadata
    mock_headers = MagicMock()
    mock_headers.get.side_effect = lambda name, default=None: (
        "15" if name == "Retry-After" else default
    )
    fp = MagicMock()
    fp.read.return_value = b""  # empty/invalid JSON body
    http_err = urllib.error.HTTPError(
        "http://dummy", 429, "Too Many Requests", mock_headers, fp
    )

    # Second attempt succeeds
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = (
        b'{"choices": [{"message": {"content": "Success Header"}}]}'
    )

    mock_urlopen.side_effect = [http_err, mock_res]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success Header"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()
    sleep_args, _ = mock_sleep.call_args
    assert sleep_args[0] >= 15.0


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_retry_after_invalid_header_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import urllib.error
    from pr_review import _send_request

    # First attempt raises HTTPError 429 with invalid Retry-After header
    mock_headers = MagicMock()
    mock_headers.get.side_effect = lambda name, default=None: (
        "invalid_delay" if name == "Retry-After" else default
    )
    fp = MagicMock()
    fp.read.return_value = b""
    http_err = urllib.error.HTTPError(
        "http://dummy", 429, "Too Many Requests", mock_headers, fp
    )

    # Second attempt succeeds
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = (
        b'{"choices": [{"message": {"content": "Success Invalid Header"}}]}'
    )

    mock_urlopen.side_effect = [http_err, mock_res]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success Invalid Header"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()
    sleep_args, _ = mock_sleep.call_args
    assert sleep_args[0] >= 1.0  # base_delay is 1.0


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_http_error_exhausted(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    import pytest
    import urllib.error
    from pr_review import _send_request

    # Always raise HTTPError 500
    mock_headers = MagicMock()
    fp = MagicMock()
    fp.read.return_value = b"Internal Server Error"
    http_err = urllib.error.HTTPError(
        "http://dummy", 500, "Internal Server Error", mock_headers, fp
    )
    mock_urlopen.side_effect = http_err

    with pytest.raises(ValueError) as excinfo:
        _send_request("http://dummy", {}, {})

    assert "OpenRouter HTTP Error 500: Internal Server Error" in str(excinfo.value)
    # 1 initial + 5 retries = 6 attempts total
    assert mock_urlopen.call_count == 6
    assert mock_sleep.call_count == 5


@patch("time.sleep")
@patch("pr_review.urllib.request.urlopen")
def test_send_request_timeout_error_then_success(
    mock_urlopen: MagicMock, mock_sleep: MagicMock
) -> None:
    from pr_review import _send_request

    # First attempt raises TimeoutError
    time_err = TimeoutError("Request timed out")

    # Second attempt succeeds
    mock_res = MagicMock()
    mock_res.__enter__.return_value = mock_res
    mock_res.read.return_value = (
        b'{"choices": [{"message": {"content": "Success Timeout"}}]}'
    )

    mock_urlopen.side_effect = [time_err, mock_res]

    content = _send_request("http://dummy", {}, {})
    assert content == "Success Timeout"
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once()
