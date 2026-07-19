"""
Tests for app.utils.ast_parser.integration.initialize_ast_capabilities()
propagating the enhancer's "skipped" flag through to its result dict.

Regression coverage: ZiyaASTEnhancer.process_codebase()'s hostile-directory
guard sets result["skipped"] = True, but initialize_ast_capabilities()
previously rebuilt its return dict from a fixed key list that dropped
"skipped" — so the signal never reached context_enhancer.py, which then
treated the skip as a genuine failure (see
tests/test_context_enhancer_ast_init.py::TestSkippedResultIsNotAFailure).
"""

from unittest import mock

import pytest

from app.utils.ast_parser import integration


@pytest.fixture(autouse=True)
def _clean_ast_state():
    """Reset module-level AST state before/after each test — mirrors the
    cleanup pattern in test_context_enhancer_ast_init.py."""
    integration._indexing_in_progress.clear()
    integration._initialized_projects.clear()
    yield
    integration._indexing_in_progress.clear()
    integration._initialized_projects.clear()


class TestSkippedFlagPropagation:
    def test_skipped_true_propagates_through(self, tmp_path):
        enhancer_result = {
            "skipped": True,
            "files_processed": 0,
            "ast_context": "# AST Analysis\n\nProject directory is too broad for AST indexing. Open a specific project folder.",
            "token_count": 10,
            "file_list": [],
        }
        with mock.patch(
            "app.utils.ast_parser.integration.check_dependencies", return_value=True
        ), mock.patch(
            "app.utils.ast_parser.ziya_ast_enhancer.ZiyaASTEnhancer.process_codebase",
            return_value=enhancer_result,
        ):
            result = integration.initialize_ast_capabilities(str(tmp_path))

        assert result["skipped"] is True
        assert result["files_processed"] == 0

    def test_skipped_false_by_default_for_normal_processing(self, tmp_path):
        """Negative control: a normal (non-skipped) result must default
        "skipped" to False rather than being absent or truthy by accident."""
        enhancer_result = {
            "files_processed": 3,
            "ast_context": "# AST Analysis\n\n...",
            "token_count": 50,
            "file_list": ["a.py", "b.py", "c.py"],
        }
        with mock.patch(
            "app.utils.ast_parser.integration.check_dependencies", return_value=True
        ), mock.patch(
            "app.utils.ast_parser.ziya_ast_enhancer.ZiyaASTEnhancer.process_codebase",
            return_value=enhancer_result,
        ):
            result = integration.initialize_ast_capabilities(str(tmp_path))

        assert result["skipped"] is False
        assert result["files_processed"] == 3
