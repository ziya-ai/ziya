"""
Mind-map placement must not reward mass ("gravity well").

Sep 2026 live store: one root held 145 of 252 memories with a 509-tag
tag set (next largest: 68); 65% of its members came from a project that
had a dozen roots of its own.  Cause: placement scored raw
|mem ∩ node| (monotone in node size), node tags only ever accreted, and
provenance (scope.project_paths, set on every memory) was never read.

These tests pin the two corrections in MemoryStorage:
  - place_memory_in_mindmap: size-normalised score + soft provenance gate
  - repair_mindmap pass 2b: rebalance placed memories out of the well

Each was confirmed to FAIL against the raw-overlap placer.
"""
import pytest

from app.models.memory import Memory, MemoryScope, MindMapNode
from app.storage.memory import MemoryStorage


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


X, Y = "/w/project-x", "/w/project-y"


def _mem(storage, content, tags, project, mid=None):
    m = Memory(content=content, tags=list(tags),
               scope=MemoryScope(project_paths=[project]))
    if mid:
        m.id = mid
    storage.save(m)
    return m


def _fat_root(storage, project, n_tags=500, members=5, shared="shared"):
    """A root of ``project`` with a huge tag set that includes ``shared``.
    Its members are stored so provenance is computable."""
    refs = []
    for i in range(members):
        refs.append(_mem(storage, f"x fact {i}", [f"xt{i}"], project).id)
    node = MindMapNode(id="fat", handle="Fat root",
                       tags=[shared] + [f"fat{i}" for i in range(n_tags - 1)],
                       memory_refs=refs)
    storage.save_mindmap_node(node)
    return node


def _small_node(storage, project, nid="small", shared="shared", members=2):
    refs = [_mem(storage, f"y fact {i}", [f"yt{i}"], project).id for i in range(members)]
    node = MindMapNode(id=nid, handle="Small node",
                       tags=[shared, "a", "b", "c"], memory_refs=refs)
    storage.save_mindmap_node(node)
    return node


class TestNormalisedPlacement:

    def test_shared_one_tag_each_prefers_small_node_same_project(self, storage):
        """Pure normalisation: same provenance everywhere, one shared tag
        with each candidate.  Raw overlap ties (1 vs 1) and dict order
        would keep feeding the fat root; the normalised score must pick
        the small node."""
        _fat_root(storage, X)
        _small_node(storage, X)
        m = _mem(storage, "new", ["shared", "unrelated"], X)
        assert storage.place_memory_in_mindmap(m) == "small"
        assert m.id in storage.get_mindmap_node("small").memory_refs
        assert m.id not in storage.get_mindmap_node("fat").memory_refs
        assert storage.get(m.id).scope.domain_node == "small"

    def test_fat_root_needs_many_more_overlaps_to_win(self, storage):
        """Mass is a cost: a 500-tag node needs ~sqrt(500/4) ≈ 11x the
        overlap of a 4-tag node to out-score it.  Positive control that
        the fat node CAN still win when it is genuinely the better fit."""
        _fat_root(storage, X)
        _small_node(storage, X)
        m = _mem(storage, "very fat", ["shared"] + [f"fat{i}" for i in range(1, 20)], X)
        assert storage.place_memory_in_mindmap(m) == "fat"


class TestProvenanceGate:

    def test_cross_project_memory_lands_on_own_project_node(self, storage):
        """The seam from the audit: fat root of X, small node of Y, a
        Y-memory sharing one tag with each -> must land on Y."""
        _fat_root(storage, X)
        _small_node(storage, Y)
        m = _mem(storage, "y thing", ["shared"], Y)
        assert storage.place_memory_in_mindmap(m) == "small"

    def test_gate_beats_score_when_compatible_node_exists(self, storage):
        """Provenance outranks score: even when the X root would out-score
        the Y node on tags, a Y-memory goes to the Y node."""
        _fat_root(storage, X, n_tags=8)          # small enough to score well
        _small_node(storage, Y)
        # overlaps 3 fat tags (3/sqrt(8)=1.06) vs 1 small tag (1/2=0.5)
        m = _mem(storage, "y thing", ["fat1", "fat2", "fat3", "shared"], Y)
        assert storage.place_memory_in_mindmap(m) == "small"

    def test_gate_is_soft_first_of_its_project_still_places(self, storage):
        """No compatible node -> best topical node wins rather than
        leaving the memory unplaced."""
        _fat_root(storage, X)
        m = _mem(storage, "z thing", ["shared"], "/w/project-z")
        assert storage.place_memory_in_mindmap(m) == "fat"

    def test_memory_without_provenance_is_compatible_everywhere(self, storage):
        _fat_root(storage, X)
        _small_node(storage, Y)
        m = Memory(content="no project", tags=["shared"])
        storage.save(m)
        # falls through to score; the small node wins on normalisation
        assert storage.place_memory_in_mindmap(m) == "small"

    def test_node_without_provenance_is_compatible(self, storage):
        """An empty (freshly organised) node has no members to profile;
        it must be eligible or nothing could ever seed it."""
        storage.save_mindmap_node(MindMapNode(id="empty", handle="E", tags=["qos"]))
        m = _mem(storage, "q", ["qos"], Y)
        assert storage.place_memory_in_mindmap(m) == "empty"

    def test_minority_share_below_threshold_is_incompatible(self, storage):
        """13% share (like the 2 Kuiper memories on a 15-member Ziya node)
        does not make a node compatible; 20% is the floor."""
        refs = [_mem(storage, f"x{i}", ["t"], X).id for i in range(7)]
        refs += [_mem(storage, "y0", ["t"], Y).id]           # 1/8 = 12.5%
        storage.save_mindmap_node(MindMapNode(id="mixed", handle="M", tags=["shared", "k"],
                                              memory_refs=refs))
        _small_node(storage, Y)
        m = _mem(storage, "y new", ["shared"], Y)
        assert storage.place_memory_in_mindmap(m) == "small"


class TestRepairRebalance:

    def test_repair_moves_misplaced_out_of_well(self, storage):
        """Memories already sitting in the well (placed by the old scorer)
        are moved to the clearly-better same-project node on repair, and
        scope.domain_node follows."""
        fat = _fat_root(storage, X)
        _small_node(storage, Y)
        stuck = _mem(storage, "y stuck", ["shared"], Y)
        stuck.scope.domain_node = "fat"
        storage.save(stuck)
        fat.memory_refs.append(stuck.id)
        storage.save_mindmap_node(fat)

        result = storage.repair_mindmap()
        assert result["rebalanced"] == 1
        assert stuck.id in storage.get_mindmap_node("small").memory_refs
        assert stuck.id not in storage.get_mindmap_node("fat").memory_refs
        assert storage.get(stuck.id).scope.domain_node == "small"

    def test_repair_leaves_near_tie_alone(self, storage):
        """Two same-project nodes of equal size sharing one tag with the
        memory: no 2x margin, so the current placement stands (no churn
        against organizer choices)."""
        a = _small_node(storage, X, nid="a")
        _small_node(storage, X, nid="b")
        m = _mem(storage, "m", ["shared"], X)
        a.memory_refs.append(m.id)
        storage.save_mindmap_node(a)
        result = storage.repair_mindmap()
        assert result["rebalanced"] == 0
        assert m.id in storage.get_mindmap_node("a").memory_refs

    def test_repair_does_not_move_tagless_fit_unless_incompatible(self, storage):
        """A memory with no tag overlap with its node was placed by
        something other than tags; only provenance may move it."""
        a = _small_node(storage, X, nid="a")
        _small_node(storage, X, nid="b")
        m = _mem(storage, "m", ["a"], X)          # overlaps b (and a) on 'a'
        # Make it overlap nothing on a: give a distinct tag set.
        a.tags = ["zz"]
        a.memory_refs.append(m.id)
        storage.save_mindmap_node(a)
        assert storage.repair_mindmap()["rebalanced"] == 0

    def test_repair_moves_provenance_incompatible_even_without_margin(self, storage):
        refs = [_mem(storage, f"x{i}", ["t"], X).id for i in range(8)]
        node = MindMapNode(id="xnode", handle="X", tags=["shared", "a", "b", "c"],
                           memory_refs=refs)
        _small_node(storage, Y)                    # identical tag set, same score
        m = _mem(storage, "y", ["shared"], Y)
        node.memory_refs.append(m.id)
        storage.save_mindmap_node(node)
        assert storage.repair_mindmap()["rebalanced"] == 1
        assert m.id in storage.get_mindmap_node("small").memory_refs

    def test_repair_rebalance_is_idempotent(self, storage):
        fat = _fat_root(storage, X)
        _small_node(storage, Y)
        stuck = _mem(storage, "y stuck", ["shared"], Y)
        fat.memory_refs.append(stuck.id)
        storage.save_mindmap_node(fat)
        storage.repair_mindmap()
        assert storage.repair_mindmap()["rebalanced"] == 0
