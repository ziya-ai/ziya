"""Read-only signature queries must not log "escalation denied".

The task-card header polls /task-cards/signature-summary (and the editor
polls /{id}/scope-status) on every window focus.  Both go through
app/api/task_cards._escalation_rows, which asks
scope_approvals.is_scope_authorized_with_reason "is this block signed?"
for every escalating block.  With the default log=True each unsigned
block wrote a "🔒 SCOPE_AUTHZ: no approval record ... escalation denied"
line per poll, flooding the log during normal use although nothing was
being enforced.  The runtime gate (is_scope_authorized) still logs.

Run:
    pytest tests/test_scope_status_quiet.py -v
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from app.utils import scope_approvals as sa


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path))
    monkeypatch.setattr(sa.sc, "task_scope_hash", lambda *s: "deadbeef" * 8)
    log = MagicMock()
    monkeypatch.setattr(sa, "logger", log)
    return log


def test_log_false_is_silent_but_still_denies(monkeypatch, tmp_path):
    log = _setup(monkeypatch, tmp_path)
    assert sa.is_scope_authorized_with_reason("b-x", object(), log=False) == (False, "no_record")
    assert not log.info.called and not log.warning.called


def test_runtime_gate_still_logs_the_denial(monkeypatch, tmp_path):
    log = _setup(monkeypatch, tmp_path)
    assert sa.is_scope_authorized("b-x", object()) is False
    assert any("escalation denied" in str(c) for c in log.info.call_args_list)


def test_escalation_rows_queries_quietly():
    """Seam: the polled UI endpoints share _escalation_rows; its approval
    check must pass log=False."""
    from app.api import task_cards
    src = inspect.getsource(task_cards._escalation_rows)
    call = src[src.index("is_scope_authorized_with_reason("):]
    call = call[:call.index(")") + 1]
    assert "log=False" in call, call
