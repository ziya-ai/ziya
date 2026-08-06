"""
Task run API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from ..models.task_card import Artifact
from ..models.task_binding import TaskBinding
from ..models.task_run import TaskRun, IterationSummary, IterationStatus
from ..storage.projects import ProjectStorage
from ..storage.task_bindings import TaskBindingStorage
from ..storage.task_runs import TaskRunStorage
from ..utils.paths import get_ziya_home, get_project_dir
from ..utils.logging_utils import logger

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/task-runs",
    tags=["task-runs"],
)


def _get_storage(project_id: str) -> TaskRunStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return TaskRunStorage(get_project_dir(project_id))


@router.get("", response_model=List[TaskRun])
async def list_task_runs(
    project_id: str,
    card_id: Optional[str] = Query(None),
):
    """List runs for a project.  Filter by card_id if supplied."""
    return _get_storage(project_id).list(card_id=card_id)


@router.get("/{run_id}/lineage", response_model=List[TaskRun])
async def get_run_lineage(project_id: str, run_id: str):
    """Every attempt in this run's lineage, oldest attempt first.

    A resume creates a new run and leaves the source intact, so a card
    that was retried twice has three run records.  Presenting those as
    three sibling tiles is what made it unclear whether prior state
    survived; the GUI collapses them to one tile plus an attempt rail,
    and this is what it reads.

    Always contains at least the run itself, including for records
    written before lineage tracking existed.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return storage.list_lineage(run.root_run_id or run.id)


@router.get("/{run_id}", response_model=TaskRun)
async def get_task_run(project_id: str, run_id: str):
    run = _get_storage(project_id).get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return run


@router.delete("/{run_id}", status_code=204)
async def delete_task_run(project_id: str, run_id: str):
    """Delete a completed run record.  Does not cancel running runs —
    use POST /cancel to stop a running run first."""
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running run")
    storage.delete(run_id)


@router.post("/{run_id}/cancel", response_model=TaskRun)
async def cancel_task_run(project_id: str, run_id: str):
    """Cancel a running task run.

    Two paths:

    * **Soft-cancel (live executor).** When the run's executor is alive
      in *this* process, set ``cancel_requested`` and return.  The
      block executor honors the flag at the next iteration / sibling
      boundary and any in-flight Task invocation completes normally.
      This is the design/task-cards.md §Cancellation path.

    * **Force-cancel (zombie run).** When on-disk status is ``running``
      but no live executor exists for this run in this process, the
      run is a zombie left over from a prior server lifetime: the
      executor coroutine was killed by the restart and no flag-watcher
      will ever see ``cancel_requested``.  Mark the run ``cancelled``
      directly so the UI reflects reality.  The startup reconciler
      catches most of these; this branch handles a zombie that arrived
      *during* this server's lifetime (e.g. crash without restart).
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        # Idempotent: already terminal, return unchanged.
        return run
    # Live executor: standard soft-cancel path.
    if storage.is_active(run_id):
        return storage.request_cancel(run_id)
    # No live executor: force the terminal state directly so the UI
    # cancel button is not silently a no-op.
    import time as _time
    run.status = "cancelled"  # type: ignore[assignment]
    run.cancel_requested = True
    if run.completed_at is None:
        run.completed_at = _time.time()
    run.updated_at = int(_time.time() * 1000)
    storage._write_json(storage._run_file(run_id), run.model_dump())
    return run


@router.post("/{run_id}/pause", response_model=TaskRun)
async def pause_task_run(project_id: str, run_id: str):
    """Request a soft pause on a running task run.

    Sets ``pause_requested`` on the run file; the executor observes it
    at the next boundary (between Repeat iterations, sequence siblings,
    or until loops — the same boundaries soft-cancel uses) and flips
    status to ``paused``.  An in-flight Task/LLM invocation is never
    interrupted — pause lands at the next boundary.

    The on-disk flag is the cross-process channel (the executor reads
    it via storage.get), exactly like soft-cancel.  We deliberately do
    NOT gate on the process-local live-run set: it is per-instance and
    a fresh storage object is built per request, so it cannot see a
    coroutine that marked itself active on a different instance.  A
    genuinely dead run (zombie) simply never observes the flag — a
    no-op that the startup reconciler cleans up.  Idempotent on
    terminal runs.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        return run  # terminal — nothing to pause
    return storage.request_pause(run_id)


@router.post("/{run_id}/resume", response_model=TaskRun)
async def resume_task_run(project_id: str, run_id: str):
    """Clear a run's pause flag so its executor's wait-loop resumes.

    The executor restores status to ``running`` at its next poll.
    Idempotent on terminal runs and on runs that aren't paused.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        return run
    return storage.request_resume(run_id)


@router.post("/{run_id}/step", response_model=TaskRun)
async def step_task_run(project_id: str, run_id: str, count: int = 1):
    """Advance a held run by ``count`` boundaries, then hold again.

    Grants step credits and leaves ``pause_requested`` SET, which is the
    whole difference from /resume: the executor's wait-loop spends one
    credit to cross the boundary it is holding at, then holds at the
    next one.  Credits accumulate, so ``count=3`` advances three
    boundaries.

    A step on a *running* (not yet paused) run is meaningful rather than
    a no-op: ``request_step`` sets the pause flag too, so the run
    advances to its next boundary and holds there.  That is how you take
    control of a card already in flight.

    Granularity is a block/iteration boundary — the same three points
    pause and soft-cancel land on (sequence siblings, Repeat iterations,
    until loops).  Stepping past a Task block runs that entire Task,
    including all of its LLM iterations and tool calls; there is no
    mid-Task hold point.

    Note the run's ``status`` blips paused → running → paused across a
    step, because the wait-loop's exit path restores ``running`` before
    the next boundary re-pauses.  That is accurate — the executor really
    is running during the block — so a caller wanting a stable "held"
    indicator should key on ``pause_requested`` / ``step_budget`` rather
    than on ``status`` alone.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        return run  # terminal — nothing to step
    if count < 1:
        raise HTTPException(
            status_code=422, detail="count must be >= 1",
        )
    return storage.request_step(run_id, count)
@router.get("/{run_id}/iterations")
async def list_iterations(
    project_id: str, run_id: str,
    block_id: Optional[str] = Query(None),
    status: Optional[IterationStatus] = Query(None),
    signature: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    include: str = Query("", description="comma-list; 'artifact' hydrates full Artifacts"),
) -> Dict[str, Any]:
    """Filter iteration summaries across one or all Repeat blocks in a run.

    Defaults return summaries only; pass include=artifact to also load
    each match's full Artifact from disk (expensive at scale — use a
    tight filter with include=artifact).
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")

    includes = {tok.strip() for tok in include.split(",") if tok.strip()}
    hydrate = "artifact" in includes

    matches: List[Dict[str, Any]] = []
    for bid, state in run.block_states.items():
        if block_id and bid != block_id:
            continue
        for summary in state.iteration_summaries:
            if status and summary.status != status:
                continue
            if signature and summary.signature != signature:
                continue
            row: Dict[str, Any] = {
                "block_id": bid,
                "summary": summary.model_dump(),
            }
            if hydrate and summary.has_artifact:
                artifact = storage.read_iteration_artifact(
                    run_id, bid, summary.index,
                )
                row["artifact"] = artifact.model_dump() if artifact else None
            matches.append(row)

    matches.sort(key=lambda r: (r["block_id"], r["summary"]["index"]))
    return {
        "total": len(matches),
        "limit": limit,
        "offset": offset,
        "items": matches[offset:offset + limit],
    }


@router.get("/{run_id}/artifacts/{filename}")
async def get_artifact_blob(project_id: str, run_id: str, filename: str):
    """Serve one frozen artifact blob for a run.

    Reads through ``read_artifact_blob`` so at-rest-encrypted bytes are
    decrypted transparently — the browser must never receive an ALE
    envelope.

    Security: ``filename`` is attacker-influenced (artifact names come
    from model output), so path resolution goes through
    ``resolve_artifact_blob_path``, which refuses separators, ``..``,
    and any symlink resolving outside the run's own artifacts dir.
    Content type is mapped from a fixed extension table and only
    known-safe types are served ``inline``; everything else (notably
    HTML/JS/SVG, which could execute in Ziya's origin) is forced to
    ``application/octet-stream`` with ``Content-Disposition:
    attachment``.
    """
    from ..utils.task_artifacts import (
        INLINE_SAFE_MEDIA_TYPES,
        media_type_for_filename,
        read_artifact_blob,
        resolve_artifact_blob_path,
    )

    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")

    artifacts_dir = get_project_dir(project_id) / "task_runs" / run_id / "artifacts"
    path, err = resolve_artifact_blob_path(str(artifacts_dir), filename)
    if err:
        # "not found" is a 404; every other rejection is a bad request.
        if "not found" in err:
            raise HTTPException(status_code=404, detail=err)
        raise HTTPException(status_code=400, detail=err)

    blob = read_artifact_blob(str(path))
    if blob is None:
        raise HTTPException(
            status_code=500,
            detail="Artifact exists but could not be read or decrypted",
        )

    media_type = media_type_for_filename(filename)
    if media_type in INLINE_SAFE_MEDIA_TYPES:
        disposition = f'inline; filename="{path.name}"'
    else:
        media_type = "application/octet-stream"
        disposition = f'attachment; filename="{path.name}"'

    return Response(
        content=blob,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            # Frozen at emit time and never rewritten — safe to cache hard.
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


class ResumeFromResponse(BaseModel):
    """The new run, plus the binding that makes it visible in the GUI.

    Returning the run alone was not enough: the GUI can only render a
    run through a TaskBinding (``Conversation.tsx`` maps bindings to
    tiles and ``useTaskBindings`` is the only source), and no other
    endpoint binds an *existing* run — POST /task-bindings launches its
    own run, and /{id}/launch 409s unless ``run_id`` is null.  So a
    resumed run was unrenderable and, worse, unrecoverable after a
    reload: nothing on disk associated it with a chat.

    ``binding`` is None only when the source run has no chat to bind to
    (no ``source_conversation_id``) or the write failed; the run is
    still valid and executing in that case.
    """
    run: TaskRun
    binding: Optional[TaskBinding] = None


def _source_anchor(project_id: str, chat_id: str, run_id: str) -> Optional[str]:
    """The anchor of the binding that displayed the source run, if any.

    Reusing it puts the resumed run's tile at the same point in the
    conversation as the run it continues, which is where a user looking
    at the original will expect to find it.  Falls back to None
    (unanchored, rendered at the chat tail) rather than guessing.
    """
    try:
        for b in TaskBindingStorage(get_project_dir(project_id)).list_for_chat(chat_id):
            if b.run_id == run_id:
                return b.anchor_message_id
    except Exception as e:
        logger.warning(f"resume-from: anchor lookup failed for {run_id[:8]}: {e}")
    return None


@router.post("/{run_id}/resume-from/{block_id}", response_model=ResumeFromResponse)
async def resume_run_from_block(
    project_id: str, run_id: str, block_id: str,
    mode: str = Query("retry", pattern="^(retry|continue)$"),
):
    """Continue a finished run, preserving prior state.

    Two modes, differing only in which block becomes the resume point:

    * ``retry`` — re-execute ``block_id``.  Use when the block failed
      and you want another go at it.
    * ``continue`` — accept ``block_id``'s recorded outcome and start at
      the block AFTER it.  Use when you fixed the problem by hand, or
      want to skip past a failure.  Because the resume gate replays
      everything ahead of the resume point, pointing it at the successor
      is precisely what makes ``block_id`` replay from record — no
      executor change is needed to support this.

    Creates a NEW run rather than reviving the source run, keeping that
    run as an immutable record (the same guarantee ``card_snapshot`` and
    ``permissions_snapshot`` provide).  The new run joins the source
    run's **lineage** (``root_run_id`` / ``attempt`` / ``resume_kind``),
    so the GUI shows one threaded tile with an attempt rail rather than
    an unexplained second tile — which is what made it unclear whether
    prior state had been preserved.  It:

    * executes the source run's ``card_snapshot`` tree, so block ids
      line up with the artifacts being replayed and card edits made
      since the source run cannot change what resumes;
    * replays each earlier block's recorded Artifact instead of
      executing it, so ``{{sibling("id")}}`` and ``{{previous_sibling}}``
      still resolve — this is what preserves prior deck state;
    * re-executes ``state`` blocks (pure literal writes) to rebuild
      ``{{var.NAME}}``, since ``variables`` is not persisted;
    * carries the source run's ``parameter_overrides`` forward, or the
      resumed blocks would silently fall back to authored baselines.

    The new run is also **bound to the same chat** as the source run,
    reusing its anchor.  Without that the resumed run has no route to
    the screen at all — see ResumeFromResponse.  Binding happens after
    the launch so a binding failure degrades to an invisible-but-running
    run rather than losing the run entirely.

    Distinct from POST /{run_id}/resume, which merely clears a pause
    flag on a *live* executor and cannot help a run whose coroutine has
    already unwound.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if storage.is_active(run_id) or run.status in ("running", "paused"):
        # NOTE: is_active() is effectively always False here — _active_runs
        # is a per-instance set and _get_storage builds a fresh storage per
        # request — so the on-disk status check is what actually gates this.
        # Resuming a live run would double-execute its remaining blocks.
        raise HTTPException(
            status_code=409,
            detail=f"Run is still {run.status}; cancel it before resuming.",
        )
    snapshot = run.card_snapshot or {}
    root = snapshot.get("root")
    if not root:
        # Pre-snapshot runs cannot be resumed safely: the live card's
        # block ids may no longer match this run's block_states.
        raise HTTPException(
            status_code=422,
            detail="Run has no card_snapshot; it predates resume support.",
        )

    from ..utils.resume_targets import resolve_resume_point
    resume_point, user_target, err = resolve_resume_point(root, block_id, mode)
    if err and user_target is None:
        # Unknown block id.
        raise HTTPException(status_code=404, detail=err)
    if err:
        # Known block, but the request cannot be honoured — e.g.
        # continuing past the last block would launch a run that replays
        # everything and executes nothing, which looks like a successful
        # resume that did nothing.
        raise HTTPException(
            status_code=422, detail=err,
        )

    from ..models.task_card import Block
    try:
        root_block = Block(**root)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail=f"Card snapshot is not loadable: {e}",
        )

    # Prior deck state: every block that completed in the source run.
    # Blocks with no artifact never completed, so there is nothing to
    # replay and the resume gate substitutes a marker.
    resume_artifacts = {
        bid: st.artifact
        for bid, st in (run.block_states or {}).items()
        if st.artifact is not None
    }

    from .task_cards import _launch_run_for_card
    new_run = await _launch_run_for_card(
        project_id=project_id,
        card_id=run.card_id,
        source_conversation_id=run.source_conversation_id,
        parameter_overrides=dict(run.parameter_overrides or {}),
        resume_root=root_block,
        resume_from_block_id=resume_point,
        resume_artifacts=resume_artifacts,
        # Lineage: this attempt continues the source run's chain.
        parent_run_id=run.id,
        root_run_id=run.root_run_id or run.id,
        attempt=(run.attempt or 1) + 1,
        resume_kind=("retry_from" if mode == "retry" else "continue_from"),
        # The block the USER pointed at — for a continue this is NOT the
        # resume point (that is its successor), and naming the successor
        # here would make the UI report the wrong stage.
        resumed_from_block_id=user_target,
    )

    # Bind the new run so the GUI can render it, and so it survives a
    # reload.  Deliberately non-fatal: the run is already executing by
    # this point, and reporting a 500 would tell the user their resume
    # failed when in fact it is running — just invisibly.
    binding = None
    chat_id = run.source_conversation_id
    if chat_id:
        # Resolve the anchor BEFORE the create call rather than inline as
        # an argument.  Inline, an escape from _source_anchor skips
        # create() altogether and costs the whole binding — so the run
        # becomes unrenderable over a merely cosmetic failure, which is
        # the opposite of this block's stated non-fatal intent.  The
        # helper already swallows its own errors, so this guard only
        # fires on something unforeseen; the fallback (None) is exactly
        # what the helper returns on a handled failure, and an unanchored
        # binding still renders at the chat tail.
        try:
            anchor = _source_anchor(project_id, chat_id, run_id)
        except Exception as e:  # noqa: BLE001 — anchor is cosmetic
            logger.warning(f"resume-from: anchor resolution failed: {e}")
            anchor = None
        try:
            binding = TaskBindingStorage(get_project_dir(project_id)).create(
                chat_id=chat_id,
                card_id=run.card_id,
                run_id=new_run.id,
                anchor_message_id=anchor,
            )
        except Exception as e:
            logger.warning(
                f"resume-from: binding {new_run.id[:8]} to chat "
                f"{chat_id[:8]} failed: {e}",
            )
    else:
        logger.info(
            f"resume-from: run {new_run.id[:8]} has no source chat; unbound",
        )
    return ResumeFromResponse(run=new_run, binding=binding)


@router.post(
    "/{run_id}/resume-iteration/{block_id}/{index}",
    response_model=ResumeFromResponse,
)
async def resume_run_from_iteration(
    project_id: str, run_id: str, block_id: str, index: int,
    mode: str = Query(
        "retry_iteration",
        pattern="^(retry_iteration|continue_iteration)$",
    ),
):
    """Continue a finished run from a point INSIDE a loop.

    The gap this closes: a loop was only ever resumable at iteration 0.
    ``find_resume_target`` normalises a loop-body click up to the loop
    itself, and the loop then re-plans from scratch — so a five-iteration
    campaign that lost iteration 5 to an expired credential had to re-pay
    all five, discarding four banked passes.  That is the most expensive
    lost work the task system had, because a long loop is precisely where
    a run is most likely to outlive a credential.

    Two modes, mirroring the block-level pair:

    * ``retry_iteration`` — re-run ``index``.
    * ``continue_iteration`` — accept ``index``'s recorded result and run
      the next one.  Use after fixing the cause by hand.

    Iterations before the resume point replay their recorded artifacts, so
    the first executed iteration sees the same ``{{previous}}`` /
    ``{{all}}`` bindings it would have seen in the source run.  Blocks
    BEFORE the loop replay through the existing block-level gate, exactly
    as a block-level resume does.

    Refused (422) for a parallel loop, and when the immediate
    predecessor's full artifact was not retained — both cases would
    otherwise produce a run that looks successful while feeding an empty
    input to the work.  See ``resolve_iteration_resume``.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if storage.is_active(run_id) or run.status in ("running", "paused"):
        raise HTTPException(
            status_code=409,
            detail=f"Run is still {run.status}; cancel it before resuming.",
        )
    snapshot = run.card_snapshot or {}
    root = snapshot.get("root")
    if not root:
        raise HTTPException(
            status_code=422,
            detail="Run has no card_snapshot; it predates resume support.",
        )

    state = (run.block_states or {}).get(block_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"Block {block_id} has no recorded state in this run.",
        )

    # Iterations this run inherited from an earlier attempt, rather than
    # executing itself.  Only meaningful when the inheritance belongs to
    # the loop being resumed — a different loop's indices would otherwise
    # be read as satisfying this one's predecessor requirement.
    inherited = (
        dict(run.resume_iteration_artifacts or {})
        if run.resumed_from_block_id == block_id else {}
    )

    from ..utils.resume_targets import resolve_iteration_resume
    start, err = resolve_iteration_resume(
        root, block_id, index,
        [s.model_dump() for s in (state.iteration_summaries or [])],
        mode,
        inherited=inherited,
    )
    if err:
        # 422 rather than 400: the request is well-formed, but this
        # particular loop/iteration cannot honour it.  The detail names
        # which of the refusals applied so the user can act on it.
        raise HTTPException(status_code=422, detail=err)

    from ..models.task_card import Block
    try:
        root_block = Block(**root)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail=f"Card snapshot is not loadable: {e}",
        )

    # Block-level prior state, exactly as the block-level resume builds
    # it: everything ahead of the loop replays from record.
    resume_artifacts = {
        bid: st.artifact
        for bid, st in (run.block_states or {}).items()
        if st.artifact is not None
    }

    # Per-iteration prior state for the loop being resumed.  Read here,
    # at launch, rather than during execution: the source run may be
    # deleted while the new one is still going, and a resume that depends
    # on a file it does not own is a resume that silently breaks later.
    #
    # Seeded from what THIS run inherited, then overlaid with what it
    # executed.  Both halves are required: the executed half alone loses
    # every iteration the source attempt replayed, so a chain of resumes
    # would shed its early iterations one attempt at a time and the
    # propagation chain would quietly shorten with each retry.  The
    # overlay order matters — a freshly executed iteration supersedes an
    # inherited record for the same index.
    iteration_artifacts = {
        int(k): v for k, v in inherited.items() if int(k) < start
    }
    for summary in (state.iteration_summaries or []):
        if summary.index >= start:
            continue
        if not summary.has_artifact:
            continue
        got = storage.read_iteration_artifact(run_id, block_id, summary.index)
        if got is not None:
            iteration_artifacts[summary.index] = got

    from .task_cards import _launch_run_for_card
    new_run = await _launch_run_for_card(
        project_id=project_id,
        card_id=run.card_id,
        source_conversation_id=run.source_conversation_id,
        parameter_overrides=dict(run.parameter_overrides or {}),
        resume_root=root_block,
        # The LOOP is the block-level resume point: blocks before it
        # replay, and the loop itself executes — starting at ``start``.
        resume_from_block_id=block_id,
        resume_artifacts=resume_artifacts,
        resume_from_iteration=start,
        resume_iteration_artifacts=iteration_artifacts,
        parent_run_id=run.id,
        root_run_id=run.root_run_id or run.id,
        attempt=(run.attempt or 1) + 1,
        resume_kind=mode,
        resumed_from_block_id=block_id,
    )

    # Bind so the resumed run is renderable and survives a reload — the
    # same non-fatal treatment as the block-level path, for the same
    # reason: the run is already executing by now, so a binding failure
    # must not be reported as a failed resume.
    binding = None
    chat_id = run.source_conversation_id
    if chat_id:
        try:
            anchor = _source_anchor(project_id, chat_id, run_id)
        except Exception as e:  # noqa: BLE001 — anchor is cosmetic
            logger.warning(f"resume-iteration: anchor resolution failed: {e}")
            anchor = None
        try:
            binding = TaskBindingStorage(get_project_dir(project_id)).create(
                chat_id=chat_id,
                card_id=run.card_id,
                run_id=new_run.id,
                anchor_message_id=anchor,
            )
        except Exception as e:
            logger.warning(
                f"resume-iteration: binding {new_run.id[:8]} to chat "
                f"{chat_id[:8]} failed: {e}",
            )
    else:
        logger.info(
            f"resume-iteration: run {new_run.id[:8]} has no source chat; unbound",
        )
    return ResumeFromResponse(run=new_run, binding=binding)
@router.get("/{run_id}/iterations/{block_id}/{index}", response_model=Artifact)
async def get_iteration_artifact(
    project_id: str, run_id: str, block_id: str, index: int,
):
    """Fetch the full Artifact for one iteration.  Returns 404 if the
    iteration was retained as a summary-only record (beyond the
    pass-retention cap) or if it never existed."""
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    artifact = storage.read_iteration_artifact(run_id, block_id, index)
    if not artifact:
        raise HTTPException(status_code=404, detail="Iteration artifact not found")
    return artifact
