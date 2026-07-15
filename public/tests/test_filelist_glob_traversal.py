"""
Regression coverage for PenPal #86 [LOW, CWE-22]: path traversal via an
unvalidated glob pattern in FileListTool.

FileListTool.execute() containment-checks its `path` argument via
_resolve_and_validate, but passed `pattern` straight to Path.glob().
Path.glob traverses `..` on Python < 3.13, so pattern="../../../etc/*"
escaped the validated base — and the result loop's symlink-recovery
fallback surfaced the out-of-tree entries (name + size), an enumeration
oracle. Confirmed live: an unguarded "../*.txt" listed a sibling file
outside the project root. Fixed by rejecting any `..` path *component*
in the pattern (split on both separators, platform-independent).

On success FileListTool returns {"content": <listing str>}; on rejection
it returns {"error": True, "message": ...}.
"""
import os
import tempfile
import pytest

from app.mcp.tools.fileio import FileListTool


@pytest.fixture
def project_with_secret_sibling():
    """A project root with a sibling file OUTSIDE it, reachable by a
    traversal pattern if the guard is absent."""
    root = tempfile.mkdtemp()
    parent = os.path.dirname(root)
    sentinel = os.path.join(parent, "penpal86_secret.txt")
    with open(sentinel, "w") as f:
        f.write("should never be listed")
    with open(os.path.join(root, "keep.py"), "w") as f:
        f.write("x = 1\n")
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "sub", "nested.py"), "w") as f:
        f.write("y = 2\n")
    yield root, sentinel
    try:
        os.remove(sentinel)
    except OSError:
        pass


class TestGlobTraversalBlocked:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("pattern", [
        "../*.txt",
        "../../*",
        "../../../etc/*",
        r"..\..\*",            # Windows-style separator
        "sub/../../*.txt",
    ])
    async def test_traversal_pattern_rejected(self, project_with_secret_sibling, pattern):
        root, _ = project_with_secret_sibling
        tool = FileListTool()
        result = await tool.execute(path=".", pattern=pattern, _workspace_path=root)
        assert result.get("error") is True
        assert ".." in result.get("message", "")
        # The sentinel's basename never appears in any output.
        assert "penpal86_secret.txt" not in str(result)

    @pytest.mark.asyncio
    async def test_sentinel_never_leaked(self, project_with_secret_sibling):
        root, _ = project_with_secret_sibling
        tool = FileListTool()
        result = await tool.execute(path=".", pattern="../*.txt", _workspace_path=root)
        assert "penpal86_secret.txt" not in str(result)


class TestBenignPatternsStillWork:
    @pytest.mark.asyncio
    async def test_simple_glob(self, project_with_secret_sibling):
        root, _ = project_with_secret_sibling
        tool = FileListTool()
        result = await tool.execute(path=".", pattern="*.py", _workspace_path=root)
        assert not result.get("error")
        assert "keep.py" in result.get("content", "")

    @pytest.mark.asyncio
    async def test_recursive_glob(self, project_with_secret_sibling):
        root, _ = project_with_secret_sibling
        tool = FileListTool()
        result = await tool.execute(path=".", pattern="**/*.py", _workspace_path=root)
        assert not result.get("error")
        assert "nested.py" in result.get("content", "")

    @pytest.mark.asyncio
    async def test_dotdot_in_filename_not_false_positive(self, project_with_secret_sibling):
        # "a..b.txt" is a filename, not a `..` path component — must be allowed.
        root, _ = project_with_secret_sibling
        with open(os.path.join(root, "a..b.txt"), "w") as f:
            f.write("ok")
        tool = FileListTool()
        result = await tool.execute(path=".", pattern="a..b.txt", _workspace_path=root)
        assert not result.get("error"), result
        assert "a..b.txt" in result.get("content", "")


class TestPreFixNegativeControl:
    """Proves the vulnerability was real: the raw glob (no guard) escapes."""

    def test_raw_glob_traverses_out_of_base(self, project_with_secret_sibling):
        from pathlib import Path
        root, _ = project_with_secret_sibling
        hits = [str(p) for p in Path(root).resolve().glob("../*.txt")]
        assert any("penpal86_secret.txt" in h for h in hits), (
            "pre-fix raw glob should reach the out-of-tree sentinel"
        )
