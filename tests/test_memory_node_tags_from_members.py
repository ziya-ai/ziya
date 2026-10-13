"""Part C of the gravity-well fix: node tags derived from members, flag-gated.

The live root carried 513 tags on 28 members because every merge/organize/
enrichment path unions tags and nothing prunes.  With the flag on, repair
recomputes a node's tags from what its members actually carry; the accreted
tags disappear and cell division (which excludes the parent's own tags from
split candidates) can bite again.  With the flag off (default) nothing
changes -- the replay measures it as its own arm before it is turned on.
"""
import pytest

from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage
from app.utils.memory_collections import member_node_tags


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


def test_member_node_tags_rules():
    assert member_node_tags([["projb", "timing"], ["projb", "links"], ["projb"]]) == ["projb"]
    # nothing shared by half -> derive_tags fallback (tags on >=2 members)
    assert member_node_tags([["a", "x"], ["a", "y"], ["b", "z"], ["b", "w"], ["c"]], 0.5) == ["a", "b"]
    assert member_node_tags([]) is None and member_node_tags([[], []]) is None


def _fat_root(storage):
    sub = ["timing"] * 5 + ["sched"] * 5 + ["links"] * 4          # each < 50% of 14
    ms = [Memory(content=f"projb fact {i}", tags=["projb", sub[i]]) for i in range(14)]
    for m in ms:
        storage.save(m)
    storage.save_mindmap_node(MindMapNode(
        id="root", handle="Topic B", tags=["projb"] + [f"acc{i}" for i in range(500)],
        memory_refs=[m.id for m in ms]))
    return ms


def test_flag_off_leaves_tags_alone(tmp_path):
    st = MemoryStorage(memory_dir=tmp_path / "m")
    _fat_root(st)
    assert st.repair_mindmap()["retagged"] == 0
    assert len(st.get_mindmap_node("root").tags) == 501


def test_flag_on_drops_accreted_tags_and_unblocks_division(tmp_path, monkeypatch):
    from app.memory.maintenance import maybe_divide_node
    monkeypatch.setattr(MemoryStorage, "NODE_TAGS_FROM_MEMBERS", True)
    st = MemoryStorage(memory_dir=tmp_path / "m")
    _fat_root(st)
    assert st.repair_mindmap()["retagged"] == 1
    assert st.get_mindmap_node("root").tags == ["projb"]      # 14/14; timing/sched/links all < 0.5
    assert st.repair_mindmap()["retagged"] == 0                # idempotent
    # the 5-member 'timing' cluster is no longer masked by an accreted tag
    assert maybe_divide_node(st, "root") == ["root-timing"]
