"""Infra-fault gate policy: kind-dependent fleet-hold decisions.

The policy under test exists because ``INFRA_ERROR_KINDS`` conflates two
unlike things.  ``authentication_error`` is a session property: one
occurrence proves every sibling in a fan-out is already dead.
``throttling_error`` and friends are request properties: one occurrence
proves nothing about the other siblings, and gating on it would abort
recoverable work.

These tests pin both halves, plus the boundary cases where an
over-eager gate would do real damage (a 2-wide fan-out, a single
isolated throttle) and where an under-eager gate would waste a whole
frontier-tier fan-out (a dead credential).
"""

import pytest

from app.utils.infra_gate import (
    DEFAULT_GATE_RATIO,
    IMMEDIATE_GATE_KINDS,
    MIN_PROPORTIONAL_FAULTS,
    PROPORTIONAL_GATE_KINDS,
    InfraFault,
    gate_reason,
    is_infra_gating_kind,
    should_gate,
    summarize,
)


def _f(kind, idx=0, path=("CL0",), block="b"):
    return InfraFault(kind=kind, block_id=f"{block}{idx}",
                      call_path=path, index=idx, at=0.0)


class TestKindPartition:
    """The two classes must stay disjoint and cover the real kinds."""

    def test_no_kind_is_in_both_classes(self):
        assert not (IMMEDIATE_GATE_KINDS & PROPORTIONAL_GATE_KINDS)

    def test_auth_is_immediate(self):
        assert "authentication_error" in IMMEDIATE_GATE_KINDS

    @pytest.mark.parametrize("kind", [
        "throttling_error", "transient_service_error", "connection_error",
    ])
    def test_request_level_kinds_are_proportional(self, kind):
        assert kind in PROPORTIONAL_GATE_KINDS

    def test_every_executor_infra_kind_is_classified(self):
        """Drift guard: a new INFRA_ERROR_KINDS entry must be classified.

        An unclassified kind silently never gates, which is the failure
        mode this whole module exists to prevent.
        """
        from app.agents.task_executor import INFRA_ERROR_KINDS
        unclassified = [
            k for k in INFRA_ERROR_KINDS if not is_infra_gating_kind(k)
        ]
        assert not unclassified, (
            f"unclassified infra kinds will never gate: {unclassified}"
        )

    def test_a_work_failure_is_not_a_gating_kind(self):
        assert not is_infra_gating_kind("tool_error")
        assert not is_infra_gating_kind("")


class TestImmediateGate:
    """A session-level fault gates on the first occurrence."""

    def test_single_auth_fault_gates_a_wide_fanout(self):
        assert should_gate([_f("authentication_error")], fanout_width=20)

    def test_single_auth_fault_gates_even_at_width_one(self):
        """Width 1 disables the proportional test, not the immediate one."""
        assert should_gate([_f("authentication_error")], fanout_width=1)

    def test_reason_names_the_session_cause(self):
        r = gate_reason([_f("authentication_error")], fanout_width=20)
        assert "session-level" in r


class TestProportionalGate:
    """Request-level faults need evidence of breadth."""

    def test_one_throttle_in_twenty_does_not_gate(self):
        """The over-eager-abort case: 19 healthy siblings must survive."""
        assert not should_gate([_f("throttling_error")], fanout_width=20)

    def test_one_throttle_in_two_does_not_gate(self):
        """Narrow fan-out: 1/2 is 50% but is still a single fault.

        Without MIN_PROPORTIONAL_FAULTS this crosses any ratio <= 0.5 and
        aborts a 2-wide fan-out on one unlucky request.
        """
        assert not should_gate([_f("throttling_error")], fanout_width=2)

    def test_two_of_two_gates(self):
        faults = [_f("throttling_error", 0), _f("throttling_error", 1)]
        assert should_gate(faults, fanout_width=2)

    def test_third_of_a_wide_fanout_gates(self):
        faults = [_f("throttling_error", i) for i in range(7)]
        assert should_gate(faults, fanout_width=20)

    def test_just_below_threshold_does_not_gate(self):
        faults = [_f("throttling_error", i) for i in range(6)]
        assert not should_gate(faults, fanout_width=20)

    def test_reason_quantifies_breadth(self):
        faults = [_f("throttling_error", i) for i in range(7)]
        r = gate_reason(faults, fanout_width=20)
        assert "7 of 20" in r

    def test_explicit_ratio_overrides_default(self):
        faults = [_f("throttling_error", i) for i in range(2)]
        assert not should_gate(faults, fanout_width=20)
        assert should_gate(faults, fanout_width=20, ratio=0.1)


class TestClassesDoNotCombine:
    """Two unrelated causes must not sum into a quorum."""

    def test_mixed_kinds_gate_via_auth_not_arithmetic(self):
        faults = [_f("authentication_error", 0), _f("throttling_error", 1)]
        assert should_gate(faults, fanout_width=20)
        assert "session-level" in gate_reason(faults, fanout_width=20)

    def test_a_throttle_plus_a_connection_error_still_counts_together(self):
        """Both ARE proportional kinds, so they legitimately aggregate."""
        faults = [_f("throttling_error", i) for i in range(4)]
        faults += [_f("connection_error", i + 4) for i in range(3)]
        assert should_gate(faults, fanout_width=20)


class TestNoFaultsNeverGates:

    def test_empty_does_not_gate(self):
        assert not should_gate([], fanout_width=20)

    def test_empty_reason_is_blank(self):
        assert gate_reason([], fanout_width=20) == ""

    def test_reason_is_blank_whenever_gate_is_false(self):
        faults = [_f("throttling_error")]
        assert not should_gate(faults, fanout_width=20)
        assert gate_reason(faults, fanout_width=20) == ""


class TestRatioEnvOverride:
    """A typo must not disable or neuter the gate."""

    @pytest.mark.parametrize("bad", ["0", "0.0", "-1", "2.5", "abc", "1e9"])
    def test_out_of_range_falls_back_to_default(self, bad, monkeypatch):
        monkeypatch.setenv("ZIYA_TASK_INFRA_GATE_RATIO", bad)
        six = [_f("throttling_error", i) for i in range(6)]
        seven = [_f("throttling_error", i) for i in range(7)]
        assert not should_gate(six, fanout_width=20)
        assert should_gate(seven, fanout_width=20)

    def test_valid_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv("ZIYA_TASK_INFRA_GATE_RATIO", "0.9")
        faults = [_f("throttling_error", i) for i in range(7)]
        assert not should_gate(faults, fanout_width=20)


class TestSummarizeAnswersTheUXQuestions:
    """The three things a held run currently cannot tell you."""

    def test_reports_breadth_not_just_the_first_fault(self):
        faults = [_f("authentication_error", i) for i in range(18)]
        s = summarize(faults, fanout_width=20)
        assert s["fault_count"] == 18
        assert s["fanout_width"] == 20

    def test_carries_the_call_path_for_the_breadcrumb(self):
        path = ("CL0", "CL1: Ziya Ground Truth", "audit-mcp-security")
        s = summarize([_f("authentication_error", 0, path)], fanout_width=20)
        assert s["call_path"] == list(path)

    def test_deepest_path_wins(self):
        shallow = _f("throttling_error", 0, ("CL0",))
        deep = _f("throttling_error", 1, ("CL0", "CL1", "auditor"))
        s = summarize([shallow, deep], fanout_width=4)
        assert s["call_path"] == ["CL0", "CL1", "auditor"]

    def test_auth_is_fleet_wide_even_when_isolated(self):
        """One dead credential IS fleet-wide regardless of count."""
        s = summarize([_f("authentication_error")], fanout_width=20)
        assert s["fleet_wide"] is True

    def test_one_throttle_is_not_fleet_wide(self):
        s = summarize([_f("throttling_error")], fanout_width=20)
        assert s["fleet_wide"] is False

    def test_majority_throttling_is_fleet_wide(self):
        faults = [_f("throttling_error", i) for i in range(12)]
        assert summarize(faults, fanout_width=20)["fleet_wide"] is True

    def test_primary_kind_prefers_the_actionable_one(self):
        """An auth fault outranks a more numerous throttle."""
        faults = [_f("throttling_error", i) for i in range(5)]
        faults.append(_f("authentication_error", 9))
        assert summarize(faults, 20)["primary_kind"] == "authentication_error"

    def test_kind_histogram_is_reported(self):
        faults = [_f("throttling_error", i) for i in range(3)]
        faults += [_f("connection_error", 9)]
        assert summarize(faults, 20)["kinds"] == {
            "throttling_error": 3, "connection_error": 1,
        }

    def test_block_ids_are_deduped_and_sorted(self):
        faults = [_f("throttling_error", 1), _f("throttling_error", 1),
                  _f("throttling_error", 0)]
        assert summarize(faults, 4)["block_ids"] == ["b0", "b1"]

    def test_empty_summary_is_well_formed(self):
        s = summarize([], fanout_width=20)
        assert s["fault_count"] == 0
        assert s["primary_kind"] is None
        assert s["fleet_wide"] is False
        assert s["call_path"] == []


class TestDefaultsAreSane:

    def test_ratio_is_a_minority_not_a_majority(self):
        """A third, deliberately: a throttle here already burned ~75 s of
        retries, so a third of the fan-out failing that way is stronger
        evidence than the raw count suggests."""
        assert 0.2 < DEFAULT_GATE_RATIO < 0.5

    def test_min_faults_protects_narrow_fanouts(self):
        assert MIN_PROPORTIONAL_FAULTS >= 2
