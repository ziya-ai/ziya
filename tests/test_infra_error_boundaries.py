"""ServiceInfraError must stop at a boundary that reports it.

``ServiceInfraError`` is a BaseException on purpose (app/utils/infra_errors):
inside a memory pass it has to pass the ``except Exception`` fallbacks and
reach ``memory_pause.guarded``, which pauses memory.  Every OTHER path that
reaches an embedding or service-model call needs its own boundary, because
``except Exception`` there does not see it either.  Found by audit on
2026-11-16 after a chat turn ended with an empty stream:

  path                               | without a boundary
  -----------------------------------|-------------------------------------
  prompt assembly, provider probe    | turn ends, empty stream
  until judge (task cards)           | run left "running" with no task
  improve judge / self-improve critic| same, despite "never raises"
  intent judge (end of a chat turn)  | turn dies after the answer streamed
  memory tools called by the model   | turn dies mid tool call
  /api/chat with no conversation id  | response ends with no error frame

Each test drives the real function with a provider/model that raises the
outage, and asserts both that the outage path ran and what came out.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.models.task_card import Artifact, Block
from app.utils.infra_errors import ServiceInfraError


class CredentialRetrievalError(Exception):
    pass


def _outage(where: str = "test") -> ServiceInfraError:
    return ServiceInfraError(
        CredentialRetrievalError(
            "Error when retrieving credentials from custom-process: expired"),
        where=where)


def _service_model_outage():
    return patch("app.services.model_resolver.call_service_model",
                 new=AsyncMock(side_effect=_outage("call_service_model")))


# ── prompt assembly: the provider probe itself ────────────────────────────

def test_query_vectors_survives_provider_probe_outage(monkeypatch):
    """A server started with expired credentials never caches a provider,
    so get_embedding_provider() raises on every turn."""
    import app.memory.prompt as mprompt
    import app.services.embedding_service as es
    calls = []

    def probe():
        calls.append(1)
        raise _outage("embed_text")

    monkeypatch.setattr(es, "get_embedding_provider", probe)
    monkeypatch.setattr(mprompt, "_query_vec_cache", {})
    assert mprompt._query_vectors(["test"]) == []
    assert calls, "the provider was never asked for"


# ── task system ───────────────────────────────────────────────────────────

def test_until_judge_outage_is_a_task_infra_error():
    """Held, not "condition unmet": TaskInfraError is what the block
    executor and run driver record as a hold."""
    from app.agents.task_executor import TaskInfraError
    from app.agents.until_evaluator import evaluate_condition_verdict
    with _service_model_outage() as call:
        with pytest.raises(TaskInfraError) as ei:
            asyncio.run(evaluate_condition_verdict(
                "the report exists", Artifact(summary="ran")))
    assert call.await_count == 1
    assert ei.value.infra_kind == "authentication_error"


def test_until_judge_ordinary_failure_still_means_unmet():
    from app.agents.until_evaluator import evaluate_condition_verdict
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(side_effect=RuntimeError("bad reply"))):
        v = asyncio.run(evaluate_condition_verdict(
            "the report exists", Artifact(summary="ran")))
    assert v.satisfied is False


def test_block_executor_does_not_swallow_the_until_hold():
    """The seam: the caller's ``except Exception`` would turn the hold back
    into "unmet" unless TaskInfraError is re-raised ahead of it."""
    import inspect
    import app.agents.block_executor as be
    src = inspect.getsource(be)
    call = src.index("_evaluate_until_condition_with_model(condition, artifact)")
    tail = src[call:call + 600]
    assert "except TaskInfraError" in tail
    assert tail.index("except TaskInfraError") < tail.index("except Exception")


def _level() -> Block:
    return Block(block_type="group", id="g-1", name="level",
                 body=[Block(block_type="task", id="t-1", name="one",
                             instructions="do the thing")])


def test_improve_judge_outage_is_an_error_verdict():
    import app.agents.improve_evaluator as ev
    from app.utils import self_improve as si
    with _service_model_outage() as call:
        out = asyncio.run(ev.evaluate_improvement(_level(), Artifact(summary="ran")))
    assert call.await_count == 1
    assert out.get("verdict") == si.JUDGE_ERROR_VERDICT


def test_improve_judge_does_not_swallow_cancellation():
    import app.agents.improve_evaluator as ev
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ev.evaluate_improvement(_level(), Artifact(summary="ran")))


def test_self_improve_critic_outage_means_no_improvement():
    from app.utils.card_self_improve import critique
    with _service_model_outage() as call:
        out = asyncio.run(critique(
            _level(), Artifact(summary="ran"), criterion=None,
            prior_lessons=[], rewrites_used=0, rewrites_budget=2))
    assert call.await_count == 1
    assert out["verdict"] == "no_improvement"
    assert out["patch"] == {}


# ── chat turn: boundaries outside the directly writable tree ──────────────
# These three are fixed by diffs to app/services/intent_judge.py,
# app/mcp/tools/memory_tools.py and app/server.py; they fail until those
# are applied.

def test_intent_judge_outage_fails_closed():
    from app.services.intent_judge import judge_dangling_intent
    text = "The first half is done.\n\nNow I'll update the second file."
    with _service_model_outage() as call:
        assert asyncio.run(judge_dangling_intent(text)) is False
    assert call.await_count == 1


def test_memory_tool_outage_is_a_tool_error_not_a_dead_turn():
    from app.mcp.tools.memory_tools import _OffLoopMemoryBase
    ran = []

    class _Tool(_OffLoopMemoryBase):
        name = "memory_probe"
        description = "test"

        def _execute_sync(self, **kwargs):
            ran.append(kwargs)
            raise _outage("embed_text")

    out = asyncio.run(_Tool().execute(query="x"))
    assert ran, "the tool body did not run"
    assert out.get("error") is True
    assert "authentication_error" in out.get("message", "")


def test_keepalive_wrapper_reports_base_exception():
    from app.server import _keepalive_wrapper

    async def source():
        yield "data: {\"content\": \"partial\"}\n\n"
        raise _outage("embed_text")

    async def collect():
        return [f async for f in _keepalive_wrapper(source())]

    frames = asyncio.run(collect())
    payloads = [json.loads(f[6:]) for f in frames if f.startswith("data: ")]
    assert payloads[0] == {"content": "partial"}
    assert any(p.get("error") for p in payloads), payloads
    assert payloads[-1].get("type") == "stream_end"
