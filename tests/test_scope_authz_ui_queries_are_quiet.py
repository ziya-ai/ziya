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

import pytest

from app.utils import scope_approvals as sa

# app.utils.logging_utils.logger is a ModeAwareLogger wrapping a stdlib logger
# configured with propagate=False, so caplog's root-level handler never sees
# its records; the positive control below read as "nothing logged" for that
# reason alone.  Attach caplog's handler to the underlying logger directly
# (same fixture as test_scope_authz_query_path_is_quiet.py).
_UNDERLYING = sa.logger._logger


@pytest.fixture
def denial_log(caplog):
    # First use clears handlers (lazy configuration); do it before attaching.
    sa.logger._ensure_configured()
    _UNDERLYING.addHandler(caplog.handler)
    prev = _UNDERLYING.level
    _UNDERLYING.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        _UNDERLYING.removeHandler(caplog.handler)
        _UNDERLYING.setLevel(prev)


class _Scope:
    # Any privilege-bearing escalation so task_scope_hash is non-empty.
    shell_commands = ["rm"]
    writable_paths = []
    allowed_tools = None


def _hash_is_nonempty():
    from app.config import scope_canonical as sc
    return bool(sc.task_scope_hash(_Scope()))


def test_log_false_suppresses_denial_lines(denial_log, monkeypatch):
    caplog = denial_log
    if not _hash_is_nonempty():  # scope shape not privilege-bearing in this build
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
