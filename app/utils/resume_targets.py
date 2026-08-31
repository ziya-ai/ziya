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

from typing import Any, Dict, List, Optional, Set, Tuple

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
    call_snapshots: Optional[Dict[str, Any]] = None,
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

    # Resolved within its OWNING tree, so a loop inside a called card is
    # found.  Its iterations are already recorded against this run (callee
    # block states persist), and the executor now walks through the call to
    # reach the loop itself, so there is nothing left to refuse: a mid-loop
    # resume of a callee loop is the same operation as one in the caller.
    tree, _chain = locate_block(root, call_snapshots, block_id)
    node = find_block(tree, block_id) if tree is not None else None
    if node is None:
        return None, f"Block {block_id} not found in this run's card snapshot."
    if not is_loop_node(node):
        return None, (
            "Only a Repeat or Until block has iterations to resume from. "
            "Use retry or continue to resume from this block instead."
        )
    if node.get("repeat_parallel"):
        # Still refused, and still for the original reason: an INDEX has no
        # meaning where iterations receive ``previous=None``.  But the
        # remedy is no longer expensive — a block-level retry of a parallel
        # loop replays every banked iteration and re-runs only the ones
        # that never finished (see ``parallel_replay_indices``), so pointing
        # the user at it costs them nothing.
        return None, (
            "This loop runs its iterations in parallel, so they do not "
            "depend on each other and there is no single point to resume "
            "from. Retry the loop instead — iterations that already "
            "succeeded are replayed from record, not re-run."
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


# Bound on how many Call frames a target may be unwound through.  Matches
# the executor's own MAX_CALL_DEPTH in spirit rather than by import (this
# module deliberately depends on nothing), and the loop below also
# terminates on a repeated id, so this is a cap against a run file whose
# ``call_snapshots`` were hand-edited into a cycle.
MAX_CALL_UNWIND = 8


def enclosing_call_block(
    call_snapshots: Optional[Dict[str, Any]], target_id: str,
) -> Optional[str]:
    """The id of the Call block whose CALLEE tree contains ``target_id``.

    A Call runs its target inline in the caller's run, so the callee's
    blocks stream status and persist in ``run.block_states`` — but the
    callee's TREE is in neither the card nor ``card_snapshot``.  It lives
    only in ``run.call_snapshots``, keyed by the Call block's own id (see
    ``block_executor._record_call_audit``), and the recorded tree carries
    the callee card's OWN block ids.

    That asymmetry is the defect this exists for: a run held inside a
    called card records a ``held_at_block_id`` naming a callee block, which
    ``find_resume_target`` cannot see at all.  Every resume request built
    from it 404'd, so a multi-phase study that died on an expired
    credential could not be continued by any route except Restart — which
    discards every phase it had already paid for.

    Returns None when ``target_id`` is not inside any recorded callee,
    which is the normal case for a block in the caller's own tree.
    """
    hit = enclosing_call(call_snapshots, target_id)
    return hit[0] if hit else None


def enclosing_call(
    call_snapshots: Optional[Dict[str, Any]], target_id: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """``(call_block_id, callee_root)`` for the call containing ``target_id``.

    The root is returned alongside the id because every caller that needs
    to know a block is inside a callee also needs that callee's TREE — to
    resolve the block's position, its successor, or its label.  Looking it
    up twice invited the two lookups to disagree.
    """
    for call_block_id, snap in (call_snapshots or {}).items():
        if not isinstance(snap, dict):
            continue
        root = snap.get("root")
        if isinstance(root, dict) and snapshot_contains(root, target_id):
            return call_block_id, root
    return None


def locate_block(
    root: Dict[str, Any],
    call_snapshots: Optional[Dict[str, Any]],
    block_id: str,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Find the tree ``block_id`` lives in, and how to reach it.

    Returns ``(tree, call_chain)``:

    * ``tree`` — the block tree actually containing ``block_id``: this
      run's own ``card_snapshot`` root, or the recorded root of the callee
      that owns it.  None when the id is nowhere.
    * ``call_chain`` — Call block ids that must be DESCENDED INTO to reach
      it, outermost first.  Empty for a block in the caller's own tree.

    This replaces an earlier design that substituted the Call block for the
    callee block and resumed there.  That was wrong in the way that costs
    the most: a fan-out held on iteration 19 of 20 inside a called card
    re-entered the callee from its own start and re-ran every banked
    iteration — measured at 14 hours of work discarded by a control
    labelled "resume".  The chain lets the executor's resume gate walk
    THROUGH the call to the real block instead (see
    ``block_executor.ExecutionContext.resume_call_chain``).

    Walks outward from the target rather than inward from the root because
    only the outward direction is indexed: ``call_snapshots`` maps a call
    block id to its callee tree, so "who contains this?" is a scan of that
    map, while "what does this call contain?" would require resolving
    targets by name against live cards.
    """
    if snapshot_contains(root, block_id):
        return root, []
    tree: Optional[Dict[str, Any]] = None
    chain: List[str] = []
    seen = {block_id}
    current = block_id
    for _ in range(MAX_CALL_UNWIND):
        hit = enclosing_call(call_snapshots, current)
        if hit is None:
            return None, []
        call_id, callee_root = hit
        if call_id in seen:
            return None, []      # cycle in a hand-edited run file
        seen.add(call_id)
        if tree is None:
            # First hop out: this callee is the tree holding the target.
            tree = callee_root
        chain.append(call_id)
        if snapshot_contains(root, call_id):
            chain.reverse()      # innermost-first -> outermost-first
            return tree, chain
        current = call_id
    return None, []


def resume_call_chain(
    root: Dict[str, Any],
    call_snapshots: Optional[Dict[str, Any]],
    block_id: str,
) -> List[str]:
    """Just the chain from ``locate_block`` — the executor's descent hint."""
    return locate_block(root, call_snapshots, block_id)[1]


def parallel_replay_indices(
    root: Dict[str, Any],
    block_id: str,
    summaries: Optional[List[Dict[str, Any]]],
    call_snapshots: Optional[Dict[str, Any]] = None,
) -> Optional[List[int]]:
    """Iterations of a PARALLEL loop that a resume can replay from record.

    Returns None when ``block_id`` is not a parallel loop, so the caller
    can tell "nothing to bank" apart from "not applicable".

    Only ``passed`` iterations holding a retained artifact qualify.  Both
    conditions matter and for different reasons:

    * a ``failed`` iteration is the work the user is resuming to redo, so
      replaying it would make the resume a no-op that reports success;
    * an iteration past PASS_ARTIFACT_RETENTION_CAP has only a summary on
      disk, and replaying an absent artifact would drop its outputs from
      the loop's result while still counting it done.

    Index-set semantics, not a prefix, because a parallel fan-out has no
    ordering to take a prefix of: iterations receive ``previous=None``, so
    each one is independent and "which ones are already banked" is the
    only question with an answer.  That is also why this is safe where
    ``resolve_iteration_resume`` correctly refuses — it is not resuming
    the loop AT an index, it is re-running the subset that never finished.
    """
    tree, _chain = locate_block(root, call_snapshots, block_id)
    if tree is None:
        return None
    node = find_block(tree, block_id)
    if not is_loop_node(node) or not (node or {}).get("repeat_parallel"):
        return None
    out: List[int] = []
    for s in (summaries or []):
        if s.get("status") != "passed" or not s.get("has_artifact", True):
            continue
        if s.get("replayed"):
            # Carried from an earlier attempt.  Still replayable — the
            # artifact was copied onto this run at launch — so it counts.
            pass
        idx = s.get("index")
        if isinstance(idx, int) and idx >= 0:
            out.append(idx)
    return sorted(set(out))


def serial_replay_prefix(
    root: Dict[str, Any],
    block_id: str,
    summaries: Optional[List[Dict[str, Any]]],
    call_snapshots: Optional[Dict[str, Any]] = None,
    inherited: Optional[Set[int]] = None,
) -> Optional[int]:
    """Leading iterations of a SERIAL loop that a resume can replay.

    Returns the index the loop should START at, or None when ``block_id``
    is not a serial loop — so the caller can tell "no prefix to bank"
    (0) apart from "not applicable".

    The serial counterpart to ``parallel_replay_indices``, and the reason
    a block-level retry of a serial loop no longer discards its banked
    iterations.  Until this existed only the parallel shape consulted its
    record: a serial campaign held on iteration 22 re-planned from zero
    and re-paid for all 22, while the tile's own recovery banner promised
    they would be "replayed from record".

    A PREFIX rather than an index set, because a serial loop's iterations
    are dependent: ``{{previous}}`` binds the immediately preceding
    iteration, so a gap cannot be tolerated the way it can in a fan-out.
    That is also why the walk STOPS at the first index that is not a
    retained pass rather than skipping it:

    * a ``failed`` iteration is where the work actually stopped, so it is
      the first thing the resume must re-run;
    * an iteration past PASS_ARTIFACT_RETENTION_CAP holds only a summary,
      so replaying it would feed the next iteration an empty
      ``{{previous}}``.  Stopping there still banks everything before it,
      which is strictly better than the whole-loop re-run that was the
      only prior option.

    ``inherited`` is the set of indices the run carried from an earlier
    attempt (``run.resume_iteration_artifacts``).  Consulted alongside
    ``summaries`` for the same reason ``resolve_iteration_resume`` does
    it: a run that is itself a resume records summaries only for the
    iterations it EXECUTED, so a chain of resumes would otherwise shed
    its banked prefix one attempt at a time.

    Presence here is a claim about the RECORD, not about the disk.  The
    caller must read the artifacts in index order and truncate at the
    first one missing, since a hole would break the dependency chain
    this prefix exists to preserve.
    """
    tree, _chain = locate_block(root, call_snapshots, block_id)
    if tree is None:
        return None
    node = find_block(tree, block_id)
    if not is_loop_node(node) or (node or {}).get("repeat_parallel"):
        return None
    recorded: Dict[int, Dict[str, Any]] = {}
    for s in (summaries or []):
        idx = s.get("index")
        if isinstance(idx, int) and idx >= 0:
            recorded[idx] = s
    carried = set(inherited or ())
    start = 0
    while True:
        s = recorded.get(start)
        if s is None:
            if start not in carried:
                break
        elif s.get("status") != "passed" or not s.get("has_artifact", True):
            break
        start += 1
    return start


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
    call_snapshots: Optional[Dict[str, Any]] = None,
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

    # ``call_snapshots`` is what makes a hold inside a called card
    # resumable at all: without it the callee's block ids are invisible to
    # this tree and the request is rejected as unknown.  Resolution happens
    # WITHIN the tree that owns the block — the callee's own recorded root
    # when it is inside a call — so the resume point is the real block
    # rather than the Call block standing in for it.  The executor reaches
    # it via the descent chain (``resume_call_chain``).
    tree, _chain = locate_block(root, call_snapshots, block_id)
    if tree is None:
        return None, None, f"Block {block_id} not found in this run's card snapshot."

    normalized = find_resume_target(tree, block_id)
    if normalized is None:
        return None, None, f"Block {block_id} not found in this run's card snapshot."

    if mode == "retry":
        return normalized, normalized, None

    # Successor within the owning tree.  When the target is the LAST block
    # of a callee there is nothing after it there, so continue at the block
    # after the Call itself — walking out one frame at a time, which is
    # what "continue past the final stage of Phase 1" has to mean.
    successor = next_execution_target(tree, normalized)
    if successor is None:
        _tree, chain = locate_block(root, call_snapshots, normalized)
        for call_id in reversed(chain):
            parent_tree, _ = locate_block(root, call_snapshots, call_id)
            if parent_tree is None:
                break
            successor = next_execution_target(parent_tree, call_id)
            if successor is not None:
                break
    if successor is None:
        return None, normalized, (
            "Nothing follows this block, so there is nothing to continue "
            "to. Use retry to re-run it, or rerun the card from scratch."
        )
    return successor, normalized, None
