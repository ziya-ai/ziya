"""A chat turn must not end silently when the memory embedding call hits an outage.

Observed 2026-11-16: with Midway expired, a submitted message produced a
200 response with an empty event stream, no error banner and no log line
past "No files selected".  The chain:

  build_messages_for_streaming (turn-pool thread)
    -> get_memory_prompt_sections -> _relevance_core -> _query_vectors
    -> BedrockTitanProvider.embed_text raises ServiceInfraError

``ServiceInfraError`` is a BaseException on purpose (app/utils/infra_errors),
so it passed every ``except Exception`` between the embedding call and the
relay pump, killed the pump task, and the subscriber saw only end-of-stream.

Pinned here:
  - prompt assembly treats an embedding outage as "no relevance ranking
    this turn" and returns, so the turn reaches the model call, where the
    same credential failure is reported through the normal error path;
  - the relay turns ANY non-cancellation failure of its source, including
    a BaseException, into a client-visible error frame.
"""
import asyncio
import json

import pytest

import app.agents.chat_turn_relay as relay
import app.memory.prompt as mprompt
from app.utils.infra_errors import ServiceInfraError


class CredentialRetrievalError(Exception):
    pass


def _outage() -> ServiceInfraError:
    return ServiceInfraError(
        CredentialRetrievalError(
            "Error when retrieving credentials from custom-process: "
            "failed to initialize iibs client"),
        where="embed_text")


class _OutageProvider:
    """Embedding provider whose every call is an infrastructure outage."""
    dim = 4

    def __init__(self):
        self.calls = 0

    def embed_text(self, text):
        self.calls += 1
        raise _outage()


@pytest.fixture
def outage_provider(monkeypatch):
    import app.services.embedding_service as es
    provider = _OutageProvider()
    monkeypatch.setattr(es, "get_embedding_provider", lambda: provider)
    monkeypatch.setattr(mprompt, "_query_vec_cache", {})
    monkeypatch.setenv("ZIYA_MEMORY_CORE_POLICY", "relevance")
    return provider


def test_query_vectors_returns_empty_on_embedding_outage(outage_provider):
    vecs = mprompt._query_vectors(["test"])
    assert outage_provider.calls == 1, "the embedding path did not run"
    assert vecs == []


def test_query_vectors_stops_calling_after_first_outage(outage_provider):
    """One failed call per turn, not one per query text: each attempt can
    block on a credential process for seconds."""
    assert mprompt._query_vectors(["one", "two", "three"]) == []
    assert outage_provider.calls == 1


def test_relevance_core_returns_empty_on_embedding_outage(outage_provider):
    core = mprompt._relevance_core([object()], ["test"])
    assert outage_provider.calls == 1, "the embedding path did not run"
    assert core == []


# ── relay backstop ────────────────────────────────────────────────────────

def _parse(frame: str):
    return json.loads(frame[len("data: "):])


@pytest.mark.asyncio
async def test_relay_reports_base_exception_from_source(monkeypatch):
    monkeypatch.setattr(relay, "_checkpoint_now", _noop_checkpoint)
    relay._reset_for_tests()

    async def source():
        raise _outage()
        yield  # pragma: no cover -- makes this an async generator

    await relay.start_turn("conv-base-exc", source())
    frames = []
    async for f in relay.subscribe("conv-base-exc"):
        frames.append(_parse(f))
    relay._reset_for_tests()

    errors = [f for f in frames if f.get("error")]
    assert errors, f"subscriber saw no error frame, only: {frames}"
    assert errors[0].get("error_type")
    assert frames[-1].get("type") == "stream_end"


@pytest.mark.asyncio
async def test_relay_cancellation_is_still_not_an_error(monkeypatch):
    """The backstop must not reclassify Stop as a failure."""
    monkeypatch.setattr(relay, "_checkpoint_now", _noop_checkpoint)
    relay._reset_for_tests()
    started = asyncio.Event()

    async def source():
        started.set()
        await asyncio.sleep(60)
        yield "data: {}\n\n"  # pragma: no cover

    await relay.start_turn("conv-cancel", source())
    await started.wait()
    await relay.cancel_turn("conv-cancel")
    frames = []
    async for f in relay.subscribe("conv-cancel"):
        frames.append(_parse(f))
    relay._reset_for_tests()

    assert not [f for f in frames if f.get("error")], frames
    assert frames[-1] == {"type": "stream_end", "reason": "cancelled"}


async def _noop_checkpoint(turn):
    return None
