"""
The run driver: one coroutine that takes a created TaskRun from "queued"
to its terminal status, shared by every path that starts a run.

Before this module the interactive launch endpoint (app/api/task_cards.py)
and the scheduler (task_scheduler._fire_one) each had their own copy of
the execute-and-record loop, and the scheduler's was a ~40-line subset of
the endpoint's ~260.  A scheduled run therefore never took the executor
lock (so Cancel in the UI wrote ``cancelled`` over a run whose executor
kept going, and a sibling server's startup reconcile marked it ``held``
while it was live), never emitted ``run_started`` / ``run_completed`` on
the relay, never recorded a ``TaskInfraError`` as ``held`` (the headless
unsigned-escalation hold the executor raises on purpose came back as
``failed``), never got the credentials preflight, and never had its
managed jobs cleaned up.  Both callers now build a :class:`RunLaunch` and
call :func:`start_run`.

``execute`` is injected rather than imported here so each caller's test
seam keeps working: the API tests patch ``app.api.task_cards.execute_block``
and the scheduler tests patch ``app.agents.block_executor.execute_block``.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from ..utils.logging_utils import logger
from . import live_runs as _live_runs
from . import task_run_stream_relay as _relay

Executor = Callable[[Any, Any], Awaitable[Any]]


@dataclass
class RunLaunch:
    """Everything the driver needs to run one created TaskRun."""
    run_id: str
    root: Any                      # Block: the tree this run executes
    project_id: str
    project_root: Optional[str]
    card: Any                      # TaskCard (for scope, name, harvest)
    run_storage: Any               # TaskRunStorage
    deck_scope: Any = None
    # Passed to the memory harvest; the project's recorded path when known.
    project_path: Optional[str] = None
    overrides: Dict[str, Any] = field(default_factory=dict)
    # Extra ExecutionContext kwargs: the resume_* family from the
    # resume-from-block endpoint, ``scope_stack`` from the scheduler.
    context_kwargs: Dict[str, Any] = field(default_factory=dict)
    # The block executor.  None -> app.agents.block_executor.execute_block,
    # resolved at call time so a patched module attribute is honoured.
    execute: Optional[Executor] = None
    # Short label for log lines ("Task run" / "Scheduled run").
    label: str = "Task run"


def _default_execute(block: Any, ctx: Any) -> Awaitable[Any]:
    from .block_executor import execute_block
    return execute_block(block, ctx)


async def _preflight(launch: RunLaunch) -> bool:
    """Credentials preflight.  Returns False when the run was held here
    and must not start.

    Two of ten runs of one long campaign card died at 0.0 minutes because
    they were relaunched straight into a dead endpoint, producing a run
    record whose only content was the error.  A cheap check first turns
    that into an explicit held run -- a HELD run rather than a refusal,
    because a refusal leaves no record the user could see or resume,
    whereas 'held' is exactly "the work never started because the
    infrastructure was down".  For a scheduled fire at 03:00 this is the
    difference between a nightly that reads "failed" and one that reads
    "held: credentials expired".

    Bedrock-only, opt-out-able (ZIYA_SKIP_LAUNCH_PREFLIGHT=1: a stubbed
    executor under test, a card doing only shell/file work, an offline
    environment) and best-effort: a failure of the check ITSELF proceeds
    to launch -- "cannot verify" is not "invalid".
    """
    run_id = launch.run_id
    try:
        skip = os.environ.get(
            "ZIYA_SKIP_LAUNCH_PREFLIGHT", "",
        ).strip().lower() in ("1", "true", "yes")
        if skip:
            logger.debug("Launch preflight skipped (ZIYA_SKIP_LAUNCH_PREFLIGHT)")
            return True
        from .models import ModelManager
        endpoint = (ModelManager.get_state() or {}).get("endpoint", "bedrock")
        if endpoint != "bedrock":
            return True
        from ..utils.aws_utils import check_aws_credentials
        # STS round trip (~10s cold) plus botocore client setup: never on
        # the event loop.
        ok, msg = await asyncio.to_thread(check_aws_credentials, is_server_startup=False)
        if ok:
            return True
        launch.run_storage.mark_held(
            run_id, reason="authentication_error",
            block_id=getattr(launch.root, "id", "") or "",
            error=(f"Launch preflight failed — the run did not start. "
                   f"{msg or 'AWS credentials are not valid.'}"),
        )
        await _relay.safe_push(run_id, {
            "type": "run_completed", "run_id": run_id,
            "status": "held", "at": time.time(),
            "error": msg or "credentials invalid",
        })
        logger.warning(f"⏸️ {launch.label} held before start (preflight): {run_id[:8]}")
        return False
    except Exception as e:  # noqa: BLE001 — never block a launch on the check
        logger.debug("Launch preflight skipped: %s", e)
        return True


async def drive_run(launch: RunLaunch) -> None:
    """Execute ``launch.root`` for ``launch.run_id`` and record the outcome."""
    run_id = launch.run_id
    run_storage = launch.run_storage
    card = launch.card
    label = launch.label
    logger.info(f"🚀 TASK_RUN: _run coroutine entered for {run_id[:8]}")

    if not await _preflight(launch):
        return

    # Defense in depth: re-set the request-scoped ContextVar inside the
    # spawned task.  asyncio.create_task copies the current Context, so
    # this is normally redundant for the launch endpoint -- but a
    # scheduler fire has no request at all, and code that reads
    # app.context.get_project_root() directly (artifact paths, tool-batch
    # cwd, MCP workspace routing) would otherwise fall through to the
    # server process's cwd.
    if launch.project_root:
        from ..context import set_project_root
        set_project_root(launch.project_root)

    async def _emit_run(status: str, **extra: Any) -> None:
        await _relay.safe_push(run_id, {
            "type": "run_completed" if status != "started" else "run_started",
            "run_id": run_id,
            "status": status,
            "at": time.time(),
            **extra,
        })

    def _terminal(base: str) -> str:
        """Reclassify a terminal status against what completed.  Reads
        block_states back from disk rather than tracking progress here:
        the executor already persists every block's outcome."""
        from ..utils.run_outcome import classify_terminal_status
        fresh = run_storage.get(run_id)
        return classify_terminal_status(base, fresh.block_states if fresh else None)

    # Mark this run as actively executing in this process (and take its
    # cross-process executor lock) so the cancel endpoint can tell "live
    # executor -- soft-cancel" from "zombie from a prior server lifetime".
    run_storage.mark_active(run_id)
    # Journal every relay event to disk so a sibling server (one that did
    # not start this run) can tail it and show the same live detail.
    try:
        _relay.open_journal(run_id, run_storage.journal_path(run_id))
    except Exception as e:  # noqa: BLE001 — observation is optional
        logger.debug("Could not open run journal: %s", e)

    from .block_executor import BlockExecutionCancelled, ExecutionContext
    from .task_executor import TaskExecutorError
    execute = launch.execute or _default_execute
    ctx: Optional[ExecutionContext] = None
    try:
        logger.info(f"🚀 TASK_RUN: {run_id[:8]} → marking running")
        run_storage.update_status(run_id, "running")
        await _emit_run("started")
        ctx = ExecutionContext(
            run_id=run_id,
            project_root=launch.project_root,
            project_id=launch.project_id,
            storage=run_storage,
            overrides=dict(launch.overrides or {}),
            deck_scope=launch.deck_scope,
            card_scope=getattr(card, "scope", None),
            **launch.context_kwargs,
        )
        logger.info(
            f"🚀 TASK_RUN: {run_id[:8]} → execute_block start "
            f"(type={getattr(launch.root, 'block_type', '?')})"
        )
        artifact = await execute(launch.root, ctx)
        logger.info(
            f"🚀 TASK_RUN: {run_id[:8]} → execute_block returned "
            f"(summary_len={len(artifact.summary)}, failed={artifact.failed})"
        )
        run_storage.set_artifact(run_id, artifact)
        final_status = _terminal("failed" if artifact.failed else "done")
        run_storage.update_status(run_id, final_status)
        await _emit_run(final_status)
        # Feed what the run established into probationary memory
        # (run_memory_harvest.py).  Fire-and-forget; it skips anything
        # other than done / partial itself.
        from .run_memory_harvest import schedule_harvest
        schedule_harvest(
            run_id=run_id, card=card, artifact=artifact, status=final_status,
            project_path=launch.project_path or launch.project_root,
        )
        logger.info(f"✅ {label} complete: {run_id[:8]}")
    except BlockExecutionCancelled:
        # A user-stopped run that got partway carries the same workspace
        # hazard as a crash-partway.
        st = _terminal("cancelled")
        run_storage.update_status(run_id, st)
        await _emit_run(st)
        logger.info(f"🛑 {label} cancelled: {run_id[:8]}")
    except TaskExecutorError as e:
        # An infrastructure fault is not a verdict on the work: the card
        # never reached a decision, so recording it as "failed" both
        # misdescribes it and throws away the position the run had
        # reached.  Detected by attribute rather than by importing
        # TaskInfraError.
        kind = getattr(e, "infra_kind", "")
        if kind:
            blk = getattr(e, "block_id", "") or ""
            faults = None
            gate = None
            try:
                if ctx is not None:
                    faults = ctx.infra_summary()
                    gate = ctx.infra_gated_reason
            except Exception:  # noqa: BLE001 — surfacing must not mask the hold
                pass
            run_storage.mark_held(
                run_id, reason=kind, block_id=blk, error=str(e),
                faults=faults, gate_reason=gate,
            )
            await _emit_run("held", error=str(e))
            n = (faults or {}).get("fault_count", 1)
            logger.warning(
                f"⏸️ {label} held ({kind}): {run_id[:8]} at block "
                f"{blk[:14] or '?'} — {n} fault(s)"
                + (f", gate: {gate}" if gate else "") + f" — {e}"
            )
        else:
            st = _terminal("failed")
            run_storage.update_status(run_id, st, error=str(e))
            await _emit_run(st, error=str(e))
            logger.warning(f"❌ {label} failed: {run_id[:8]}: {e}")
    except asyncio.CancelledError:
        # Two things cancel this coroutine and they must be told apart:
        # a force-stop (live_runs.abort) and the event loop shutting down.
        # Only the first is a verdict on the run; the second is the
        # startup reconciler's job.
        if _live_runs.consume_abort(run_id):
            aborted = run_storage.mark_aborted(run_id)
            if aborted is not None and aborted.status == "held":
                await _emit_run("held", error=aborted.error or "force-stopped")
            logger.info(f"🛑 {label} force-stopped: {run_id[:8]}")
        raise
    except Exception as e:  # noqa: BLE001 — a background task must not bubble
        st = _terminal("failed")
        run_storage.update_status(run_id, st, error=str(e))
        await _emit_run(st, error=str(e))
        logger.error(f"❌ {label} crashed: {run_id[:8]}: {e}", exc_info=True)
    finally:
        # Managed jobs owned by this run (design/job-runner.md, Ownership):
        # killed when the run ends or is cancelled.  A HELD run keeps them
        # running; on resume job_status shows them.
        try:
            final = run_storage.get(run_id)
            if final is None or final.status != "held":
                from app.jobs.manager import get_job_manager
                killed = await asyncio.to_thread(
                    get_job_manager().kill_owned, {"run_id": run_id}, "run_end")
                if killed:
                    logger.info(f"🧵 run {run_id[:8]} ended; killed {len(killed)} job(s)")
        except Exception as je:  # noqa: BLE001 -- cleanup never masks the run's verdict
            logger.warning(f"job cleanup at run end failed: {je}")
        run_storage.mark_inactive(run_id)
        _relay.close_journal(run_id)
        _live_runs.unregister(run_id)


def start_run(launch: RunLaunch) -> "asyncio.Task[None]":
    """Schedule :func:`drive_run` in the background and keep the handle in
    live_runs -- the only in-process lever that can interrupt a run stuck
    inside a block (see app/agents/live_runs.py)."""
    task = asyncio.create_task(drive_run(launch), name=f"ziya-run-{launch.run_id[:8]}")
    _live_runs.register(launch.run_id, task)
    return task
