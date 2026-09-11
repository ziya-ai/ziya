"""
Tests for the global-organizer redesign (§3 of Docs/design/memory-redesign.md).

The organizer now enforces four structural invariants on every ``reorganize``
via a deterministic (no-LLM) consolidation pass:

  * depth <= 2 (roots + one level of children, no grandchildren);
  * occupancy >= MIN_NODE_OCCUPANCY (2) per node (roots count descendants),
    with fold-into-parent (children) / fold-into-sibling-or-general (roots);
  * bounded root count (<= MAX_ROOTS), with a target ≈ memories / 4;
  * every memory reachable from exactly one node (no orphan, no double-parent).

``should_auto_organize`` now triggers on structure debt (root explosion,
hollow map), and ``maintenance.maybe_divide_node`` gained depth + occupancy
guards.

These tests FAIL on the stage-4 backup (backup organizer.py has no
``consolidate_mindmap`` / ``MIN_NODE_OCCUPANCY`` / structure-debt
``should_auto_organize``; they cannot even import) and PASS on the change.
The 40-memory case is a SEAM test: it drives the public ``reorganize`` entry
and asserts on the STORED mindmap, not on a helper's return value.
"""
from unittest.mock import patch

import pytest

from app.models.memory import Memory, MemoryScope, MindMapNode
# Importing these new symbols is itself the fail-on-backup guard: the stage-4
# backup organizer.py defines none of them, so collection errors there.
from app.memory.organizer import (
    reorganize,
    consolidate_mindmap,
    should_auto_organize,
    _find_matching_node,
    _n_target,
    MIN_NODE_OCCUPANCY,
    MAX_ROOTS,
    _GENERAL_ROOT_ID,
    AUTO_ORGANIZE_ORPHAN_THRESHOLD,
)


# ── helpers ─────────────────────────────────────────────────────────────

def _store(tmp_path):
    from app.storage.memory import MemoryStorage
    return MemoryStorage(memory_dir=tmp_path / "memory")


def _add_mem(store, mid, tags, layer="domain_context", domain_node=None):
    m = Memory(id=mid, content=f"Durable fact {mid} about {','.join(tags) or 'x'}.",
               layer=layer, tags=list(tags),
               scope=MemoryScope(domain_node=domain_node))
    store.save(m)
    return m


def _add_node(store, nid, handle, tags, refs, parent=None):
    node = MindMapNode(id=nid, handle=handle, parent=parent,
                       tags=list(tags), memory_refs=list(refs))
    store.save_mindmap_node(node)
    return node


def _depth(store, node):
    d, cur, guard = 0, node, 0
    while cur is not None and cur.parent:
        d += 1
        cur = store.get_mindmap_node(cur.parent)
        guard += 1
        if guard > 20:
            break
    return d


def _placements(store):
    """memory_id -> list of node ids that reference it."""
    out = {}
    for n in store.list_mindmap_nodes():
        for mid in n.memory_refs:
            out.setdefault(mid, []).append(n.id)
    return out


def _noop_llm_patches():
    """Neutralize every LLM-backed / disk-logging phase of reorganize so the
    seam test exercises only bootstrap (a no-op when all memories are placed)
    and the deterministic consolidation pass."""
    async def _aget(*a, **k):
        return {}

    return [
        patch("app.memory.organizer.cleanup_corpus", _aget),
        patch("app.memory.organizer.extract_all_relations", _aget),
        patch("app.memory.rem.rem_phase", _aget),
        patch("app.memory.organize_history.append_organize_result",
              lambda *a, **k: None),
    ]


# ── 1. Seam: 40 memories / 5 clusters organize to a compact map ─────────

@pytest.mark.asyncio
async def test_reorganize_synthetic_40_compact_no_empty_depth2(tmp_path):
    """A 40-memory store pre-fragmented into 10 small roots (2 per cluster)
    organizes — through the public reorganize entry — to <= 10 non-empty
    nodes at depth <= 2, with the fragmented per-cluster roots merged."""
    store = _store(tmp_path)
    clusters = ["alpha", "beta", "gamma", "delta", "omega"]
    # 5 clusters × 8 memories = 40, each cluster split across 2 roots of 4.
    for ci, ctag in enumerate(clusters):
        for half in (0, 1):
            nid = f"domain_{ctag}_{half}"
            refs = []
            for k in range(4):
                mid = f"m_{ctag}_{half}_{k}"
                _add_mem(store, mid, tags=[ctag, f"{ctag}sub"],
                         layer="architecture", domain_node=nid)
                refs.append(mid)
            # Both halves of a cluster share identical tags → similar-root
            # merge fuses them (tag Jaccard 1.0 + shared handle word).
            _add_node(store, nid, handle=f"{ctag.title()} Topic",
                      tags=[ctag, f"{ctag}sub"], refs=refs)

    assert len([n for n in store.list_mindmap_nodes() if n.parent is None]) == 10

    patches = _noop_llm_patches()
    for p in patches:
        p.start()
    try:
        await reorganize(store)
    finally:
        for p in patches:
            p.stop()

    nodes = store.list_mindmap_nodes()
    roots = [n for n in nodes if n.parent is None]
    active_ids = {m.id for m in store.list_memories(status="active")}

    # zero empty nodes
    assert all(n.memory_refs or n.children for n in nodes)
    # depth <= 2
    assert all(_depth(store, n) <= 2 for n in nodes)
    # compact: total nodes <= 10, roots within target/bound
    assert len(nodes) <= 10
    assert len(roots) <= _n_target(len(active_ids))
    # the 10 fragmented roots collapsed to the 5 clusters
    assert len(roots) == 5
    # every memory reachable from exactly one node
    placements = _placements(store)
    assert all(len(v) == 1 for v in placements.values())
    assert set(placements) == active_ids


# ── 2. 97 singleton roots merge below the bound ─────────────────────────

def test_97_singleton_roots_merge_below_bound(tmp_path):
    """97 memories each in their own singleton root consolidate to a count
    within the hard bound, with zero empty nodes and single parentage."""
    store = _store(tmp_path)
    # 97 memories spread over 6 tag groups; each memory is its own root.
    groups = ["red", "green", "blue", "cyan", "pink", "gold"]
    for i in range(97):
        g = groups[i % len(groups)]
        mid = f"m{i:03d}"
        nid = f"domain_solo_{i:03d}"
        _add_mem(store, mid, tags=[g], layer="architecture", domain_node=nid)
        # Distinct handles (no shared handle word) → exercises the occupancy
        # fold path: singletons (1 ref < 2) fold into a tag-matching sibling.
        _add_node(store, nid, handle=f"Solo Domain {i:03d}", tags=[g], refs=[mid])

    roots_before = sum(1 for n in store.list_mindmap_nodes() if n.parent is None)
    assert roots_before == 97

    consolidate_mindmap(store)

    nodes = store.list_mindmap_nodes()
    roots = [n for n in nodes if n.parent is None]
    active_ids = {m.id for m in store.list_memories(status="active")}

    assert len(roots) <= MAX_ROOTS
    assert len(roots) < roots_before  # actually merged
    assert all(n.memory_refs or n.children for n in nodes)          # zero empty
    assert all(_depth(store, n) <= 2 for n in nodes)                # depth <= 2
    placements = _placements(store)
    assert all(len(v) == 1 for v in placements.values())            # single parent
    assert set(placements) == active_ids                            # none orphaned


# ── 3. No memory is orphaned or double-parented ─────────────────────────

def test_memory_never_orphaned_or_double_parented(tmp_path):
    store = _store(tmp_path)
    # Two disjoint, well-occupied roots.
    _add_mem(store, "d", tags=["alpha"], layer="architecture", domain_node="A")
    _add_mem(store, "a1", tags=["alpha"], layer="architecture", domain_node="A")
    _add_mem(store, "a2", tags=["alpha"], layer="architecture", domain_node="A")
    _add_mem(store, "b1", tags=["beta"], layer="architecture", domain_node="B")
    _add_mem(store, "b2", tags=["beta"], layer="architecture", domain_node="B")
    # An orphan not referenced by any node, but tag-matching root B.
    _add_mem(store, "o1", tags=["beta"], layer="architecture", domain_node=None)

    # "d" is DOUBLE-PARENTED: referenced by both A and B.
    _add_node(store, "A", "Alpha", tags=["alpha"], refs=["d", "a1", "a2"])
    _add_node(store, "B", "Beta", tags=["beta"], refs=["d", "b1", "b2"])

    consolidate_mindmap(store)

    active_ids = {m.id for m in store.list_memories(status="active")}
    placements = _placements(store)
    # every active memory referenced by exactly one node
    assert set(placements) == active_ids
    assert all(len(v) == 1 for v in placements.values())
    # "d" kept in its scope-preferred owner (A), not B
    assert placements["d"] == ["A"]
    # scope.domain_node agrees with the single owner
    for m in store.list_memories(status="active"):
        assert m.scope.domain_node == placements[m.id][0]


# ── 4. should_auto_organize triggers on the new criteria ────────────────

def test_should_auto_organize_root_explosion(tmp_path):
    store = _store(tmp_path)
    # Two healthy roots holding all 40 memories...
    for r, tag in (("A", "alpha"), ("B", "beta")):
        refs = []
        for k in range(20):
            mid = f"{r}{k}"
            _add_mem(store, mid, tags=[tag], domain_node=r)
            refs.append(mid)
        _add_node(store, r, r, tags=[tag], refs=refs)
    # ...plus 30 hollow roots (structure debt). orphan_count == 0.
    for i in range(30):
        _add_node(store, f"empty{i}", f"Empty {i}", tags=[], refs=[])
    assert should_auto_organize(store) is True


def test_should_auto_organize_hollow_map(tmp_path):
    store = _store(tmp_path)
    # 3 nodes, 2 of them hollow -> empty_fraction = 0.66 > 0.3, orphan 0.
    _add_mem(store, "m1", tags=["x"], domain_node="A")
    _add_mem(store, "m2", tags=["x"], domain_node="A")
    _add_node(store, "A", "Alpha", tags=["x"], refs=["m1", "m2"])
    _add_node(store, "E1", "Empty1", tags=[], refs=[])
    _add_node(store, "E2", "Empty2", tags=[], refs=[])
    assert should_auto_organize(store) is True


def test_should_auto_organize_healthy_map_is_false(tmp_path):
    store = _store(tmp_path)
    for r, tag in (("A", "alpha"), ("B", "beta")):
        refs = []
        for k in range(4):
            mid = f"{r}{k}"
            _add_mem(store, mid, tags=[tag], domain_node=r)
            refs.append(mid)
        _add_node(store, r, r, tags=[tag], refs=refs)
    assert should_auto_organize(store) is False


def test_should_auto_organize_orphan_rule_preserved(tmp_path):
    """The original orphan-count rule still fires (no nodes yet)."""
    store = _store(tmp_path)
    for i in range(AUTO_ORGANIZE_ORPHAN_THRESHOLD):
        _add_mem(store, f"m{i}", tags=["x"])
    assert should_auto_organize(store) is True


# ── 5. Strengthened _find_matching_node (§3.3) ──────────────────────────

def test_find_matching_node_merges_on_tag_jaccard(tmp_path):
    store = _store(tmp_path)
    n = _add_node(store, "domain_pkt", "Packet Routing",
                  tags=["routing", "packets"], refs=["x"])
    # Different handle, but tag Jaccard 1.0 and a shared handle word (routing)
    # -> matches, where the old score>=4 rule would have missed.
    assert _find_matching_node("Routing Layer", ["routing", "packets"],
                               [n]) == "domain_pkt"


def test_find_matching_node_generic_words_do_not_fuse(tmp_path):
    store = _store(tmp_path)
    n = _add_node(store, "domain_ds", "Design System",
                  tags=["ui"], refs=["x"])
    # "Memory System" shares only the generic word "system" and no tags -> no match.
    assert _find_matching_node("Memory System", ["persistence"], [n]) is None


# ── 6. Consolidation invariant: no node left under-occupancy ────────────

def test_consolidate_leaves_no_underoccupied_node(tmp_path):
    store = _store(tmp_path)
    # A healthy root, an under-occupied child, and an under-occupied root.
    _add_mem(store, "p1", tags=["core"], domain_node="P")
    _add_mem(store, "p2", tags=["core"], domain_node="P")
    _add_mem(store, "c1", tags=["core", "sub"], domain_node="P-sub")
    _add_mem(store, "lone", tags=["misc"], domain_node="L")
    _add_node(store, "P", "Parent", tags=["core"], refs=["p1", "p2"])
    _add_node(store, "P-sub", "Child", tags=["sub"], refs=["c1"], parent="P")
    # register child in parent's children list
    parent = store.get_mindmap_node("P")
    parent.children = ["P-sub"]
    store.save_mindmap_node(parent)
    _add_node(store, "L", "Lonely", tags=["misc"], refs=["lone"])

    consolidate_mindmap(store)

    nodes = store.list_mindmap_nodes()
    node_by_id = {n.id: n for n in nodes}
    for n in nodes:
        if n.id == _GENERAL_ROOT_ID:
            continue
        if n.parent is not None:
            # every surviving child holds >= MIN_NODE_OCCUPANCY
            assert len(n.memory_refs) >= MIN_NODE_OCCUPANCY
        else:
            total = len(n.memory_refs) + sum(
                len(node_by_id[c].memory_refs) for c in n.children if c in node_by_id)
            assert total >= MIN_NODE_OCCUPANCY
    # no node exceeds depth 2 and no memory is double-parented
    assert all(_depth(store, n) <= 2 for n in nodes)
    placements = _placements(store)
    assert all(len(v) == 1 for v in placements.values())
