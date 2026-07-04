"""Unit tests for the automated code review script."""

from pr_review import get_modified_lines, parse_llm_json, truncate_diff


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
