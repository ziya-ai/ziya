"""The output store (design/typed-findings.md, notes 44-47): validated
outputs from chat (store_output) and from Task Card runs (a contracted emit)
land in one .ziya/outputs/<family>.jsonl per schema family, and ``analyze``
reads it with nested access -- the interactive query surface.

Seams: execute_single_tool -> StoreOutputTool -> check_value -> output_store;
emit_artifact inside a collector with a contract -> output_store; the REAL
``analyze`` (DuckDB) over the resulting file.
"""
import asyncio
import json

import pytest

from app.mcp.tools.output_tools import StoreOutputTool
from app.utils import output_store
from app.utils.evidence import store as st
from tests.test_ledger_tool_seam import _model_text

FINDING = {"id": "T-X", "title": "Retry queue flushed on reload", "verdict": "candidate",
           "impact": {"fleet_total": {"mid": {"value": 248000}, "unit": "host-s/day"}}}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    st._ledgers.clear()
    root = tmp_path / "proj"
    (root / ".ziya" / "schemas").mkdir(parents=True)
    (root / ".ziya" / "schemas" / "nf.json").write_text(json.dumps(
        {"allOf": [{"$ref": "ziya:finding/v1"}], "required": ["impact"]}))
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    return root


def _store(root, **args):
    """The tool result as the model sees it (JSON text, quotes unescaped)."""
    return asyncio.run(_model_text(root, StoreOutputTool, args)).replace('\\"', '"')


def test_store_output_appends_a_passing_value_and_refuses_a_failing_one(proj):
    text = _store(proj, schema="project:nf", data=FINDING)
    assert '"stored": true' in text and '.ziya/outputs/finding.jsonl' in text
    [rec] = output_store.read(str(proj), "finding")
    assert rec["key"] == "T-X" and rec["version"] == 1
    assert rec["schema_lineage"] == ["project:nf", "ziya:finding/v1"]
    assert rec["data"]["title"] == "Retry queue flushed on reload"
    assert rec["unverified_numbers"] == ["/impact/fleet_total/mid"]
    assert rec["provenance"]["via"] == "tool"
    assert "stored_at" in rec and "at" not in rec, "AT is a DuckDB reserved word"

    bad = _store(proj, schema="project:nf", data={"id": "T-Y", "title": "t", "verdict": "candidate"})
    assert '"stored": false' in bad and "/impact: required" in bad
    assert len(output_store.read(str(proj), "finding")) == 1, "nothing stored on refusal"


def test_restoring_the_same_id_is_a_new_version(proj):
    _store(proj, schema="ziya:finding", data=FINDING)
    _store(proj, schema="ziya:finding", data={**FINDING, "verdict": "confirmed"})
    recs = output_store.read(str(proj), "finding")
    assert [r["version"] for r in recs] == [1, 2]
    [cur] = output_store.latest(str(proj), "finding")
    assert cur["data"]["verdict"] == "confirmed"


def test_a_contracted_emit_inside_a_run_lands_in_the_same_file(proj):
    from app.utils import output_contracts as oc, task_artifacts as ta
    _store(proj, schema="ziya:finding", data=FINDING)
    s = oc.SchemaSet.resolve(["project:nf"], str(proj))
    from app.context import set_ledger_root, reset_ledger_root
    # task_executor pins the card's project as the ledger root for the task;
    # the store writes there (never to the process cwd).
    lr = set_ledger_root(str(proj))
    tok = ta.start_artifact_collection(block_id="lane-1", contracts=oc.bind_contracts(
        [{"name": "finding", "schema": "project:nf"}], s))
    try:
        ok, _ = ta.emit_part({"part_type": "data", "name": "finding",
                              "data": {**FINDING, "id": "T-RUN"}})
        assert ok
        [part] = ta.collected_parts()
        assert part["stored_id"].startswith("out_")
    finally:
        ta.finish_artifact_collection(tok)
        reset_ledger_root(lr)
    recs = output_store.read(str(proj), "finding")
    assert [r["key"] for r in recs] == ["T-X", "T-RUN"]
    assert recs[1]["provenance"]["via"] == "run" and recs[1]["provenance"]["part"] == "finding"


def test_analyze_reads_the_store_with_nested_access(proj):
    pytest.importorskip("duckdb")
    pytest.importorskip("sqlglot")
    from app.mcp.tools.dataset_tools import AnalyzeTool
    _store(proj, schema="ziya:finding", data=FINDING)
    _store(proj, schema="ziya:finding", data={**FINDING, "id": "T-Z", "verdict": "confirmed",
                                              "impact": {"fleet_total": {"mid": {"value": 100}}}})
    text = asyncio.run(_model_text(proj, AnalyzeTool, {
        "sql": "SELECT key, data.verdict AS verdict, data.impact.fleet_total.mid.value AS mid "
               "FROM o ORDER BY key",
        "inputs": {"o": output_store.relative_path("finding")},
    }))
    assert "T-X" in text and "T-Z" in text and "248000" in text and "confirmed" in text


def test_family_naming():
    assert output_store.family_of(["project:audit-finding", "ziya:finding/v1"], "project:audit-finding") == "finding"
    assert output_store.family_of(["ziya:per-entity-table/v1"], "ziya:per-entity-table/v1") == "per_entity_table"
    assert output_store.family_of(["project:custom"], "project:custom") == "custom"
    assert output_store.family_of([], "inline:abc") == "inline"


def test_store_output_is_registered_and_is_a_write():
    from app.mcp.builtin_tools import get_builtin_tools_for_category
    from app.tool_batch import _READ_ONLY_BUILTINS
    assert StoreOutputTool in get_builtin_tools_for_category("output_contracts")
    assert "store_output" not in _READ_ONLY_BUILTINS


def test_a_collector_outside_any_request_stores_nothing(tmp_path, monkeypatch):
    # No ledger root and no request project root: the hook must not guess cwd.
    from app.utils import output_contracts as oc, task_artifacts as ta
    monkeypatch.chdir(tmp_path)
    s = oc.SchemaSet.resolve(["ziya:finding/v1"])
    tok = ta.start_artifact_collection(contracts=oc.bind_contracts(
        [{"name": "finding", "schema": "ziya:finding/v1"}], s))
    try:
        ok, _ = ta.emit_part({"part_type": "data", "name": "finding", "data": FINDING})
        assert ok and "stored_id" not in ta.collected_parts()[0]
    finally:
        ta.finish_artifact_collection(tok)
    assert not (tmp_path / ".ziya" / "outputs").exists()
