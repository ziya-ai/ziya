"""
Tests for ZiyaASTEnhancer.process_codebase()'s "too broad to index" guard.

Regression coverage: the guard's early-return result previously had no way
to signal "this was a deliberate skip, not a failure" to its caller
(context_enhancer.initialize_ast_if_enabled), which treated
files_processed == 0 as always-an-error. The guard's result now carries
"skipped": True so that distinction survives through
app.utils.ast_parser.integration.initialize_ast_capabilities().
"""

import os

import pytest

from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


@pytest.fixture
def enhancer():
    return ZiyaASTEnhancer()


class TestHostileDirectoryGuard:
    def test_home_directory_is_skipped(self, enhancer):
        home_dir = os.path.expanduser("~")
        result = enhancer.process_codebase(home_dir)
        assert result["skipped"] is True
        assert result["files_processed"] == 0
        assert "too broad" in result["ast_context"].lower()

    def test_filesystem_root_is_skipped(self, enhancer):
        result = enhancer.process_codebase("/")
        assert result["skipped"] is True
        assert result["files_processed"] == 0

    def test_tmp_directory_is_skipped(self, enhancer):
        result = enhancer.process_codebase("/tmp")
        assert result["skipped"] is True

    def test_var_directory_is_skipped(self, enhancer):
        result = enhancer.process_codebase("/var")
        assert result["skipped"] is True

    def test_normal_project_directory_is_not_skipped(self, enhancer, tmp_path):
        """Negative control: a legitimate, specific project directory must
        not be treated as hostile, and its result must not carry the
        skipped flag (so it isn't mistaken for a deliberate bail-out)."""
        (tmp_path / "example.py").write_text("x = 1\n")
        result = enhancer.process_codebase(str(tmp_path))
        assert result.get("skipped", False) is False
        assert result["files_processed"] >= 1
