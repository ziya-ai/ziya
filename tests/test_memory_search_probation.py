"""
memory_search falls through to the PROBATIONARY store on a miss — and
surfaces matches labelled as unverified instead of promoting them.

Prior behaviour (still visible at git HEAD, guarded below): on a miss the
tool substring-matched the legacy ``proposals.json`` queue and called
``approve_proposal`` on every hit, promoting straight to the active store
and bypassing probation entirely.  The live store had 15 stale rows in
the legacy file and 266 open rows in the probationary store, so the path
was both wrong (bypass) and dead (wrong file).

Now:
  * a miss with a matching OPEN proposal returns it, labelled
    "[probationary — unverified]", with a ``probationary`` count
  * a ``search_hit`` signal is recorded on the proposal
  * the proposal stays ``open`` and NOTHING is written to memories.json
  * every query token must match (a broad query cannot dump the queue)
  * a genuine miss still reports ``out_of_domain``
  * lifecycle: search_hit + response_match promotes ("searched_and_used");
    search_hit alone does NOT
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from app.models.memory import MemoryProposal


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Real MemoryStorage + ProposalsStore on tmp dirs, wired as singletons."""
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    import app.storage.memory as mem_mod
    import app.storage.proposals as prop_mod

    mem = MemoryStorage(tmp_path / "mem")
    props = ProposalsStore(tmp_path / "mem")
    monkeypatch.setattr(mem_mod, "_instance", mem)
    monkeypatch.setattr(prop_mod, "_instance", props)
    # Embeddings off: keyword-only search path, deterministic miss.
    monkeypatch.setattr(
        "app.services.embedding_service.semantic_search",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no embeddings")),
        raising=False,
    )
    return mem, props


def _open_proposal(props, content, tags=()):
    p = MemoryProposal(content=content, tags=list(tags), layer="architecture")
    pid = props.add(p, activity_count=0)
    return pid


async def _search(query):
    from app.mcp.tools.memory_tools import MemorySearchTool
    return await MemorySearchTool().execute(query=query)


# ── memory_search fallthrough ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_miss_surfaces_open_proposal_without_promoting(stores):
    mem, props = stores
    pid = _open_proposal(props, "The phaser scheduler uses DWRR across five classes.",
                         tags=["phaser", "dwrr"])

    out = await _search("phaser dwrr")

    assert out.get("probationary") == 1 and out["count"] == 1
    assert pid in out["content"]
    assert "unverified" in out["content"]
    assert "auto_promoted" not in out
    # Still on probation; nothing landed in the active store.
    row = props.get(pid)
    assert row and row["status"] == "open"
    assert mem.list_memories(status="active") == []
    # Signal recorded for the lifecycle engine.
    assert any(s.get("name") == "search_hit" for s in row.get("signals", []))


@pytest.mark.asyncio
async def test_broad_query_does_not_dump_queue(stores):
    """Every query token must appear — a one-token overlap is not a hit."""
    _, props = stores
    _open_proposal(props, "The phaser scheduler uses DWRR across five classes.")

    out = await _search("phaser bananas")
    assert out.get("out_of_domain") is True
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_genuine_miss_is_out_of_domain(stores):
    _, props = stores
    _open_proposal(props, "Unrelated fact about lunar tides.")
    out = await _search("kubernetes ingress")
    assert out.get("out_of_domain") is True
    assert "probationary" not in out


@pytest.mark.asyncio
async def test_active_hit_takes_precedence_over_probation(stores):
    """The fallthrough only runs on a MISS in the active store."""
    mem, props = stores
    from app.models.memory import Memory
    mem.save(Memory(content="Phaser DWRR active memory.", tags=["phaser"]))
    _open_proposal(props, "Phaser DWRR probationary duplicate.")

    out = await _search("phaser dwrr")
    assert "probationary" not in out
    assert out["count"] == 1
    assert "unverified" not in out["content"]


# ── lifecycle rule ───────────────────────────────────────────────────────────

def _prop(signals):
    return {"id": "prop_x", "content": "c", "layer": "domain_context",
            "corroborations": 0, "signals": signals}


def test_search_hit_plus_use_promotes():
    from app.memory.lifecycle import _evaluate_promotion
    p = _prop([{"name": "search_hit"}, {"name": "response_match"}])
    assert _evaluate_promotion(p) == "searched_and_used"


def test_search_hit_alone_does_not_promote():
    """Being SHOWN once is not evidence of correctness."""
    from app.memory.lifecycle import _evaluate_promotion
    assert _evaluate_promotion(_prop([{"name": "search_hit"}])) is None
    assert _evaluate_promotion(_prop([{"name": "search_hit"}] * 3)) is None


def test_searched_and_used_rule_appears_once():
    """Guard against the duplicated rule that landed on first apply."""
    import inspect
    from app.memory import lifecycle
    src = inspect.getsource(lifecycle._evaluate_promotion)
    assert src.count('"searched_and_used"') == 1


# ── HEAD guard ───────────────────────────────────────────────────────────────

def test_regresses_on_prefix_source():
    try:
        head = subprocess.run(
            ["git", "show", "HEAD:app/mcp/tools/memory_tools.py"],
            capture_output=True, text=True, check=True,
        ).stdout
    except Exception:
        pytest.skip("no git HEAD")
    if "approve_proposal" not in head:
        pytest.skip("HEAD already carries the fix")
    from app.mcp.tools import memory_tools
    import inspect
    cur = inspect.getsource(memory_tools)
    assert "approve_proposal" not in cur
    assert 'name="search_hit"' in cur
