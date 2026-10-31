"""Read-only scope checks from the UI must not log "escalation denied".

2026-10-20: the header's Task Cards indicator re-polls
``/task-cards/signature-summary`` on every window focus, and that route asks
``is_scope_authorized_with_reason`` about every escalating block of every
card.  With the default ``log=True`` each unsigned block wrote one INFO/WARNING
line per poll per tab -- dozens of identical SCOPE_AUTHZ lines every few
seconds for checks that enforce nothing.  The runtime gate keeps logging.
"""
import inspect
import logging

from app.utils import scope_approvals as sa


class _Scope:
    # Any privilege-bearing escalation so task_scope_hash is non-empty.
    shell_commands = ["rm"]
    writable_paths = []
    allowed_tools = None


def _hash_is_nonempty():
    from app.config import scope_canonical as sc
    return bool(sc.task_scope_hash(_Scope()))


def test_log_false_suppresses_denial_lines(caplog, monkeypatch):
    if not _hash_is_nonempty():  # scope shape not privilege-bearing in this build
        import pytest
        pytest.skip("stand-in scope carries no escalation")
    monkeypatch.setattr(sa, "get_record", lambda task_id: None)
    with caplog.at_level(logging.INFO):
        ok, reason = sa.is_scope_authorized_with_reason("b-quiet", _Scope(), log=False)
    assert (ok, reason) == (False, "no_record")
    assert not [r for r in caplog.records if "SCOPE_AUTHZ" in r.getMessage()]
    # Positive control: the runtime default still logs.
    with caplog.at_level(logging.INFO):
        sa.is_scope_authorized_with_reason("b-loud", _Scope())
    assert [r for r in caplog.records if "SCOPE_AUTHZ" in r.getMessage()]


def test_signature_summary_path_passes_log_false():
    """Seam: the route used by the header poll must opt out of logging."""
    from app.api import task_cards
    src = inspect.getsource(task_cards._escalation_rows)
    assert "is_scope_authorized_with_reason(" in src
    call = src[src.index("is_scope_authorized_with_reason("):]
    call = call[:call.index(")") + 1]
    assert "log=False" in call, call
