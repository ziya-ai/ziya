"""The read-only approval queries must not write "escalation denied" lines.

Observed 2026-10-20: the server log repeated ~30 lines of
``SCOPE_AUTHZ: no approval record for task 'b-…' — escalation denied``
every few seconds.  No task was trying to escalate.  The header's
Task-Cards attention indicator (useHeaderAttention) polls
``GET …/task-cards/signature-summary`` on every window focus, from every
open tab; that endpoint asks ``is_scope_authorized_with_reason`` about
every escalating block of every card, and the predicate logged each
"no" as if a runtime escalation had been refused.

Pinned here:
  * ``log=False`` suppresses every denial line while the reason code is
    still returned (the UI's answer);
  * the default (the runtime gate) still logs, so enforcement denials are
    not silenced by accident;
  * the seam: ``_escalation_rows`` in app/api/task_cards.py — the sole
    backend of both ``/signature-summary`` and ``/{id}/scope-status`` —
    passes ``log=False``.
"""
import inspect
import logging
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.models.task_card import TaskScope
from app.utils import scope_approvals as sa

# app.utils.logging_utils.logger is a ModeAwareLogger wrapping a stdlib logger
# that it configures with propagate=False, so caplog's root-level handler
# never sees its records.  Attach caplog's handler to that logger directly.
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH)
    pub_path = tmp_path / "approve_ed25519.pub"
    pub_path.write_bytes(pub)
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub_path))
    monkeypatch.setenv("ZIYA_SCOPE_APPROVALS_DIR", str(tmp_path / "scope_approvals"))
    return key


# ``wget`` escalates (not in the base allowlist); ``curl`` would hash to ""
# and make the denial vacuous — see tests/test_scope_approval_reasons.py.
SCOPE = TaskScope(shell_commands=["wget"])


def _denials(caplog):
    return [r for r in caplog.records if "escalation denied" in r.getMessage()]


def test_no_record_is_quiet_with_log_false(env, denial_log):
    ok, reason = sa.is_scope_authorized_with_reason("b-quiet", SCOPE, log=False)
    assert (ok, reason) == (False, "no_record")   # the answer is unchanged
    assert _denials(denial_log) == []


def test_signature_invalid_is_quiet_with_log_false(env, denial_log):
    h = sc.task_scope_hash(SCOPE)
    sa.save_record({"task_id": "b-bad", "scope_hash": h, "approved_by": "x",
                    "approved_at": int(time.time()), "signature": "AAAA"})
    ok, reason = sa.is_scope_authorized_with_reason("b-bad", SCOPE, log=False)
    assert (ok, reason) == (False, "signature_invalid")
    assert _denials(denial_log) == []


def test_runtime_gate_still_logs_by_default(env, denial_log):
    """Positive control: the enforcement path keeps its audit line, and
    the capture used by the quiet tests above can actually see it."""
    assert sa.is_scope_authorized("b-loud", SCOPE) is False
    lines = _denials(denial_log)
    assert len(lines) == 1
    assert "b-loud" in lines[0].getMessage()


def test_escalation_rows_uses_the_quiet_form():
    """Seam: both read-only endpoints go through _escalation_rows, which must
    ask the predicate without the enforcement log line."""
    from app.api import task_cards
    src = inspect.getsource(task_cards._escalation_rows)
    call = src[src.index("is_scope_authorized_with_reason("):]
    call = call[:call.index(")") + 1]
    assert "log=False" in call, call
    # And the two routes really do route through it.
    assert "_escalation_rows(" in inspect.getsource(task_cards.get_signature_summary)
