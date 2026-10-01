"""
Per-domain aging for probationary proposals (app/utils/memory_clock.py).

The global activity counter advanced on every streamed response in every
project: ~44 ticks per active day on the live store, 85% of them Ziya, so
ARCHIVAL_AGE_THRESHOLD=7 was about four hours of any work and Kuiper
proposals decayed on Ziya days before a Kuiper conversation could confirm
them.

The anchor test drives the real run_lifecycle_pass: a Kuiper proposal must
survive 50 Ziya sessions (it is archived as "decayed" on the global-counter
code) and decay after 7 Kuiper sessions (positive control that aging still
happens).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import app.utils.memory_clock as mc
from app.memory.lifecycle import ARCHIVAL_AGE_THRESHOLD, FAST_TRACK_MIN_AGE

K, Z, ZN = "/w/KuiperEAR-switch-mgr", "/w/ziya", "/w/ZiyaInternal"
DAY = 86400_000
T0 = 1_790_000_000_000


@pytest.fixture(autouse=True)
def clock_file(tmp_path, monkeypatch):
    p = tmp_path / "memory" / "domain_clocks.json"
    monkeypatch.setattr(mc, "_default_path", p)
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    return p


# ── ticking ───────────────────────────────────────────────────────────


def test_one_tick_per_conversation_per_day():
    for _ in range(40):                                 # 40 responses, one session
        mc.tick_session("c1", K, now_ms=T0)
    assert mc.load()["projects"][K] == 1
    mc.tick_session("c2", K, now_ms=T0)                 # parallel job: new session
    mc.tick_session("c1", K, now_ms=T0 + DAY)           # same chat next day
    s = mc.load()
    assert s["projects"][K] == 3 and s["global"] == 3


def test_snapshot_is_post_tick():
    stamp = mc.tick_session("c1", K, now_ms=T0)
    assert stamp["projects"][K] == 1
    ctx = mc.build_context({}, now_ms=T0)
    assert mc.domain_age(stamp, [K], ctx) == 0          # own session is not a confirmation


# ── age arithmetic ────────────────────────────────────────────────────


def test_age_counts_only_own_collection():
    stamp = mc.snapshot(now_ms=T0)
    for i in range(5):
        mc.tick_session(f"z{i}", Z, now_ms=T0)
    mc.tick_session("zn", ZN, now_ms=T0)
    mc.tick_session("k", K, now_ms=T0)
    ctx = mc.build_context({Z: Z, ZN: Z, K: K}, now_ms=T0)
    assert mc.domain_age(stamp, [K], ctx) == 1
    assert mc.domain_age(stamp, [Z], ctx) == 6          # ZiyaInternal is the same collection
    assert mc.domain_age(stamp, [], ctx) == 7           # no project -> global sessions


def test_legacy_proposal_keeps_global_counter():
    ctx = mc.build_context({}, now_ms=T0)
    assert mc.effective_counter({"activity_count_at_proposal": 3}, 50, ctx) == 50
    assert mc.effective_counter({"activity_count_at_proposal": 3,
                                 "domain_clock": mc.snapshot(now_ms=T0)}, 50, ctx) == 3


def test_dormant_backstop():
    p = {"domain_clock": mc.snapshot(now_ms=T0), "scope": {"project_paths": [K]}}
    assert not mc.is_dormant(p, mc.build_context({}, now_ms=T0 + 89 * DAY))
    assert mc.is_dormant(p, mc.build_context({}, now_ms=T0 + 91 * DAY))
    mc.tick_session("k", K, now_ms=T0 + DAY)            # any session in the domain resets it
    assert not mc.is_dormant(p, mc.build_context({}, now_ms=T0 + 91 * DAY))


def test_malformed_clock_file_reads_empty(clock_file):
    clock_file.parent.mkdir(parents=True, exist_ok=True)
    clock_file.write_text(json.dumps({"count": 10}))    # the old counter's shape
    assert mc.load() == mc._empty()


# ── seam: the real lifecycle sweep ────────────────────────────────────


async def _sweep(tmp_path, proposals, global_counter):
    from app.memory.lifecycle import run_lifecycle_pass
    from app.storage.memory import MemoryStorage
    store = MemoryStorage(memory_dir=tmp_path / "mem")
    with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
         patch("app.storage.memory.get_memory_storage", return_value=store), \
         patch("app.memory.lifecycle.current_activity_count", return_value=global_counter), \
         patch("app.services.embedding_service.get_embedding_cache",
               return_value=MagicMock(get=MagicMock(return_value=None))):
        return await run_lifecycle_pass()


@pytest.mark.asyncio
async def test_kuiper_proposal_survives_other_domain_work(tmp_path):
    from app.models.memory import MemoryProposal, MemoryScope
    from app.storage.proposals import ProposalsStore
    proposals = ProposalsStore(memory_dir=tmp_path / "props")
    stamp = mc.tick_session("k0", K)
    p = MemoryProposal(content="QoS Phase 1 adds ground-side congestion control.",
                       layer="domain_context", scope=MemoryScope(project_paths=[K]))
    p.domain_clock = stamp
    with patch("app.services.embedding_service.embed_and_cache"):
        pid = proposals.add(p, activity_count=100)

    for i in range(50):
        mc.tick_session(f"z{i}", Z)
    await _sweep(tmp_path, proposals, 150)
    assert proposals.get(pid)["status"] == "open"

    for i in range(ARCHIVAL_AGE_THRESHOLD):
        mc.tick_session(f"k{i + 1}", K)
    await _sweep(tmp_path, proposals, 157)
    assert proposals.get(pid)["status"] == "archived"


# ── seam: the proposals API reports the sweep's own decision ──────────


def test_api_would_promote_uses_domain_counter(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.memory import router
    from app.models.memory import MemoryProposal, MemoryScope
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    proposals = ProposalsStore(memory_dir=tmp_path / "props")
    p = MemoryProposal(content="Gen2 GA caps each merlin at 10 spots in 500 MHz mode.",
                       layer="decision", scope=MemoryScope(project_paths=[K]),
                       quality=0.9)
    p.domain_clock = mc.snapshot()
    with patch("app.services.embedding_service.embed_and_cache"):
        proposals.add(p, activity_count=100)
    for i in range(FAST_TRACK_MIN_AGE):
        mc.tick_session(f"k{i}", K)
    app = FastAPI(); app.include_router(router)
    with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
         patch("app.storage.memory.get_memory_storage",
               return_value=MemoryStorage(memory_dir=tmp_path / "mem")), \
         patch("app.memory.lifecycle.current_activity_count", return_value=100):
        rows = TestClient(app).get("/api/v1/memory/proposals").json()
    assert rows[0]["age"] == FAST_TRACK_MIN_AGE
    assert rows[0]["would_promote"] == "quality_fast_track"


# ── seam: every proposal source stamps the clock ──────────────────────


def test_proposal_sources_are_wired():
    import inspect
    import app.memory.extractor as ex
    import app.mcp.tools.memory_tools as mt
    src = inspect.getsource(ex.run_post_conversation_extraction)
    assert "tick_session(conversation_id, project_path)" in src
    assert src.count("proposal.domain_clock = _domain_clock") == 2
    assert "proposal.domain_clock = clock_snapshot()" in inspect.getsource(mt)
