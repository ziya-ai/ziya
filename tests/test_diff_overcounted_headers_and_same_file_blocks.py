"""Three applier defects behind the long-red diff fixtures (2026-11-02).

1. ``clean_duplicate_headers`` deduped ``---``/``+++`` lines across the whole
   text, so a combined diff carrying the same file in several ``diff --git``
   blocks lost the later blocks' file headers; ``split_combined_diff`` then
   had no file to attach their hunks to and every hunk after the first was
   dropped (and, before that, the orphan header text was written into the
   file as a context line).  Dedupe is per block now.
2. A ``@@`` header that declares more lines than its body has made the
   parser read past the hunk.  ``lower_overcounted_hunk_headers`` lowers it
   -- but only for a hunk BOUNDED by a following header, where the body
   cannot have been truncated.  The final hunk's surplus may be truncated
   trailing context, and the fuzzy matcher's placement depends on it
   (prompts_manager_log_level_change).
3. The single-hunk "truncated full-file replacement" path fired on an
   8-line header over a 6-line body and rewrote the file from the body,
   dropping the two unmentioned lines and reporting changes_written=False
   for a file it had changed.  It now needs a surplus of 5+ lines and
   records the write.

The fixtures markdown_renderer_fence_lookahead_false_applied and
json_escape_sequence are the end-to-end checks; these are the seams.
"""
import os

from app.utils.diff_utils.parsing.diff_preprocessor import lower_overcounted_hunk_headers
from app.utils.diff_utils.pipeline.pipeline_manager import clean_duplicate_headers, apply_diff_pipeline


def _headers(text):
    return [l for l in text.splitlines() if l.startswith('@@')]


def test_bounded_overcounted_header_is_lowered_final_hunk_is_not():
    diff = (
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        "@@ -1,7 +1,7 @@\n a\n-b\n+B\n c\n"        # body 3/3, declared 7/7, bounded
        "@@ -20,7 +20,7 @@\n x\n-y\n+Y\n"          # body 2/2, declared 7/7, final
    )
    out = lower_overcounted_hunk_headers(diff)
    assert _headers(out) == ["@@ -1,3 +1,3 @@", "@@ -20,7 +20,7 @@"]


def test_lowering_never_raises_a_count():
    diff = "@@ -1,2 +1,2 @@\n a\n b\n c\n-d\n+D\n@@ -9,1 +9,1 @@\n z\n"
    assert _headers(lower_overcounted_hunk_headers(diff)) == ["@@ -1,2 +1,2 @@", "@@ -9,1 +9,1 @@"]


def test_duplicate_header_dedupe_is_per_block():
    diff = (
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-a\n+A\n"
        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -5,1 +5,1 @@\n-e\n+E\n"
    )
    cleaned = clean_duplicate_headers(diff)
    assert cleaned.count("+++ b/f.py") == 2, cleaned
    # A genuine repeat inside ONE block is still removed.
    dup = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-a\n+A\n"
    assert clean_duplicate_headers(dup).count("+++ b/f.py") == 1


def test_same_file_in_three_blocks_applies_every_hunk(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("a\nb\nc\nd\ne\nf\ng\nh\ni\n")
    block = "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"
    diff = (
        block + "@@ -1,2 +1,2 @@\n-a\n+A\n b\n"
        + block + "@@ -4,2 +4,2 @@\n-d\n+D\n e\n"
        + block + "@@ -8,2 +8,2 @@\n-h\n+H\n i\n"
    )
    r = apply_diff_pipeline(diff, str(f), user_codebase_dir=str(tmp_path))
    body = f.read_text()
    assert body == "A\nb\nc\nD\ne\nf\ng\nH\ni\n", body
    assert "+++" not in body and "diff --git" not in body
    assert r.get("status") == "success"


def test_small_overcount_on_tiny_file_keeps_unmentioned_tail(tmp_path):
    f = tmp_path / "t.ts"
    f.write_text("function f() {\n    const x = 1;\n    old();\n    return x;\n}\n")
    # Header claims the whole 5-line file; body mentions 3 lines.  Not a
    # whole-file rewrite -- the surplus is a miscount, and lines 4-5 stay.
    diff = (
        "diff --git a/t.ts b/t.ts\n--- a/t.ts\n+++ b/t.ts\n"
        "@@ -1,5 +1,5 @@\n function f() {\n     const x = 1;\n-    old();\n+    fresh();\n"
    )
    r = apply_diff_pipeline(diff, str(f), user_codebase_dir=str(tmp_path))
    assert f.read_text() == "function f() {\n    const x = 1;\n    fresh();\n    return x;\n}\n"
    assert r.get("changes_written") is True
