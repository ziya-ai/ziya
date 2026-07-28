"""
Tests for app.mcp.tools.pdf_tools._load_index path-validation gate.

Regression coverage for the reported bug: a PDF under a path
explicitly approved via Project Settings' "Add External Path" feature
(app.services.folder_service._explicit_external_paths) was rejected by
pdf_search / pdf_outline / pdf_read_pages with "is outside the allowed
directories", even though the same path is fully readable via file_read
once approved.

_load_index resolves the path through the same traversal-safe
validator as FileReadTool, gated by _get_all_readable_prefixes() (safe
write paths + task-granted paths + include-dir / explicit-external
paths). These tests exercise that gate directly, mocking PdfIndex so
no real PDF parsing is required.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.mcp.tools.pdf_tools import _load_index, PdfOutlineTool, PdfSearchTool, PdfReadPagesTool


def run(coro):
    # asyncio.run() always creates and tears down a fresh event loop,
    # avoiding cross-test/cross-file coupling to a stale or already-closed
    # loop (see tests/test_fileio_tools.py for the same fix and rationale).
    return asyncio.run(coro)


def _fake_index(path):
    idx = MagicMock()
    idx.path = path
    idx.page_count = 3
    idx.total_tokens = 100
    idx.meta = {"metadata": {}, "outline": [], "figures": [], "tables": []}
    idx.search.return_value = [{"page": 1, "score": 1.0, "snippet": "ptp pulse"}]
    idx.read_pages.return_value = [{"page": 1, "text": "hello"}]
    return idx


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "internal.pdf").write_bytes(b"%PDF-1.4 fake")
    return str(root)


@pytest.fixture
def external_pdf(tmp_path):
    """A PDF that lives outside the project root, mirroring a vendor
    docs directory added via Project Settings."""
    ext_dir = tmp_path.parent / f"vendor-docs-{tmp_path.name}"
    ext_dir.mkdir(exist_ok=True)
    pdf_path = ext_dir / "Aldrin3-XL_Hardware_Datasheet.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    return str(pdf_path), str(ext_dir)


class TestLoadIndexPathGate:

    def test_project_relative_pdf_always_allowed(self, project_root, monkeypatch):
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        with patch("app.utils.pdf_rag.PdfIndex.get_or_build",
                    side_effect=lambda p: _fake_index(p)):
            idx, err = run(_load_index("internal.pdf", {"_workspace_path": project_root}))
        assert err is None
        assert idx is not None

    def test_unapproved_external_pdf_rejected(self, project_root, external_pdf, monkeypatch):
        pdf_path, _ext_dir = external_pdf
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        idx, err = run(_load_index(pdf_path, {"_workspace_path": project_root}))
        assert idx is None
        assert err is not None
        assert err["error"] is True
        assert "outside the allowed directories" in err["message"]

    def test_explicitly_approved_external_pdf_allowed(self, project_root, external_pdf, monkeypatch):
        """This is the exact reported bug: a path added via Project
        Settings' 'Add External Path' must be usable by pdf_search."""
        pdf_path, ext_dir = external_pdf
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {ext_dir},
            raising=False,
        )
        with patch("app.utils.pdf_rag.PdfIndex.get_or_build",
                    side_effect=lambda p: _fake_index(p)):
            idx, err = run(_load_index(pdf_path, {"_workspace_path": project_root}))
        assert err is None, err
        assert idx is not None

    def test_include_dirs_env_var_also_grants_access(self, project_root, external_pdf, monkeypatch):
        """The --include / ZIYA_INCLUDE_DIRS mechanism is a second,
        independent path to the same allowlist."""
        pdf_path, ext_dir = external_pdf
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", ext_dir)
        with patch("app.utils.pdf_rag.PdfIndex.get_or_build",
                    side_effect=lambda p: _fake_index(p)):
            idx, err = run(_load_index(pdf_path, {"_workspace_path": project_root}))
        assert err is None, err
        assert idx is not None

    def test_traversal_rejected_even_with_include_dir(self, project_root, monkeypatch):
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", "/tmp")
        idx, err = run(_load_index("../../../etc/passwd", {"_workspace_path": project_root}))
        assert idx is None
        assert err["error"] is True

    def test_empty_path_rejected(self, project_root):
        idx, err = run(_load_index("", {"_workspace_path": project_root}))
        assert idx is None
        assert err["error"] is True
        assert "empty" in err["message"]

    def test_non_pdf_extension_rejected(self, project_root, monkeypatch):
        (Path(project_root) / "notes.txt").write_text("not a pdf")
        idx, err = run(_load_index("notes.txt", {"_workspace_path": project_root}))
        assert idx is None
        assert err["error"] is True
        assert "Not a PDF" in err["message"]

    def test_missing_file_rejected(self, project_root):
        idx, err = run(_load_index("does_not_exist.pdf", {"_workspace_path": project_root}))
        assert idx is None
        assert err["error"] is True
        assert "Not a file" in err["message"]


# ── End-to-end through the public tools ─────────────────────────────

class TestPdfToolsEndToEndExternalPath:
    """Exercise pdf_outline / pdf_search / pdf_read_pages the way the
    model actually calls them, against an approved external path."""

    @pytest.fixture(autouse=True)
    def _patch_index(self):
        with patch("app.utils.pdf_rag.PdfIndex.get_or_build",
                    side_effect=lambda p: _fake_index(p)):
            yield

    def test_pdf_outline_on_approved_external_path(self, project_root, external_pdf, monkeypatch):
        pdf_path, ext_dir = external_pdf
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", {ext_dir}, raising=False
        )
        result = run(PdfOutlineTool().execute(path=pdf_path, _workspace_path=project_root))
        assert "error" not in result
        assert result["page_count"] == 3

    def test_pdf_search_on_approved_external_path(self, project_root, external_pdf, monkeypatch):
        pdf_path, ext_dir = external_pdf
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", {ext_dir}, raising=False
        )
        result = run(PdfSearchTool().execute(
            path=pdf_path, query="ptp pulse", _workspace_path=project_root,
        ))
        assert "error" not in result
        assert result["hits"]

    def test_pdf_search_on_unapproved_external_path_still_rejected(self, project_root, external_pdf, monkeypatch):
        pdf_path, _ext_dir = external_pdf
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        result = run(PdfSearchTool().execute(
            path=pdf_path, query="ptp pulse", _workspace_path=project_root,
        ))
        assert result.get("error") is True
        assert "outside the allowed directories" in result["message"]

    def test_pdf_read_pages_on_approved_external_path(self, project_root, external_pdf, monkeypatch):
        pdf_path, ext_dir = external_pdf
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", {ext_dir}, raising=False
        )
        result = run(PdfReadPagesTool().execute(
            path=pdf_path, start_page=1, _workspace_path=project_root,
        ))
        assert "error" not in result
        assert result["pages"]
