"""
Resume-target resolution for task runs.

Two user-facing acts, one mechanism
-----------------------------------
A terminated run offers two forward moves, and the difference between
them is purely *which block becomes the resume point*:

* **retry from X** — re-execute X.  Resume point = X.
* **continue from X** — accept X's recorded outcome and start at the
  block after it.  Resume point = X's successor.

That symmetry is why ``continue`` needs no executor change at all.  The
resume gate in ``block_executor.execute_block`` replays every block
ahead of the resume point (see ``_replay_artifact``), so pointing the
gate at X's successor makes X itself replay — which *is* "accept the
recorded outcome", including when that outcome was a failure.

Why targets get normalized
--------------------------
Only structural blocks have durable per-block state:
``_mark_block_status`` writes to ``run.block_states`` only when
``ctx.binding_stack`` is empty, and only ``repeat`` / ``until`` push
that stack.  A block inside a loop body therefore has no per-iteration
record and cannot be a resume point; such a request resolves to the
*outermost* enclosing loop, which does.

Operates on the raw ``card_snapshot`` dict rather than a rehydrated
``Block`` so a snapshot written by an older or newer schema cannot fail
validation during what is only an id lookup.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Blocks that push a binding frame, and therefore whose bodies have no
# durable per-block state.  Must stay in step with the containers that
# call ``ctx.binding_stack.append`` in block_executor.
_LOOP_TYPES = ("repeat", "until")


def find_block(
    node: Dict[str, Any], target_id: str,
) -> Optional[Dict[str, Any]]:
    """The raw snapshot node with ``target_id``, or None."""
    if node.get("id") == target_id:
        return node
    for child in (node.get("body") or []):
        found = find_block(child, target_id)
        if found is not None:
            return found
    return None


def is_loop_node(node: Optional[Dict[str, Any]]) -> bool:
    return bool(node) and node.get("block_type") in _LOOP_TYPES


def resolve_iteration_resume(
    root: Dict[str, Any],
    block_id: str,
    index: int,
    summaries: List[Dict[str, Any]],
    mode: str = "retry_iteration",
    inherited: Optional[Dict[int, Any]] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """Resolve a click on an iteration dot into a loop start index.

    Returns ``(start_index, error)``.  ``start_index`` is the first
    iteration the resumed loop EXECUTES; every earlier iteration replays
    its recorded artifact so the propagation chain stays intact.

    * ``retry_iteration`` — re-run ``index``.  Start = ``index``.
    * ``continue_iteration`` — accept ``index``'s recorded outcome and
      run the next one.  Start = ``index + 1``.

    ``inherited`` is the run's own ``resume_iteration_artifacts`` — the
    iterations IT replayed from an earlier attempt, keyed by index.  It
    must be consulted alongside ``summaries``, because a run that is
    itself a mid-loop resume records only the iterations it EXECUTED: a
    run resumed at 3 has summaries for 3 and 4 only, so retrying its own
    first visible dot looked like "iteration 2 was never recorded" even
    though 2's artifact sits on that run's record.  Without this a
    mid-loop resume works exactly once and refuses every attempt after
    it — worst on precisely the long campaigns the feature exists for,
    where chained resumes are the normal case.

    Only presence is read, never content: an inherited entry is a dict on
    a run read from disk and an ``Artifact`` in-process, and treating it
    as "there is an input for this index" is true of both.

    The refusals below are deliberate, and each prevents a resume that
    would appear to work while silently producing wrong results:

    **Parallel loops.**  Iterations cannot see each other (see
    ``_execute_repeat``: bindings carry index and item, never previous),
    so "resume at 3" has no ordering meaning — 0..2 were not
    prerequisites of 3.  Resuming would just run fewer iterations than
    the card asks for while reporting the loop complete.

    **A missing predecessor artifact.**  Only iterations with
    ``has_artifact`` have a file on disk; passes beyond
    PASS_ARTIFACT_RETENTION_CAP (50) do not.  Replaying an absent
    artifact yields an empty ``{{previous}}``, so a body that reads it
    would run against nothing and its failure would look like a defect in
    the card.  Only the IMMEDIATE predecessor is required, since that is
    what ``{{previous}}`` binds; ``{{all}}`` degrades to a shorter history,
    which is visible in the summary rather than silently wrong.
    """
    if mode not in ("retry_iteration", "continue_iteration"):
        return None, f"unknown iteration resume mode {mode!r}"

    node = find_block(root, block_id)
    if node is None:
        return None, f"Block {block_id} not found in this run's card snapshot."
    if not is_loop_node(node):
        return None, (
            "Only a Repeat or Until block has iterations to resume from. "
            "Use retry or continue to resume from this block instead."
        )
    if node.get("repeat_parallel"):
        return None, (
            "This loop runs its iterations in parallel, so they do not "
            "depend on each other and there is no meaningful point to "
            "resume from. Retry the whole loop instead."
        )
    if index < 0:
        return None, "Iteration index must be zero or greater."

    recorded = {int(s.get("index", -1)): s for s in (summaries or [])}
    carried = {int(k) for k in (inherited or {})}
    if index not in recorded and index not in carried:
        return None, (
            f"Iteration {index} was never recorded for this loop, so there "
            f"is nothing to resume from."
        )

    start = index if mode == "retry_iteration" else index + 1
    if start == 0:
        return 0, None  # nothing to replay

    # An inherited predecessor already IS a replayable artifact — it was
    # carried onto this run for exactly that purpose — so it satisfies the
    # requirement without a retention check.
    if (start - 1) in carried:
        return start, None

    prev = recorded.get(start - 1)
    if prev is None:
        return None, (
            f"Iteration {start - 1} was never recorded, so iteration "
            f"{start} has no prior result to build on."
        )
    if not prev.get("has_artifact", True):
        return None, (
            f"Iteration {start - 1}'s full result was not retained (only "
            f"the first 50 passing iterations of a loop are kept), so "
            f"iteration {start} cannot be given its input. Retry the whole "
            f"loop instead."
        )
    return start, None


def snapshot_contains(node: Dict[str, Any], target_id: str) -> bool:
    """True if ``target_id`` names ``node`` or anything beneath it."""
    if node.get("id") == target_id:
        return True
    return any(
        snapshot_contains(c, target_id) for c in (node.get("body") or [])
    )


def find_resume_target(
    node: Dict[str, Any], target_id: str, in_loop: bool = False,
) -> Optional[str]:
    """Resolve a requested block id to a *resumable* block id.

    Returns the id to actually resume from, or None if ``target_id``
    names no block in the snapshot tree.

    The ``not in_loop`` guard on the substitution below is what makes
    the answer the OUTERMOST enclosing loop rather than the innermost:
    for an ``until`` nested in a ``repeat``, an inner target must
    resolve to the repeat, since the until is itself inside the
    repeat's body and equally unpersisted.  ``group`` and ``parallel``
    do not push bindings, so their children resolve to themselves.
    """
    if node.get("id") == target_id:
        return None if in_loop else target_id
    is_loop = node.get("block_type") in _LOOP_TYPES
    for child in (node.get("body") or []):
        found = find_resume_target(child, target_id, in_loop or is_loop)
        if found is not None:
            return found
        # Child subtree held the target but it was unresumable.  If we
        # are the OUTERMOST loop wrapping it, we are the answer; if we
        # are ourselves inside a loop, defer to that ancestor.
        if is_loop and not in_loop and snapshot_contains(child, target_id):
            return node.get("id")
    return None


def _subtree_size(node: Dict[str, Any]) -> int:
    """Node count of ``node``'s subtree, including itself."""
    return 1 + sum(_subtree_size(c) for c in (node.get("body") or []))


def _preorder(
    node: Dict[str, Any], in_loop: bool = False,
    out: Optional[List[Tuple[Dict[str, Any], bool]]] = None,
) -> List[Tuple[Dict[str, Any], bool]]:
    """Depth-first pre-order walk yielding ``(node, is_inside_a_loop)``.

    Pre-order matters: it is the order ``_execute_sequence`` visits
    blocks, so "the next block" in this list is the next block the
    executor would reach.
    """
    if out is None:
        out = []
    out.append((node, in_loop))
    is_loop = node.get("block_type") in _LOOP_TYPES
    for child in (node.get("body") or []):
        _preorder(child, in_loop or is_loop, out)
    return out


def next_execution_target(
    root: Dict[str, Any], resolved_id: str,
) -> Optional[str]:
    """The resumable block the executor reaches *after* ``resolved_id``.

    Skips ``resolved_id``'s entire subtree, which is the whole point: if
    the user continues past a Group of five stages, they mean the block
    after the group, not the group's first child.  Subtree width is
    computed from the tree rather than inferred from indentation, so a
    deeply nested container is skipped correctly.

    Returns None when ``resolved_id`` is the last resumable block in the
    deck — there is nothing to continue to, and the caller should say so
    rather than silently launching a run that executes nothing.
    """
    flat = _preorder(root)
    idx = next(
        (i for i, (n, _) in enumerate(flat) if n.get("id") == resolved_id),
        None,
    )
    if idx is None:
        return None
    after = idx + _subtree_size(flat[idx][0])
    for node, in_loop in flat[after:]:
        # A block inside a loop body has no durable state and cannot be
        # a resume point; the loop that owns it was already skipped as
        # part of some earlier subtree, so keep scanning.
        if in_loop:
            continue
        bid = node.get("id")
        if bid:
            return bid
    return None


def resolve_resume_point(
    root: Dict[str, Any], block_id: str, mode: str = "retry",
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a user's click into the block the new run resumes at.

    Returns ``(resume_point, normalized_target, error)``:

    * ``resume_point`` — what to hand the executor as
      ``resume_from_block_id``.
    * ``normalized_target`` — the block the USER pointed at, after loop
      normalization.  Recorded on the run as ``resumed_from_block_id``
      so the UI can say "continued from Stage 5" rather than naming the
      successor, which would be confusing.
    * ``error`` — a human-readable reason, or None on success.

    For ``retry`` the two ids are the same.  For ``continue`` they
    differ, and that difference is exactly what makes the target replay
    instead of re-execute.
    """
    if mode not in ("retry", "continue"):
        return None, None, f"unknown resume mode {mode!r} (expected retry | continue)"

    normalized = find_resume_target(root, block_id)
    if normalized is None:
        return None, None, f"Block {block_id} not found in this run's card snapshot."

    if mode == "retry":
        return normalized, normalized, None

    successor = next_execution_target(root, normalized)
    if successor is None:
        return None, normalized, (
            "Nothing follows this block, so there is nothing to continue "
            "to. Use retry to re-run it, or rerun the card from scratch."
        )
    return successor, normalized, None
