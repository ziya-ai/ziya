"""
Tests for CLI session factory functions (_init_and_authenticate, _create_cli_session).

These factory functions were extracted from duplicated boilerplate across the
5 CLI command handlers (cmd_chat, cmd_ask, cmd_review, cmd_explain, cmd_task).
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call
from types import SimpleNamespace


def _make_cli_args(**overrides):
    """Build a SimpleNamespace with all required fields for CLI commands."""
    defaults = dict(
        exclude=[],
        profile=None,
        endpoint="bedrock",
        model="sonnet3.5",
        model_id=None,
        root=None,
        include_only=[],
        include=[],
        region=None,
        thinking_level=None,
        temperature=None,
        top_p=None,
        top_k=None,
        max_output_tokens=None,
        no_stream=False,
        debug=False,
        files=["src/main.py"],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestInitAndAuthenticate:
    """Tests for _init_and_authenticate factory function."""

    @patch('app.cli._check_auth_quick', return_value=True)
    @patch('app.plugins.initialize')
    @patch('app.cli.setup_env')
    def test_calls_setup_env_plugins_and_auth(self, mock_setup, mock_plugins, mock_auth):
        """Should call setup_env, initialize_plugins, and _check_auth_quick in order."""
        from app.cli import _init_and_authenticate
        args = _make_cli_args()

        _init_and_authenticate(args)

        mock_setup.assert_called_once_with(args)
        mock_plugins.assert_called_once()
        mock_auth.assert_called_once_with(None)  # profile=None

    @patch('app.cli._check_auth_quick', return_value=True)
    @patch('app.plugins.initialize')
    @patch('app.cli.setup_env')
    def test_passes_profile_to_auth_check(self, mock_setup, mock_plugins, mock_auth):
        """Should pass the args.profile to _check_auth_quick."""
        from app.cli import _init_and_authenticate
        args = _make_cli_args(profile="my-profile")

        _init_and_authenticate(args)

        mock_auth.assert_called_once_with("my-profile")

    @patch('app.cli._check_auth_quick', return_value=False)
    @patch('app.cli._print_auth_error')
    @patch('app.plugins.initialize')
    @patch('app.cli.setup_env')
    def test_exits_on_auth_failure(self, mock_setup, mock_plugins, mock_print_err, mock_auth):
        """Should call _print_auth_error and sys.exit(1) on auth failure."""
        from app.cli import _init_and_authenticate
        args = _make_cli_args()

        with pytest.raises(SystemExit) as exc_info:
            _init_and_authenticate(args)

        assert exc_info.value.code == 1
        mock_print_err.assert_called_once()

    @patch('app.cli._check_auth_quick', return_value=True)
    @patch('app.plugins.initialize')
    @patch('app.cli.setup_env')
    def test_skip_setup_env(self, mock_setup, mock_plugins, mock_auth):
        """skip_setup_env=True should not call setup_env."""
        from app.cli import _init_and_authenticate
        args = _make_cli_args()

        _init_and_authenticate(args, skip_setup_env=True)

        mock_setup.assert_not_called()
        mock_plugins.assert_called_once()
        mock_auth.assert_called_once()


class TestCreateCliSession:
    """Tests for _create_cli_session factory function."""

    @patch('app.cli.CLI')
    @patch('app.cli.resolve_files', return_value=["src/main.py"])
    @patch('app.cli._init_and_authenticate')
    def test_returns_cli_with_resolved_files(self, mock_init, mock_resolve, mock_cli_cls):
        """Should call _init_and_authenticate, resolve files, and return CLI."""
        from app.cli import _create_cli_session
        args = _make_cli_args(files=["src/"])

        os.environ["ZIYA_USER_CODEBASE_DIR"] = "/project"
        try:
            result = _create_cli_session(args)
        finally:
            os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)

        mock_init.assert_called_once_with(args)
        mock_resolve.assert_called_once_with(["src/"], "/project")
        mock_cli_cls.assert_called_once_with(files=["src/main.py"])
        assert result == mock_cli_cls.return_value

    @patch('app.cli.CLI')
    @patch('app.cli.resolve_files')
    @patch('app.cli._init_and_authenticate')
    def test_explicit_files_skips_resolve(self, mock_init, mock_resolve, mock_cli_cls):
        """When files= is passed explicitly, resolve_files should not be called."""
        from app.cli import _create_cli_session
        args = _make_cli_args()

        result = _create_cli_session(args, files=["explicit.py"])

        mock_resolve.assert_not_called()
        mock_cli_cls.assert_called_once_with(files=["explicit.py"])

    @patch('app.cli.CLI')
    @patch('app.cli.resolve_files', return_value=[])
    @patch('app.cli._init_and_authenticate')
    def test_no_files_attr_returns_empty(self, mock_init, mock_resolve, mock_cli_cls):
        """When args has no files, CLI should be created with empty list."""
        from app.cli import _create_cli_session
        args = _make_cli_args()
        del args.files  # simulate no files attribute

        os.environ["ZIYA_USER_CODEBASE_DIR"] = "/project"
        try:
            _create_cli_session(args)
        finally:
            os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)

        mock_cli_cls.assert_called_once_with(files=[])


class TestResumePathAuthFix:
    """Regression test: --resume must authenticate (previously bypassed)."""

    @patch('app.cli._check_auth_quick', return_value=False)
    @patch('app.cli._print_auth_error')
    @patch('app.plugins.initialize')
    @patch('app.cli.setup_env')
    def test_resume_path_checks_auth(self, mock_setup, mock_plugins, mock_print_err, mock_auth):
        """cmd_chat --resume should fail early on bad credentials."""
        from app.cli import cmd_chat
        args = _make_cli_args(resume=True, ephemeral=False, files=[])

        with pytest.raises(SystemExit) as exc_info:
            cmd_chat(args)

        assert exc_info.value.code == 1
        mock_print_err.assert_called()
