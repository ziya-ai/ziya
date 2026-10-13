"""Memory import: dispositions, collision policy, reference rewriting,
mind-map merge, fingerprint refusals, headless seeding.

Asserts on the *store after import* (what the recipient's Ziya will read),
and on the export->import round trip, since that is the real use.
"""
import copy
import json

import pytest

from app.models.memory import Memory, MemoryScope, MindMapNode
from app.storage.memory import MemoryStorage
from app.utils.memory_export import ExportSelection, build_bundle, plan_export
from app.utils.memory_import import (
    ImportOptions, apply_import, bundle_fingerprint, load_bundle, plan_import,
    preview_rows, seed_store,
)


def make_bundle(memories, mindmap=None, **hdr):
    return {"format": "ziya-memory-export", "version": 1, "exported_by": "alice",
            "exported_at": "2026-10-07T00:00:00Z", "memories": memories,
            "mindmap": mindmap or [], **hdr}


def mem(id, content, **kw):
    return {"id": id, "content": content, "layer": kw.pop("layer", "domain_context"),
            "tags": kw.pop("tags", []), "scope": kw.pop("scope", {"domain_node": None, "project_paths": []}),
            "related": kw.pop("related", []), "relations": kw.pop("relations", {}),
            "importance": 0.5, "created": "2026-01-01", **kw}


@pytest.fixture
def store(tmp_path):
    s = MemoryStorage(memory_dir=tmp_path / "mine")
    s.save_mindmap_node(MindMapNode(id="domain_net", handle="billing fixes",
                                    tags=["billing"], memory_refs=["m_n1"]))
    s.save_many([
        Memory(id="m_n1", content="BILL-793 fix: retry on connection reset.",
               scope=MemoryScope(project_paths=["/Users/bob/shop"])),
        Memory(id="m_mine", content="My own fact about the bench.", layer="architecture"),
    ])
    return s


def rows(store):
    return {r["id"]: r for r in store._load_memories()}


# -- validation ---------------------------------------------------------------------

def test_load_bundle_rejects_bad_shapes():
    with pytest.raises(ValueError, match="not a ziya-memory-export"):
        load_bundle({"format": "other", "version": 1, "memories": []})
    with pytest.raises(ValueError, match="unsupported bundle version"):
        load_bundle(make_bundle([], version=99))
    with pytest.raises(ValueError, match="lacks id or content"):
        load_bundle(make_bundle([{"id": "x"}]))
    with pytest.raises(ValueError, match="repeats memory id"):
        load_bundle(make_bundle([mem("a", "one"), mem("a", "two")]))
    assert load_bundle(make_bundle([mem("a", "one")], mindmap=None))["mindmap"] == []


# -- dispositions -------------------------------------------------------------------

def test_dispositions_new_identical_duplicate_collision(store):
    b = load_bundle(make_bundle([
        mem("m_new", "A brand new fact."),
        mem("m_n1", "BILL-793 fix: retry on connection reset."),           # identical
        mem("m_dup", "my own fact  about the bench."),               # same content, other id
        mem("m_mine", "A different fact wearing my id."),            # collision
    ]))
    plan = plan_import(store, b, ImportOptions())
    assert plan.dispositions == {
        "m_new": "new", "m_n1": "identical", "m_dup": "duplicate_content",
        "m_mine": "collision_renamed",
    }
    assert plan.id_map["m_n1"] == "m_n1"
    assert plan.id_map["m_dup"] == "m_mine"            # redirected to the existing one
    assert plan.id_map["m_mine"] != "m_mine" and plan.id_map["m_mine"].startswith("m_")
    assert plan.counts["to_write"] == 2

    apply_import(store, b, plan, ImportOptions(), source="test.json")
    after = rows(store)
    assert set(after) == {"m_n1", "m_mine", "m_new", plan.id_map["m_mine"]}
    assert after["m_mine"]["content"] == "My own fact about the bench."   # untouched
    assert after[plan.id_map["m_mine"]]["content"] == "A different fact wearing my id."
    new = after["m_new"]
    assert new["learned_from"] == "imported" and "imported" in new["tags"]
    assert new["imported_from"]["exported_by"] == "alice"
    assert new["imported_from"]["source"] == "test.json"
    assert new["imported_from"]["fingerprint"] == plan.bundle_fingerprint


def test_collision_skip_and_overwrite(store):
    b = load_bundle(make_bundle([mem("m_mine", "Replacement content.")]))
    plan = plan_import(store, b, ImportOptions(on_collision="skip"))
    assert plan.dispositions == {"m_mine": "collision_skipped"} and "m_mine" not in plan.id_map
    apply_import(store, b, plan, ImportOptions(on_collision="skip"))
    assert rows(store)["m_mine"]["content"] == "My own fact about the bench."

    plan = plan_import(store, b, ImportOptions(on_collision="overwrite"))
    assert plan.dispositions == {"m_mine": "collision_overwritten"}
    apply_import(store, b, plan, ImportOptions(on_collision="overwrite"))
    assert rows(store)["m_mine"]["content"] == "Replacement content."


def test_duplicate_content_can_be_imported_when_asked(store):
    b = load_bundle(make_bundle([mem("m_dup", "My own fact about the bench.")]))
    opts = ImportOptions(skip_duplicate_content=False)
    plan = plan_import(store, b, opts)
    assert plan.dispositions == {"m_dup": "new"}


def test_source_label_tag_and_unknown_layer_warning(store):
    b = load_bundle(make_bundle([mem("m_x", "Odd layer.", layer="not_a_layer")]))
    opts = ImportOptions(source_label="alice-billing")
    plan = plan_import(store, b, opts)
    assert plan.warnings and "unknown layer" in plan.warnings[0]
    apply_import(store, b, plan, opts)
    r = rows(store)["m_x"]
    assert r["layer"] == "domain_context"
    assert "from:alice-billing" in r["tags"]


# -- references and mind-map -------------------------------------------------------------

def test_relations_follow_renames_and_redirects_and_drop_skipped(store):
    b = load_bundle(make_bundle([
        mem("m_a", "Fact A.", relations={"supports": ["m_mine", "m_dup", "m_gone"]},
            related=["m_mine"]),
        mem("m_mine", "Colliding content."),                      # renamed
        mem("m_dup", "My own fact about the bench."),             # redirected -> m_mine
        mem("m_gone", "Skipped by collision policy? no: unique"),
    ]))
    plan = plan_import(store, b, ImportOptions())
    apply_import(store, b, plan, ImportOptions())
    a = rows(store)["m_a"]
    renamed = plan.id_map["m_mine"]
    # m_mine (bundle) -> renamed id; m_dup -> existing m_mine; m_gone imported as-is.
    assert a["relations"] == {"supports": [renamed, "m_mine", "m_gone"]}
    assert a["related"] == [renamed]


def test_mindmap_merges_existing_node_and_creates_new(store):
    b = load_bundle(make_bundle(
        [mem("m_n9", "New billing fact."), mem("m_b1", "Bench fact.", layer="architecture")],
        mindmap=[
            {"id": "domain_net", "handle": "alice's billing", "parent": None,
             "children": ["net_sub"], "cross_links": ["domain_bench"],
             "memory_refs": ["m_n9"], "tags": ["reset"]},
            {"id": "net_sub", "handle": "net sub", "parent": "domain_net",
             "children": [], "cross_links": [], "memory_refs": [], "tags": []},
            {"id": "domain_bench", "handle": "bench", "parent": None,
             "children": [], "cross_links": ["domain_net"], "memory_refs": ["m_b1"], "tags": ["bench"]},
        ]))
    plan = plan_import(store, b, ImportOptions())
    assert plan.nodes_merged == ["domain_net"]
    assert plan.nodes_new == ["domain_bench", "net_sub"]
    apply_import(store, b, plan, ImportOptions())
    nodes = store._load_mindmap()
    net = nodes["domain_net"]
    assert net["handle"] == "billing fixes"                      # mine wins
    assert net["memory_refs"] == ["m_n1", "m_n9"]               # union
    assert set(net["tags"]) == {"billing", "reset"}
    assert net["children"] == ["net_sub"] and net["cross_links"] == ["domain_bench"]
    assert nodes["net_sub"]["parent"] == "domain_net"
    assert nodes["domain_bench"]["memory_refs"] == ["m_b1"]
    assert rows(store)["m_b1"]["scope"]["domain_node"] == "domain_bench"
    assert rows(store)["m_n9"]["scope"]["domain_node"] == "domain_net"


def test_include_mindmap_false_leaves_tree_alone(store):
    b = load_bundle(make_bundle([mem("m_z", "Z.")],
                                mindmap=[{"id": "domain_zed", "handle": "zed", "memory_refs": ["m_z"]}]))
    opts = ImportOptions(include_mindmap=False)
    plan = plan_import(store, b, opts)
    assert plan.nodes_new == [] and plan.counts["nodes_new"] == 0
    apply_import(store, b, plan, opts)
    assert set(store._load_mindmap()) == {"domain_net"}
    assert rows(store)["m_z"]["scope"]["domain_node"] is None


# -- bounds ---------------------------------------------------------------------------

def test_apply_refuses_when_bundle_or_store_changed(store):
    b = load_bundle(make_bundle([mem("m_new", "Fresh.")]))
    plan = plan_import(store, b, ImportOptions())
    altered = copy.deepcopy(b)
    altered["memories"][0]["content"] = "Edited after preview."
    with pytest.raises(ValueError, match="bundle changed"):
        apply_import(store, altered, plan, ImportOptions())
    store.save(Memory(id="m_late", content="Arrived between preview and import."))
    with pytest.raises(ValueError, match="store changed"):
        apply_import(store, b, plan, ImportOptions())


def test_preview_rows_show_disposition_and_final_id(store):
    b = load_bundle(make_bundle([mem("m_dup", "My own fact about the bench."), mem("m_new", "x" * 400)]))
    plan = plan_import(store, b, ImportOptions())
    pv = preview_rows(b, plan)
    assert pv[0] == {"id": "m_dup", "final_id": "m_mine", "disposition": "duplicate_content",
                     "layer": "domain_context", "content": "My own fact about the bench."}
    assert len(pv[1]["content"]) == 160


# -- round trip and seeding ------------------------------------------------------------------

def test_export_then_import_round_trip_into_empty_store(tmp_path):
    src = MemoryStorage(memory_dir=tmp_path / "alice")
    src.save_mindmap_node(MindMapNode(id="domain_bench", handle="bench", tags=["bench"],
                                      children=["bench_io"], memory_refs=["m_b1"]))
    src.save_mindmap_node(MindMapNode(id="bench_io", handle="io", parent="domain_bench",
                                      memory_refs=["m_b2"]))
    src.save_many([
        Memory(id="m_b1", content="Bench uses a flat JSON store.", layer="architecture",
               scope=MemoryScope(project_paths=["/Users/alice/ziya"]),
               relations={"supports": ["m_b2"]}, learned_from_conversation="conv-1"),
        Memory(id="m_b2", content="Bench IO is non-blocking.", layer="decision",
               scope=MemoryScope(project_paths=["/Users/alice/ziya"])),
    ])
    sel = ExportSelection()
    plan, _ = plan_export(src, sel)
    bundle = json.loads(json.dumps(build_bundle(src, sel, plan, exported_by="alice")))

    result = seed_store(tmp_path / "job", bundle, source="alice.json")
    assert result["imported"] == 2
    dst = MemoryStorage(memory_dir=tmp_path / "job")
    got = rows(dst)
    assert set(got) == {"m_b1", "m_b2"}
    assert got["m_b1"]["relations"] == {"supports": ["m_b2"]}
    assert got["m_b1"]["scope"] == {"domain_node": "domain_bench", "project_paths": ["ziya"]}
    assert "learned_from_conversation" not in got["m_b1"]
    nodes = dst._load_mindmap()
    assert nodes["bench_io"]["parent"] == "domain_bench"
    assert nodes["domain_bench"]["children"] == ["bench_io"]
    # The recipient's store reads back through the normal API.
    assert {m.id for m in dst.list_memories()} == {"m_b1", "m_b2"}
    assert dst.expand_node("domain_bench") and {m.id for m in dst.expand_node("domain_bench")} == {"m_b1", "m_b2"}

    # Seeding again is a no-op.
    again = seed_store(tmp_path / "job", bundle)
    assert again["imported"] == 0 and again["counts"]["identical"] == 2


def test_bundle_fingerprint_depends_on_content_and_nodes():
    a = make_bundle([mem("x", "one")])
    b = make_bundle([mem("x", "two")])
    c = make_bundle([mem("x", "one")], mindmap=[{"id": "n"}])
    assert bundle_fingerprint(a) != bundle_fingerprint(b)
    assert bundle_fingerprint(a) != bundle_fingerprint(c)
    assert bundle_fingerprint(a) == bundle_fingerprint(copy.deepcopy(a))
