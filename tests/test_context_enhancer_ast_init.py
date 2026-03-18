"""
Regression tests for context_enhancer.initialize_ast_if_enabled.

Covers the bug where the _initialize_ast closure referenced the
pre-alias name `_indexing_in_progress` instead of `_ast_in_progress`,
causing a NameError in the background thread's finally block.
"""

import os
import threading
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _clean_ast_state():
    """Reset module-level AST state before each test."""
    from app.utils.ast_parser import integration
    integration._indexing_in_progress.clear()
    integration._initialized_projects.clear()
    yield
    integration._indexing_in_progress.clear()
    integration._initialized_projects.clear()


@pytest.fixture
def _enable_ast(tmp_path, monkeypatch):
    """Set env vars so start_ast_initialization proceeds."""
    monkeypatch.setenv("ZIYA_ENABLE_AST", "true")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))


def _run_init_synchronously():
    """Call start_ast_initialization and join the background thread."""
    from app.utils.context_enhancer import initialize_ast_if_enabled
    initialize_ast_if_enabled()
    # Wait for any daemon threads spawned by the call
    for t in threading.enumerate():
        if t.name != threading.current_thread().name and t.daemon:
            t.join(timeout=10)


class TestInitializeAstCleanup:
    """The finally block must discard the dir from _indexing_in_progress."""

    def test_success_path_cleans_up(self, _enable_ast, tmp_path):
        """After successful init, dir is removed from _indexing_in_progress."""
        from app.utils.ast_parser import integration

        fake_result = {"files_processed": 5}
        with mock.patch(
            "app.utils.context_enhancer.initialize_ast_capabilities",
            return_value=fake_result,
        ), mock.patch(
            "app.utils.context_enhancer._broadcast_ast_complete",
        ):
            _run_init_synchronously()

        abs_dir = os.path.abspath(str(tmp_path))
        assert abs_dir not in integration._indexing_in_progress, (
            "_indexing_in_progress should be cleaned up after successful init"
        )

    def test_failure_path_cleans_up(self, _enable_ast, tmp_path):
        """After failed init, dir is still removed from _indexing_in_progress."""
        from app.utils.ast_parser import integration

        with mock.patch(
            "app.utils.context_enhancer.initialize_ast_capabilities",
            side_effect=RuntimeError("boom"),
        ):
            _run_init_synchronously()

        abs_dir = os.path.abspath(str(tmp_path))
        assert abs_dir not in integration._indexing_in_progress, (
            "_indexing_in_progress should be cleaned up even after failure"
        )

    def test_no_nameerror_in_finally(self, _enable_ast, tmp_path):
        """Regression: the finally block must not raise NameError.

        Previously `_indexing_in_progress` was used instead of the
        aliased `_ast_in_progress`, causing a NameError in the thread.
        """
        from app.utils.ast_parser import integration
        from app.utils.context_enhancer import _ast_indexing_status

        with mock.patch(
            "app.utils.context_enhancer.initialize_ast_capabilities",
            side_effect=RuntimeError("simulated failure"),
        ):
            _run_init_synchronously()

        # If the finally block raised NameError, the status dict would
        # still show is_indexing=True because the except block runs
        # before finally. A NameError in finally would prevent normal
        # cleanup but the except block sets is_indexing=False, so check
        # the _indexing_in_progress set — it would NOT be cleaned up.
        abs_dir = os.path.abspath(str(tmp_path))
        assert abs_dir not in integration._indexing_in_progress, (
            "NameError in finally block — _ast_in_progress alias is broken"
        )
        # Also verify the error was captured, not masked by NameError
        assert _ast_indexing_status.get("error") is not None
