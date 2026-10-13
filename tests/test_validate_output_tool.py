"""validate_output: output schemas in an ordinary conversation.

Seam: execute_single_tool -> DirectMCPTool -> ValidateOutputTool ->
output_contracts.check_value -> the REAL evidence ledger, asserting on what
the model receives.  Plus: the tool is exposed by default, and a contracted
emit inside a run now carries ``schema_lineage`` (what the run inspector
keys its finding cards on).
"""
import asyncio
import json

import pytest

from app.mcp.tools.output_tools import ValidateOutputTool
from app.utils.evidence import store as st
from tests.test_ledger_tool_seam import _model_text

FINDING = {"id": "T-X", "title": "t", "verdict": "candidate"}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    st._ledgers.clear()
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _run(root, **args):
    """The tool result exactly as the model receives it (JSON text)."""
    return asyncio.run(_model_text(root, ValidateOutputTool, args)).replace('\\"', '"')


def test_an_invalid_finding_reports_the_field_to_the_model(proj):
    per_unit = {"n": {"value": 3}, "mean": {"value": 1}, "median": {"value": 5},
              "p10": {"value": 6}, "p90": {"value": 9}, "unit": "s"}
    text = _run(proj, schema="ziya:finding", data={**FINDING, "impact": {"per_unit": per_unit}})
    assert "/impact/per_unit/sd: required (standard deviation per unit)" in text
    assert "p10 (6) must not exceed median (5)" in text
    assert '"ok": false' in text


def test_a_valid_value_passes_and_without_data_the_fields_are_listed(proj):
    ok = _run(proj, schema="ziya:finding/v1", data=FINDING)
    assert '"ok": true' in ok
    desc = _run(proj, schema="ziya:finding")
    assert "- verdict: one of" in desc and "ziya:finding/v1" in desc


def test_claims_resolve_against_the_projects_ledger(proj):
    led = st.ledger_for(str(proj))
    oid = led.record_observation("tool", inline={"v": 1})["id"]
    led.record_claim("s2.total", 250_000, namespace="ns", unit="host-s/day",
                     derivation={"obs_ids": [oid]})
    data = {**FINDING, "impact": {"fleet_total": {"mid": {"claim": "s2.total"}}}}
    text = _run(proj, schema="ziya:finding", data=data, namespace="ns")
    assert "ns.s2.total" in text and "250000" in text
    lit = {**FINDING, "impact": {"fleet_total": {"mid": {"value": 280}}}}
    refused = _run(proj, schema="ziya:finding", data=lit, namespace="ns")
    assert "a literal number is not allowed" in refused, "a namespace implies require_claims"
    allowed = _run(proj, schema="ziya:finding", data=lit, namespace="ns", require_claims=False)
    assert "unverified_numbers" in allowed


def test_project_schemas_and_bad_references(proj):
    (proj / ".ziya" / "schemas").mkdir(parents=True)
    (proj / ".ziya" / "schemas" / "nf.json").write_text(json.dumps(
        {"allOf": [{"$ref": "ziya:finding/v1"}], "required": ["owner"]}))
    text = _run(proj, schema="project:nf", data=FINDING)
    assert "/owner: required" in text and "ziya:finding/v1" in text   # lineage
    assert "unknown schema reference" in _run(proj, schema="nope", data={})


def test_validate_output_is_on_by_default_and_read_only():
    from app.mcp.builtin_tools import get_builtin_tools_for_category, is_builtin_category_enabled
    from app.tool_batch import _READ_ONLY_BUILTINS
    assert is_builtin_category_enabled("output_contracts")
    assert ValidateOutputTool in get_builtin_tools_for_category("output_contracts")
    assert "validate_output" in _READ_ONLY_BUILTINS


def test_contracted_parts_carry_schema_lineage(tmp_path):
    from app.utils import output_contracts as oc, task_artifacts as ta
    (tmp_path / ".ziya" / "schemas").mkdir(parents=True)
    (tmp_path / ".ziya" / "schemas" / "nf.json").write_text(json.dumps(
        {"allOf": [{"$ref": "ziya:finding/v1"}]}))
    s = oc.SchemaSet.resolve(["project:nf"], str(tmp_path))
    tok = ta.start_artifact_collection(contracts=oc.bind_contracts(
        [{"name": "finding", "schema": "project:nf"}], s))
    try:
        ok, _ = ta.emit_part({"part_type": "data", "name": "finding", "data": FINDING})
        assert ok
        [p] = ta.collected_parts()
        assert p["schema_lineage"] == ["project:nf", "ziya:finding/v1"]
    finally:
        ta.finish_artifact_collection(tok)
