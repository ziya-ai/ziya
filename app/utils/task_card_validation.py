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

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..agents.task_call import CallResolutionError, resolve_call_target
from ..models.task_card import Block
from .roster_keys import roster_key_problems

# Block types the executor knows how to run.  A local set rather than a
# read off the Literal so a new block type must be consciously added
# here too — the alternative is a validator that silently accepts a type
# the executor will reject.
KNOWN_BLOCK_TYPES = frozenset({
    "task", "repeat", "parallel", "until", "schedule", "state", "group",
    "call", "ask",
})

# A template variable name has to be reachable as {{var.NAME}}.  Anything
# else binds the answer where nothing can read it, which is decidable here
# and therefore refused rather than discovered at run time.
_ASK_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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
    enclosing: Tuple[str, ...] = (),
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

    elif btype == "ask":
        _check_ask(block, err, enclosing)

    if btype in CONTAINER_TYPES and not (block.body or []):
        warn(f"{btype} block has an empty body - it will do nothing")

    # What this block is, as an ENCLOSURE for the subtree beneath it.  A
    # parallel Repeat is both at once, which is why these accumulate as tags
    # rather than resolving to a single kind.
    child_enclosing = enclosing
    if btype == "parallel" or (btype == "repeat" and block.repeat_parallel):
        child_enclosing = (*child_enclosing, "parallel")
    if btype in ("repeat", "until"):
        child_enclosing = (*child_enclosing, "loop")

    for child in block.body or []:
        _walk(child, [*ancestry, _label(block)], res, project_id,
              project_root, child_enclosing)


def _check_ask(block: Block, err, enclosing: Tuple[str, ...]) -> None:
    """Refusals for a human-in-the-loop Ask block.

    An Ask holds the run at a block boundary with status ``awaiting_input``
    until a human answers, then binds the answer into the run the way a
    State block binds its literals.

    The last two refusals are about the single-slot shape of the answer
    record rather than about the block itself, and both are errors rather
    than warnings because in each case the alternative is a run that carries
    on having acted on an answer nobody gave for that occasion.  Neither is
    a permanent limit -- they are the honest boundary of what the current
    record can express, stated where an author finds out before launching
    rather than after.
    """
    # ``getattr`` rather than attribute access, matching the repeat checks
    # above: extra="allow" means a card authored against a newer schema can
    # arrive carrying these as model extras, and a validator that raises
    # AttributeError reports nothing at all about the rest of the tree.
    if not (getattr(block, "ask_question", None) or "").strip():
        err(
            "ask block has no ask_question - it would hold the run "
            "indefinitely on a blank prompt, with nothing telling the "
            "operator what is being asked"
        )
    if block.body:
        err(
            f"ask block has a body of {len(block.body)} block(s) - ask is a "
            f"leaf, so nothing inside it would ever run"
        )
    name = (getattr(block, "ask_variable", None) or "").strip()
    if name and not _ASK_VARIABLE_RE.match(name):
        err(
            f"ask_variable {name!r} is not a usable template name - the "
            f"answer would be bound where no {{{{var.NAME}}}} can reach it"
        )
    choices = getattr(block, "ask_choices", None)
    if choices is not None:
        cleaned = [str(c).strip() for c in choices]
        if not cleaned:
            err(
                "ask_choices is present but empty - either offer choices or "
                "omit the field for a free-text answer"
            )
        if any(not c for c in cleaned):
            err("ask_choices contains a blank choice")
        dupes = sorted({c for c in cleaned if c and cleaned.count(c) > 1})
        if dupes:
            err(
                f"ask_choices repeats {dupes} - a duplicated choice cannot be "
                f"told apart in the recorded answer"
            )
    if "parallel" in enclosing:
        err(
            "ask block sits inside a parallel container - a run records ONE "
            "open question at a time, so concurrent asks would overwrite "
            "each other and the run would proceed on whichever answer landed "
            "last. Put the checkpoint above the fan-out"
        )
    if "loop" in enclosing:
        err(
            "ask block sits inside a loop body - answers are recorded per "
            "block, so the second iteration would silently reuse the first "
            "iteration's answer instead of asking again. Put the checkpoint "
            "outside the loop, or ask before the loop about the whole batch"
        )


def _literal_roster_size(raw: str) -> Optional[int]:
    """Item count when a for_each source is a literal JSON array.

    None when the source is templated or unparseable - the roster then
    only exists at run time, so nothing here can size it.
    """
    text = (raw or "").strip()
    if "{{" in text or not text.startswith("["):
        return None
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        return None
    return len(items) if isinstance(items, list) else None


def _check_for_each_cap(block: Block, raw: str, warn) -> None:
    """Flag a repeat_max that will clip a for_each roster.

    repeat_max is a legitimate cost ceiling, but on a for_each block it is
    also a SCOPE limit, and the two are easy to conflate: the loop
    completes every iteration it PLANNED and reports success, while the
    roster's tail was never dispatched.  Measured on a real run - a
    108-item roster under a cap of 60 dropped 48 items.

    It is especially easy to get wrong in this mode because the cap is not
    authorable here: RepeatBlockEditor renders the repeat_max input only
    for 'until', and switching modes does not clear it, so a number set as
    an until-bound goes on to act as a for_each clip the editor never
    shows.  The pre-existing wide-fanout warning does not cover this: it
    speaks about concurrency, and reading "60 parallel iterations will run
    8 at a time" as confirmation that all 60 run is the natural reading.

    A literal source is decidable now, so the warning names the real loss.
    A templated source is not, which is exactly why a fixed cap on one is
    worth stating: the author is choosing a number without being able to
    know the roster it will be measured against.
    """
    cap = block.repeat_max
    if not cap or cap <= 0:
        return  # None/0 mean uncapped - run the whole roster.
    roster = _literal_roster_size(raw)
    if roster is None:
        warn(
            f"repeat_max={cap} caps a templated for_each roster - if the "
            f"source resolves to more than {cap} items the remainder is "
            f"never run, and the block still reports success. Set "
            f"repeat_max to 0 to run the whole roster, and bound cost with "
            f"repeat_max_concurrency instead"
        )
    elif roster > cap:
        warn(
            f"repeat_max={cap} clips this for_each roster of {roster} "
            f"items - {roster - cap} would never run. Set repeat_max to 0 "
            f"to run all {roster}"
        )


def _check_require_complete(block: Block, raw: str, err) -> None:
    """Refusals for ``repeat_require_complete`` decidable before launch.

    Mirrors the executor's plan-time refusals in
    ``block_executor._plan_iterations`` - the two must agree on what the
    field means, which is why key derivation is shared via
    ``roster_keys`` rather than reimplemented here.

    A finite cap is refused for ANY source; roster-key hazards are only
    decidable for a literal source (a templated roster exists at run
    time, where the executor applies the same refusals).
    """
    if block.repeat_max and block.repeat_max > 0:
        err(
            f"repeat_require_complete with repeat_max={block.repeat_max} "
            f"is a contradiction - a completeness requirement and a cost "
            f"ceiling cannot both hold. Remove one; bound cost with "
            f"repeat_max_concurrency instead"
        )
    text = (raw or "").strip()
    if "{{" in text or not text.startswith("["):
        return  # Templated: the roster's keys exist only at run time.
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        return
    if not isinstance(items, list):
        return
    for problem in roster_key_problems(
        items, getattr(block, "repeat_item_key", None),
    ):
        err(problem)


def _check_repeat(block: Block, err, warn) -> None:
    mode = block.repeat_mode or "count"

    # The roster completeness assertion only means something where a
    # roster exists.  count runs N anonymous passes; until stops on a
    # condition - neither has a member set to be complete OVER.
    if getattr(block, "repeat_require_complete", False) and mode != "for_each":
        err(
            f"repeat_require_complete is set but repeat_mode is {mode!r} "
            f"- only for_each has a roster to assert completeness over"
        )

    if mode == "for_each":
        raw = block.repeat_for_each_source or ""
        if not raw.strip():
            err(
                "repeat_mode is 'for_each' but repeat_for_each_source is "
                "empty - the loop has no items to iterate over"
            )
        # A templated source's ITEMS resolve at runtime and are not
        # checkable here, but a cap that will clip them is.
        _check_for_each_cap(block, raw, warn)
        if getattr(block, "repeat_require_complete", False):
            _check_require_complete(block, raw, err)

    elif mode == "count":
        if block.repeat_count is not None and block.repeat_count < 1:
            warn(
                f"repeat_count is {block.repeat_count} - the body will "
                f"never run"
            )

    elif mode == "until":
        # An uncapped until-mode Repeat is inert rather than unbounded:
        # _plan_iterations reads ``int(block.repeat_max or 1)``, so the
        # body runs once and the until-condition is evaluated after the
        # only pass it will ever get.  "Until" that cannot loop is
        # indistinguishable from count=1, and nothing said so - the editor
        # displayed 3 for the same field, and the sibling Until BLOCK
        # defaults to 5, so all three descriptions of one concept
        # disagreed.  Stated here rather than raising the runtime default,
        # which would silently change spend on every existing card.
        if not block.repeat_max:
            warn(
                "repeat_mode is 'until' but repeat_max is unset - it "
                "defaults to 1, so the body runs once and the "
                "until-condition can never cause a second pass. Set "
                "repeat_max to the number of attempts the loop may make"
            )

    # Wide parallel fan-out: bounded by the concurrency cap, but the
    # author is choosing a shape whose wall-clock cost is set by that cap
    # rather than by the fan-out width, which is worth stating up front.
    #
    # ``planned`` was previously read off repeat_count/repeat_max alone,
    # so an UNCAPPED for_each - the shape most in need of the warning -
    # could never fire it.  For for_each the roster IS the width: size it
    # from a literal source when decidable, and for a templated source
    # say "roster-sized" rather than silently reading a cap that is not
    # there.
    if block.repeat_parallel:
        limit = block.repeat_max_concurrency
        planned: Optional[int]
        if mode == "for_each":
            roster = _literal_roster_size(block.repeat_for_each_source or "")
            cap = block.repeat_max if (block.repeat_max or 0) > 0 else None
            if roster is not None:
                planned = min(roster, cap) if cap else roster
            else:
                planned = cap  # templated: unknowable unless capped
        else:
            planned = block.repeat_count or block.repeat_max or 0
        if limit is None and planned is not None and planned > _WIDE_FANOUT_THRESHOLD:
            warn(
                f"{planned} parallel iterations with no "
                f"repeat_max_concurrency - they will run "
                f"{_WIDE_FANOUT_THRESHOLD} at a time (the default cap)"
            )
        elif limit is None and planned is None:
            warn(
                f"a roster-sized parallel fan-out with no "
                f"repeat_max_concurrency - if the templated roster "
                f"resolves to more than {_WIDE_FANOUT_THRESHOLD} items "
                f"they will run {_WIDE_FANOUT_THRESHOLD} at a time (the "
                f"default cap)"
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
