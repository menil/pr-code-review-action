"""Unit tests for the automated code review script."""

from unittest.mock import MagicMock, patch
from pr_review import get_modified_lines, parse_llm_json, truncate_diff, annotate_diff


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


@patch("urllib.request.urlopen")
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


@patch("urllib.request.urlopen")
def test_send_request_empty_choices(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = b'{"choices": []}'
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(ValueError, match="OpenRouter returned empty choices"):
        _send_request("http://dummy", {}, {})


@patch("urllib.request.urlopen")
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


@patch("urllib.request.urlopen")
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


@patch("urllib.request.urlopen")
def test_send_request_null_content_refusal(mock_urlopen: MagicMock) -> None:
    import pytest
    from pr_review import _send_request

    mock_res = MagicMock()
    mock_res.read.return_value = b'{"choices": [{"message": {"content": null, "refusal": "I cannot review this"}, "finish_reason": "refusal"}]}'
    mock_urlopen.return_value.__enter__.return_value = mock_res

    with pytest.raises(ValueError, match="OpenRouter Refusal: I cannot review this"):
        _send_request("http://dummy", {}, {})


@patch("urllib.request.urlopen")
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
