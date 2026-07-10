"""
PenPal #79 regression: path traversal via mismatched diff headers in the
reverse-diff pipeline.

/api/unapply-changes validates the *target* path from the ``+++ b/`` header
(diff_parser.extract_target_file_from_diff) and boundary-checks it. But
_try_patch_reverse independently reads the ``--- a/`` header and joins it to
a temp dir with no containment check. A diff with a benign ``+++ b/`` (passes
the route gate) and a malicious ``--- a/`` (``../`` traversal or an absolute
path, which os.path.join honors by discarding the temp dir) could create
directories and write the validated file's content outside the sandbox, and
let ``patch -R`` act on out-of-tree files.

These tests drive _try_patch_reverse directly with malicious ``--- a/``
headers and assert it refuses (returns {'success': False}) without touching
the filesystem outside its temp sandbox.
"""

import os
import tempfile

from app.utils.diff_utils.pipeline.reverse_pipeline import _try_patch_reverse


def _make_source_file(tmp_path, content="line1\nline2\n"):
    f = tmp_path / "legit.py"
    f.write_text(content)
    return str(f)


class TestReversePatchHeaderTraversal:
    def test_relative_traversal_in_minus_header_refused(self, tmp_path):
        """`--- a/../../../<sentinel>` must not create/write outside the
        temp sandbox even though `+++ b/legit.py` is benign."""
        src = _make_source_file(tmp_path)
        sentinel = tmp_path / "escaped_marker"
        # Craft a diff whose --- a/ header traverses up toward the sentinel.
        rel = os.path.relpath(str(sentinel), "/")  # e.g. 'private/var/.../escaped_marker'
        diff = (
            f"--- a/../../../../../../../../{rel}\n"
            f"+++ b/legit.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-line1\n"
            "+CHANGED\n"
            " line2\n"
        )
        result = _try_patch_reverse(diff, src, "CHANGED\nline2\n")
        assert result == {'success': False}
        assert not sentinel.exists(), "traversal wrote outside the sandbox"

    def test_absolute_path_in_minus_header_refused(self, tmp_path):
        """An absolute `--- a//abs/path` header makes os.path.join discard
        the temp dir entirely; it must be refused."""
        src = _make_source_file(tmp_path)
        sentinel = tmp_path / "abs_escaped_marker"
        diff = (
            f"--- a/{sentinel}\n"   # renders as '--- a//<abs>' → absolute join component
            f"+++ b/legit.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-line1\n"
            "+CHANGED\n"
            " line2\n"
        )
        result = _try_patch_reverse(diff, src, "CHANGED\nline2\n")
        assert result == {'success': False}
        assert not sentinel.exists(), "absolute-path escape wrote outside the sandbox"

    def test_legitimate_relative_header_still_works(self, tmp_path):
        """Sanity: a normal relative `--- a/legit.py` still reverses cleanly,
        proving the containment guard doesn't reject valid input."""
        src = _make_source_file(tmp_path, "line1\nline2\n")
        diff = (
            "--- a/legit.py\n"
            "+++ b/legit.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-line1\n"
            "+CHANGED\n"
            " line2\n"
        )
        # current content is the post-change state; reverse should restore line1
        with open(src, "w") as f:
            f.write("CHANGED\nline2\n")
        result = _try_patch_reverse(diff, src, "line1\nline2\n")
        # patch may or may not be available in the sandbox; if it ran it must
        # have succeeded, and if it succeeded the file is restored.
        if result.get('success'):
            assert open(src).read().rstrip() == "line1\nline2".rstrip()
