"""The un-approved-escalation notice names the exact signing command.

2026-10-11: `ziya task sweep` reported three blocks running at the floor and
said "Approve the card in the GUI deck (or via ziya-approve)".  A bare
`ziya-approve` signs the shell-config escalation in mcp_config.json, so the
operator was asked to sign nine unrelated shell commands and the card still
ran at the floor.  The notice must print `--task <card> --project <project>
--all` with the real ids, and say what the bare command does instead.
"""
import pytest

from app.models.task_card import Block


def _root():
    return Block(block_type="group", id="g", name="root", body=[
        Block(block_type="task", id="b-aaa", name="P0 - Reconcile",
              instructions="x", scope={"shell_commands": ["git add"]}),
        Block(block_type="task", id="b-bbb", name="Commit one group",
              instructions="x", scope={"shell_commands": ["git commit"]}),
    ])


@pytest.fixture
def unauthorized(monkeypatch):
    import app.utils.scope_approvals as sa
    monkeypatch.setattr(sa, "is_scope_authorized", lambda *a, **k: False)


def test_notice_prints_the_task_mode_command_with_real_ids(unauthorized, capsys):
    from app import cli_card_runner as r
    r._warn_unauthorized_scopes(_root(), card_id="card-123", project_id="proj-456")
    out = capsys.readouterr().out
    assert "sudo ziya-approve --task card-123 --project proj-456 --all" in out
    assert "(block b-aaa)" in out and "(block b-bbb)" in out
    assert "signs the shell-config escalation, not this card" in out
    # The old generic hint, which sent the operator to the wrong signer.
    assert "(or via ziya-approve)" not in out


def test_notice_without_ids_still_names_the_flags(unauthorized, capsys):
    from app import cli_card_runner as r
    r._warn_unauthorized_scopes(_root())
    out = capsys.readouterr().out
    assert "--task <card id> --project <project id> --all" in out


def test_authorized_blocks_print_nothing(monkeypatch, capsys):
    import app.utils.scope_approvals as sa
    monkeypatch.setattr(sa, "is_scope_authorized", lambda *a, **k: True)
    from app import cli_card_runner as r
    r._warn_unauthorized_scopes(_root(), card_id="c", project_id="p")
    assert capsys.readouterr().out == ""
