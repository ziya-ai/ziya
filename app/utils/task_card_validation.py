"""Pre-launch structural validation of a task card block tree.

``Block`` sets ``extra="allow"`` and declares no validators, which is
the right call for forward compatibility — a card file written by a newer
version must still load — but it means an authoring mistake is accepted
silently and surfaces only when the executor reaches the block.  For a
long fan-out card that can be hours in, and for an orchestrator whose
Call blocks ARE the deck, a typo in the last target is discovered only
after every earlier phase has run and been paid for.

Two severities, deliberately:

* **error** — the run cannot do what the card says.  A Task with no
  instructions raises ``TaskExecutorError`` at dispatch; a ``for_each``
  with no source raises ``ForEachSourceError`` when the loop is reached;
  an unresolvable ``call_target`` yields a failed artifact.  Worth
  stopping for.
* **warning** — suspicious but runnable.  A typo'd field name is
  absorbed by ``extra="allow"`` and the authored intent silently
  dropped, which the author almost certainly wants to know about, but
  refusing to launch over it would be a behaviour change rather than a
  safety net.

What this module deliberately does NOT do:

* Re-implement cycle detection or the call-depth cap.  The executor
  already enforces both (``ExecutionContext.call_stack`` and
  ``MAX_CALL_DEPTH``) and is tested on them; duplicating that logic here
  would create two places for it to disagree.
* Judge a templated ``for_each`` source.  It resolves against runtime
  artifacts, so it cannot be checked statically, and flagging the
  canonical planner-then-fan-out shape would teach authors to ignore the
  validator.
* Invent findings it cannot substantiate.  With no project context a
  Call target is unresolvable-in-principle rather than wrong, and a
  resolver that throws an unexpected exception means "cannot verify",
  not "invalid" — the same rule the launch credentials preflight
  follows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..agents.task_call import CallResolutionError, resolve_call_target
from ..models.task_card import Block

# Block types the executor knows how to run.  A local set rather than a
# read off the Literal so a new block type must be consciously added
# here too — the alternative is a validator that silently accepts a type
# the executor will reject.
KNOWN_BLOCK_TYPES = frozenset({
    "task", "repeat", "parallel", "until", "schedule", "state", "group",
    "call",
})

# Container types whose whole purpose is their body.
CONTAINER_TYPES = frozenset({
    "repeat", "parallel", "until", "schedule", "group",
})

# Above this many planned parallel iterations, note that the run will be
# bounded by the concurrency cap.  Mirrors
# block_executor.DEFAULT_REPEAT_CONCURRENCY; not imported, to keep this
# module importable from the API layer without pulling in the executor.
_WIDE_FANOUT_THRESHOLD = 8


@dataclass
class Finding:
    """One validation result, located well enough to act on."""

    message: str
    block_id: str
    #: Human-readable ancestry, e.g. "Root > Fan out > Audit step".  A
    #: bare block id is not locatable in a 40-block card.
    path: str
    severity: str = "error"


@dataclass
class ValidationResult:
    errors: List[Finding] = field(default_factory=list)
    warnings: List[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing blocks a launch.  Warnings do not."""
        return not self.errors

    def summary(self) -> str:
        """Operator-readable text, or "" when clean.

        Errors first: the reader is deciding what to fix, and a warning
        list ahead of the blocking reason buries it.
        """
        lines: List[str] = []
        for f in self.errors:
            lines.append(f"ERROR [{f.path}]: {f.message}")
        for f in self.warnings:
            lines.append(f"WARNING [{f.path}]: {f.message}")
        return "\n".join(lines)


def _label(block: Block) -> str:
    return block.name or block.id or block.block_type or "?"


def validate_card_tree(
    root: Optional[Block],
    *,
    project_id: Optional[str] = None,
    project_root: Optional[str] = None,
) -> ValidationResult:
    """Walk ``root`` and report structural defects.

    ``project_id`` / ``project_root`` enable Call-target resolution.
    Without them target checking is skipped rather than failed.
    """
    res = ValidationResult()
    if root is None:
        res.errors.append(Finding(
            message="card has no root block", block_id="", path="(root)",
        ))
        return res
    _walk(root, [], res, project_id, project_root)
    return res


def _walk(
    block: Block,
    ancestry: List[str],
    res: ValidationResult,
    project_id: Optional[str],
    project_root: Optional[str],
) -> None:
    path = " > ".join([*ancestry, _label(block)])
    bid = block.id or ""

    def err(msg: str) -> None:
        res.errors.append(Finding(message=msg, block_id=bid, path=path))

    def warn(msg: str) -> None:
        res.warnings.append(Finding(
            message=msg, block_id=bid, path=path, severity="warning",
        ))

    btype = block.block_type

    if btype not in KNOWN_BLOCK_TYPES:
        err(f"unknown block_type {btype!r} - the executor will refuse it")
        return  # Nothing below can be interpreted against an unknown type.

    # Fields the model absorbed but nothing reads.  Almost always a typo
    # for a real field, and the authored intent is silently lost.
    for name in sorted((block.model_extra or {})):
        warn(
            f"unknown field {name!r} is ignored - check the spelling "
            f"(nothing reads it, so any value set there has no effect)"
        )

    if btype == "task":
        if not (block.instructions or "").strip():
            err("task has no instructions - dispatch will fail")

    elif btype == "call":
        if not (block.call_target or "").strip():
            err("call block has no call_target")
        else:
            _check_call_target(block, err, warn, project_id, project_root)

    elif btype == "repeat":
        _check_repeat(block, err, warn)

    elif btype == "until":
        # until_condition is legitimately empty for the /goal shape,
        # which relies on Artifact.self_assessment instead (see
        # design/goal-exit-conditions.md), so its absence is not a
        # finding.  An absent cap is: _execute_until defaults to 5,
        # which may not be what an author writing an open-ended loop
        # expects.
        if not block.until_max:
            warn("until block has no until_max - defaults to 5 iterations")

    if btype in CONTAINER_TYPES and not (block.body or []):
        warn(f"{btype} block has an empty body - it will do nothing")

    for child in block.body or []:
        _walk(child, [*ancestry, _label(block)], res, project_id, project_root)


def _check_repeat(block: Block, err, warn) -> None:
    mode = block.repeat_mode or "count"

    if mode == "for_each":
        raw = block.repeat_for_each_source or ""
        if not raw.strip():
            err(
                "repeat_mode is 'for_each' but repeat_for_each_source is "
                "empty - the loop has no items to iterate over"
            )
        # A templated source resolves at runtime; not checkable here.

    elif mode == "count":
        if block.repeat_count is not None and block.repeat_count < 1:
            warn(
                f"repeat_count is {block.repeat_count} - the body will "
                f"never run"
            )

    # Wide parallel fan-out: bounded by the concurrency cap, but the
    # author is choosing a shape whose wall-clock cost is set by that cap
    # rather than by the fan-out width, which is worth stating up front.
    if block.repeat_parallel:
        planned = block.repeat_count or block.repeat_max or 0
        limit = block.repeat_max_concurrency
        if limit is None and planned > _WIDE_FANOUT_THRESHOLD:
            warn(
                f"{planned} parallel iterations with no "
                f"repeat_max_concurrency - they will run "
                f"{_WIDE_FANOUT_THRESHOLD} at a time (the default cap)"
            )


def _check_call_target(
    block: Block, err, warn,
    project_id: Optional[str],
    project_root: Optional[str],
) -> None:
    """Resolve a Call target now rather than when the block is reached.

    ``resolve_call_target`` is pure lookup with no side effects, which is
    what makes this safe to run at launch.
    """
    if not project_id:
        return  # Cannot verify; not a defect.
    try:
        resolve_call_target(
            block.call_target, block.call_target_kind,
            project_id=project_id, project_root=project_root,
        )
    except CallResolutionError as e:
        err(f"call target {block.call_target!r} does not resolve: {e}")
    except Exception as e:  # noqa: BLE001
        # A resolver fault is not a verdict on the card.
        warn(
            f"could not verify call target {block.call_target!r} "
            f"({type(e).__name__}: {e})"
        )
