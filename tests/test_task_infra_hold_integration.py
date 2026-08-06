"""
End-to-end fault injection for the infra-hold path (E1).

``test_task_infra_hold.py`` covers the pieces in isolation — the error
type, ``mark_held`` persistence, and that ``classify_terminal_status``
leaves ``held`` alone.  What it does NOT cover is the join: that a
terminal infra chunk arriving from the model stream actually traverses
task_executor → the run loop's ``except TaskExecutorError`` → a held run
that the resume endpoint will accept.

That join is where the original bug lived.  Ten consecutive runs of one
campaign card were recorded as ``failed`` — every one of them stopped by
expired credentials, a lost Bedrock endpoint, or exhausted throttling
retries — and each retry re-paid the earlier stages because the position
had been discarded.  Every link individually looked fine; the chain was
what was broken.

The stream is faked (no network, no credentials) by yielding the exact
chunk shapes ``_classify_and_handle_error`` emits on its non-retryable
path: ctype in {transient_service_error, throttling_error,
connection_error, authentication_error} with the message in
``detail``/``retry_message`` rather than ``content``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block, TaskScope
from app.models.task_run import TaskRunCreate


# ── fault-injecting fake executor ────────────────────────────────────

class _FaultExecutor:
    """Yields some real output, then a terminal infra chunk.

    Text first, deliberately: a fault that lands mid-task is the realistic
    case (the campaign runs died hours in, not at t=0), and it proves the
    hold path does not depend on the task having produced nothing.
    """
    chunk_type = "connection_error"
    detail_key = "detail"
    detail = 'Could not connect to the endpoint URL: "https://bedrock-runtime..."'

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        yield {"type": "text", "content": "partial work before the fault"}
        yield {"type": type(self).chunk_type,
               type(self).detail_key: type(self).detail}


class _CleanExecutor:
    """Baseline: finishes normally with a self-assessment so the artifact
    is not failed.  Used to prove the hold path is fault-specific rather
    than firing on every run."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        yield {"type": "text", "content": "did the work"}
        yield {"type": "stream_end"}


@pytest.fixture
def fault_env():
    """Patch the model layer so nothing touches a network or credentials."""
    def _apply(executor_cls):
        return patch.multiple(
            "app.agents.models.ModelManager",
            get_state=lambda: {"aws_region": "us-east-1",
                               "aws_profile": "x",
                               "current_model": "fake",
                               "endpoint": "fake-not-bedrock"},
        ), patch("app.streaming_tool_executor.StreamingToolExecutor", executor_cls), \
           patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[])
    return _apply


def _task(instructions: str = "do it") -> Block:
    return Block(block_type="task", id="b-task-1", name="Stage 1",
                 instructions=instructions)


# ── task_executor raises the right exception for each infra ctype ─────

class TestInfraChunkRaisesInfraError:
    """All four terminal infra ctypes must produce TaskInfraError with the
    kind preserved — the run loop branches on that attribute, so a kind
    that arrives empty silently degrades the run back to ``failed``."""

    @pytest.mark.parametrize("ctype", [
        "transient_service_error", "throttling_error",
        "connection_error", "authentication_error",
    ])
    @pytest.mark.asyncio
    async def test_each_infra_ctype(self, ctype, tmp_path):
        _FaultExecutor.chunk_type = ctype
        _FaultExecutor.detail_key = "detail"
        with patch("app.streaming_tool_executor.StreamingToolExecutor",
                   _FaultExecutor), \
             patch("app.agents.models.ModelManager.get_state",
                   return_value={"aws_region": "us-east-1",
                                 "aws_profile": "x", "current_model": "f"}), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=[]):
            with pytest.raises(task_executor.TaskExecutorError) as exc:
                await task_executor.execute_task_block(
                    _task(), project_root=str(tmp_path),
                )
        assert getattr(exc.value, "infra_kind", "") == ctype
        assert getattr(exc.value, "block_id", "") == "b-task-1"

    @pytest.mark.parametrize("key", ["detail", "retry_message", "error"])
    @pytest.mark.asyncio
    async def test_message_read_from_each_carrier_key(self, key, tmp_path):
        # These chunks put their message in detail/retry_message/error,
        # never in 'content' — reading the wrong key would surface a held
        # run with an empty reason, which is what the ORIGINAL bug looked
        # like (run.error was "").
        _FaultExecutor.chunk_type = "authentication_error"
        _FaultExecutor.detail_key = key
        _FaultExecutor.detail = "creds expired, run mwinit"
        with patch("app.streaming_tool_executor.StreamingToolExecutor",
                   _FaultExecutor), \
             patch("app.agents.models.ModelManager.get_state",
                   return_value={"aws_region": "us-east-1",
                                 "aws_profile": "x", "current_model": "f"}), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=[]):
            with pytest.raises(task_executor.TaskExecutorError) as exc:
                await task_executor.execute_task_block(
                    _task(), project_root=str(tmp_path),
                )
        assert "creds expired" in str(exc.value)

    @pytest.mark.asyncio
    async def test_plain_error_chunk_is_NOT_an_infra_hold(self, tmp_path):
        # ctype 'error' is a work-related failure and must keep routing to
        # ``failed``.  If this ever gained an infra_kind, every ordinary
        # task failure would masquerade as an infrastructure outage.
        class _PlainError(_FaultExecutor):
            async def stream_with_tools(self, messages, tools=None,
                                        project_root=None, **_):
                yield {"type": "error", "content": "the card is wrong"}

        with patch("app.streaming_tool_executor.StreamingToolExecutor",
                   _PlainError), \
             patch("app.agents.models.ModelManager.get_state",
                   return_value={"aws_region": "us-east-1",
                                 "aws_profile": "x", "current_model": "f"}), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=[]):
            with pytest.raises(task_executor.TaskExecutorError) as exc:
                await task_executor.execute_task_block(
                    _task(), project_root=str(tmp_path),
                )
        assert getattr(exc.value, "infra_kind", "") == ""


# ── the run loop's branch: held vs failed ────────────────────────────

class TestRunLoopRouting:
    """Reproduces the ``except TaskExecutorError`` branch in
    app/api/task_cards.py::_run without standing up the HTTP layer, so the
    routing decision is asserted directly on a real storage object."""

    @pytest.fixture()
    def storage(self, tmp_path):
        from app.storage.task_runs import TaskRunStorage
        return TaskRunStorage(tmp_path)

    def _route(self, storage, run_id, exc):
        """The production branch, transcribed."""
        kind = getattr(exc, "infra_kind", "")
        if kind:
            blk = getattr(exc, "block_id", "") or ""
            storage.mark_held(run_id, reason=kind, block_id=blk,
                              error=str(exc))
        else:
            from app.utils.run_outcome import classify_terminal_status
            fresh = storage.get(run_id)
            st = classify_terminal_status(
                "failed", fresh.block_states if fresh else None)
            storage.update_status(run_id, st, error=str(exc))

    def test_infra_error_produces_a_held_run(self, storage):
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        exc = task_executor.TaskInfraError(
            "Task execution failed (connection_error): endpoint gone",
            infra_kind="connection_error", block_id="b-loop",
        )
        self._route(storage, run.id, exc)
        got = storage.get(run.id)
        assert got.status == "held"
        assert got.held_reason == "connection_error"
        assert got.held_at_block_id == "b-loop"
        assert "endpoint gone" in got.error

    def test_plain_error_still_produces_failed(self, storage):
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        self._route(storage, run.id,
                    task_executor.TaskExecutorError("bad instructions"))
        got = storage.get(run.id)
        assert got.status == "failed"
        assert got.held_reason is None

    def test_held_is_not_downgraded_to_partial(self, storage):
        # The regression this guards: 'partial' answers "how much got
        # done", 'held' answers "why it stopped".  Running the
        # reclassification pass over a held run would erase the only
        # actionable half — that the INFRASTRUCTURE needs attention.
        from app.models.task_run import TaskRunBlockState
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id="a", block_type="task", status="done"))
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id="b", block_type="task", status="queued"))
        exc = task_executor.TaskInfraError(
            "x", infra_kind="throttling_error", block_id="b")
        self._route(storage, run.id, exc)
        assert storage.get(run.id).status == "held"


# ── the payoff: a held run is resumable ──────────────────────────────

class TestHeldRunIsResumable:
    """The whole point of E1.  A ``failed`` run and a ``held`` run are
    equally terminal, so the distinction only earns its keep if the held
    one can actually be continued from where it stopped."""

    @pytest.fixture()
    def storage(self, tmp_path):
        from app.storage.task_runs import TaskRunStorage
        return TaskRunStorage(tmp_path)

    def test_resume_gate_admits_held(self, storage):
        # The endpoint 409s on ("running", "paused") only.  'held' must
        # NOT be in that set or the run is stranded — which is precisely
        # what happened to the ten campaign runs recorded as failed.
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.mark_held(run.id, reason="authentication_error",
                          block_id="b-3")
        got = storage.get(run.id)
        assert got.status not in ("running", "paused"), (
            "held must pass the resume-from-block 409 gate"
        )

    def test_held_at_block_id_names_the_resume_target(self, storage):
        # Without this the user has to infer the resume point by eye from
        # the run map, which for a 10-block card with a loop is exactly
        # the friction that made re-running from scratch feel easier.
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.mark_held(run.id, reason="connection_error",
                          block_id="b-bf007c11")
        assert storage.get(run.id).held_at_block_id == "b-bf007c11"

    def test_completed_at_is_stamped(self, storage):
        # 'held' is terminal for the run OBJECT (the coroutine unwound),
        # so the tile needs a runtime and record_activity must stop
        # letting heartbeats through.
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        storage.mark_held(run.id, reason="throttling_error")
        assert storage.get(run.id).completed_at is not None

    def test_heartbeats_stop_on_a_held_run(self, storage):
        # record_activity guards on status in ("queued", "running"); a
        # held run that kept accepting heartbeats would look alive in the
        # tile forever.
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        storage.mark_held(run.id, reason="connection_error")
        assert storage.record_activity(run.id, note="still going?") is None


# ── clean runs are unaffected ────────────────────────────────────────

class TestNoFalsePositives:
    @pytest.mark.asyncio
    async def test_clean_stream_does_not_raise(self, tmp_path):
        with patch("app.streaming_tool_executor.StreamingToolExecutor",
                   _CleanExecutor), \
             patch("app.agents.models.ModelManager.get_state",
                   return_value={"aws_region": "us-east-1",
                                 "aws_profile": "x", "current_model": "f"}), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
                   return_value=[]):
            artifact = await task_executor.execute_task_block(
                _task(), project_root=str(tmp_path),
            )
        assert "did the work" in artifact.summary
        assert getattr(artifact, "failed", False) is False
