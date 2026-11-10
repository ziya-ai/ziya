"""Mistyped commands get a short, actionable error instead of a usage wall.

Reported transcript:
  ziya shodow --allow-control   -> 6-line server usage + "unrecognized arguments"
  ziya shadow help              -> started a shadow session that tried to exec
                                   ~/.local/bin/help, exit 127, journal removed
"""

import sys
from unittest.mock import patch

import pytest


# -- helper in app.cli_commands ---------------------------------------------

def test_typo_suggests_closest_command():
    from app.cli_commands import unknown_command_message
    msg = unknown_command_message(["shodow", "--allow-control"])
    assert msg is not None
    assert "'shodow'" in msg
    assert "ziya shadow" in msg


def test_unrelated_word_lists_commands():
    from app.cli_commands import unknown_command_message, TOP_LEVEL_COMMANDS
    msg = unknown_command_message(["zzzzzz"])
    assert msg is not None and "'zzzzzz'" in msg
    for cmd in TOP_LEVEL_COMMANDS:
        assert cmd in msg


def test_flags_only_returns_none():
    from app.cli_commands import unknown_command_message
    assert unknown_command_message(["--bogus-flag"]) is None
    assert unknown_command_message([]) is None


# -- server-mode parser seam (app.main.parse_arguments) ---------------------

def test_server_parser_typo_is_one_line_hint(monkeypatch, capsys):
    from app import main as ziya_main
    monkeypatch.setattr(sys, "argv", ["ziya", "shodow", "--allow-control"])
    with pytest.raises(SystemExit) as exc:
        ziya_main.parse_arguments()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Did you mean 'ziya shadow'?" in err
    assert "usage:" not in err


def test_server_parser_unknown_flag_still_argparse_error(monkeypatch, capsys):
    from app import main as ziya_main
    monkeypatch.setattr(sys, "argv", ["ziya", "--bogus-flag"])
    with pytest.raises(SystemExit) as exc:
        ziya_main.parse_arguments()
    assert exc.value.code == 2
    assert "unrecognized arguments: --bogus-flag" in capsys.readouterr().err


def test_server_parser_valid_args_still_parse(monkeypatch):
    from app import main as ziya_main
    monkeypatch.setattr(sys, "argv", ["ziya", "--port", "1234"])
    args = ziya_main.parse_arguments()
    assert args.port == 1234


# -- unknown option on a subcommand: the SUBCOMMAND's usage, not the root's --
#
# Reported transcript:
#   ziya shadow --allow-remote  -> "usage: ziya [-h] {chat,ask,...} ..." +
#                                  "unrecognized arguments" — the root usage,
#                                  which lists no shadow options at all
#   ziya -h shadow              -> started a shadow session (the -h was
#                                  silently dropped by the argv pre-processor)

def _run_cli_main(monkeypatch, argv):
    from app import cli
    monkeypatch.setattr(sys, "argv", ["ziya", *argv])
    with patch("app.shadow.pty_host.run_interactive", return_value=0) as run, \
         pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code, run


def test_unknown_subcommand_option_shows_subcommand_usage(monkeypatch, capsys):
    code, run = _run_cli_main(monkeypatch, ["shadow", "--allow-remote-control"])
    run.assert_not_called()
    assert code == 2
    err = capsys.readouterr().err
    assert "usage: ziya shadow" in err
    assert "--allow-control" in err              # the options are listed
    assert "unrecognized arguments: --allow-remote-control" in err
    assert "Did you mean --allow-control?" in err
    assert "{chat,ask" not in err                # not the root parser's usage


def test_help_before_command_prints_help_not_session(monkeypatch, capsys):
    code, run = _run_cli_main(monkeypatch, ["-h", "shadow"])
    run.assert_not_called()
    assert code == 0
    out = capsys.readouterr().out
    assert "usage: ziya shadow" in out
    assert "--allow-control" in out


def test_flags_before_command_still_move_after_it():
    from app.cli import _reorder_argv
    assert _reorder_argv(["--profile", "x", "chat", "src/"]) == \
        ["chat", "src/", "--profile", "x"]
    # Nothing before the command is dropped any more, whatever it looks like.
    assert _reorder_argv(["-m", "haiku", "ask", "q"]) == ["ask", "q", "-m", "haiku"]
    assert _reorder_argv(["ask", "q"]) == ["ask", "q"]


# -- ziya shadow: unresolvable command never starts a session ---------------

def _shadow_args(argv):
    from app.cli import create_parser
    return create_parser().parse_args(["shadow", *argv])


def test_shadow_help_word_prints_help(capsys):
    from app.cli import cmd_shadow
    args = _shadow_args(["help"])
    with patch("shutil.which", return_value=None), \
         patch("app.shadow.pty_host.run_interactive") as run:
        cmd_shadow(args)
    run.assert_not_called()
    out = capsys.readouterr().out
    assert "--allow-control" in out


def test_shadow_bare_option_name_hints_flag(capsys):
    from app.cli import cmd_shadow
    args = _shadow_args(["allow-control"])
    with patch("shutil.which", return_value=None), \
         patch("app.shadow.pty_host.run_interactive") as run, \
         pytest.raises(SystemExit) as exc:
        cmd_shadow(args)
    run.assert_not_called()
    assert exc.value.code == 127
    assert "--allow-control" in capsys.readouterr().err


def test_shadow_missing_command_not_found(capsys):
    from app.cli import cmd_shadow
    args = _shadow_args(["definitely-not-a-command-xyz"])
    with patch("app.shadow.pty_host.run_interactive") as run, \
         pytest.raises(SystemExit) as exc:
        cmd_shadow(args)
    run.assert_not_called()
    assert exc.value.code == 127
    assert "command not found: definitely-not-a-command-xyz" in capsys.readouterr().err


def test_shadow_resolvable_command_still_runs():
    from app.cli import cmd_shadow
    args = _shadow_args(["--allow-control", "gated", "true"])
    with patch("app.shadow.pty_host.run_interactive", return_value=0) as run, \
         pytest.raises(SystemExit) as exc:
        cmd_shadow(args)
    assert exc.value.code == 0
    run.assert_called_once()
    assert run.call_args[0][0] == ["true"]
    assert run.call_args.kwargs["control_ceiling"] == "gated"
