"""Memory paths must not make Bedrock embedding calls (or memory-store I/O) on
the event loop.

Follow-on to tests/test_lifecycle_not_event_loop_blocking.py.  An audit of
every ``async def`` reaching ``embed_and_cache`` / ``embed_text`` /
``semantic_search`` / ``get_embedding_provider`` (first call is a Bedrock
smoke test) without a thread hop found:

* all seven memory MCP tools: zero-await ``execute()``, so memory_search
  (query embed + one re-embed per result via ``save_many``), memory_save
  and memory_propose ran their Bedrock calls inline;
* ``extractor.run_post_conversation_extraction``: ``deduplicate`` embeds
  every candidate inline, plus inline saves/adds;
* ``feedback.apply_feedback``: counter bumps went through ``save_many``,
  which re-embedded every loaded memory;
* the organize/REM/harvest coroutines and three first-call provider probes.

Two storage changes back the fix, and are tested here directly:

* ``save``/``save_many`` skip the re-embed when content is unchanged and a
  vector is cached (a counter bump is not a reason to call Bedrock);
* ``patch_many`` patches fields on the FRESH record under the file lock.
  Off the loop, "load objects, bump, save_many(objects)" could write a
  stale whole record back: resurrect a memory deleted in between, or undo
  an archive.  Counter bumps use ``patch_many`` instead.
"""
import ast
import asyncio
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Probe:
    def __init__(self, result=None):
        self.result = result
        self.thread = None
        self.calls = 0

    def __call__(self, *a, **kw):
        self.thread = threading.current_thread()
        self.calls += 1
        return self.result


def _assert_off_loop(probe, what):
    assert probe.calls >= 1, f"{what}: probe never ran -- the seam was not exercised"
    assert probe.thread is not threading.main_thread(), (
        f"{what} ran on the event-loop thread; use asyncio.to_thread")


# ── 1. static guard over every coroutine the audit found ────────────────────

# Calls that reach Bedrock or the memory/proposal store.  Generic method names
# only count on a store-looking receiver.
_BLOCKING = {"deduplicate", "embed_and_cache", "embed_text", "semantic_search",
             "get_embedding_provider", "find_similar_memories", "_archive_memory",
             "_apply_updates", "_bump_loaded_only"}
_STORE_METHODS = {"save", "save_many", "patch_many", "search", "add"}
_STORE_RECEIVERS = {"store", "proposals", "proposals_store", "ps"}
# ProposalsStore reads/decrypts/rewrites its event log on each of these.
_PROPOSAL_METHODS = {"get", "record_signal", "corroborate_by_id", "list_open"}
_PROPOSAL_RECEIVERS = {"proposals", "proposals_store", "ps"}
_OFFLOAD = {"to_thread", "run_in_executor"}

_TARGETS = [
    ("app/memory/extractor.py", "run_post_conversation_extraction"),
    ("app/memory/feedback.py", "apply_feedback"),
    ("app/memory/organizer.py", "cleanup_corpus"),
    ("app/memory/rem.py", "detect_staleness"),
    ("app/memory/rem.py", "synthesize_node"),
    ("app/agents/run_memory_harvest.py", "_harvest"),
    ("app/server.py", "_initialize_memory_background"),
    ("app/api/memory.py", "backfill_embeddings"),
    ("app/services/embedding_service.py", "backfill_embeddings"),
]


def _inline_blocking_calls(fn):
    found = []

    def visit(node, offloaded):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            off = offloaded
            if isinstance(ch, ast.Call):
                f = ch.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                recv = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ""
                if name in _OFFLOAD:
                    off = True
                elif not offloaded and (name in _BLOCKING or (
                        name in _STORE_METHODS and recv in _STORE_RECEIVERS) or (
                        name in _PROPOSAL_METHODS and recv in _PROPOSAL_RECEIVERS)):
                    found.append(f"L{ch.lineno} {ast.unparse(f)}")
            visit(ch, off)
    visit(fn, False)
    return found


@pytest.mark.parametrize("path,func", _TARGETS)
def test_coroutine_makes_no_inline_blocking_memory_call(path, func):
    tree = ast.parse((ROOT / path).read_text())
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.AsyncFunctionDef) and n.name == func]
    assert fns, f"async {func} not found in {path} -- renamed?"
    offenders = [c for fn in fns for c in _inline_blocking_calls(fn)]
    assert offenders == [], (
        f"{path}:{func} calls these on the event loop:\n  " + "\n  ".join(offenders))


def test_guard_is_not_vacuous():
    src = "async def f(store):\n    store.save(x)\n    await asyncio.to_thread(store.save, x)\n"
    fn = ast.parse(src).body[0]
    assert _inline_blocking_calls(fn) == ["L2 store.save"]


# ── 2. memory MCP tools ─────────────────────────────────────────────────────

def _tool_classes():
    from app.mcp.tools import memory_tools as mt
    from app.mcp.tools.base import BaseMCPTool
    return [c for c in vars(mt).values()
            if isinstance(c, type) and issubclass(c, BaseMCPTool)
            and c.__module__ == mt.__name__ and c.__name__.endswith("Tool")]


def test_all_seven_memory_tools_found():
    assert len(_tool_classes()) == 7


@pytest.mark.parametrize("cls", _tool_classes(), ids=lambda c: c.__name__)
def test_tool_body_runs_off_the_loop(cls, monkeypatch):
    probe = _Probe({"ok": True})
    monkeypatch.setattr(cls, "_execute_sync", lambda self, **kw: probe(**kw),
                        raising=False)
    out = _run(cls().execute(query="x"))
    assert out == {"ok": True}
    _assert_off_loop(probe, f"{cls.__name__}.execute")


def test_memory_search_real_body_searches_off_the_loop(monkeypatch):
    """Drive the real search body, not just the wrapper."""
    import app.storage.memory as sm
    import app.storage.proposals as sp
    from app.mcp.tools.memory_tools import MemorySearchTool
    probe = _Probe([])

    class _Store:
        def list_memories(self, **kw):
            return []
        search = staticmethod(probe)

        def list_mindmap_nodes(self):
            return []

    class _Props:
        def list_open(self):
            return []

    monkeypatch.setattr(sm, "get_memory_storage", lambda: _Store())
    monkeypatch.setattr(sp, "get_proposals_store", lambda: _Props())
    out = _run(MemorySearchTool().execute(query="widget"))
    assert out.get("count") == 0
    _assert_off_loop(probe, "MemorySearchTool store.search")


# ── 3. feedback ─────────────────────────────────────────────────────────────

def test_apply_feedback_persists_off_the_loop(monkeypatch):
    from app.memory import feedback as fb
    probe = _Probe(None)
    monkeypatch.setattr(fb, "_apply_updates", probe)
    fb.record_load("conv-off-loop", ["m_1"])
    out = _run(fb.apply_feedback("conv-off-loop", ""))
    assert out.get("loaded") == 1, out
    _assert_off_loop(probe, "feedback._apply_updates")


# ── 4. storage: no needless re-embed, field patches don't resurrect ─────────

class _Cache:
    def __init__(self):
        self.vecs = {}

    def get(self, mid):
        return self.vecs.get(mid)


@pytest.fixture
def mstore(tmp_path, monkeypatch):
    import app.services.embedding_service as es
    from app.storage.memory import MemoryStorage
    cache = _Cache()
    embeds = []

    def fake_embed(mid, content):
        embeds.append(mid)
        cache.vecs[mid] = [1.0]

    monkeypatch.setattr(es, "embed_and_cache", fake_embed)
    monkeypatch.setattr(es, "get_embedding_cache", lambda: cache)
    return MemoryStorage(memory_dir=tmp_path), embeds


def _mem(content):
    from app.models.memory import Memory
    return Memory(content=content, layer="domain_context")


def test_save_skips_embed_when_content_unchanged(mstore):
    store, embeds = mstore
    m = store.save(_mem("alpha fact"))
    assert embeds == [m.id]                     # created: embedded
    m.importance = 0.9
    store.save(m)
    assert embeds == [m.id], "counter-only save re-embedded"
    m.content = "alpha fact, revised"
    store.save(m)
    assert embeds == [m.id, m.id]               # content changed: embedded


def test_save_many_skips_embed_when_content_unchanged(mstore):
    store, embeds = mstore
    a, b = store.save(_mem("a fact")), store.save(_mem("b fact"))
    embeds.clear()
    a.importance = 0.8
    b.content = "b fact, revised"
    store.save_many([a, b])
    assert embeds == [b.id]


def test_patch_many_updates_fresh_record_and_never_resurrects(mstore):
    store, embeds = mstore
    keep, gone = store.save(_mem("kept fact")), store.save(_mem("deleted fact"))
    embeds.clear()
    assert store.delete(gone.id)

    def bump(rec):
        rec["corroborations"] = (rec.get("corroborations") or 0) + 1

    n = store.patch_many({keep.id: bump, gone.id: bump})
    assert n == 1
    assert store.get(gone.id) is None, "patch resurrected a deleted memory"
    assert store.get(keep.id).corroborations == 1
    assert embeds == []
