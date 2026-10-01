"""An infrastructure outage pauses memory instead of being guessed around.

app/memory has 12 service-model call sites behind 34 ``except Exception``
handlers whose documented fallbacks are: contradiction classify -> AGREES,
comparison -> ADD, extraction -> nothing, embedding None -> no dedup.  So
in the hours between a credential expiring and the next ``mwinit``, the
store kept changing on answers nobody gave, and the log said ``warning``.

Pinned here, end to end:
  - call_service_model / embed_text raise ServiceInfraError (a
    BaseException carrying infra_kind) for outage-class failures only;
  - an outage mid-extraction leaves the store byte-identical to before the
    turn, records no session tick, and writes the pause file;
  - while paused, every entry point returns skipped without calling the
    model, and the system prompt says so;
  - resume clears it and the next turn extracts normally.
"""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.utils import memory_pause as mp
from app.utils.infra_errors import ServiceInfraError, infra_kind, is_infra_error


class CredentialRetrievalError(Exception):
    pass


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZIYA_ENABLE_MEMORY", "true")
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as es
    monkeypatch.setattr(es, "_provider", None)
    monkeypatch.setattr(es, "_cache", None)
    from app.mcp.builtin_tools import invalidate_category_cache
    invalidate_category_cache()
    yield
    invalidate_category_cache()


def _store_hash():
    from app.utils.paths import get_ziya_home
    d = get_ziya_home() / "memory"
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")) if d.exists() else []:
        # The pause file is the expected write; the clock is asserted by value.
        if p.is_file() and p.name not in (mp.PAUSE_FILE, "domain_clocks.json"):
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


HISTORY = [
    {"role": "human", "content": "Our orders service keeps everything in PostgreSQL 15; the schema lives in db/orders.sql. We decided never to use MongoDB for orders again after the migration pain."},
    {"role": "assistant", "content": "Noted: PostgreSQL 15 is the database of record for orders; no MongoDB."},
    {"role": "human", "content": "Remember that for future sessions when we design new order features and reporting dashboards."},
    {"role": "assistant", "content": "I will. Postgres 15 for orders, schema in db/orders.sql, MongoDB rejected."},
    {"role": "human", "content": "Also: the reporting replica lags the primary by about 30 seconds, so dashboards must never read the primary directly. That was decided after the Q2 incident."},
    {"role": "assistant", "content": "Understood: dashboards read the replica; ~30s lag is acceptable by decision."},
    {"role": "human", "content": "Good. One more durable fact: order ids are ULIDs, not UUIDs, because we sort by them in the warehouse."},
    {"role": "assistant", "content": "Recorded: ULID order ids, chosen for sortability."},
]


# ── classifier / resolver ──────────────────────────────────────────────


def test_classifier_kinds_and_chain():
    assert infra_kind(CredentialRetrievalError("Failed to force refresh the credentials")) == "authentication_error"
    e = RuntimeError("wrapped")
    e.__cause__ = ConnectionResetError("peer reset")
    assert infra_kind(e) == "connection_error"
    assert not is_infra_error(ValueError("prompt too long for this model"))
    assert not isinstance(ServiceInfraError(ConnectionError("x")), Exception)


@pytest.mark.asyncio
async def test_call_service_model_raises_typed_outage(monkeypatch):
    import app.services.model_resolver as mr
    monkeypatch.setattr(mr, "resolve_service_model", lambda c: {"endpoint": "bedrock", "model_id": "m"})
    monkeypatch.setattr(mr, "_call_bedrock", AsyncMock(side_effect=CredentialRetrievalError("expired")))
    with pytest.raises(ServiceInfraError) as ei:
        try:
            await mr.call_service_model("memory_organization", "s", "u")
        except Exception:
            pytest.fail("outage was catchable as Exception")
    assert ei.value.infra_kind == "authentication_error"
    # a model-side failure is still an ordinary exception
    monkeypatch.setattr(mr, "_call_bedrock", AsyncMock(side_effect=ValueError("validation")))
    with pytest.raises(ValueError):
        await mr.call_service_model("memory_organization", "s", "u")


def test_embed_text_raises_outage_and_keeps_none_for_empty(monkeypatch):
    from app.services.embedding_service import BedrockTitanProvider
    p = BedrockTitanProvider.__new__(BedrockTitanProvider)
    p._dim, p._model_id, p._warned = 1024, "titan", False

    class _Client:
        def invoke_model(self, **kw):
            raise CredentialRetrievalError("Failed to force refresh the credentials")
    p._get_client = lambda: _Client()
    assert p.embed_text("   ") is None
    with pytest.raises(ServiceInfraError):
        p.embed_text("some text")


# ── extraction turn ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outage_mid_turn_records_nothing_and_pauses():
    from app.memory.extractor import run_post_conversation_extraction
    from app.utils import memory_clock
    before, clock_before = _store_hash(), memory_clock.load()
    outage = AsyncMock(side_effect=CredentialRetrievalError("Failed to force refresh the credentials"))
    with patch("app.services.model_resolver._call_bedrock", outage), \
         patch("app.services.model_resolver.resolve_service_model",
               return_value={"endpoint": "bedrock", "model_id": "m"}):
        res = await run_post_conversation_extraction(HISTORY, conversation_id="c1", project_path="/w/p")
    assert outage.await_count >= 1, "the model was never reached"
    assert res.get("skipped") and res.get("reason") == "memory_paused"
    assert _store_hash() == before
    after = memory_clock.load()
    assert (after["global"], after["projects"].get("/w/p", 0)) == (clock_before["global"], clock_before["projects"].get("/w/p", 0)), "a session that never ran was counted"
    rec = mp.status()
    assert rec and rec["kind"] == "authentication_error" and "extraction" in rec["where"][0]


@pytest.mark.asyncio
async def test_paused_entry_points_skip_without_calling_and_resume_restores():
    from app.memory.extractor import run_post_conversation_extraction
    from app.memory.feedback import apply_feedback
    from app.memory.maintenance import run_post_save_maintenance
    from app.memory.prompt import get_memory_prompt_sections
    from app.agents import system_jobs as sj
    mp.pause(ServiceInfraError(CredentialRetrievalError("expired")), "test")
    live = AsyncMock(return_value="[]")
    with patch("app.services.model_resolver._call_bedrock", live), \
         patch("app.services.model_resolver.resolve_service_model",
               return_value={"endpoint": "bedrock", "model_id": "m"}):
        assert (await run_post_conversation_extraction(HISTORY, conversation_id="c2"))["reason"] == "memory_paused"
        assert (await apply_feedback("c2", "some reply"))["reason"] == "memory_paused"
        assert run_post_save_maintenance("m_none")["reason"] == "memory_paused"
        assert sj._memory_lifecycle_gate() is False and sj._memory_organize_gate() is False
    assert live.await_count == 0
    assert "paused since" in get_memory_prompt_sections()[1]

    assert mp.resume()["kind"] == "authentication_error"
    assert mp.status() is None and mp.resume() is None
    assert "paused since" not in get_memory_prompt_sections()[1]
    with patch("app.services.model_resolver._call_bedrock", live), \
         patch("app.services.model_resolver.resolve_service_model",
               return_value={"endpoint": "bedrock", "model_id": "m"}):
        res = await run_post_conversation_extraction(HISTORY, conversation_id="c3", project_path="/w/p")
    assert not res.get("skipped"), res
    assert live.await_count >= 1                # positive: extraction ran again


def test_pause_is_a_circuit_breaker_not_a_latch(monkeypatch):
    """After the probe wait the next entry point runs for real; success clears
    the pause on its own, another outage re-arms it with a doubled wait."""
    mp.resume()                                            # start unpaused
    calls = {"n": 0}
    outage = {"on": False}

    @mp.guarded("probe")
    def entry():
        calls["n"] += 1
        if outage["on"]:
            raise ServiceInfraError(ConnectionError("down"), where="probe")
        return {"ok": True}

    assert entry()["ok"]                                   # healthy: runs
    outage["on"] = True
    assert entry()["reason"] == "memory_paused"            # outage: pauses (1 call)
    assert entry()["reason"] == "memory_paused" and calls["n"] == 2  # within wait: skipped
    # Age the pause past the first probe wait.
    rec = mp.status(); rec["last_seen_at"] -= (mp.PROBE_BASE_S + 1) * 1000
    mp._path().write_text(json.dumps(rec))
    assert entry()["reason"] == "memory_paused" and calls["n"] == 3  # probed, still down
    assert mp.status()["repeat_count"] == 1                # wait doubled
    assert mp.probe_due() is False and mp.probe_wait_s(mp.status()) == 2 * mp.PROBE_BASE_S
    rec = mp.status(); rec["last_seen_at"] -= (2 * mp.PROBE_BASE_S + 1) * 1000
    mp._path().write_text(json.dumps(rec))
    outage["on"] = False
    assert entry()["ok"] and calls["n"] == 4               # probed, back up
    assert mp.status() is None                             # auto-resumed
    assert "retries automatically" not in (mp.prompt_notice() or "")


def test_prompt_notice_says_it_retries():
    mp.pause(ServiceInfraError(ConnectionError("down")), "test")
    assert "retries automatically" in mp.prompt_notice()
    mp.resume()


def test_api_resume_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.memory import router
    app = FastAPI(); app.include_router(router)
    tc = TestClient(app)
    assert tc.get("/api/v1/memory/pause").json() == {"paused": False, "record": None}
    mp.pause(ServiceInfraError(ConnectionError("down")), "test")
    assert tc.get("/api/v1/memory/pause").json()["paused"] is True
    assert tc.post("/api/v1/memory/resume").json()["resumed"] is True
    assert tc.post("/api/v1/memory/resume").json()["resumed"] is False
