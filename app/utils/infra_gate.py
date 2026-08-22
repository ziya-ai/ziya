"""
Infra-fault gate policy: when does one subagent's fault hold the fleet?

A Task Card fan-out (``repeat_parallel``) dispatches N sibling tasks
concurrently.  When one hits an infrastructure fault the question is
whether the OTHERS are about to hit the same wall, and the honest answer
depends entirely on the fault's kind:

  - ``authentication_error`` is a property of the SESSION, not the
    request.  One expired credential means all N are already dead; the
    only useful response is to stop admitting work immediately.  Waiting
    for a quorum here burns the whole fan-out to learn something the
    first fault already proved.

  - ``throttling_error`` / ``transient_service_error`` /
    ``connection_error`` are properties of a REQUEST.  One sibling
    throttling while 19 succeed is not a system hold, and aborting the
    fan-out on it would destroy recoverable work.  These need evidence of
    breadth before they justify a gate.

Note on severity: a throttle reaching this module has ALREADY exhausted
``agent.py``'s 4-attempt retry ladder (5/10/20/40 s backoff), so it is
not a casual blip — it is a request that could not be completed after
~75 s of trying.  That is why the proportional threshold is set at a
third rather than a majority: by the time a third of a fan-out has
independently burned its full retry ladder, the shared dependency is the
likelier explanation than coincidence.

Why this is a separate, pure module: the same policy has to be readable
from the executor (which enforces it), from the surfacing layer (which
explains it), and from tests (which pin it) — the arrangement
``task_tool_floor`` already uses for the tool-allowlist floor, and for
the same reason: a policy duplicated across enforcement and explanation
sites eventually disagrees with itself.

No I/O, no async, no model state.  The executor owns the fault list;
this file owns the judgment about it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# Fault kinds that are a property of the session rather than the request.
# One occurrence is proof the whole fan-out is dead, so these gate on the
# FIRST fault with no quorum requirement.
IMMEDIATE_GATE_KINDS: frozenset = frozenset({
    "authentication_error",
})

# Fault kinds that are a property of a single request.  These gate only
# once enough of the fan-out has failed the same way to make a shared
# cause more plausible than coincidence.
#
# ``connection_error`` sits here rather than in IMMEDIATE_GATE_KINDS
# deliberately: it covers both "the endpoint is gone" (systemic) and
# "one socket died" (isolated), and the two are indistinguishable from a
# single occurrence.  Treating it as immediate would abort healthy
# fan-outs on one flaky connection.
PROPORTIONAL_GATE_KINDS: frozenset = frozenset({
    "throttling_error",
    "transient_service_error",
    "connection_error",
})

# Fraction of a fan-out that must fault before a proportional kind gates.
DEFAULT_GATE_RATIO = 0.34

# Absolute floor, applied to proportional kinds regardless of ratio.
#
# Load-bearing for narrow fan-outs: without it, a 2-wide fan-out reaches
# 50% on a single fault and would gate on one isolated throttle — the
# exact over-eager abort the proportional path exists to avoid.  Two
# faults is the minimum evidence that can distinguish "shared cause"
# from "one unlucky request".
MIN_PROPORTIONAL_FAULTS = 2


@dataclass(frozen=True)
class InfraFault:
    """One infrastructure fault observed inside a fan-out.

    ``call_path`` is the chain of Call targets that led to the faulting
    block, outermost first — the thing that answers "which card, and
    which of its subagents" without expanding anything in the UI.
    """
    kind: str
    block_id: str = ""
    call_path: Tuple[str, ...] = ()
    index: Optional[int] = None
    at: float = 0.0


def _gate_ratio() -> float:
    """Configured proportional threshold, clamped to a sane range.

    Read per-call rather than cached at import so a long-running server
    picks up a changed setting without a restart.  Out-of-range and
    unparseable values fall back to the default rather than raising: a
    typo'd env var must not be able to disable the gate entirely (0.0
    would gate on the first fault of any kind) or neuter it (values > 1
    can never be reached).
    """
    raw = os.environ.get("ZIYA_TASK_INFRA_GATE_RATIO")
    if not raw:
        return DEFAULT_GATE_RATIO
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_GATE_RATIO
    if not (0.0 < val <= 1.0):
        return DEFAULT_GATE_RATIO
    return val


def is_infra_gating_kind(kind: str) -> bool:
    """True if ``kind`` participates in gating at all."""
    return kind in IMMEDIATE_GATE_KINDS or kind in PROPORTIONAL_GATE_KINDS


def should_gate(
    faults: Sequence[InfraFault], fanout_width: int,
    ratio: Optional[float] = None,
) -> bool:
    """True if the fan-out should stop admitting new work.

    ``fanout_width`` is the number of siblings dispatched, used as the
    denominator for proportional kinds.  A width of 0 or 1 makes the
    proportional test meaningless (there is nothing left to protect), so
    only the immediate kinds can gate there.

    Immediate and proportional kinds are evaluated independently: a
    single ``authentication_error`` gates even when no proportional
    threshold is met, and vice versa.  They are not summed, because
    adding an auth fault to a throttle count would let two unrelated
    causes combine into a quorum neither established on its own.
    """
    if not faults:
        return False
    if any(f.kind in IMMEDIATE_GATE_KINDS for f in faults):
        return True
    if fanout_width <= 1:
        return False
    proportional = [f for f in faults if f.kind in PROPORTIONAL_GATE_KINDS]
    if len(proportional) < MIN_PROPORTIONAL_FAULTS:
        return False
    threshold = (ratio if ratio is not None else _gate_ratio()) * fanout_width
    return len(proportional) >= threshold


def gate_reason(
    faults: Sequence[InfraFault], fanout_width: int,
    ratio: Optional[float] = None,
) -> str:
    """Human-readable justification for a gate decision.

    Returned empty when the gate would not fire, so a caller can use it
    as both the test and the log line.
    """
    if not should_gate(faults, fanout_width, ratio):
        return ""
    immediate = [f for f in faults if f.kind in IMMEDIATE_GATE_KINDS]
    if immediate:
        return (
            f"{immediate[0].kind} is a session-level fault — every sibling "
            f"in this fan-out shares the same dead credential"
        )
    proportional = [f for f in faults if f.kind in PROPORTIONAL_GATE_KINDS]
    # ``faults`` is RUN-scoped while ``fanout_width`` describes ONE loop, so
    # nested fan-outs can push the numerator past the denominator: an outer
    # 3-wide loop whose body is an inner 10-wide loop produced "33 of 10
    # siblings (330%)" through the real executor.  Widen the denominator
    # rather than clamping the count -- dropping faults would understate the
    # damage and disagree with ``kinds``, whose counts are unclamped.
    denom = max(fanout_width, len(proportional))
    pct = (len(proportional) / denom * 100) if denom else 0.0
    return (
        f"{len(proportional)} of {denom} siblings ({pct:.0f}%) hit "
        f"request-level infra faults after exhausting their retry ladders"
    )


def summarize(
    faults: Sequence[InfraFault], fanout_width: int = 0,
) -> Dict[str, object]:
    """Aggregate a fault list into the shape the hold surface needs.

    The point of this function is that a hold is not the first fault: it
    is the terminal state of a progressive collapse, and what a reader
    needs is its BREADTH.  ``fault_count`` against ``fanout_width`` is
    what distinguishes "my credential died and took all 20 auditors"
    from "one auditor got throttled" — two situations that share a
    status and call for opposite responses.

    ``primary_kind`` is the most frequent kind, with immediate-gate
    kinds winning ties: when a session fault and a throttle are both
    present, the session fault is the actionable one.
    """
    if not faults:
        return {
            "fault_count": 0, "fanout_width": fanout_width,
            "primary_kind": None, "kinds": {}, "call_path": [],
            "fleet_wide": False, "block_ids": [],
        }
    kinds: Dict[str, int] = {}
    for f in faults:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    primary = max(
        kinds.items(),
        key=lambda kv: (kv[0] in IMMEDIATE_GATE_KINDS, kv[1]),
    )[0]
    # Deepest path wins: the most specific location is the most useful
    # breadcrumb, and a nested callee's path strictly contains its
    # caller's.  Ties broken by first occurrence for determinism.
    deepest = max(faults, key=lambda f: len(f.call_path))
    # Same run-scoped-numerator / loop-scoped-denominator mismatch as in
    # gate_reason: ``faults`` is RUN-scoped while ``fanout_width`` describes
    # ONE loop, so nested fan-outs accumulate more faults than any single
    # loop was wide -- the real executor produced "33 of 10", which a
    # surface renders verbatim.  Widening is the honest reading (33 things
    # faulted, so at least 33 were dispatched); clamping the count would
    # understate the damage and disagree with ``kinds``, which is unclamped.
    effective_width = max(fanout_width, len(faults))
    # "Fleet-wide" is a claim about breadth, so it requires either a
    # session-level kind (which is fleet-wide by nature, whatever the
    # count) or a majority of the fan-out.  Uses the widened denominator so
    # a nested collapse does not read as fleet-wide purely because the
    # arithmetic overflowed.
    fleet_wide = (
        primary in IMMEDIATE_GATE_KINDS
        or (effective_width > 1 and len(faults) >= effective_width / 2)
    )
    return {
        "fault_count": len(faults),
        "fanout_width": effective_width,
        "primary_kind": primary,
        "kinds": kinds,
        "call_path": list(deepest.call_path),
        "fleet_wide": fleet_wide,
        "block_ids": sorted({f.block_id for f in faults if f.block_id}),
    }
