"""
Provenance collections: the gravity-well fix, round two.

Round one gated placement on the raw project path.  On the live store
(2026-09-26) that made things worse: the Kuiper topic spans three repos
(switch-mgr 18.6% / np-switchd 2.7% / qos 1.8% of its root), so every
Kuiper memory was judged INCOMPATIBLE with the node named after it, while
ziya-0.4.0.1 at 54% passed.  The root's Ziya share rose 65% -> 77%.

These tests pin:
  - app/utils/memory_collections.infer_collections: projects that
    co-occur in small nodes form one collection; strays do not fuse them
  - MemoryStorage.place_memory_in_mindmap: compatibility by collection
  - MemoryStorage.repair_mindmap pass 2c: a foreign collection is split
    out of a node even when it is the majority there
The storage-level tests fail against the path-gated code.
"""
import pytest

from app.models.memory import Memory, MemoryScope, MindMapNode
from app.storage.memory import MemoryStorage
from app.utils.memory_collections import (
    derive_tags, infer_collections, keys_by_memory_id, owner_collection,
    plan_collection_splits)


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


SM, NP, Q = "/w/KuiperEAR-switch-mgr", "/w/KuiperEAR-np-switchd", "/w/qos"
ZI, ZN = "/w/ziya", "/w/ZiyaInternal"


# ── pure inference ────────────────────────────────────────────────────


def _nodes(**members):
    """node id -> list of project paths; returns (nodes, proj_by_id)."""
    nodes, proj, i = {}, {}, 0
    for nid, projects in members.items():
        refs = []
        for p in projects:
            mid = f"m{i}"; i += 1
            proj[mid] = p
            refs.append(mid)
        nodes[nid] = {"id": nid, "memory_refs": refs, "children": []}
    return nodes, proj


class TestInferCollections:

    def test_topic_across_three_repos_is_one_collection(self):
        nodes, proj = _nodes(tti=[SM] * 3 + [NP] * 2, sched=[SM] * 2 + [Q] * 2,
                             z=[ZI] * 2 + [ZN] * 2)
        c = infer_collections(nodes, proj)
        assert c[SM] == c[NP] == c[Q]
        assert c[ZI] == c[ZN]
        assert c[SM] != c[ZI]

    def test_strays_in_several_nodes_do_not_fuse(self):
        """The live-store regression: one Kuiper stray in each of two
        otherwise-Ziya nodes summed to the pair weight and fused Ziya
        with Kuiper.  The share floor keeps strays out of evidence."""
        nodes, proj = _nodes(a=[ZI] * 6 + [SM], b=[ZI] * 5 + [ZN] * 2 + [SM],
                             k=[SM] * 3)
        c = infer_collections(nodes, proj)
        assert c[ZI] != c[SM]
        assert c[ZI] == c[ZN]          # positive: real co-location still fuses

    def test_large_node_is_not_evidence(self):
        """The fat root itself mixes everything; it must not fuse."""
        nodes, proj = _nodes(fat=[ZI] * 10 + [SM] * 10)
        c = infer_collections(nodes, proj)
        assert c[ZI] != c[SM]

    def test_single_pairing_below_weight_does_not_fuse(self):
        nodes, proj = _nodes(two=[NP, ZI])
        c = infer_collections(nodes, proj)
        assert c[NP] != c[ZI]


class TestOwnerAndPlan:

    def test_owner_follows_children_not_the_flood(self):
        nodes, proj = _nodes(root=[ZI] * 16 + [SM] * 3, tti=[SM] * 3 + [NP] * 2)
        nodes["root"]["children"] = ["tti"]
        key = keys_by_memory_id(proj, infer_collections(nodes, proj))
        assert owner_collection("root", nodes, key) == key["m16"]      # SM's collection
        plans = plan_collection_splits(nodes, key)
        assert [(n, len(ids)) for n, _k, ids in plans] == [("root", 16)]

    def test_lone_foreign_member_is_tolerated(self):
        nodes, proj = _nodes(z=[ZI] * 8 + [SM])
        key = keys_by_memory_id(proj, infer_collections(nodes, proj))
        assert plan_collection_splits(nodes, key) == []

    def test_derive_tags_prefers_shared(self):
        assert derive_tags([["a", "b"], ["a", "c"], ["a", "b"]]) == ["a", "b"]
        assert derive_tags([["x"], ["y"]]) == ["x", "y"]


# ── storage seams ─────────────────────────────────────────────────────


def _mem(storage, content, tags, project):
    m = Memory(content=content, tags=list(tags), scope=MemoryScope(project_paths=[project]))
    storage.save(m)
    return m


@pytest.fixture
def real_layout(storage):
    """The live-store shape: a Kuiper root (500 tags) flooded by Ziya,
    whose children tie the three Kuiper repos together; one Ziya root."""
    k = [_mem(storage, f"kuiper fact {i}", ["kuiper"], SM) for i in range(3)]
    z_ctx = [_mem(storage, f"ziya ctx {i}", ["zctx"], ZI if i % 2 else ZN) for i in range(8)]
    z_orph = [_mem(storage, f"ziya orphan {i}", ["orphan-a", "orphan-b"], ZI) for i in range(8)]
    tti = [_mem(storage, f"tti {i}", ["tti"], SM if i < 3 else NP) for i in range(5)]
    sched = [_mem(storage, f"sched {i}", ["sched"], SM if i < 2 else Q) for i in range(4)]
    zr = [_mem(storage, f"zroot {i}", ["ui"], ZI if i < 2 else ZN) for i in range(4)]
    storage.save_mindmap_node(MindMapNode(
        id="kroot", handle="Kuiper", children=["k-tti", "k-sched"],
        tags=["kuiper", "net"] + [f"fill{i}" for i in range(498)],
        memory_refs=[m.id for m in k + z_ctx + z_orph]))
    storage.save_mindmap_node(MindMapNode(
        id="k-tti", handle="TTI", parent="kroot", tags=["tti", "kuiper", "a", "b"],
        memory_refs=[m.id for m in tti]))
    storage.save_mindmap_node(MindMapNode(
        id="k-sched", handle="Sched", parent="kroot",
        tags=["sched", "kuiper", "c", "d", "e", "f", "g", "h", "i"],
        memory_refs=[m.id for m in sched]))
    storage.save_mindmap_node(MindMapNode(
        id="zroot", handle="Ziya", tags=["zctx", "ziya", "ui", "db"],
        memory_refs=[m.id for m in zr]))
    return {"k": k, "z_ctx": z_ctx, "z_orph": z_orph}


class TestCollectionPlacement:

    def test_repo_without_its_own_node_lands_in_its_topic(self, storage, real_layout):
        """An np-switchd memory sharing one tag with a Kuiper child and one
        with the Ziya root.  By path, np-switchd holds 0% of k-sched and
        the higher-scoring Ziya root wins; by collection, k-sched is its
        topic's node."""
        m = _mem(storage, "np sched", ["sched", "zctx"], NP)
        assert storage.place_memory_in_mindmap(m) == "k-sched"


class TestLiveKuiperLayout:
    """The live store on 2026-09-26, by count: the Kuiper root holds 13
    switch-mgr, 2 np-switchd, 2 qos and 47 Ziya memories; its children are
    pure switch-mgr (so np-switchd and qos are NOT inferred into
    switch-mgr's collection); a catch-all ``domain_general`` holds 2
    np-switchd.  The first split fix moved np-switchd and qos out of the
    Kuiper root (split + rebalance into the catch-all).  Every Kuiper-repo
    memory must stay on a Kuiper node; the Ziya flood must still leave."""

    @pytest.fixture
    def live(self, storage):
        kk = [_mem(storage, f"sm {i}", ["kuiper", f"k{i}"], SM) for i in range(13)]
        kk += [_mem(storage, f"np {i}", ["kuiper", "dfu"], NP) for i in range(2)]
        kk += [_mem(storage, f"q {i}", ["kuiper", "dfu"], Q) for i in range(2)]
        zz = [_mem(storage, f"z {i}", ["zctx"], ZI if i % 3 else ZN) for i in range(47)]
        tti = [_mem(storage, f"tti {i}", ["tti"], SM) for i in range(5)]
        gen = [_mem(storage, f"gen {i}", ["dfu", "fdb"], NP) for i in range(2)]
        zr = [_mem(storage, f"zr {i}", ["ui"], ZI if i < 2 else ZN) for i in range(4)]
        storage.save_mindmap_node(MindMapNode(
            id="kroot", handle="Kuiper", children=["k-tti"],
            tags=["kuiper", "net", "dfu"] + [f"fill{i}" for i in range(510)],
            memory_refs=[m.id for m in kk + zz]))
        storage.save_mindmap_node(MindMapNode(
            id="k-tti", handle="TTI", parent="kroot", tags=["tti", "kuiper"],
            memory_refs=[m.id for m in tti]))
        storage.save_mindmap_node(MindMapNode(
            id="domain_general", handle="General", tags=["dfu", "fdb"],
            memory_refs=[m.id for m in gen]))
        storage.save_mindmap_node(MindMapNode(
            id="zroot", handle="Ziya", tags=["zctx", "ui", "a", "b"],
            memory_refs=[m.id for m in zr]))
        return {"kuiper": kk, "ziya": zz}

    def test_no_kuiper_memory_leaves_kuiper_nodes(self, storage, live):
        for _ in range(3):
            storage.repair_mindmap()
        kuiper_nodes = {"kroot", "k-tti"}
        holder = {r: n.id for n in storage.list_mindmap_nodes() for r in n.memory_refs}
        stray = [m.content for m in live["kuiper"] if holder.get(m.id) not in kuiper_nodes]
        assert stray == []
        # positive: the Ziya flood did leave the Kuiper root
        kroot = set(storage.get_mindmap_node("kroot").memory_refs)
        assert not ({m.id for m in live["ziya"]} & kroot)

    def test_catch_all_id_matches_organizer(self):
        from app.memory.organizer import _GENERAL_ROOT_ID
        from app.utils.memory_collections import CATCH_ALL_NODE_IDS
        assert _GENERAL_ROOT_ID in CATCH_ALL_NODE_IDS


class TestInheritedChildTags:

    def test_generic_parent_memory_stays_on_parent(self, storage, real_layout):
        """A memory tagged only with the parent's topic tag must not jump
        into a divided child that merely inherited that tag."""
        m = _mem(storage, "generic kuiper", ["kuiper"], SM)
        assert storage.place_memory_in_mindmap(m) == "kroot"

    def test_child_still_wins_on_its_own_tag(self, storage, real_layout):
        m = _mem(storage, "tti specific", ["tti", "kuiper"], SM)
        assert storage.place_memory_in_mindmap(m) == "k-tti"


class TestCollectionSplit:

    def test_repair_drains_majority_foreign_collection(self, storage, real_layout):
        result = storage.repair_mindmap()
        assert result["collection_splits"] == 16
        kroot = storage.get_mindmap_node("kroot")
        assert set(kroot.memory_refs) == {m.id for m in real_layout["k"]}
        # tag-matching members went to the node their collection owns
        zroot = storage.get_mindmap_node("zroot")
        assert {m.id for m in real_layout["z_ctx"]} <= set(zroot.memory_refs)
        assert storage.get(real_layout["z_ctx"][0].id).scope.domain_node == "zroot"
        # the rest became a new root built from their own tags
        orph_ids = {m.id for m in real_layout["z_orph"]}
        new = [n for n in storage.list_mindmap_nodes() if orph_ids <= set(n.memory_refs)]
        assert len(new) == 1 and new[0].parent is None
        assert new[0].tags == ["orphan-a", "orphan-b"]
        assert storage.get(real_layout["z_orph"][0].id).scope.domain_node == new[0].id
        # the topic's own children are untouched
        assert len(storage.get_mindmap_node("k-tti").memory_refs) == 5

    def test_split_is_idempotent(self, storage, real_layout):
        storage.repair_mindmap()
        second = storage.repair_mindmap()
        assert second["collection_splits"] == 0
        assert second["rebalanced"] == 0
