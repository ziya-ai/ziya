"""Backstops: no entry point may lose a ServiceInfraError silently.

tests/test_infra_error_boundaries.py pins each call site that converts the
outage into its own documented result.  This file pins the layer under
them: the outermost handler of every kind of entry point, so a call site
added later without its own handling still ends somewhere that reports.

  entry point              | backstop                         | without it
  -------------------------|----------------------------------|---------------------------
  HTTP route               | InfraErrorMiddleware -> 503      | 500, empty body, cause lost
  builtin tool call        | execute_single_tool handler      | chat turn dies mid tool call
  DirectMCPTool._arun      | its own handler                  | same, for LangChain callers
  task run                 | drive_run -> held                | run "running" forever
  task block               | execute_block -> block held      | block "running" forever
  delegate                 | _run_delegate -> failed          | delegate "running" forever
  /memory/organize task    | _run -> status cleared           | "already_running" forever

Measured before the fixes (2026-11-16): the HTTP case returned status 500
with an empty body and logged only "No response returned".
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.models.task_card import Artifact, Block
from app.utils.infra_errors import ServiceInfraError
from app.utils.infra_error_middleware import (
    InfraErrorMiddleware, find_infra_error, infra_error_payload)


class CredentialRetrievalError(Exception):
    pass


def _outage(where: str = "embed_text") -> ServiceInfraError:
    return ServiceInfraError(
        CredentialRetrievalError(
            "Error when retrieving credentials from custom-process: expired"),
        where=where)


# ── HTTP ──────────────────────────────────────────────────────────────────

def _mini_app(order: str):
    """A FastAPI app with one BaseHTTPMiddleware and the infra middleware,
    in either order.  add_middleware puts the LAST added outermost."""
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI()
    hits = []

    class _Outer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await call_next(request)

    @app.get("/boom")
    def boom():
        hits.append("boom")
        raise _outage()

    @app.get("/plain")
    def plain():
        raise ValueError("ordinary bug")

    @app.get("/ok")
    def ok():
        return {"ok": True}

    if order == "innermost":
        app.add_middleware(InfraErrorMiddleware)
        app.add_middleware(_Outer)
    else:
        app.add_middleware(_Outer)
        app.add_middleware(InfraErrorMiddleware)
    return app, hits


@pytest.mark.parametrize("order", ["innermost", "outermost"])
def test_middleware_turns_outage_into_503(order):
    from fastapi.testclient import TestClient
    app, hits = _mini_app(order)
    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert hits == ["boom"], "the route did not run"
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error_type"] == "authentication_error"
    assert body["where"] == "embed_text"
    assert body["detail"]
    assert r.headers.get("retry-after")


def test_middleware_leaves_other_responses_alone():
    from fastapi.testclient import TestClient
    app, _ = _mini_app("innermost")
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/ok").json() == {"ok": True}
    assert c.get("/plain").status_code == 500


def test_find_infra_error_unwraps_exception_groups():
    err = _outage()
    grp = BaseExceptionGroup("tg", [BaseExceptionGroup("inner", [err])])
    assert find_infra_error(grp) is err
    assert find_infra_error(ValueError("x")) is None
    assert infra_error_payload(err)["error_type"] == "authentication_error"


def test_started_response_is_closed_not_answered_twice():
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise _outage()

    async def send(msg):
        sent.append(msg)

    asyncio.run(InfraErrorMiddleware(app)(
        {"type": "http", "path": "/s", "method": "GET"}, None, send))
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 200
    assert sent[1]["more_body"] is False


def test_real_app_memory_route_answers_503():
    """The seam: the middleware is registered on the real app, and a memory
    route that hits the outage answers with the cause."""
    from fastapi.testclient import TestClient
    from app.server import app
    ran = []

    def boom(self, *a, **k):
        ran.append(1)
        raise _outage()

    with patch("app.storage.memory.MemoryStorage.search", boom):
        r = TestClient(app, raise_server_exceptions=False).get(
            "/api/v1/memory/search", params={"q": "anything"})
    assert ran, "the route never reached the store"
    assert r.status_code == 503, f"{r.status_code} {r.text[:200]}"
    assert r.json()["error_type"] == "authentication_error"


def test_organize_task_does_not_stay_running():
    import app.api.memory as mem_api

    async def go():
        with patch("app.memory.reorganize", AsyncMock(side_effect=_outage("call_service_model")),
                   create=True) as call:
            out = await mem_api.organize_memories()
            for _ in range(50):
                if not mem_api._organize_task_status.get("running"):
                    break
                await asyncio.sleep(0.01)
            return out, call.await_count

    try:
        out, calls = asyncio.run(go())
        assert out == {"status": "started"}
        assert calls == 1, "reorganize was not called"
        assert mem_api._organize_task_status["running"] is False
        assert mem_api._organize_task_status["error"]
    finally:
        mem_api._organize_task_status.update(
            {"running": False, "result": None, "error": None, "started_at": None})


# ── tools ─────────────────────────────────────────────────────────────────

class _NoArgs(BaseModel):
    pass


def _outage_tool():
    from app.mcp.tools.base import BaseMCPTool
    ran = []

    class _Tool(BaseMCPTool):
        name = "infra_probe"
        description = "raises an outage"
        InputSchema = _NoArgs

        async def execute(self, **kwargs):
            ran.append(1)
            raise _outage()

    return _Tool(), ran


def test_direct_tool_arun_reports_outage():
    from app.mcp.enhanced_tools import DirectMCPTool
    tool, ran = _outage_tool()
    out = asyncio.run(DirectMCPTool(tool)._arun())
    assert ran, "the tool did not run"
    assert isinstance(out, str) and "Error" in out


def test_execute_single_tool_reports_outage_to_the_model():
    from app.mcp.enhanced_tools import DirectMCPTool
    from app.tool_execution import ToolExecContext, execute_single_tool
    tool, ran = _outage_tool()
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    executor = SimpleNamespace(
        _format_tool_result=lambda name, text, a: text,
        _infer_syntax_hint=lambda name, a: "json",
        _get_tool_header=lambda name, a: "infra_probe",
    )
    ctx = ToolExecContext(
        tool_id="tc-1", tool_name="infra_probe", actual_tool_name="infra_probe",
        args={}, all_tools=[DirectMCPTool(tool)],
        internal_tool_names=set(), mcp_manager=manager, project_root=None,
        conversation_id="conv-infra", conversation=[], recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=executor,
    )

    async def pull():
        return [ev async for ev in execute_single_tool(ctx)]

    events = asyncio.run(asyncio.wait_for(pull(), 30))
    assert ran, "the tool did not run"
    model = [e for e in events if e.get("type") == "tool_result_for_model"]
    assert model, [e.get("type") for e in events]
    assert "ERROR" in json.dumps(model[-1])


# ── task system ───────────────────────────────────────────────────────────

@pytest.fixture
def storage(tmp_path):
    from app.storage.task_runs import TaskRunStorage
    return TaskRunStorage(tmp_path)


def _block_status(run, block_id):
    states = run.block_states
    if isinstance(states, dict):
        st = states.get(block_id)
    else:
        st = next((s for s in states if getattr(s, "block_id", None) == block_id), None)
    return getattr(st, "status", None) if st is not None else None


def test_drive_run_holds_instead_of_leaving_the_run_running(storage, monkeypatch):
    from app.agents.run_driver import RunLaunch, drive_run
    from app.models.task_run import TaskRunCreate
    monkeypatch.setenv("ZIYA_SKIP_LAUNCH_PREFLIGHT", "1")
    run = storage.create(TaskRunCreate(card_id="card-1"))
    ran = []

    async def execute(block, ctx):
        ran.append(1)
        raise _outage("call_service_model")

    launch = RunLaunch(
        run_id=run.id,
        root=Block(block_type="task", id="t-1", name="t", instructions="do it"),
        project_id="p", project_root=None,
        card=SimpleNamespace(id="card-1", name="c", scope=None),
        run_storage=storage, execute=execute)
    asyncio.run(drive_run(launch))
    assert ran, "the executor did not run"
    final = storage.get(run.id)
    assert final.status == "held", final.status
    assert "credentials" in (final.error or "")


def test_drive_run_does_not_swallow_cancellation(storage, monkeypatch):
    from app.agents.run_driver import RunLaunch, drive_run
    from app.models.task_run import TaskRunCreate
    monkeypatch.setenv("ZIYA_SKIP_LAUNCH_PREFLIGHT", "1")
    run = storage.create(TaskRunCreate(card_id="card-1"))

    async def execute(block, ctx):
        raise asyncio.CancelledError()

    launch = RunLaunch(
        run_id=run.id,
        root=Block(block_type="task", id="t-1", name="t", instructions="do it"),
        project_id="p", project_root=None,
        card=SimpleNamespace(id="card-1", name="c", scope=None),
        run_storage=storage, execute=execute)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(drive_run(launch))
    assert storage.get(run.id).status != "held"


def test_execute_block_marks_the_block_held(storage):
    from app.agents.block_executor import ExecutionContext, execute_block
    from app.models.task_run import TaskRunCreate
    from app.models.task_run import TaskRunBlockState
    run = storage.create(TaskRunCreate(card_id="card-1"))
    storage.set_block_state(run.id, TaskRunBlockState(block_id="t-1", block_type="task"))
    block = Block(block_type="task", id="t-1", name="t", instructions="do it")
    ctx = ExecutionContext(run_id=run.id, storage=storage)
    with patch("app.agents.block_executor.execute_task_block",
               new=AsyncMock(side_effect=_outage("call_service_model"))) as call:
        with pytest.raises(ServiceInfraError):
            asyncio.run(execute_block(block, ctx))
    assert call.await_count == 1
    assert _block_status(storage.get(run.id), "t-1") == "held"


def test_delegate_outage_is_reported_failed(tmp_path):
    from app.agents.delegate_manager import DelegateManager
    from app.models.delegate import DelegateSpec
    project_dir = tmp_path / "proj"
    (project_dir / "chats").mkdir(parents=True)
    mgr = DelegateManager("proj-1", project_dir, max_concurrency=2)
    mgr._persist_delegate_message = MagicMock()
    mgr._build_delegate_messages = MagicMock(
        return_value=[{"role": "user", "content": "go"}])
    mgr.on_delegate_failed = AsyncMock()
    ran = []

    async def stream(spec, messages, plan_id):
        ran.append(1)
        raise _outage()
        yield  # pragma: no cover -- makes this an async generator

    mgr._create_delegate_stream = stream
    spec = DelegateSpec(delegate_id="d-1", name="worker", conversation_id="c-1")
    asyncio.run(mgr._run_delegate("plan-1", spec))
    assert ran, "the delegate stream did not start"
    mgr.on_delegate_failed.assert_awaited_once()
    assert "credentials" in mgr.on_delegate_failed.await_args.args[2]
