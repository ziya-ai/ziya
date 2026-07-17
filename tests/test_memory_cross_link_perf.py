"""Regression tests for PenPal #53 [CWE-400]: discover_cross_links O(n^2) fix.

The whole-corpus cross-link pass called discover_cross_links once per node,
and each call re-listed every node from disk and recomputed _same_branch_ids
(itself many disk reads) — O(n^2) disk I/O. The fix adds optional injected
`node_list` + `branch_cache` so a whole-corpus caller amortizes that work.

These tests verify (a) the cached path produces the SAME links as the
per-call path (correctness preserved), (b) the branch_cache is populated and
reused, and (c) the shared node_list is not re-fetched per call.
"""
import pytest
from unittest.mock import MagicMock

from app.memory.maintenance import discover_cross_links, _same_branch_ids


def _node(nid, tags, parent=None, children=None, cross_links=None):
    n = MagicMock()
    n.id = nid
    n.tags = tags
    n.parent = parent
    n.children = children or []
    n.cross_links = cross_links or []
    return n


class _FakeStore:
    """Minimal mind-map store backed by an in-memory dict, counting disk reads."""

    def __init__(self, nodes):
        self._nodes = {n.id: n for n in nodes}
        self.list_calls = 0
        self.get_calls = 0
        self.saved = []

    def list_mindmap_nodes(self):
        self.list_calls += 1
        return list(self._nodes.values())

    def get_mindmap_node(self, nid):
        self.get_calls += 1
        return self._nodes.get(nid)

    def save_mindmap_node(self, node):
        self.saved.append(node.id)


def _corpus():
    # Two separate branches (no shared ancestry) that share a tag → should link.
    a = _node("a", ["python", "security"])
    b = _node("b", ["python", "security"])   # shares 2 tags with a, diff branch
    c = _node("c", ["cooking"])              # unrelated
    return [a, b, c]


def test_cached_path_matches_uncached(monkeypatch):
    # Ensure CROSS_LINK_MIN_OVERLAP is satisfiable (2 shared tags).
    store1 = _FakeStore(_corpus())
    uncached = discover_cross_links(store1, "a")

    store2 = _FakeStore(_corpus())
    nodes = store2.list_mindmap_nodes()
    cache = {}
    cached = discover_cross_links(store2, "a", node_list=nodes, branch_cache=cache)

    # Same links discovered regardless of the cache path.
    assert set(uncached) == set(cached)


def test_branch_cache_is_populated_and_reused():
    store = _FakeStore(_corpus())
    nodes = store.list_mindmap_nodes()
    cache = {}
    discover_cross_links(store, "a", node_list=nodes, branch_cache=cache)
    assert "a" in cache  # branch set memoized
    reads_after_first = store.get_calls
    # Second call for same node reuses cache → no new _same_branch_ids disk reads
    discover_cross_links(store, "a", node_list=nodes, branch_cache=cache)
    # get_mindmap_node is still called for the node itself, but branch walk is cached;
    # the count should not grow by the full branch-walk amount.
    assert store.get_calls <= reads_after_first + 2


def test_node_list_not_refetched_when_injected():
    store = _FakeStore(_corpus())
    nodes = store.list_mindmap_nodes()   # list_calls == 1
    baseline = store.list_calls
    discover_cross_links(store, "a", node_list=nodes, branch_cache={})
    # Injected node_list means discover_cross_links must NOT call list_mindmap_nodes again.
    assert store.list_calls == baseline


def test_uncached_call_still_refetches():
    # Negative control: without node_list, each call re-lists (the O(n^2) source).
    store = _FakeStore(_corpus())
    baseline = store.list_calls
    discover_cross_links(store, "a")
    assert store.list_calls == baseline + 1


def test_no_tags_short_circuits():
    store = _FakeStore([_node("x", [])])
    assert discover_cross_links(store, "x") == []
