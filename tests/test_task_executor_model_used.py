"""The model a task block actually ran on is recorded, not inferred.

A scope's ``model_tier`` resolves to a concrete model per endpoint, and a
block with no model fields runs on whatever the launch resolved to, so the
card definition alone cannot answer "what model did this block use, and
what context ceiling were its notices measured against?".  The executor
therefore records the resolved facts in three places:

  - the ``task_started`` event (live UI),
  - the persisted ``Artifact`` (durable, per block),
  - each ``IterationSummary`` (per loop iteration, via _record_iteration).

These tests pin the shape of that record, that ``context_limit`` follows
the same extended-context rule as the executor's context-size notice, and
that the lookup is purely diagnostic — an executor lacking the attributes
(every test fake in this suite) must not fail the run.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.agents.task_executor import resolve_model_used
from app.models.task_card import Artifact, Block, TaskScope
from app.models.task_run import IterationSummary


# ── resolve_model_used: pure ─────────────────────────────────────────

class TestResolveModelUsed:
    def test_plain_200k_model(self):
        ex = SimpleNamespace(
            model_config={"name": "haiku-4.5", "token_limit": 200000},
            model_id="us.anthropic.claude-haiku-4-5",
            endpoint="bedrock",
        )
        assert resolve_model_used(ex) == {
            "model": "haiku-4.5",
            "model_id": "us.anthropic.claude-haiku-4-5",
            "endpoint": "bedrock",
            "context_limit": 200000,
        }

    def test_extended_context_uses_extended_limit(self):
        # Mirrors StreamingToolExecutor._handle_usage_event: the notice is
        # measured against extended_context_limit when the model supports
        # it, so the recorded ceiling must be the same number.
        ex = SimpleNamespace(
            model_config={
                "name": "sonnet4.6", "token_limit": 200000,
                "supports_extended_context": True,
                "extended_context_limit": 1000000,
            },
            model_id="x", endpoint="bedrock",
        )
        assert resolve_model_used(ex)["context_limit"] == 1000000

    def test_extended_flag_without_limit_falls_back_to_base(self):
        ex = SimpleNamespace(
            model_config={"name": "m", "token_limit": 200000,
                          "supports_extended_context": True},
            model_id="x", endpoint="bedrock",
        )
        assert resolve_model_used(ex)["context_limit"] == 200000

    def test_native_1m_model(self):
        ex = SimpleNamespace(
            model_config={"name": "fable5.1", "token_limit": 1000000},
            model_id="us.anthropic.claude-fable-5-1", endpoint="bedrock",
        )
        assert resolve_model_used(ex)["context_limit"] == 1000000

    def test_dict_model_id_is_stringified(self):
        # Some configs carry a region map; the record must stay a string
        # so it serialises and renders predictably.
        ex = SimpleNamespace(
            model_config={"name": "m"},
            model_id={"us": "a", "global": "b"}, endpoint="bedrock",
        )
        out = resolve_model_used(ex)
        assert isinstance(out["model_id"], str)

    def test_bare_object_yields_all_none(self):
        # The diagnostic must never raise: a fake / shim with none of the
        # attributes gets a record of Nones, not an AttributeError.
        out = resolve_model_used(object())
        assert out == {"model": None, "model_id": None,
                       "endpoint": None, "context_limit": None}

    def test_none_model_config_tolerated(self):
        ex = SimpleNamespace(model_config=None, model_id=None, endpoint="google")
        out = resolve_model_used(ex)
        assert out["model"] is None
        assert out["model_id"] is None
        assert out["endpoint"] == "google"
        assert out["context_limit"] is None


# ── Wiring through execute_task_block ────────────────────────────────

class _FakeExecutor:
    """Executor fake that DOES carry model facts, so the wiring can be
    asserted end-to-end.  Class attrs are overwritten per test."""

    model_config = {"name": "haiku-4.5", "token_limit": 200000}
    model_id = "us.anthropic.claude-haiku-4-5"
    endpoint = "bedrock"
    init_kwargs = None

    def __init__(self, *args, **kwargs):
        type(self).init_kwargs = kwargs

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


class _BareFakeExecutor:
    """Same stream, but no model attributes at all — the shape of every
    pre-existing fake in tests/test_task_executor_*.py.  Deliberately not
    a subclass of _FakeExecutor: inherited class attributes would still
    resolve via getattr and defeat the point."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def patched(request):
    cls = getattr(request, "param", _FakeExecutor)
    with patch("app.streaming_tool_executor.StreamingToolExecutor", cls), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "global-model"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield cls


def _task(**scope_kwargs) -> Block:
    return Block(
        block_type="task", id="task-1", name="T", instructions="do it",
        scope=TaskScope(**scope_kwargs) if scope_kwargs else None,
    )


class TestArtifactRecordsModel:
    @pytest.mark.asyncio
    async def test_artifact_carries_resolved_model(self, patched):
        art = await task_executor.execute_task_block(_task(), run_id="r1")
        assert art.model == "haiku-4.5"
        assert art.model_id == "us.anthropic.claude-haiku-4-5"
        assert art.endpoint == "bedrock"
        assert art.context_limit == 200000

    @pytest.mark.asyncio
    async def test_artifact_model_is_executors_not_global(self, patched):
        # The regression: the log line and any record used the server's
        # global current_model.  The artifact must reflect the executor
        # this block was actually constructed with.
        art = await task_executor.execute_task_block(_task(model_tier="small"))
        assert art.model == "haiku-4.5"
        assert art.model != "global-model"
        # And the tier really was handed to the executor.
        assert patched.init_kwargs["model_override"] == "small"

    @pytest.mark.asyncio
    async def test_task_started_event_carries_model(self, patched):
        events = []

        async def _fake_safe_push(run_id, evt):
            events.append(evt)

        # execute_task_block's _emit goes straight through the relay
        # module's safe_push (same seam test_task_executor_progress_events
        # uses).  Requires a run_id — _emit is a no-op without one.
        with patch("app.agents.task_run_stream_relay.safe_push", _fake_safe_push):
            await task_executor.execute_task_block(_task(), run_id="r2")

        started = [e for e in events if e.get("type") == "task_started"]
        assert started, f"no task_started among {[e.get('type') for e in events]}"
        ev = started[0]
        assert ev["model"] == "haiku-4.5"
        assert ev["model_id"] == "us.anthropic.claude-haiku-4-5"
        assert ev["endpoint"] == "bedrock"
        assert ev["context_limit"] == 200000

    @pytest.mark.asyncio
    @pytest.mark.parametrize("patched", [_BareFakeExecutor], indirect=True)
    async def test_bare_executor_does_not_fail_run(self, patched):
        # Every pre-existing executor fake lacks these attributes; the
        # record must degrade to None rather than break the run.
        art = await task_executor.execute_task_block(_task(), run_id="r3")
        assert art.failed is False
        assert art.model is None
        assert art.context_limit is None


# ── _record_iteration carries the model onto the summary ─────────────

class _CapturingStorage:
    def __init__(self):
        self.summaries = []
        self.artifacts = []

    def write_iteration_artifact(self, run_id, block_id, index, artifact):
        self.artifacts.append((block_id, index, artifact))

    def append_iteration_summary(self, run_id, block_id, summary):
        self.summaries.append((block_id, summary))


class TestIterationSummaryModel:
    @pytest.mark.asyncio
    async def test_summary_records_artifact_model(self):
        from app.agents import block_executor as be
        storage = _CapturingStorage()
        ctx = be.ExecutionContext(
            run_id="r-model", project_id="p1", project_root="/tmp",
            storage=storage,
        )
        loop = Block(block_type="repeat", id="loop-1", name="L",
                     repeat_mode="count", repeat_count=2)
        art = Artifact(summary="ok", model="opus4.8",
                       model_id="global.anthropic.claude-opus-4-8",
                       endpoint="bedrock", context_limit=1000000)
        await be._record_iteration(loop, ctx, 0, art)
        assert len(storage.summaries) == 1
        block_id, summary = storage.summaries[0]
        assert block_id == "loop-1"
        assert summary.model == "opus4.8"

    @pytest.mark.asyncio
    async def test_summary_model_none_when_artifact_lacks_it(self):
        # Container-synthesised and legacy artifacts have no model; the
        # summary must not invent one.
        from app.agents import block_executor as be
        storage = _CapturingStorage()
        ctx = be.ExecutionContext(
            run_id="r-model", project_id="p1", project_root="/tmp",
            storage=storage,
        )
        loop = Block(block_type="repeat", id="loop-1", name="L",
                     repeat_mode="count", repeat_count=1)
        await be._record_iteration(loop, ctx, 0, Artifact(summary="ok"))
        assert storage.summaries[0][1].model is None


# ── Model shapes ─────────────────────────────────────────────────────

class TestModelShapes:
    def test_artifact_fields_default_none(self):
        # Records written before the fields existed load with None, not
        # a validation error.
        a = Artifact(summary="s")
        assert a.model is None and a.model_id is None
        assert a.endpoint is None and a.context_limit is None

    def test_iteration_summary_model_default_none(self):
        s = IterationSummary(index=0, status="passed")
        assert s.model is None

    def test_iteration_summary_accepts_model(self):
        s = IterationSummary(index=0, status="passed", model="opus4.8")
        assert s.model_dump()["model"] == "opus4.8"
