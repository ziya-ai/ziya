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

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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

# ── real launch harness ──────────────────────────────────────────────
#
# The classes below drive the ACTUAL launch endpoint rather than a copy of
# its logic.  An earlier version of this file transcribed the
# ``except TaskExecutorError`` branch from app/api/task_cards.py::_run and
# asserted against the transcription, on the reasoning that standing up a
# project + card + spawned coroutine was heavy scaffolding around six
# lines of dispatch.
#
# That reasoning was wrong, and the same pattern proved it wrong elsewhere
# in this suite: test_card_scope_status_endpoint.py had a local copy of an
# endpoint body, the endpoint was corrected, and the stale copy produced
# three FALSE failures against a correct fix — while it would have kept
# passing against a broken one.  A test that re-implements its subject
# verifies only its own copy.
#
# Everything here is real except the model stream: the FastAPI router,
# _launch_run_for_card, the spawned _run coroutine, execute_block,
# execute_task_block, and TaskRunStorage reading and writing real files.


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-hold"
    proj = ziya_home / "projects" / project_id
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": "Test",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return project_id


@pytest.fixture
def launch_client(ziya_home, project_dir):
    """Real task-cards router over a temp ZIYA_HOME.

    Mirrors the fixture shape in test_api_task_cards.py so the two files
    stand up the launch path the same way.
    """
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}), \
         patch("app.api.task_cards.get_ziya_home", return_value=ziya_home), \
         patch("app.api.task_cards.get_project_dir",
               return_value=ziya_home / "projects" / project_dir):
        from app.api.task_cards import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), project_dir


def _wait_until_settled(project_id, run_id, timeout=5.0):
    """Poll on-disk run state until the executor is done with the run.

    Tests the COMPLEMENT of the live set (``queued``/``running``) rather
    than enumerating terminal statuses.  A hardcoded terminal list is
    precisely what went stale when ``partial`` and ``held`` were added —
    four guards in app/api/task_runs.py still list only
    ``done/failed/cancelled`` — so this helper refuses to keep its own
    copy of that set.
    """
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_project_dir
    storage = TaskRunStorage(get_project_dir(project_id))
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = storage.get(run_id)
        if run and run.status not in ("queued", "running"):
            return run
        time.sleep(0.02)
    return storage.get(run_id)


def _model_layer_patches(executor_cls):
    """Patches that keep the run off the network, as a reusable tuple."""
    return (
        patch("app.streaming_tool_executor.StreamingToolExecutor",
              executor_cls),
        patch("app.agents.models.ModelManager.get_state",
              return_value={"aws_region": "us-east-1", "aws_profile": "x",
                            "current_model": "fake",
                            "endpoint": "fake-not-bedrock"}),
        patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
              return_value=[]),
    )


def _launch(tc, pid, executor_cls, card_name="T"):
    """Create a card, launch it, and wait for the run to settle.

    Returns ``(run, card)``.  The model-layer patches stay active across
    the poll because the run executes in a background task spawned by the
    endpoint — releasing them at the end of the POST would let the real
    executor be imported mid-run.
    """
    card = tc.post(
        f"/api/v1/projects/{pid}/task-cards",
        json={"name": card_name, "root": {
            "block_type": "task", "name": "Stage 1",
            "instructions": "do it",
        }},
    ).json()
    p1, p2, p3 = _model_layer_patches(executor_cls)
    with p1, p2, p3:
        resp = tc.post(
            f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
            json={},
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["id"]
        run = _wait_until_settled(pid, run_id)
    assert run is not None, "run record disappeared"
    return run, card


class TestRunLoopRoutingEndToEnd:
    """The join: a terminal infra chunk from the stream must come out the
    far end as a ``held`` run carrying its resume coordinates.

    This is the assertion the ten failed campaign runs could not satisfy.
    Each of them stopped on expired credentials, a lost endpoint, or
    exhausted throttling retries, and each was recorded as ``failed`` with
    the loop position discarded — so the next attempt re-paid every stage
    it had already completed.
    """

    def test_infra_fault_yields_a_held_run(self, launch_client):
        tc, pid = launch_client
        _FaultExecutor.chunk_type = "connection_error"
        _FaultExecutor.detail_key = "detail"
        _FaultExecutor.detail = 'Could not connect to the endpoint URL'
        run, card = _launch(tc, pid, _FaultExecutor)
        assert run.status == "held", (
            f"infra fault produced {run.status!r}; a run stopped by "
            f"infrastructure must be held, not failed"
        )
        assert run.held_reason == "connection_error"
        assert "Could not connect" in (run.error or "")

    def test_held_run_records_where_to_resume(self, launch_client):
        # Without a block id the operator has to infer the resume point by
        # eye from the run map, which for a multi-stage card with a loop is
        # the friction that made restarting from scratch feel easier.
        tc, pid = launch_client
        _FaultExecutor.chunk_type = "authentication_error"
        _FaultExecutor.detail_key = "detail"
        _FaultExecutor.detail = "creds expired, run mwinit"
        run, card = _launch(tc, pid, _FaultExecutor)
        assert run.status == "held"
        assert run.held_at_block_id, "held run names no resume target"
        # The id must be one the run's own block_states are keyed by —
        # a resume target the run map cannot locate is no better than none.
        assert run.held_at_block_id in run.block_states, (
            f"held_at_block_id {run.held_at_block_id!r} is not a block of "
            f"this run: {sorted(run.block_states)}"
        )
        assert run.held_at_block_id == card["root"]["id"]

    @pytest.mark.parametrize("kind", [
        "transient_service_error", "throttling_error",
        "connection_error", "authentication_error",
    ])
    def test_every_infra_kind_holds(self, kind, launch_client):
        tc, pid = launch_client
        _FaultExecutor.chunk_type = kind
        _FaultExecutor.detail_key = "detail"
        _FaultExecutor.detail = f"simulated {kind}"
        run, _ = _launch(tc, pid, _FaultExecutor)
        assert run.status == "held"
        assert run.held_reason == kind

    def test_clean_run_still_completes(self, launch_client):
        # The hold path must be fault-specific.  A preflight or a guard
        # that held healthy runs would be worse than not checking at all —
        # which is exactly what the launch preflight did before it gained
        # an opt-out.
        tc, pid = launch_client
        run, _ = _launch(tc, pid, _CleanExecutor)
        assert run.status == "done", f"clean run reported {run.status!r}"
        assert run.held_reason is None
        assert run.held_at_block_id is None

    def test_held_is_not_reclassified_as_partial(self, launch_client):
        # 'partial' answers "how much got done"; 'held' answers "why it
        # stopped".  Collapsing the two loses the only actionable half —
        # that the infrastructure needs attention, not the card.
        tc, pid = launch_client
        _FaultExecutor.chunk_type = "throttling_error"
        _FaultExecutor.detail_key = "detail"
        _FaultExecutor.detail = "rate limited"
        run, _ = _launch(tc, pid, _FaultExecutor)
        assert run.status == "held"
        assert run.status != "partial"

    def test_held_run_is_terminal_on_disk(self, launch_client):
        # completed_at drives the tile's runtime display and stops
        # record_activity from letting heartbeats through, so a held run
        # that never stamps it looks alive forever.
        tc, pid = launch_client
        _FaultExecutor.chunk_type = "connection_error"
        _FaultExecutor.detail_key = "detail"
        _FaultExecutor.detail = "gone"
        run, _ = _launch(tc, pid, _FaultExecutor)
        assert run.completed_at is not None
        assert run.status == "held"


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
