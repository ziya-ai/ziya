"""Bedrock service tier for Task Card runs.

Task Card runs request the discounted Flex tier on every Bedrock call;
interactive chat sends nothing.  These tests cover the whole path at its
seams rather than in halves:

  * env → tier resolution (default on, ``ZIYA_TASK_SERVICE_TIER`` disables)
  * ``execute_block`` sets the run-scoped tier so a Task body sees it,
    nested blocks inherit it, and it is cleared when the run returns
  * each Bedrock provider actually puts the tier on the wire call
    (asserted on the kwargs the boto3 client receives, not on an
    intermediate), sends nothing outside a run, retries once without
    the tier on the model's "not supported" rejection and remembers it,
    and surfaces any OTHER ValidationException untouched.

Wire shapes are the ones verified live on 2026-09-04: invoke_model*
takes ``serviceTier="flex"``; converse* takes ``serviceTier={"type": ...}``.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from app.context import (
    get_task_service_tier, set_task_service_tier, reset_task_service_tier,
)
from app.providers.base import ErrorEvent, ProviderConfig, ThinkingConfig
from app.utils import service_tier as st


REJECT = ("An error occurred (ValidationException) when calling the "
          "InvokeModel operation: The provided service tier is not "
          "supported for this model.")
OTHER_VALIDATION = ("An error occurred (ValidationException) when calling "
                    "the InvokeModel operation: max_tokens: must be >= 1")


@pytest.fixture(autouse=True)
def _clean_cache():
    st.reset_unsupported_cache()
    yield
    st.reset_unsupported_cache()


@pytest.fixture
def in_run():
    """Simulate being inside a task run with the default tier."""
    tok = set_task_service_tier("flex")
    try:
        yield
    finally:
        reset_task_service_tier(tok)


def _config():
    return ProviderConfig(thinking=ThinkingConfig(enabled=False, mode="adaptive"))


# ---------------------------------------------------------------------------
# Helper module
# ---------------------------------------------------------------------------

class TestEnvResolution:
    def test_unset_defaults_to_flex(self):
        assert st.resolve_task_tier_from_env({}) == "flex"

    @pytest.mark.parametrize("v", ["default", "off", "none", "", "standard", "0", "DEFAULT"])
    def test_disable_values(self, v):
        assert st.resolve_task_tier_from_env({st.ENV_VAR: v}) is None
        assert st.is_valid_tier_setting(v)

    def test_explicit_tier_passes_through(self):
        assert st.resolve_task_tier_from_env({st.ENV_VAR: "priority"}) == "priority"
        assert st.resolve_task_tier_from_env({st.ENV_VAR: " Flex "}) == "flex"

    def test_unrecognised_disables_and_is_flagged(self):
        assert st.resolve_task_tier_from_env({st.ENV_VAR: "cheap"}) is None
        assert not st.is_valid_tier_setting("cheap")


class TestRequestedTier:
    def test_none_outside_run(self):
        assert get_task_service_tier() is None
        assert st.requested_tier("m") is None
        assert st.invoke_kwargs("m") == {}
        assert st.converse_kwargs("m") == {}

    def test_shapes_inside_run(self, in_run):
        assert st.invoke_kwargs("m") == {"serviceTier": "flex"}
        assert st.converse_kwargs("m") == {"serviceTier": {"type": "flex"}}

    def test_negative_cache_is_per_model(self, in_run):
        st.mark_unsupported("claude", "flex")
        assert st.requested_tier("claude") is None
        assert st.requested_tier("qwen") == "flex"
        assert st.is_marked_unsupported("claude", "flex")


class TestErrorMatching:
    @pytest.mark.parametrize("msg", [
        REJECT,
        "400: unsupported service_tier 'flex'",
        "'flex' is not supported for 'service_tier' on this model",
    ])
    def test_tier_rejections(self, msg):
        assert st.is_service_tier_unsupported_error(msg)

    def test_other_validation_not_matched(self):
        assert not st.is_service_tier_unsupported_error(OTHER_VALIDATION)
        assert not st.is_service_tier_unsupported_error("")


class TestResolvedTier:
    def test_invoke_header(self):
        r = {"ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-service-tier": "flex"}}}
        assert st.resolved_tier_from_response(r) == "flex"

    def test_converse_structure(self):
        assert st.resolved_tier_from_response({"serviceTier": {"type": "default"}}) == "default"

    def test_absent(self):
        assert st.resolved_tier_from_response({"ResponseMetadata": {}}) is None
        assert st.resolved_tier_from_response(None) is None


# ---------------------------------------------------------------------------
# Seam: execute_block → task body sees the tier
# ---------------------------------------------------------------------------

class TestExecuteBlockSeam:
    def _run(self, block, seen):
        from app.agents.block_executor import execute_block, ExecutionContext
        from app.models.task_card import Artifact

        async def _stub(block, project_root=None, project_id=None, run_id=None):
            seen.append(get_task_service_tier())
            return Artifact(summary="ok", failed=False, tokens=1, duration_ms=1)

        with patch("app.agents.block_executor.execute_task_block", _stub):
            return asyncio.run(execute_block(block, ExecutionContext(run_id="r")))

    def test_task_sees_flex_and_it_is_cleared_after(self, monkeypatch):
        from app.models.task_card import Block
        monkeypatch.delenv(st.ENV_VAR, raising=False)
        seen = []
        self._run(Block(block_type="task", id="t", name="t", instructions="x"), seen)
        assert seen == ["flex"]
        assert get_task_service_tier() is None

    def test_nested_blocks_inherit_one_setting(self, monkeypatch):
        from app.models.task_card import Block
        monkeypatch.delenv(st.ENV_VAR, raising=False)
        seen = []
        group = Block(block_type="group", id="g", name="g", body=[
            Block(block_type="task", id="a", name="a", instructions="x"),
            Block(block_type="task", id="b", name="b", instructions="y"),
        ])
        self._run(group, seen)
        assert seen == ["flex", "flex"]
        assert get_task_service_tier() is None

    def test_env_disable(self, monkeypatch):
        from app.models.task_card import Block
        monkeypatch.setenv(st.ENV_VAR, "default")
        seen = []
        self._run(Block(block_type="task", id="t", name="t", instructions="x"), seen)
        assert seen == [None]

    def test_env_priority(self, monkeypatch):
        from app.models.task_card import Block
        monkeypatch.setenv(st.ENV_VAR, "priority")
        seen = []
        self._run(Block(block_type="task", id="t", name="t", instructions="x"), seen)
        assert seen == ["priority"]


# ---------------------------------------------------------------------------
# Seam: per-task scope.service_tier overrides the run-level tier for
# exactly that task, as observed from inside the model call
# ---------------------------------------------------------------------------

class _TierObservingExecutor:
    """Stand-in for StreamingToolExecutor that records the service tier
    visible at the moment the model call would be made — the same
    contextvar the Bedrock providers read."""

    observed: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        type(self).observed.append(get_task_service_tier())
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def observing_executor():
    _TierObservingExecutor.observed = []
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _TierObservingExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield _TierObservingExecutor


def _scoped_task(id: str, **scope_kwargs):
    from app.models.task_card import Block, TaskScope
    return Block(block_type="task", id=id, name=id, instructions="x",
                 scope=TaskScope(**scope_kwargs) if scope_kwargs else None)


class TestScopeServiceTier:
    def test_model_accepts_known_tiers_and_rejects_others(self):
        from app.models.task_card import TaskScope
        for t in ("flex", "default", "priority"):
            assert TaskScope(service_tier=t).service_tier == t
        with pytest.raises(Exception):
            TaskScope(service_tier="batch")

    def test_merge_last_non_null_wins(self):
        from app.models.task_card import TaskScope, merge_scopes
        outer = TaskScope(service_tier="flex")
        inner = TaskScope(service_tier="default")
        assert merge_scopes(outer, inner).service_tier == "default"
        assert merge_scopes(outer, TaskScope()).service_tier == "flex"
        assert merge_scopes(TaskScope(), TaskScope()).service_tier is None

    def _run(self, block, monkeypatch):
        from app.agents.block_executor import execute_block, ExecutionContext
        monkeypatch.delenv(st.ENV_VAR, raising=False)
        return asyncio.run(execute_block(block, ExecutionContext(run_id="r")))

    def test_default_pins_one_task_to_standard_siblings_stay_flex(
            self, observing_executor, monkeypatch):
        from app.models.task_card import Block
        group = Block(block_type="group", id="g", name="g", body=[
            _scoped_task("a"),
            _scoped_task("gate", service_tier="default"),
            _scoped_task("b"),
        ])
        self._run(group, monkeypatch)
        # "default" is expressed as no tier (standard billing), and the
        # override does not leak into the following sibling.
        assert observing_executor.observed == ["flex", None, "flex"]
        assert get_task_service_tier() is None

    def test_priority_on_a_task(self, observing_executor, monkeypatch):
        self._run(_scoped_task("t", service_tier="priority"), monkeypatch)
        assert observing_executor.observed == ["priority"]

    def test_flex_opts_back_in_when_run_is_disabled(self, observing_executor, monkeypatch):
        from app.agents.block_executor import execute_block, ExecutionContext
        from app.models.task_card import Block
        monkeypatch.setenv(st.ENV_VAR, "default")
        group = Block(block_type="group", id="g", name="g", body=[
            _scoped_task("a"),
            _scoped_task("cheap", service_tier="flex"),
        ])
        asyncio.run(execute_block(group, ExecutionContext(run_id="r")))
        assert observing_executor.observed == [None, "flex"]

    def test_ancestor_scope_applies_to_unscoped_leaves(self, observing_executor, monkeypatch):
        from app.models.task_card import Block, TaskScope
        group = Block(block_type="group", id="g", name="g",
                      scope=TaskScope(service_tier="default"),
                      body=[_scoped_task("a"), _scoped_task("b", service_tier="priority")])
        self._run(group, monkeypatch)
        assert observing_executor.observed == [None, "priority"]


# ---------------------------------------------------------------------------
# Seam: providers put the tier on the boto3 call
# ---------------------------------------------------------------------------

class _RecordingClient:
    """boto3 stand-in: records kwargs; rejects the tier N times."""

    def __init__(self, reject_with=None, reject_times=1, response=None):
        self.calls = []
        self._reject_with = reject_with
        self._reject_left = reject_times
        self._response = response if response is not None else {
            "body": [], "ResponseMetadata": {
                "RequestId": "req-1",
                "HTTPHeaders": {"x-amzn-bedrock-service-tier": "flex"},
            },
        }

    def _call(self, kwargs):
        self.calls.append(kwargs)
        if self._reject_with and self._reject_left > 0:
            self._reject_left -= 1
            raise Exception(self._reject_with)
        return self._response

    def invoke_model_with_response_stream(self, **kw):
        return self._call(kw)

    def converse_stream(self, **kw):
        return self._call(kw)

    def converse(self, **kw):
        return self._call(kw)


def _bedrock_provider(client):
    from app.providers.bedrock import BedrockProvider

    class _Router:
        enabled = False

    p = BedrockProvider.__new__(BedrockProvider)
    p.model_config = {}
    p.model_id = "us.anthropic.claude-test"
    p._region = "us-west-2"
    p._region_router = _Router()
    p.bedrock = client
    p._build_request_body = lambda *a, **k: {}
    return p


def _nova_provider(client):
    from app.providers.nova_bedrock import NovaBedrockProvider

    async def _no_events(response, config):
        if False:
            yield None

    p = NovaBedrockProvider.__new__(NovaBedrockProvider)
    p.model_config = {}
    p.model_id = "qwen.qwen3-test"
    p.bedrock = client
    p._build_converse_params = lambda *a, **k: {"modelId": p.model_id, "messages": []}
    p._parse_converse_stream = _no_events
    return p


def _openai_provider(client):
    from app.providers.openai_bedrock import OpenAIBedrockProvider

    async def _no_events(response):
        if False:
            yield None

    p = OpenAIBedrockProvider.__new__(OpenAIBedrockProvider)
    p.model_config = {}
    p.model_id = "openai.gpt-oss-test"
    p.bedrock = client
    p._build_request_body = lambda *a, **k: {}
    p._parse_stream = _no_events
    return p


async def _drain(provider):
    out = []
    async for ev in provider.stream_response([], None, [], _config()):
        out.append(ev)
    return out


PROVIDERS = [
    pytest.param(_bedrock_provider, "serviceTier", "flex", id="bedrock-invoke"),
    pytest.param(_nova_provider, "serviceTier", {"type": "flex"}, id="nova-converse"),
    pytest.param(_openai_provider, "serviceTier", "flex", id="openai-invoke"),
]


class TestProviderSeam:
    @pytest.mark.parametrize("make,key,wire", PROVIDERS)
    def test_tier_on_wire_inside_run(self, make, key, wire, in_run):
        client = _RecordingClient()
        asyncio.run(_drain(make(client)))
        assert len(client.calls) == 1
        assert client.calls[0][key] == wire

    @pytest.mark.parametrize("make,key,wire", PROVIDERS)
    def test_nothing_sent_outside_run(self, make, key, wire):
        client = _RecordingClient()
        asyncio.run(_drain(make(client)))
        assert len(client.calls) == 1
        assert key not in client.calls[0]

    @pytest.mark.parametrize("make,key,wire", PROVIDERS)
    def test_rejection_retries_once_without_tier_and_remembers(self, make, key, wire, in_run):
        client = _RecordingClient(reject_with=REJECT)
        p = make(client)
        out = asyncio.run(_drain(p))
        assert [key in c for c in client.calls] == [True, False], client.calls
        assert not any(isinstance(e, ErrorEvent) and "service tier" in e.message for e in out)
        assert st.is_marked_unsupported(p.model_id, "flex")
        # Second request in the same process: no probe, straight to standard.
        client2 = _RecordingClient()
        asyncio.run(_drain(make(client2)))
        assert len(client2.calls) == 1 and key not in client2.calls[0]

    @pytest.mark.parametrize("make,key,wire", PROVIDERS)
    def test_other_validation_error_is_not_retried(self, make, key, wire, in_run):
        client = _RecordingClient(reject_with=OTHER_VALIDATION)
        p = make(client)
        out = asyncio.run(_drain(p))
        assert len(client.calls) == 1
        errs = [e for e in out if isinstance(e, ErrorEvent)]
        assert errs and "max_tokens" in errs[0].message
        assert not st.is_marked_unsupported(p.model_id, "flex")


class TestServiceModelSeam:
    """Until/Improve evaluators call Bedrock through model_resolver._call_bedrock."""

    def _call(self, client):
        from app.services import model_resolver

        class _Session:
            def __init__(self, profile_name=None):
                pass

            def client(self, *a, **k):
                return client

        with patch("boto3.Session", _Session):
            return asyncio.run(model_resolver._call_bedrock(
                {"model_id": "deepseek.v3.2", "region": "us-west-2"},
                "sys", "user", 16, 0.0,
            ))

    def _resp(self):
        return {"output": {"message": {"content": [{"text": "yes"}]}}}

    def test_tier_on_converse_inside_run(self, in_run):
        client = _RecordingClient(response=self._resp())
        assert self._call(client) == "yes"
        assert client.calls[0]["serviceTier"] == {"type": "flex"}

    def test_nothing_outside_run(self):
        client = _RecordingClient(response=self._resp())
        self._call(client)
        assert "serviceTier" not in client.calls[0]

    def test_rejection_retries_and_remembers(self, in_run):
        client = _RecordingClient(reject_with=REJECT, response=self._resp())
        assert self._call(client) == "yes"
        assert ["serviceTier" in c for c in client.calls] == [True, False]
        assert st.is_marked_unsupported("deepseek.v3.2", "flex")

    def test_other_error_propagates(self, in_run):
        client = _RecordingClient(reject_with=OTHER_VALIDATION, response=self._resp())
        with pytest.raises(Exception, match="max_tokens"):
            self._call(client)
        assert len(client.calls) == 1
