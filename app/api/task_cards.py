"""
Task card API endpoints.

CRUD endpoints plus a launch endpoint.  Launch creates a TaskRun,
schedules execution in a background task, and returns immediately
with the run_id.  Clients poll GET /task-runs/{run_id} for status.
"""

import asyncio
import time
from fastapi import APIRouter, HTTPException, Query
from typing import List

from ..models.task_card import (
    Block, TaskCard, TaskCardCreate, TaskCardUpdate, TaskCardRun,
)
from ..models.task_run import TaskRun, TaskRunCreate, TaskRunBlockState
from ..storage.projects import ProjectStorage
from ..storage.task_cards import TaskCardStorage
from ..storage.task_runs import TaskRunStorage
from ..agents.task_executor import TaskExecutorError
from ..agents.block_executor import (
    execute_block, ExecutionContext, BlockExecutionCancelled,
)
from ..agents import task_run_stream_relay as _relay
from ..utils.paths import get_ziya_home, get_project_dir
from ..utils.logging_utils import logger

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/task-cards",
    tags=["task-cards"],
)


def _get_storage(project_id: str) -> TaskCardStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return TaskCardStorage(get_project_dir(project_id))


@router.get("", response_model=List[TaskCard])
async def list_task_cards(
    project_id: str,
    templates_only: bool = Query(False),
):
    """List all task cards in a project, optionally templates only."""
    return _get_storage(project_id).list(templates_only=templates_only)


@router.get("/{card_id}", response_model=TaskCard)
async def get_task_card(project_id: str, card_id: str):
    card = _get_storage(project_id).get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


def _walk_blocks(block):
    """Depth-first walk of a card's block tree (root + nested bodies)."""
    yield block
    for child in (getattr(block, "body", None) or []):
        yield from _walk_blocks(child)


@router.get("/{card_id}/scope-status")
async def get_card_scope_status(project_id: str, card_id: str):
    """Per-block escalation-approval status for a card (ASR F-001).

    For every block whose scope grants a privilege escalation (shell_commands
    or writable paths), report whether a signed approval record matches its
    CURRENT scope hash. Drives the "needs approval" banner in TaskCardEditor.
    Blocks with no escalation (or restriction-only scopes) are omitted — they
    run at the floor and need no approval. The signCommand is the exact
    ``ziya-approve`` invocation that mints the missing record.
    """
    from app.config import scope_canonical as sc
    from app.utils import scope_approvals as sa

    card = _get_storage(project_id).get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")

    blocks = []
    staged_scopes = {}  # "project:card:block" -> {name, scope} for the signer
    for block in _walk_blocks(card.root):
        scope = getattr(block, "scope", None)
        escalation = sc.task_escalation_block(scope)
        if not escalation:
            continue  # no privilege-bearing escalation -> nothing to approve
        try:
            authorized = sa.is_scope_authorized(block.id, scope)
        except Exception as e:  # noqa: BLE001 — status must never 500 the editor
            logger.warning(f"scope-status check failed for block {block.id}: {e}")
            authorized = False
        sign_command = ""
        if not authorized:
            sign_command = (
                f"sudo ziya-approve --task {card_id} "
                f"--block {block.id} --project {project_id}"
            )
            # Stage the DECRYPTED scope so the out-of-process signer (which runs
            # under sudo with no plugin system / KEK and therefore cannot
            # decrypt the card itself) can recompute the identical scope hash.
            # Stage the full scope shape (shell_commands + paths) that
            # task_escalation_block reads, NOT the reduced escalation block, so
            # the signer's hash matches what the runtime gate re-derives. This
            # cannot widen authority: the gate independently re-hashes the real
            # card, so a stale staging just fails the match and clamps to floor.
            staged_scopes[f"{project_id}:{card_id}:{block.id}"] = {
                "name": getattr(block, "name", "") or "",
                "scope": {
                    "shell_commands": list(getattr(scope, "shell_commands", []) or []),
                    "paths": [
                        {"path": getattr(e, "path", None),
                         "write": bool(getattr(e, "write", False))}
                        for e in (getattr(scope, "paths", []) or [])
                    ],
                },
            }
        blocks.append({
            "blockId": block.id,
            "name": getattr(block, "name", "") or "",
            "hasEscalation": True,
            "authorized": bool(authorized),
            "escalation": {k: list(v) for k, v in escalation.items()},
            "signCommand": sign_command,
        })

    # Merge-write the staging file: replace this card's entries (drop stale ones
    # for blocks now approved/changed), preserve other cards' staged scopes.
    try:
        import json as _json
        staging_path = get_ziya_home() / "pending_task_approvals.json"
        try:
            existing = _json.loads(staging_path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}
        prefix = f"{project_id}:{card_id}:"
        existing = {k: v for k, v in existing.items() if not k.startswith(prefix)}
        existing.update(staged_scopes)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.write_text(_json.dumps(existing, indent=2))
    except Exception as e:  # noqa: BLE001 — staging is best-effort; never 500 the editor
        logger.warning(f"Could not stage task scopes for signing: {e}")

    return {
        "cardId": card_id,
        "anyUnapproved": any(not b["authorized"] for b in blocks),
        "blocks": blocks,
    }


@router.post("", response_model=TaskCard, status_code=201)
async def create_task_card(project_id: str, body: TaskCardCreate):
    return _get_storage(project_id).create(body)


@router.put("/{card_id}", response_model=TaskCard)
async def update_task_card(project_id: str, card_id: str, body: TaskCardUpdate):
    card = _get_storage(project_id).update(card_id, body)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


@router.delete("/{card_id}", status_code=204)
async def delete_task_card(project_id: str, card_id: str):
    if not _get_storage(project_id).delete(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")


@router.post("/{card_id}/duplicate", response_model=TaskCard, status_code=201)
async def duplicate_task_card(
    project_id: str, card_id: str,
    as_template: bool = Query(False),
):
    card = _get_storage(project_id).duplicate(card_id, as_template=as_template)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


def _seed_block_states(run_storage: TaskRunStorage, run_id: str, block: Block) -> None:
    """Pre-populate TaskRun.block_states so append_iteration_summary
    has a place to write.  Walks the tree depth-first."""
    if block.id:
        run_storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type, status="queued",
        ))
    for child in block.body or []:
        _seed_block_states(run_storage, run_id, child)

async def _launch_run_for_card(
    project_id: str,
    card_id: str,
    source_conversation_id=None,
    parameter_overrides=None,
) -> TaskRun:
    """Shared helper: validates the card, creates a TaskRun, seeds
    block_states, and schedules the background executor task.
    Returns the run immediately.

    Used by the plain /launch endpoint and by the binding-creation
    endpoint, which needs the run_id before recording the binding.
    """
    storage = _get_storage(project_id)
    card = storage.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")

    run_storage = TaskRunStorage(get_project_dir(project_id))
    run = run_storage.create(TaskRunCreate(
        card_id=card_id,
        source_conversation_id=source_conversation_id,
    ))
    storage.record_run(card_id)
    _seed_block_states(run_storage, run.id, card.root)

    from ..context import get_project_root_or_none, set_project_root
    project_root = get_project_root_or_none()

    # Capture an audit-trail snapshot of effective permissions before
    # the run starts.  Done once at launch so later edits to the card
    # don't rewrite history; this is what lets us reconstruct *what
    # the agent was actually allowed to do* after the fact.
    try:
        from ..utils.permissions_snapshot import build_permissions_snapshot
        snapshot = build_permissions_snapshot(root_block=card.root, project_root=project_root)
        run_storage.set_permissions_snapshot(run.id, snapshot)
    except Exception as e:
        # Non-fatal — missing audit trail shouldn't block task execution.
        logger.warning(f"📋 TASK_LAUNCH: permissions_snapshot capture failed: {e}")

    async def _run(run_id: str, block, project_root):
        logger.info(f"🚀 TASK_RUN: _run coroutine entered for {run_id[:8]}")
        # Defense in depth: re-set the request-scoped ContextVar inside
        # the spawned task.  asyncio.create_task copies the current
        # Context, so this is normally redundant — but if project_root
        # was passed via a path other than the X-Project-Root header
        # (or if the var was cleared), tool calls fired from inside
        # the task would otherwise fall through to ``os.getcwd()``,
        # which is wherever the server happened to be launched from.
        if project_root:
            set_project_root(project_root)
        # Lifecycle event emitter — kept local so the run_id/project_id
        # are captured in closure and callers don't have to thread them.
        async def _emit_run(status: str, **extra):
            await _relay.safe_push(run_id, {
                "type": "run_completed" if status != "started" else "run_started",
                "run_id": run_id,
                "status": status,
                "at": time.time(),
                **extra,
            })
        # Mark this run as actively executing in this process so the
        # cancel endpoint can distinguish "live executor — soft-cancel"
        # from "zombie from a prior server lifetime — force-cancel".
        # The startup reconciler handles zombies left behind by a hard
        # crash that bypassed the finally block below.
        run_storage.mark_active(run_id)
        try:
            logger.info(f"🚀 TASK_RUN: {run_id[:8]} → marking running")
            run_storage.update_status(run_id, "running")
            await _emit_run("started")
            ctx = ExecutionContext(
                run_id=run_id,
                project_root=project_root,
                project_id=project_id,
                storage=run_storage,
                overrides=dict(parameter_overrides or {}),
            )
            logger.info(f"🚀 TASK_RUN: {run_id[:8]} → execute_block start (type={block.block_type})")
            artifact = await execute_block(block, ctx)
            logger.info(
                f"🚀 TASK_RUN: {run_id[:8]} → execute_block returned "
                f"(summary_len={len(artifact.summary)}, failed={artifact.failed})"
            )
            run_storage.set_artifact(run_id, artifact)
            final_status = "failed" if artifact.failed else "done"
            run_storage.update_status(run_id, final_status)
            await _emit_run(final_status)
            logger.info(f"✅ Task run complete: {run_id[:8]}")
        except BlockExecutionCancelled:
            run_storage.update_status(run_id, "cancelled")
            await _emit_run("cancelled")
            logger.info(f"🛑 Task run cancelled: {run_id[:8]}")
        except TaskExecutorError as e:
            run_storage.update_status(run_id, "failed", error=str(e))
            await _emit_run("failed", error=str(e))
            logger.warning(f"❌ Task run failed: {run_id[:8]}: {e}")
        except Exception as e:  # Broad: background task must not bubble
            run_storage.update_status(run_id, "failed", error=str(e))
            await _emit_run("failed", error=str(e))
            logger.error(f"❌ Task run crashed: {run_id[:8]}: {e}", exc_info=True)
        finally:
            # Always drop from the active-runs set, even on error.
            run_storage.mark_inactive(run_id)

    asyncio.create_task(_run(run.id, card.root, project_root))
    logger.info(f"🚀 Task card launched: {card.name} → run {run.id[:8]} (task scheduled)")
    return run


@router.post("/{card_id}/launch")
async def launch_task_card(
    project_id: str, card_id: str, body: TaskCardRun,
) -> TaskRun:
    """Launch a task card — create a TaskRun and start executing in
    the background.  Returns the run immediately; clients poll the
    task-runs endpoints for status and the final artifact.
    """
    return await _launch_run_for_card(
        project_id=project_id, card_id=card_id,
        source_conversation_id=body.source_conversation_id,
        parameter_overrides=body.parameter_overrides,
    )


@router.get("/{card_id}/schedule-state")
async def get_schedule_state(project_id: str, card_id: str) -> dict:
    """Return the scheduler's per-card fire-history record.

    Empty dict if the card has no schedule block, or has one but has
    never fired yet.  When populated:

        {
          "block_id":      "<schedule block id>",
          "next_fire_at":  <epoch ms or null>,
          "last_fire_at":  <epoch ms or null>,
          "fires_so_far":  <int>,
          "run_ids":       ["<run_id>", ...]   # most-recent first, capped
        }

    Drives the "next fire in 2h 14m / fired 47 times so far" surface
    in the schedule editor.  Read-only; the scheduler owns writes via
    its internal `_write_state` path.
    """
    storage = _get_storage(project_id)
    if not storage.get(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")
    # Lazy-import the scheduler so this endpoint stays cheap when the
    # caller is just listing cards (and to avoid pulling croniter at
    # module load).
    from ..agents.task_scheduler import _read_state
    state = _read_state(project_id)
    return state.get(card_id) or {}
