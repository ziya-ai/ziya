"""Tests for the launch-context-dependent unsigned-escalation policy
(Feature 2b): a headless run HOLDS on an unsigned scope escalation rather
than silently clamping to floor, while an interactive run keeps the
long-standing clamp-and-continue behaviour.

Covers the decision helper, the launch-context reader's defaults, and the
SEAM: that ``authorize_scope``'s identity-based "was this floored?" signal
composes with the policy so a hold fires on exactly an unsigned escalation
and never on a signed or non-escalating scope.
"""

import pytest

from app.agents.task_executor import (
    TaskInfraError,
    enforce_unsigned_escalation_policy,
    _read_launch_context,
)
from app.models.task_card import TaskScope
from app.utils.scope_approvals import authorize_scope


class TestPolicyDecision:
    def test_headless_raises_scope_unsigned_hold(self):
        with pytest.raises(TaskInfraError) as ei:
            enforce_unsigned_escalation_policy("headless", "b", "b-1")
        # Held (truthy infra_kind), specifically classified, carrying the
        # block id so the run boundary can record held_at_block_id.
        assert ei.value.infra_kind == "scope_unsigned"
        assert ei.value.block_id == "b-1"

    def test_interactive_does_not_raise(self):
        # Returns None → caller clamps-and-continues, today's behaviour.
        assert (
            enforce_unsigned_escalation_policy("interactive", "b", "b-1")
            is None
        )

    def test_unknown_context_is_treated_as_interactive(self):
        # Defensive: an unrecognised context must not hold — the safe
        # default is the non-escalating clamp-and-continue.
        assert (
            enforce_unsigned_escalation_policy("something-else", "b", "b-1")
            is None
        )

    def test_scope_unsigned_is_not_an_autoretry_kind(self):
        # A held scope_unsigned run must be resumable but NEVER auto-retried
        # (a retry without a signature would only re-clamp and hold again).
        from app.agents.task_executor import INFRA_ERROR_KINDS
        assert "scope_unsigned" not in INFRA_ERROR_KINDS


class TestLaunchContextReader:
    def test_none_storage_defaults_interactive(self):
        assert _read_launch_context(None, "r1") == "interactive"

    def test_missing_run_id_defaults_interactive(self):
        class _S:
            def get(self, _):  # pragma: no cover - should not be called
                raise AssertionError("should not read with empty run_id")
        assert _read_launch_context(_S(), "") == "interactive"

    def test_read_failure_defaults_interactive(self):
        class _S:
            def get(self, _):
                raise RuntimeError("disk gone")
        assert _read_launch_context(_S(), "r1") == "interactive"

    def test_missing_run_defaults_interactive(self):
        class _S:
            def get(self, _):
                return None
        assert _read_launch_context(_S(), "r1") == "interactive"

    def test_returns_stored_headless(self):
        class _Run:
            launch_context = "headless"
        class _S:
            def get(self, _):
                return _Run()
        assert _read_launch_context(_S(), "r1") == "headless"


class TestSeamComposition:
    """The identity signal from authorize_scope must compose with the
    policy: hold on exactly an unsigned escalation, never otherwise."""

    def test_unsigned_escalation_is_floored_then_holds_headless(self):
        # An escalating scope with no signed approval: authorize_scope
        # returns a DIFFERENT object (the floored view), which is the
        # signal the seam uses to invoke the policy.
        escalating = TaskScope(
            shell_commands=["npm"],
            paths=[],
        )
        authz = authorize_scope("no-such-approved-block", escalating)
        assert authz is not escalating, (
            "an unsigned escalation must be floored to a new object"
        )
        # Given that signal, headless must hold and interactive must not.
        with pytest.raises(TaskInfraError):
            enforce_unsigned_escalation_policy("headless", "t", "no-such-approved-block")
        assert (
            enforce_unsigned_escalation_policy("interactive", "t", "no-such-approved-block")
            is None
        )

    def test_non_escalating_scope_passes_through_no_hold(self):
        # A scope with no privilege-bearing grant is returned UNCHANGED
        # (same object), so the seam's ``_authz is not scope`` is False and
        # the policy is never consulted — even on a headless run.
        benign = TaskScope(tools=["render_diagram"])
        authz = authorize_scope("any-block", benign)
        assert authz is benign, (
            "a non-escalating scope must pass through unchanged, so a "
            "headless run with no escalation never holds"
        )
