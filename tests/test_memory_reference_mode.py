"""Seam test for the headless reference-set mode.

ZIYA_MEMORY_DIR must redirect *every* memory artifact (six modules compute
the path independently), and ZIYA_MEMORY_READONLY must silence *every*
writer and skip extraction.  The assertion is on the directory: byte-for-
byte unchanged after a turn's worth of writes.  A writer that bypasses the
guard shows up as a changed or new file, whichever module it lives in.
"""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.memory import Memory, MindMapNode


def _snapshot(d: Path):
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture(autouse=True)
def restore_memory_env():
    """_setup_memory_store writes ZIYA_MEMORY_DIR / ZIYA_MEMORY_READONLY into
    os.environ directly.  monkeypatch.delenv(raising=False) on an absent key
    records nothing, so without this the read-only flag survives teardown and
    silences every memory writer in the rest of the session."""
    keys = ("ZIYA_MEMORY_DIR", "ZIYA_MEMORY_READONLY")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def reset_singletons():
    """The store, proposals and embedding cache are process singletons keyed
    on the env at first use; reset so each test sees its own ZIYA_MEMORY_DIR."""
    import app.storage.memory as sm
    import app.storage.proposals as sp
    import app.services.embedding_service as es
    saved = (sm._instance, getattr(sp, "_instance", None), es._cache)
    sm._instance = None
    if hasattr(sp, "_instance"):
        sp._instance = None
    es._cache = None
    yield
    sm._instance, _p, es._cache = saved
    if hasattr(sp, "_instance"):
        sp._instance = _p


def test_memory_dir_env_redirects_every_artifact(tmp_path, monkeypatch, reset_singletons):
    home = tmp_path / "home"
    ref = tmp_path / "ref"
    monkeypatch.setenv("ZIYA_HOME", str(home))
    monkeypatch.setenv("ZIYA_MEMORY_DIR", str(ref))
    monkeypatch.delenv("ZIYA_MEMORY_READONLY", raising=False)

    from app.storage.memory import get_memory_storage
    from app.storage.proposals import ProposalsStore
    from app.services.embedding_service import get_embedding_cache
    from app.utils import memory_clock
    from app.memory import feedback, organize_history
    from app.models.memory import MemoryProposal

    get_memory_storage().save(Memory(id="m_1", content="A fact in the reference store."))
    get_memory_storage().save_mindmap_node(MindMapNode(id="d", handle="d"))
    ProposalsStore().add(MemoryProposal(content="A proposal in the reference store."))
    assert get_embedding_cache()._file.parent == ref
    memory_clock.tick_session("c1", "/p")
    feedback._record_feedback_stats([{"best": 0.5}])
    organize_history.append_organize_result({"cleanup": {}, "bootstrap": {}})

    names = set(_snapshot(ref))
    assert {"memories.json", "mindmap.json", "probationary.jsonl",
            "domain_clocks.json", "feedback_stats.jsonl", "organize_history.json"} <= names
    # Nothing memory-shaped landed under the home directory.
    assert not (home / "memory").exists() or _snapshot(home / "memory") == {}


def test_readonly_silences_every_writer_and_skips_extraction(tmp_path, monkeypatch, reset_singletons):
    ref = tmp_path / "ref"
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ZIYA_MEMORY_DIR", str(ref))
    monkeypatch.delenv("ZIYA_MEMORY_READONLY", raising=False)

    # Seed with writes allowed, as setup_environment does.
    from app.utils.memory_import import seed_store
    seed_store(ref, {"format": "ziya-memory-export", "version": 1,
                     "memories": [{"id": "m_ref", "content": "Reference knowledge.", "layer": "decision"}],
                     "mindmap": [{"id": "domain_ref", "handle": "ref", "memory_refs": ["m_ref"]}]})
    before = _snapshot(ref)
    assert before, "seeding wrote nothing"

    monkeypatch.setenv("ZIYA_MEMORY_READONLY", "1")
    import app.storage.memory as sm
    sm._instance = None
    from app.storage.memory import get_memory_storage
    from app.storage.proposals import ProposalsStore
    from app.services.embedding_service import get_embedding_cache
    from app.utils import memory_clock
    from app.memory import feedback, organize_history
    from app.models.memory import MemoryProposal
    import numpy as np

    store = get_memory_storage()
    # Reads work: the reference set is visible.
    assert [m.id for m in store.list_memories()] == ["m_ref"]
    assert store.get_mindmap_node("domain_ref") is not None

    # Every writer runs without error and changes nothing on disk.
    store.save(Memory(id="m_new", content="Learned during the run."))
    store.save_mindmap_node(MindMapNode(id="d2", handle="d2"))
    store.delete("m_ref")
    ProposalsStore().add(MemoryProposal(content="Proposed during the run."))
    cache = get_embedding_cache()
    cache.put("m_ref", np.ones(cache._dim if hasattr(cache, "_dim") else 1024, dtype=np.float32))
    cache.flush()
    tick = memory_clock.tick_session("c1", "/p")
    assert tick["session_key"] is None
    feedback._record_feedback_stats([{"best": 0.5}])
    organize_history.append_organize_result({"cleanup": {}, "bootstrap": {}})

    assert _snapshot(ref) == before

    # Extraction is skipped before any model call.
    import asyncio
    from app.memory.extractor import run_post_conversation_extraction
    with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
         patch("app.services.model_resolver.call_service_model",
               side_effect=AssertionError("extraction model must not be called")):
        res = asyncio.run(run_post_conversation_extraction(
            [{"role": "human", "content": "x"}] * 6, "conv-ro"))
    assert res == {"skipped": True, "reason": "memory_readonly"}
    assert _snapshot(ref) == before


def test_setup_environment_seeds_bundle_and_sets_flags(tmp_path, monkeypatch, reset_singletons):
    """The CLI/server flags reach the env in the order the store needs:
    seed with writes allowed, then lock."""
    import argparse
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ZIYA_MEMORY_DIR", raising=False)
    monkeypatch.delenv("ZIYA_MEMORY_READONLY", raising=False)
    bundle = tmp_path / "ref.json"
    bundle.write_text(json.dumps({"format": "ziya-memory-export", "version": 1,
                                  "memories": [{"id": "m_ref", "content": "Reference knowledge."}],
                                  "mindmap": []}))
    from app.config.environment import _setup_memory_store
    args = argparse.Namespace(memory_dir=str(tmp_path / "job"), memory_bundle=str(bundle),
                              memory_readonly=True)
    _setup_memory_store(args)
    assert os.environ["ZIYA_MEMORY_DIR"] == str(tmp_path / "job")
    assert os.environ["ZIYA_MEMORY_READONLY"] == "1"
    rows = json.loads((tmp_path / "job" / "memories.json").read_text())
    assert [r["id"] for r in rows] == ["m_ref"]
    assert rows[0]["imported_from"]["source"] == str(bundle)

    # Bundle without a dir gets a temp dir; a bad bundle exits non-zero.
    args = argparse.Namespace(memory_dir=None, memory_bundle=str(bundle), memory_readonly=False)
    # Plain pop, not monkeypatch.delenv: _setup_memory_store set this directly,
    # and delenv would record "1" as the value to put back at teardown.
    os.environ.pop("ZIYA_MEMORY_READONLY", None)
    # The implicit temp store must be registered for removal at exit, so
    # repeated headless runs do not accumulate ziya-memory-* dirs in $TMPDIR.
    import atexit
    handlers = []
    monkeypatch.setattr(atexit, "register",
                        lambda fn, *a, **kw: handlers.append((fn, a, kw)) or fn)
    _setup_memory_store(args)
    tmp_store = os.environ["ZIYA_MEMORY_DIR"]
    assert tmp_store != str(tmp_path / "job")
    assert Path(tmp_store, "memories.json").exists()
    assert "ZIYA_MEMORY_READONLY" not in os.environ
    assert len(handlers) == 1, "temp memory store was not scheduled for removal"
    fn, a, kw = handlers[0]
    fn(*a, **kw)
    assert not Path(tmp_store).exists()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SystemExit):
        _setup_memory_store(argparse.Namespace(memory_dir=str(tmp_path / "j2"),
                                               memory_bundle=str(bad), memory_readonly=False))
