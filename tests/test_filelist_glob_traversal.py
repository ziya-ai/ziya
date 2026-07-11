"""
Regression coverage for PenPal #86 [LOW, CWE-22]: path traversal via an
unvalidated glob `pattern` in FileListTool.

`path` was validated by _resolve_and_validate (rejects ".."), but `pattern`
flowed straight into Path.glob(). Since ".." is not special-cased by glob on
Python <3.13, pattern="../../../etc/*" traversed out of the project root and
enumerated filenames/sizes anywhere the process user could read; the lexical
relative_to() result filter is defeated by a "..".-containing path. Fixed by
rejecting a ".." path component in the pattern at the input boundary.
"""
import os
import tempfile
from pathlib import Path

import pytest
from app.mcp.tools.fileio import FileListTool


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.py").write_text("y = 2\n")
    # A sentinel OUTSIDE the project root — a traversal glob would surface it.
    outside = tmp_path.parent / f"SENTINEL_{tmp_path.name}.txt"
    outside.write_text("secret")
    yield tmp_path, outside
    if outside.exists():
        outside.unlink()


class TestPatternTraversalBlocked:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("pattern", [
        "../../../etc/*",
        "../*",
        "src/../../*",
        "..\\..\\*",          # backslash separator (Windows / literal-on-POSIX)
        "a/../../b",
    ])
    async def test_traversal_pattern_refused(self, project, pattern):
        proj, _ = project
        res = await FileListTool().execute(
            _workspace_path=str(proj), path="src", pattern=pattern,
        )
        assert res.get("error") is True
        assert ".." in res["message"]

    @pytest.mark.asyncio
    async def test_traversal_never_reaches_outside_sentinel(self, project):
        proj, outside = project
        res = await FileListTool().execute(
            _workspace_path=str(proj), path="src", pattern="../../*",
        )
        assert res.get("error") is True
        # The out-of-project sentinel name must never appear in output.
        assert outside.name not in str(res)


class TestBenignPatternsStillWork:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("pattern", ["*.py", "**/*.py", None, "*"])
    async def test_benign_pattern_allowed(self, project, pattern):
        proj, _ = project
        res = await FileListTool().execute(
            _workspace_path=str(proj), path="src", pattern=pattern,
        )
        assert not res.get("error"), res
        assert "a.py" in str(res)

    @pytest.mark.asyncio
    async def test_filename_containing_dotdot_not_falsely_blocked(self, project):
        # "a..b" contains ".." but is NOT a ".." path component — must pass.
        proj, _ = project
        (proj / "src" / "a..b.py").write_text("z = 3\n")
        res = await FileListTool().execute(
            _workspace_path=str(proj), path="src", pattern="a..b*",
        )
        assert not res.get("error"), res


class TestNegativeControlPreFix:
    """Proves the vuln was real (test is non-vacuous): the raw glob a ".."
    pattern would have used does reach outside the listing dir."""

    def test_raw_glob_dotdot_traverses(self, project):
        proj, outside = project
        hits = list((proj / "src").glob(f"../../{outside.name}"))
        assert len(hits) == 1  # pre-fix, this leaked into the tool output
