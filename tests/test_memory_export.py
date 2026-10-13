"""Memory export: selection bounds, sanitization, leak scan, fingerprint.

The export is the one sanctioned path for memory to leave the machine, so
these assert on the *bundle* (what the other user receives), not on
intermediate plan state, wherever both are available.
"""
import json

import pytest

from app.models.memory import Memory, MemoryScope, MindMapNode
from app.storage.memory import MemoryStorage
from app.utils.memory_export import (
    ExportSelection, build_bundle, export_filename, export_options,
    fingerprint_ids, plan_export, scan_memory,
)

ZIYA = "/Users/alice/workspace/ziya"
BILLING = "/Users/alice/workspace/shop-billing"


@pytest.fixture
def store(tmp_path):
    s = MemoryStorage(memory_dir=tmp_path / "memory")
    # Two root domains, one with a child; one unplaced memory.
    s.save_mindmap_node(MindMapNode(id="domain_bench", handle="bench architecture",
                                    tags=["bench"], children=["bench_io"],
                                    memory_refs=["m_b1"]))
    s.save_mindmap_node(MindMapNode(id="bench_io", handle="bench io", parent="domain_bench",
                                    tags=["io"], memory_refs=["m_b2"]))
    s.save_mindmap_node(MindMapNode(id="domain_net", handle="billing fixes",
                                    tags=["billing"], memory_refs=["m_n1", "m_n2"]))
    rows = [
        Memory(id="m_b1", content="Bench uses a flat JSON store for runs.",
               layer="architecture", scope=MemoryScope(project_paths=[ZIYA]),
               learned_from_conversation="conv-111", corroborated_by=["conv-111"],
               retrieval_loaded_count=4, relations={"supports": ["m_b2", "m_n1"]}),
        Memory(id="m_b2", content="Bench IO is non-blocking; see /Users/alice/notes.md",
               layer="decision", scope=MemoryScope(project_paths=[ZIYA])),
        Memory(id="m_n1", content="BILL-793 fix: retry on connection reset.",
               layer="domain_context", scope=MemoryScope(project_paths=[BILLING])),
        Memory(id="m_n2", content="Shared across both: token AKIAABCDEFGHIJKLMNOP",
               layer="negative_constraint",
               scope=MemoryScope(project_paths=[BILLING, ZIYA])),
        Memory(id="m_u1", content="Unscoped lexicon entry with no project.",
               layer="lexicon"),
        Memory(id="m_arch", content="An archived memory that must never export by default.",
               layer="decision", status="archived",
               scope=MemoryScope(project_paths=[ZIYA])),
    ]
    s.save_many(rows)
    return s


def ids(bundle):
    return sorted(m["id"] for m in bundle["memories"])


# -- options ------------------------------------------------------------------

def test_options_list_domains_projects_and_unscoped(store):
    opts = export_options(store)
    by_id = {d["id"]: d for d in opts["domains"]}
    assert set(by_id) == {"domain_bench", "domain_net"}
    assert by_id["domain_bench"]["count"] == 2          # includes child node member
    assert by_id["domain_bench"]["children"][0]["id"] == "bench_io"
    projects = {p["path"]: p for p in opts["projects"]}
    assert projects[ZIYA]["label"] == "ziya"
    assert projects[ZIYA]["count"] == 3                 # m_b1, m_b2, m_n2 (not archived)
    assert opts["unscoped"] == 1
    assert opts["unplaced"] == 1                        # m_u1 is in no node
    assert opts["total"] == 5                           # archived excluded


# -- selection bounds -----------------------------------------------------------

def test_default_selection_exports_every_active_memory(store):
    sel = ExportSelection()
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    assert ids(bundle) == ["m_b1", "m_b2", "m_n1", "m_n2", "m_u1"]
    assert plan.held_back == {"status": 1}


def test_domain_selects_whole_subtree_and_holds_back_others(store):
    sel = ExportSelection(domains=["domain_bench"])
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    assert ids(bundle) == ["m_b1", "m_b2"]
    # m_n1, m_n2 are in another domain; m_u1 is unplaced, so also outside.
    assert plan.held_back["outside_domains"] == 3


def test_project_exclusion_wins_over_inclusion(store):
    # m_n2 is scoped to both projects: including ZIYA must not pull it in
    # when BILLING is excluded.
    sel = ExportSelection(include_projects=[ZIYA], exclude_projects=[BILLING])
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    assert ids(bundle) == ["m_b1", "m_b2", "m_u1"]
    assert plan.held_back["excluded_project"] == 2       # m_n1, m_n2


def test_include_projects_alone_holds_back_other_projects(store):
    sel = ExportSelection(include_projects=[BILLING], include_unscoped=False)
    plan, _ = plan_export(store, sel)
    assert sorted(plan.memory_ids) == ["m_n1", "m_n2"]
    assert plan.held_back["project_not_included"] == 2
    assert plan.held_back["unscoped"] == 1


def test_layer_and_status_gates(store):
    plan, _ = plan_export(store, ExportSelection(layers=["decision"]))
    assert plan.memory_ids == ["m_b2"]
    plan, _ = plan_export(store, ExportSelection(layers=["decision"], statuses=["archived"]))
    assert plan.memory_ids == ["m_arch"]


# -- sanitization -----------------------------------------------------------------

def test_bundle_strips_provenance_and_anonymizes_paths_by_default(store):
    sel = ExportSelection()
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    text = json.dumps(bundle)
    assert "conv-111" not in text
    assert ZIYA not in text and BILLING not in text
    m_b1 = next(m for m in bundle["memories"] if m["id"] == "m_b1")
    assert m_b1["scope"]["project_paths"] == ["ziya"]
    # Header too: per-project counts and the echoed selection use labels.
    assert {c["label"] for c in bundle["counts"]["by_project"]} == {"ziya", "shop-billing", "(unscoped)"}
    assert all("path" not in c for c in bundle["counts"]["by_project"])
    for f in ("learned_from_conversation", "corroborated_by", "retrieval_loaded_count",
              "last_accessed", "interference_score"):
        assert f not in m_b1
    assert m_b1["content"] and m_b1["layer"] == "architecture"


def test_bundle_keeps_provenance_when_opted_in(store):
    sel = ExportSelection(strip_provenance=False, anonymize_paths=False)
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    m_b1 = next(m for m in bundle["memories"] if m["id"] == "m_b1")
    assert m_b1["learned_from_conversation"] == "conv-111"
    assert m_b1["scope"]["project_paths"] == [ZIYA]
    kinds = {w["kind"] for w in plan.warnings}
    assert {"provenance_included", "project_paths_included"} <= kinds


def test_relations_pruned_to_exported_ids(store):
    sel = ExportSelection(domains=["domain_bench"])
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    m_b1 = next(m for m in bundle["memories"] if m["id"] == "m_b1")
    assert m_b1["relations"] == {"supports": ["m_b2"]}   # m_n1 dropped


def test_mindmap_subset_is_connected_and_pruned(store):
    sel = ExportSelection(domains=["bench_io"])
    plan, _ = plan_export(store, sel)
    bundle = build_bundle(store, sel, plan)
    nodes = {n["id"]: n for n in bundle["mindmap"]}
    assert set(nodes) == {"domain_bench", "bench_io"}     # ancestor kept
    assert nodes["domain_bench"]["memory_refs"] == []     # m_b1 not exported
    assert nodes["bench_io"]["memory_refs"] == ["m_b2"]
    assert nodes["domain_bench"]["children"] == ["bench_io"]
    assert "access_count" not in nodes["bench_io"]
    plan2, _ = plan_export(store, ExportSelection(include_mindmap=False))
    assert build_bundle(store, ExportSelection(include_mindmap=False), plan2)["mindmap"] == []


# -- leak scan ---------------------------------------------------------------------

def test_scan_flags_credentials_local_paths_and_emails():
    hits = scan_memory({"id": "x", "content":
                        "key AKIAABCDEFGHIJKLMNOP at /Users/alice/x mail bob@example.com"})
    kinds = {h["kind"] for h in hits}
    assert "credential:aws-access-key" in kinds
    assert "local_path" in kinds and "email" in kinds
    assert all(h["memory_id"] == "x" for h in hits)
    assert scan_memory({"id": "y", "content": "Plain fact, nothing sensitive."}) == []


def test_plan_warnings_name_the_memory(store):
    plan, _ = plan_export(store, ExportSelection())
    by_mem = {}
    for w in plan.warnings:
        by_mem.setdefault(w["memory_id"], set()).add(w["kind"])
    assert "credential:aws-access-key" in by_mem["m_n2"]
    assert "local_path" in by_mem["m_b2"]
    assert "m_n1" not in by_mem


# -- fingerprint / bounds check -------------------------------------------------------

def test_fingerprint_is_order_independent():
    assert fingerprint_ids(["b", "a"]) == fingerprint_ids(["a", "b", "a"])
    assert fingerprint_ids(["a"]) != fingerprint_ids(["a", "b"])


def test_bundle_refuses_when_store_changed_after_preview(store):
    sel = ExportSelection(domains=["domain_net"])
    plan, _ = plan_export(store, sel)
    store.save(Memory(id="m_n3", content="Added after preview, lands in domain_net.",
                      scope=MemoryScope(project_paths=[BILLING], domain_node="domain_net")))
    with pytest.raises(ValueError):
        build_bundle(store, sel, plan)


def test_preview_sample_is_sanitized(store):
    plan, sample = plan_export(store, ExportSelection(), sample_size=2)
    assert len(sample) == 2
    assert all("corroborated_by" not in m for m in sample)


def test_export_filename_carries_count_and_fingerprint(store):
    plan, _ = plan_export(store, ExportSelection())
    name = export_filename(plan, when=0)
    assert name.startswith("ziya-memory-export-") and name.endswith(".json")
    assert f"-{plan.total}mem-{plan.fingerprint[:8]}" in name
