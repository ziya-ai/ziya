"""GET /api/v1/drag/file resolves like file_read (app/utils/drag_file_source.py)."""
import pytest

from app.utils import drag_file_source as dfs


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "notes.md").write_text("# hi\n")
    (tmp_path / "big.md").write_bytes(b"x" * (dfs.MAX_DRAG_FILE_BYTES + 1))
    # Only the project itself is readable in this test.
    monkeypatch.setattr("app.mcp.tools.fileio._get_all_readable_prefixes", lambda: [])
    return tmp_path


def test_resolves_a_project_file_with_a_mime(root):
    p, mime = dfs.resolve_drag_file("Docs/notes.md", str(root))
    assert p == (root / "Docs" / "notes.md").resolve()
    assert mime == "text/markdown"
    assert dfs.mime_for("x.csv") == "text/csv" and dfs.mime_for("x.unknownext") == "text/plain"


@pytest.mark.parametrize("path,status", [
    ("", 400), ("../outside.md", 403), ("/etc/passwd", 403),
    ("Docs/missing.md", 404), ("Docs", 404), ("big.md", 413),
])
def test_refusals_carry_http_status(root, path, status):
    with pytest.raises(dfs.DragFileError) as e:
        dfs.resolve_drag_file(path, str(root))
    assert e.value.status == status


def test_no_project_root_is_a_400():
    with pytest.raises(dfs.DragFileError) as e:
        dfs.resolve_drag_file("Docs/notes.md", None)
    assert e.value.status == 400
