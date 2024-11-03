import pytest
from app.utils.code_util import correct_git_diff, _find_correct_old_start_line, _format_hunk_header
from unittest.mock import mock_open, patch


@pytest.fixture
def sample_file_content():
    return [
        "Line 1",
        "Line 2",
        "Line 3",
        "Line 4",
        "Line 5",
        "Line 6",
        "Line 7",
        "Line 8",
        "Line 9",
    ]

def test_format_hunk_header():
    # Test with count = 1 (should omit count)
    assert _format_hunk_header(1, 1, 1, 1) == "@@ -1 +1 @@"

    # Test with different counts
    assert _format_hunk_header(1, 3, 1, 2) == "@@ -1,3 +1,2 @@"

    # Test with larger numbers
    assert _format_hunk_header(10, 5, 10, 6) == "@@ -10,5 +10,6 @@"


def test_find_correct_start_line(sample_file_content):
    # Test finding a line in the middle of the file
    hunk_lines = [
        " Line 4",
        "-Line 5",
        "+New line",
        " Line 6"
    ]
    assert _find_correct_old_start_line(sample_file_content, hunk_lines) == 4

    # Test finding the first line
    hunk_lines = [
        " Line 1",
        " Line 2",
        "+New Line"
    ]
    assert _find_correct_old_start_line(sample_file_content, hunk_lines) == 1

    # Test with empty original content (new file)
    assert _find_correct_old_start_line([], [" New line"]) == 0


def test_find_correct_start_line_invalid_hunk(sample_file_content):
    # Test with invalid hunk (no context or deleted lines)
    original_content = sample_file_content[:3]
    with pytest.raises(RuntimeError, match="Invalid git diff format."):
        _find_correct_old_start_line(["content"], ["+new line"])


def test_fail_to_locate_hunk_position():
    # Test when pattern cannot be found in original content
    with pytest.raises(RuntimeError, match="Failed to locate the hunk position"):
        _find_correct_old_start_line(
            ["Line 1", "Line 2", "Line 3", "Line 4"],
            [" Different line", "-Wrong content", "-Wrong content"]
        )


def test_correct_git_diff_new_file():
    new_file_diff = """diff --git a/dev/null b/new_file.txt
new file mode 100644
--- /dev/null
+++ b/new_file.txt
@@ -0,0 +1,3 @@
+Line 1
+Line 2
+Line 3"""

    corrected_diff = correct_git_diff(new_file_diff, "new_file.txt")
    assert "new file mode 100644" in corrected_diff
    assert "@@ -0,0 +1,3 @@" in corrected_diff


def test_correct_git_diff_modify_file(sample_file_content):
    file_content = "\n".join(sample_file_content[:4])
    modify_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
+++ b/test_filename
@@ -1,4 +1,4 @@
 Line 1
+New line
 Line 2
 Line 3
 Line 4"""

    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(modify_diff, str())
    assert "@@ -1,4 +1,5 @@" in corrected_diff


def test_correct_git_diff_modify_file_with_new_line_end( sample_file_content):

    file_content = "\n".join(
        [
            "Line 1",
            "",
            "Line 3\n",
        ]
    )

    modify_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
+++ b/test_filename
@@ -1,4 +1,2 @@
 Line 1
 
 Line 3
+new line"""

    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(modify_diff, str())
    assert "@@ -1,3 +1,4 @@" in corrected_diff



def test_correct_git_diff_modify_file_begin_with_deleted_line(sample_file_content):
    file_content = "\n".join(sample_file_content[:4])

    modify_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
+++ b/test_filename
@@ -1,4 +1,4 @@
-Line 1
 Line 2
 Line 3
 Line 4"""
    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(modify_diff, str())
    assert "@@ -1,4 +1,3 @@" in corrected_diff


def test_correct_git_diff_multiple_hunks_insert_line_after_insert_line(sample_file_content):
    file_content = "\n".join(sample_file_content[:6])

    multi_hunk_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
+++ b/test_filename
@@ -1,3 +1,4 @@
 Line 1
+New line
 Line 2
 Line 3
 Line 4
@@ -4,3 +5,4 @@
+Another line
 Line 5
 Line 6"""
    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(multi_hunk_diff, str())
    assert "@@ -1,4 +1,5 @@" in corrected_diff
    assert "@@ -5,2 +6,3 @@" in corrected_diff


def test_correct_git_diff_multiple_hunks_insert_line_after_delete_line(sample_file_content):
    file_content = "\n".join(sample_file_content[:6])

    multi_hunk_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
++ b/test_filename
@@ -1,3 +1,2 @@
 Line 1
-Line 2
 Line 3
 Line 4
@@ -4,3 +5,4 @@
+New line
 Line 5
 Line 6"""
    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(multi_hunk_diff, str())
    assert "@@ -1,4 +1,3 @@" in corrected_diff
    assert "@@ -5,2 +4,3 @@" in corrected_diff


def test_correct_git_diff_multiple_hunks_delete_line_after_delete_line(sample_file_content):
    file_content = "\n".join(sample_file_content[:6])

    multi_hunk_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
++ b/test_filename
@@ -1,3 +1,2 @@
 Line 1
-Line 2
 Line 3
@@ -4,3 +5,4 @@
 Line 4
-Line 5
 Line 6"""
    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(multi_hunk_diff, str())
    assert "@@ -1,3 +1,2 @@" in corrected_diff
    assert "@@ -4,3 +3,2 @@" in corrected_diff


def test_correct_git_diff_multiple_hunks_delete_line_after_insert_line(sample_file_content):
    file_content = "\n".join(sample_file_content[:6])

    multi_hunk_diff = """diff --git a/test_filename b/test_filename
--- a/test_filename
++ b/test_filename
@@ -1,2 +1,4 @@
 Line 1
+New line
 Line 2
 Line 3
@@ -4,3 +5,4 @@
 Line 4
-Line 5
 Line 6"""
    with patch("builtins.open", mock_open(read_data=file_content)) as mock_file:
        corrected_diff = correct_git_diff(multi_hunk_diff, str())
    assert "@@ -1,3 +1,4 @@" in corrected_diff
    assert "@@ -4,3 +5,2 @@" in corrected_diff


def test_correct_git_diff_file_not_found():
    diff = """diff --git a/nonexistent.txt b/nonexistent.txt
--- a/nonexistent.txt
+++ b/nonexistent.txt
@@ -1,4 +1,5 @@
 Line 1
+New line
 Line 2
 Line 3"""

    with pytest.raises(FileNotFoundError):
        correct_git_diff(diff, "nonexistent.txt")
