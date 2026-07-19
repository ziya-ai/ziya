"""
Tests for cmd_chat's non-resume auth-failure exit path.

Regression: _plugins_future is backed by a non-daemon ThreadPoolExecutor
worker submitted *before* the auth check. If auth fails, the CLI used to
call sys.exit(1), which raises SystemExit but does not prevent CPython's
interpreter shutdown from joining outstanding non-daemon threads — so the
process blocked until initialize_plugins() finished, and that call's
app.server import triggers initialize_ast_if_enabled() as a module-level
side effect, indexing whatever directory happened to be current (observed:
$HOME) *after* "Authentication failed" had already been printed.

The fix: on auth failure, shut the executor down without waiting
(cancel_futures=True) and use os._exit(1) instead of sys.exit(1), so the
process terminates immediately regardless of the background thread's state.
"""

import argparse
import os
from unittest.mock import patch, MagicMock

import pytest


def _make_chat_args(**overrides):
    defaults = dict(
        command="chat",
        files=[],
        resume=False,
        ephemeral=False,
        profile=None,
        model=None,
        region=None,
        endpoint=None,
        root=None,
        debug=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestChatAuthFailureExitsImmediately:
    def test_os_exit_called_on_auth_failure(self):
        """Auth failure in the non-resume path must call os._exit, not
        sys.exit — sys.exit alone would still block on the non-daemon
        plugins-executor thread during interpreter shutdown."""
        from app.cli import cmd_chat

        args = _make_chat_args()

        with patch("app.cli.setup_env"), \
             patch("app.plugins.initialize"), \
             patch("app.cli._check_auth_quick", return_value=(False, "some auth error")), \
             patch("app.cli._print_auth_error") as mock_print_err, \
             patch("os._exit", side_effect=SystemExit(1)) as mock_os_exit, \
             patch("app.cli.sys.exit") as mock_sys_exit:
            # os._exit is normally process-terminating and never returns; the
            # real function would halt cmd_chat here. Give the mock a
            # SystemExit side effect so it likewise halts execution instead
            # of falling through into unmocked production code below.
            with pytest.raises(SystemExit):
                cmd_chat(args)

        mock_os_exit.assert_called_once_with(1)
        mock_sys_exit.assert_not_called()
        mock_print_err.assert_called_once_with("some auth error")

    def test_plugins_executor_shutdown_without_waiting(self):
        """The background plugins ThreadPoolExecutor must be told to shut
        down with wait=False so it cannot block process exit."""
        from app.cli import cmd_chat

        args = _make_chat_args()
        captured = {}

        class _RecordingExecutor:
            """Stand-in for concurrent.futures.ThreadPoolExecutor that
            records shutdown() calls instead of managing real threads."""

            def __init__(self, *a, **kw):
                pass

            def submit(self, fn, *a, **kw):
                fut = MagicMock()
                fut.result.return_value = None
                return fut

            def shutdown(self, wait=True, cancel_futures=False):
                captured['wait'] = wait
                captured['cancel_futures'] = cancel_futures

        with patch("app.cli.setup_env"), \
             patch("app.plugins.initialize"), \
             patch("concurrent.futures.ThreadPoolExecutor", _RecordingExecutor), \
             patch("app.cli._check_auth_quick", return_value=(False, None)), \
             patch("app.cli._print_auth_error"), \
             patch("os._exit", side_effect=SystemExit(1)):
            with pytest.raises(SystemExit):
                cmd_chat(args)

        assert captured.get('wait') is False, (
            "plugins executor must be shut down with wait=False on auth "
            "failure so it cannot block process termination"
        )

    def test_successful_auth_does_not_call_os_exit(self):
        """Negative control: the exit-path change must be confined to the
        auth-failure branch — a successful auth check must not trigger
        os._exit or early-return before the normal CLI flow."""
        from app.cli import cmd_chat

        args = _make_chat_args()

        def _closing_run(coro, **kwargs):
            # asyncio.run is mocked out entirely, so the coroutine passed to
            # it (_run_async_cli(cli)) is never actually awaited — close it
            # explicitly to avoid a "coroutine was never awaited" warning.
            coro.close()

        with patch("app.cli.setup_env"), \
             patch("app.plugins.initialize"), \
             patch("app.cli._check_auth_quick", return_value=(True, None)), \
             patch("app.cli._print_auth_error") as mock_print_err, \
             patch("os._exit") as mock_os_exit, \
             patch("app.cli.resolve_files", return_value=[]), \
             patch("app.cli.CLI") as MockCLI, \
             patch("app.cli.asyncio.run", side_effect=_closing_run), \
             patch("app.cli.save_session"):
            cmd_chat(args)

        mock_os_exit.assert_not_called()
        mock_print_err.assert_not_called()
        MockCLI.assert_called_once()
