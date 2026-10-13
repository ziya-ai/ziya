"""
Output contracts (design/typed-findings.md, steps 1-3 and 7).

Layers:
  * registry: built-in / project / inline schemas, registry-only $ref, pinning;
  * validation: field-level errors as JSON pointers with descriptions, the
    fixed x-ziya-checks kinds, require_claims, claim resolution;
  * the emit seam: through the real ``emit_artifact`` tool, an invalid
    contracted part is NOT recorded and the model is told which field;
  * generated text: the prompt section and the correction message are built
    from the schema, so they cannot disagree with the check.
"""

import asyncio
import json

import pytest

from app.utils import output_contracts as oc
from app.utils import task_artifacts as ta


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    # A contracted emit that passes is appended to the PROJECT's output
    # store (.ziya/outputs/); without a project root that is the cwd, i.e.
    # this repo.  Pin it to the sandbox.
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))


def _lit(v):
    return {"value": v}


def _dist(**over):
    d = {"n": _lit(40), "mean": _lit(7), "median": _lit(5), "p10": _lit(1),
         "p90": _lit(12), "sd": _lit(3), "unit": "s/day"}
    d.update(over)
    return d


def _finding(**impact):
    f = {"id": "T-X", "title": "t", "verdict": "candidate"}
    if impact:
        f["impact"] = impact
    return f


@pytest.fixture
def schemas():
    return oc.SchemaSet.resolve(["ziya:finding/v1", "ziya:per-entity-table/v1", "ziya:text"])


# ── registry ──────────────────────────────────────────────────────────────

def test_shipped_schemas_compile_and_unversioned_means_latest(schemas):
    assert {"ziya:finding/v1", "ziya:number/v1", "ziya:distribution/v1",
            "ziya:per-entity-table/v1", "ziya:text/v1"} <= set(oc.builtin_names())
    # $ref targets are pulled in transitively and pinned too.
    assert {"ziya:number/v1", "ziya:distribution/v1"} <= set(schemas.docs)
    assert schemas.id_for("ziya:text") == "ziya:text/v1"
    for sid in schemas.docs:
        schemas.compile(sid)


@pytest.mark.parametrize("ref", ["ziya:nope/v1", "http://x/s.json", "ziya:finding/v9", "project:"])
def test_unknown_references_are_refused(ref, tmp_path):
    with pytest.raises(oc.ContractError):
        oc.SchemaSet.resolve([ref], str(tmp_path))


def test_remote_and_file_refs_inside_a_schema_are_refused(tmp_path):
    for bad in ("https://example.com/s.json", "other.json#/x"):
        with pytest.raises(oc.ContractError, match="not allowed"):
            oc.SchemaSet.resolve([{"properties": {"a": {"$ref": bad}}}], str(tmp_path))
    # Positive control: a registry ref and a same-document fragment are fine.
    oc.SchemaSet.resolve([{"$defs": {"x": {"type": "string"}},
                           "properties": {"a": {"$ref": "#/$defs/x"}, "b": {"$ref": "ziya:number/v1"}}}])


def test_inline_schema_is_capped():
    big = {"description": "x" * (oc.INLINE_SCHEMA_MAX_BYTES + 1)}
    with pytest.raises(oc.ContractError, match="limit"):
        oc.SchemaSet.resolve([big])


def test_unknown_check_kind_is_a_schema_error():
    with pytest.raises(oc.ContractError, match="x-ziya-checks"):
        oc.SchemaSet.resolve([{"x-ziya-checks": [{"expr": "a > b"}]}])


def _project_schema(root, name, schema):
    d = root / ".ziya" / "schemas"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(schema))


AUDIT = {
    "$id": "project:audit-finding",
    "allOf": [{"$ref": "ziya:finding/v1"}],
    "properties": {"impact": {"required": ["per_unit", "fleet_total", "reconciliation"]}},
    "required": ["impact"],
}


def test_project_schema_extends_a_shipped_one(tmp_path):
    _project_schema(tmp_path, "audit-finding", AUDIT)
    s = oc.SchemaSet.resolve(["project:audit-finding"], str(tmp_path))
    c = s.compile("project:audit-finding")
    errs = oc.validate_instance(c, _finding(per_unit=_dist())).errors
    assert "/impact/fleet_total: required (total cost across the population)" in errs
    # The base schema still applies through allOf.
    assert any(e.startswith("/verdict") for e in
               oc.validate_instance(c, {**_finding(), "verdict": "maybe"}).errors)


def test_pinning_survives_an_edit_after_launch(tmp_path):
    _project_schema(tmp_path, "p", {"type": "object", "required": ["a"]})
    snap = oc.SchemaSet.resolve(["project:p"], str(tmp_path)).to_snapshot()
    _project_schema(tmp_path, "p", {"type": "object"})              # weakened mid-run
    pinned = oc.SchemaSet.from_snapshot(snap).compile("project:p")
    assert not oc.validate_instance(pinned, {}).ok, "the launch version still applies"
    fresh = oc.SchemaSet.resolve(["project:p"], str(tmp_path)).compile("project:p")
    assert oc.validate_instance(fresh, {}).ok
    assert fresh.sha256 != pinned.sha256


def test_card_schema_refs_walk_every_contract_field(tmp_path):
    _project_schema(tmp_path, "p", {"type": "object"})
    card = {"block_type": "group", "body": [
        {"block_type": "state", "output_contract_defaults": [{"name": "a", "schema": "ziya:text"}]},
        {"block_type": "parallel", "gather_contract": [{"name": "f", "schema": "project:p"}], "body": [
            {"block_type": "task", "output_contract": [{"name": "f", "schema": "ziya:finding/v1"}]}]},
    ]}
    assert oc.card_schema_refs(card) == ["ziya:text", "project:p", "ziya:finding/v1"]
    snap = oc.snapshot_for_card(card, str(tmp_path))
    assert {"ziya:text/v1", "project:p", "ziya:finding/v1", "ziya:number/v1"} <= set(snap["schemas"])
    assert oc.snapshot_for_card({"block_type": "task"}, str(tmp_path)) is None


# ── validation ────────────────────────────────────────────────────────────

def test_a_missing_field_is_named_with_its_description(schemas):
    c = schemas.compile("ziya:finding/v1")
    per_unit = _dist()
    del per_unit["sd"]
    errs = oc.validate_instance(c, _finding(per_unit=per_unit)).errors
    assert errs == ["/impact/per_unit/sd: required (standard deviation per unit)"]


def test_a_complete_finding_passes(schemas):
    c = schemas.compile("ziya:finding/v1")
    f = _finding(per_unit=_dist(), population={"n_total": _lit(100), "n_affected": _lit(40)},
                 fleet_total={"low": _lit(200), "mid": _lit(280), "high": _lit(300)},
                 reconciliation={"per_unit_sum": _lit(282), "fleet_mid": _lit(280), "tolerance": 0.05})
    r = oc.validate_instance(c, f)
    assert r.ok, r.errors
    assert "/impact/per_unit/median" in r.unverified


@pytest.mark.parametrize("impact, expect", [
    ({"per_unit": _dist(p10=_lit(9))}, "p10 (9) must not exceed median (5)"),
    ({"per_unit": _dist(sd=_lit(-1))}, "sd is -1; the minimum is 0"),
    ({"population": {"n_total": _lit(10), "n_affected": _lit(11)}}, "n_affected (11) exceeds"),
    ({"fleet_total": {"low": _lit(5), "mid": _lit(3)}}, "low (5) must not exceed mid (3)"),
    ({"reconciliation": {"per_unit_sum": _lit(150), "fleet_mid": _lit(100), "tolerance": 0.1}},
     "relative difference 0.5 exceeds tolerance 0.1"),
])
def test_each_check_kind_fails_with_its_values(schemas, impact, expect):
    errs = oc.validate_instance(schemas.compile("ziya:finding/v1"), _finding(**impact)).errors
    assert any(expect in e for e in errs), errs


def test_known_verdict_requires_known_issue(schemas):
    c = schemas.compile("ziya:finding/v1")
    assert "/known_issue: required" in oc.validate_instance(c, {**_finding(), "verdict": "known"}).errors[0]
    ok = {**_finding(), "verdict": "known", "known_issue": {"id": "NET-1", "status": "open"}}
    assert oc.validate_instance(c, ok).ok


def test_require_claims_rejects_literals(schemas):
    c = schemas.compile("ziya:finding/v1")
    r = oc.validate_instance(c, _finding(per_unit=_dist()), require_claims=True)
    assert not r.ok and any("/impact/per_unit/median: a literal number" in e for e in r.errors)


def test_per_entity_table_needs_a_column_dictionary_and_a_source(schemas):
    c = schemas.compile("ziya:per-entity-table/v1")
    base = {"entity_kind": "ct", "dataset": "ds_1", "key_column": "ct", "value_column": "L", "unit": "s"}
    assert any("/columns: required" in e for e in oc.validate_instance(c, base).errors)
    good = {**base, "columns": {"ct": "terminal id", "L": "episode length, seconds"}}
    assert oc.validate_instance(c, good).ok
    nosrc = {k: v for k, v in good.items() if k != "dataset"}
    assert not oc.validate_instance(c, nosrc).ok


# ── claims ────────────────────────────────────────────────────────────────

def _fake_lookup(table):
    def lookup(ref):
        if ref not in table:
            raise LookupError(f"Unknown claim: {ref}.")
        return table[ref]
    return lookup


def test_claims_resolve_and_are_copied_onto_the_result(schemas):
    look = _fake_lookup({
        "ns.d.distribution": {"claim_id": "ns.d", "version": 2, "status": "verified",
                              "value": {"n": 40, "mean": 7, "median": 5, "p10": 9, "p90": 12, "sd": 3}},
        "ns.ok": {"claim_id": "ns.ok", "version": 1, "status": "recorded", "value": 3, "unit": "x"},
    })
    c = schemas.compile("ziya:finding/v1")
    f = _finding(per_unit={"claim": "ns.d", "field": "distribution"})
    f["evidence"] = [{"claim": "ns.ok", "role": "primary"}]
    r = oc.validate_instance(c, f, require_claims=True, claims=look)
    # The distribution check runs on the RESOLVED claim value.
    assert any("p10 (9) must not exceed median (5)" in e for e in r.errors), r.errors
    ids = {x["claim_id"]: x for x in r.resolved_claims}
    assert ids["ns.ok"]["value"] == 3 and ids["ns.d"]["version"] == 2


def test_unknown_and_refuted_claims_are_rejected(schemas):
    look = _fake_lookup({"ns.bad": {"claim_id": "ns.bad", "version": 1, "status": "refuted", "value": 1}})
    c = schemas.compile("ziya:finding/v1")
    f = _finding(population={"n_total": {"claim": "ns.bad"}, "n_affected": {"claim": "ns.gone"}})
    errs = oc.validate_instance(c, f, claims=look).errors
    assert any("ns.bad is refuted" in e for e in errs)
    assert any("'ns.gone' cannot be used" in e for e in errs)
    # With no ledger at all, a claim reference cannot be checked either.
    assert any("no evidence ledger" in e for e in oc.validate_instance(c, f).errors)


def test_claims_resolve_through_the_real_ledger(tmp_path):
    from app.utils.evidence.store import ledger_for
    led = ledger_for(str(tmp_path))
    oid = led.record_observation("tool", inline={"v": 1})["id"]
    led.record_claim("s1.total", 250_000, namespace="ns", unit="host-s/day",
                     distribution={"n": 40, "mean": 7, "median": 5, "p10": 1, "p90": 12, "sd": 3},
                     derivation={"obs_ids": [oid]})
    look = oc.ledger_claim_lookup(str(tmp_path), namespace="ns")
    s = oc.SchemaSet.resolve(["ziya:finding/v1"])
    f = _finding(per_unit={"claim": "s1.total", "field": "distribution"},
                 fleet_total={"mid": {"claim": "s1.total"}})
    r = oc.validate_instance(s.compile("ziya:finding/v1"), f, require_claims=True, claims=look)
    assert r.ok, r.errors
    mid = next(x for x in r.resolved_claims if x["pointer"] == "/impact/fleet_total/mid")
    assert (mid["claim_id"], mid["value"], mid["status"], mid["unit"]) == ("ns.s1.total", 250_000, "recorded", "host-s/day")


# ── the emit seam ─────────────────────────────────────────────────────────

def _emit(**kw):
    from app.mcp.tools.emit_artifact import EmitArtifactTool
    out = asyncio.run(EmitArtifactTool().execute(**kw))
    return out["content"][0]["text"]


@pytest.fixture
def collector(schemas):
    contracts = oc.bind_contracts(
        [{"name": "finding", "schema": "ziya:finding/v1", "required": True}], schemas)
    tok = ta.start_artifact_collection(block_id="b1", contracts=contracts)
    yield contracts
    ta.finish_artifact_collection(tok)


def test_invalid_contracted_part_is_not_recorded_then_the_fix_is(collector):
    per_unit = _dist()
    del per_unit["sd"]
    msg = _emit(name="finding", part_type="data", data=_finding(per_unit=per_unit))
    assert "rejected by its output contract" in msg
    assert "/impact/per_unit/sd: required (standard deviation per unit)" in msg
    assert ta.collected_parts() == []

    msg = _emit(name="finding", part_type="data", data=_finding(per_unit=_dist()))
    assert "recorded" in msg and "Error" not in msg
    [part] = ta.collected_parts()
    assert part["contract"] == "finding" and part["schema_id"] == "ziya:finding/v1"
    assert part["schema_sha256"] == collector["finding"].schema.sha256
    assert oc.missing_required(collector, ta.collected_parts()) == []


def test_parts_outside_the_contract_are_recorded_as_before(collector):
    msg = _emit(name="notes", text="free text")
    assert "recorded" in msg
    [p] = ta.collected_parts()
    assert "schema_sha256" not in p
    assert [c.name for c in oc.missing_required(collector, ta.collected_parts())] == ["finding"]
    # ...but the model is told the required output is still owed, by name.
    # Live run 33d141aa emitted `alpha_finding`, was told "recorded", and
    # never produced `finding`.
    assert "does NOT satisfy the required output(s) 'finding'" in msg
    assert "name='finding'" in msg
    # Once the required part exists, extra parts carry no note.
    _emit(name="finding", part_type="data", data=_finding())
    assert "Note:" not in _emit(name="more", text="extra")


def test_max_count_is_enforced(collector):
    assert "recorded" in _emit(name="finding", part_type="data", data=_finding())
    msg = _emit(name="finding", part_type="data", data=_finding())
    assert "allows 1" in msg and len(ta.collected_parts()) == 1


def test_uncontracted_collection_is_unchanged():
    tok = ta.start_artifact_collection(block_id="b")
    try:
        assert "recorded" in _emit(name="finding", part_type="data", data={"anything": 1})
    finally:
        assert len(ta.finish_artifact_collection(tok)) == 1


def test_a_text_schema_contract_validates_a_text_part():
    s = oc.SchemaSet.resolve(["ziya:text"])
    tok = ta.start_artifact_collection(contracts=oc.bind_contracts(
        [{"name": "notes", "schema": "ziya:text"}], s))
    try:
        assert "recorded" in _emit(name="notes", text="x")
        assert ta.collected_parts()[0]["schema_id"] == "ziya:text/v1"
    finally:
        ta.finish_artifact_collection(tok)


def test_require_claims_defaults_to_the_evidence_namespace(schemas):
    entry = [{"name": "f", "schema": "ziya:finding/v1"}]
    assert oc.bind_contracts(entry, schemas)["f"].require_claims is False
    assert oc.bind_contracts(entry, schemas, evidence_namespace="ns")["f"].require_claims is True
    explicit = [{**entry[0], "require_claims": False}]
    assert oc.bind_contracts(explicit, schemas, evidence_namespace="ns")["f"].require_claims is False


# ── State-block defaults (step 5) ─────────────────────────────────────────

def test_merge_contract_defaults_by_name_with_explicit_opt_out():
    d = {"finding": {"name": "finding", "schema": "ziya:finding/v1"},
         "notes": {"name": "notes", "schema": "ziya:text"}}
    own = [{"name": "finding", "schema": {"type": "object"}}]
    assert oc.merge_contract_defaults(None, d) == list(d.values())
    assert oc.merge_contract_defaults([], d) == []                 # opt-out kept
    merged = oc.merge_contract_defaults(own, d)
    assert [e["name"] for e in merged] == ["finding", "notes"]
    assert merged[0] is own[0], "the task's own entry wins over the default"
    assert oc.merge_contract_defaults(own, {}) is own
    assert oc.merge_contract_defaults(own, {"finding": d["finding"]}) is own


# ── generated text ────────────────────────────────────────────────────────

def test_prompt_section_and_correction_come_from_the_schema(schemas):
    cs = oc.bind_contracts([{"name": "finding", "schema": "ziya:finding/v1"},
                            {"name": "notes", "schema": "ziya:text", "required": False}], schemas)
    sec = oc.required_outputs_section(cs)
    assert "`finding` (required; schema ziya:finding/v1)" in sec
    assert "`notes` (optional; schema ziya:text/v1)" in sec
    assert "- verdict: one of" in sec and "per_unit: distribution" in sec
    assert oc.required_outputs_section({}) == ""
    corr = oc.correction_message(oc.missing_required(cs, []))
    assert "`finding`" in corr and "`notes`" not in corr
    assert len(corr) < oc.SUMMARY_MAX_CHARS + 1000
