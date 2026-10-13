"""Output contracts at card validation (design/typed-findings.md, step 6).

Two checks, both before any model call:
  * every contract schema reference resolves and compiles;
  * a precise reference ``{{sibling("x").outputs.NAME.path}}`` (or
    ``outputs_all``), where Task ``x`` declares or inherits a contract for
    NAME, names a path that exists in that schema; as a for_each source,
    ``outputs.NAME.path`` must name a list.

Plus the seam: the launch endpoint refuses such a card with 422 and no run.
"""
import json

import pytest

from app.models.task_card import Block
from app.utils.output_contracts import SchemaSet, field_path_problem
from app.utils.task_card_validation import validate_card_tree

FIND = [{"name": "finding", "schema": "ziya:finding/v1"}]


def _lane(contract=FIND, id_="lane"):
    return {"block_type": "task", "id": id_, "name": "Lane", "instructions": "find",
            **({"output_contract": contract} if contract is not None else {})}


def _reader(text, id_="synth"):
    return {"block_type": "task", "id": id_, "name": "Synth", "instructions": text}


def _v(body, **kw):
    return validate_card_tree(Block(block_type="group", id="g", name="Root", body=body), **kw)


def _msgs(res, kind="errors"):
    return [f.message for f in getattr(res, kind)]


# ── path existence ────────────────────────────────────────────────────────

def test_a_typo_in_a_typed_reference_is_an_error_naming_the_field():
    res = _v([_lane(), _reader('use {{sibling("lane").outputs.finding.impact.per_unitt}}')])
    [m] = _msgs(res)
    assert "per_unitt" in m and "/impact" in m and "per_unit" in m
    assert res.errors[0].path == "Root > Synth"


def test_the_correct_path_passes():
    res = _v([_lane(), _reader('use {{sibling("lane").outputs.finding.impact.per_unit.median}} and '
                               '{{sibling("lane").outputs.finding.evidence.0.claim}}')])
    assert res.ok, res.summary()


def test_outputs_all_is_checked_the_same_way():
    bad = _v([_lane(), _reader('{{sibling("lane").outputs_all.finding.titel}}')])
    assert any("titel" in m for m in _msgs(bad))
    assert _v([_lane(), _reader('{{sibling("lane").outputs_all.finding.title}}')]).ok


def test_references_to_untyped_parts_and_blocks_stay_unchecked():
    # Same path, but the lane declares no contract: nothing to check against.
    assert _v([_lane(contract=None), _reader('{{sibling("lane").outputs.finding.zzz}}')]).ok
    # A part name the contract does not declare: unchecked too.
    assert _v([_lane(), _reader('{{sibling("lane").outputs.notes.zzz}}')]).ok


def test_state_defaults_type_the_reference():
    state = {"block_type": "state", "id": "s", "name": "Givens",
             "output_contract_defaults": FIND}
    res = _v([state, _lane(contract=None), _reader('{{sibling("lane").outputs.finding.verdikt}}')])
    assert any("verdikt" in m for m in _msgs(res))
    # An explicit [] opts out, so the reference is untyped again.
    assert _v([state, _lane(contract=[]), _reader('{{sibling("lane").outputs.finding.verdikt}}')]).ok


def test_a_for_each_source_must_name_a_list():
    def loop(src):
        return {"block_type": "repeat", "id": "r", "name": "Each", "repeat_mode": "for_each",
                "repeat_for_each_source": src,
                "body": [{"block_type": "task", "name": "w", "instructions": "do {{item}}"}]}
    bad = _v([_lane(), loop('{{sibling("lane").outputs.finding.title}}')])
    assert any("not a list" in m for m in _msgs(bad))
    assert _v([_lane(), loop('{{sibling("lane").outputs.finding.next_steps}}')]).ok
    # outputs_all is always a list, whatever the path names.
    assert _v([_lane(), loop('{{sibling("lane").outputs_all.finding.title}}')]).ok


# ── schema resolution ─────────────────────────────────────────────────────

def test_an_unknown_schema_is_an_error_on_the_declaring_block():
    res = _v([_lane(contract=[{"name": "finding", "schema": "ziya:findng/v1"}])])
    [f] = res.errors
    assert "no shipped schema ziya:findng/v1" in f.message and f.path == "Root > Lane"
    assert "ziya:finding/v1" in f.message.split("shipped:")[1], "the fix is named"


def test_project_schemas_resolve_with_a_project_root_and_are_skipped_without(tmp_path):
    lane = _lane(contract=[{"name": "finding", "schema": "project:nope"}])
    assert _v([lane]).ok, "no project root: cannot verify, so not refused"
    res = _v([lane], project_root=str(tmp_path))
    assert any("does not exist" in m for m in _msgs(res))
    (tmp_path / ".ziya" / "schemas").mkdir(parents=True)
    (tmp_path / ".ziya" / "schemas" / "nope.json").write_text(json.dumps(
        {"allOf": [{"$ref": "ziya:finding/v1"}]}))
    ok = _v([lane, _reader('{{sibling("lane").outputs.finding.impact.per_unitt}}')],
            project_root=str(tmp_path))
    assert [m for m in _msgs(ok) if "per_unitt" in m], "the project schema types the reference"


def test_entry_problems_and_misplaced_fields():
    res = _v([_lane(contract=[{"name": "", "schema": "ziya:text"},
                              {"name": "a", "schema": "ziya:text"},
                              {"name": "a", "schema": "ziya:text"}]),
              {"block_type": "state", "id": "s", "output_contract": FIND}])
    errs = _msgs(res)
    assert any("has no name" in m for m in errs) and any("twice" in m for m in errs)
    assert any("ignored on a state block" in m for m in _msgs(res, "warnings"))


# ── gather contracts (step 6e) ────────────────────────────────────────────

G = {"name": "finding", "schema": "ziya:finding/v1"}


def _par(body, **g):
    return {"block_type": "parallel", "id": "p", "name": "Lanes",
            "gather_contract": [{**G, **g}], "body": body}


def _grp(id_, *tasks):
    return {"block_type": "group", "id": id_, "name": id_, "body": list(tasks)}


def _plain(id_):
    return {"block_type": "task", "id": id_, "name": id_, "instructions": "x"}


def test_single_task_children_need_no_producer():
    res = _v([_par([_plain("a"), _plain("b")])])
    assert res.ok, res.summary()
    assert not _msgs(res, "warnings"), "enforced now: no 'not enforced yet' warning"


def test_a_child_with_several_tasks_must_name_its_producer():
    res = _v([_par([_grp("lane", _plain("pull"), _plain("write"))])])
    [m] = _msgs(res)
    assert "'lane' contains 2 tasks (pull, write)" in m and "explicit id" in m
    assert _v([_par([_grp("lane", _plain("pull"), _plain("write"))], producer=["write"])]).ok


def test_producer_errors():
    state = {"block_type": "state", "id": "st", "name": "st"}
    outside = _v([_plain("x"), _par([_plain("a")], producer=["x"])])
    assert any("'x' is not a block inside" in m for m in _msgs(outside))
    on_state = _v([_par([_grp("lane", state, _plain("a"))], producer=["st"])])
    assert any("is a state block" in m for m in _msgs(on_state))
    two = _v([_par([_grp("lane", _plain("a"), _plain("b"))], producer=["a", "b"], max=1)])
    assert any("2 producers but max is 1" in m for m in _msgs(two))


def test_a_call_child_warns_that_it_gets_no_correction_turn():
    call = {"block_type": "call", "id": "c", "name": "callee", "call_target": "x"}
    res = _v([_par([call, _plain("a")])])
    assert res.ok and any("no correction turn" in m for m in _msgs(res, "warnings"))


def test_per_must_suit_the_container_and_rerun_placement():
    loop = {"block_type": "repeat", "id": "r", "name": "R", "repeat_mode": "count",
            "repeat_count": 2, "gather_contract": [G], "body": [_plain("a")]}
    assert any("per 'child' applies to parallel" in m for m in _msgs(_v([loop])))
    assert any("per 'iteration' applies to repeat" in m
               for m in _msgs(_v([_par([_plain("a")], per="iteration")])))
    grp = {**_grp("g2", _plain("a")), "gather_contract": [{**G, "rerun_missing": 1}]}
    assert any("rerun_missing is not allowed on a group" in m for m in _msgs(_v([grp])))
    tot = _v([_par([_plain("a")], per="total", rerun_missing=1)])
    assert any("not allowed on per 'total'" in m for m in _msgs(tot))
    ok = _v([_par([_plain("a")], rerun_missing=1)])
    assert ok.ok and any("re-dispatch up to 1 children" in m for m in _msgs(ok, "warnings"))
    seq = {"block_type": "repeat", "id": "r2", "name": "R2", "repeat_mode": "count",
           "repeat_count": 3, "body": [{**_plain("a"), "instructions": "after {{previous.summary}}"}],
           "gather_contract": [{**G, "per": "iteration", "rerun_missing": 1}]}
    warns = _msgs(_v([seq]), "warnings")
    assert any("up to 3 iterations" in m for m in warns)
    assert any("does not re-run" in m for m in warns)


def test_a_descendant_contract_with_an_unrelated_schema_conflicts(tmp_path):
    clash = {**_plain("a"), "output_contract": [{"name": "finding", "schema": "ziya:text"}]}
    assert any("unrelated schema" in m for m in _msgs(_v([_par([clash])])))
    same = {**_plain("a"), "output_contract": [{"name": "finding", "schema": "ziya:finding"}]}
    assert _v([_par([same])]).ok, "an unversioned ref to the same schema is the same schema"
    (tmp_path / ".ziya" / "schemas").mkdir(parents=True)
    (tmp_path / ".ziya" / "schemas" / "nf.json").write_text(json.dumps(
        {"allOf": [{"$ref": "ziya:finding/v1"}], "required": ["impact"]}))
    ext = {**_plain("a"), "output_contract": [{"name": "finding", "schema": "project:nf"}]}
    assert _v([_par([ext])], project_root=str(tmp_path)).ok


def test_a_gathered_containers_outputs_are_typed():
    bad = _v([_par([_plain("a")]), _reader('{{sibling("p").outputs_all.finding.impact.per_unitt}}')])
    assert any("per_unitt" in m for m in _msgs(bad))
    assert _v([_par([_plain("a")]),
               _reader('{{sibling("p").outputs_all.finding.impact.per_unit}}')]).ok


# ── field_path_problem directly ───────────────────────────────────────────

def test_field_path_problem_follows_branches_and_stops_at_open_nodes():
    c = SchemaSet.resolve([{"type": "object", "properties": {
        "any": {}, "num": {"$ref": "ziya:number/v1"}, "s": {"type": "string"}}}]).compile(
        {"type": "object", "properties": {
            "any": {}, "num": {"$ref": "ziya:number/v1"}, "s": {"type": "string"}}})
    assert field_path_problem(c, ["any", "deep", "er"]) is None      # open: unprovable
    assert field_path_problem(c, ["num", "unit"]) is None            # literal branch
    assert field_path_problem(c, ["num", "claim"]) is None           # claim branch
    assert "num" in field_path_problem(c, ["nmu"])
    assert "is a string" in field_path_problem(c, ["s", "x"])


# ── the launch seam ───────────────────────────────────────────────────────

def test_the_launch_endpoint_refuses_a_mistyped_reference(tmp_path):
    import os
    import time
    from unittest.mock import AsyncMock, patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.models.task_card import Artifact

    home = tmp_path / ".ziya"
    pid = "p-oc"
    pdir = home / "projects" / pid
    pdir.mkdir(parents=True)
    now = int(time.time() * 1000)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "T", "path": str(tmp_path), "settings": {},
        "createdAt": now, "lastAccessedAt": now}))
    with patch.dict(os.environ, {"ZIYA_HOME": str(home)}), \
         patch("app.api.task_cards.get_ziya_home", return_value=home), \
         patch("app.api.task_cards.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.execute_block", new=AsyncMock(return_value=Artifact(summary="ok"))):
        from app.api.task_cards import router
        app = FastAPI()
        app.include_router(router)
        tc = TestClient(app)

        def launch(text):
            card = tc.post(f"/api/v1/projects/{pid}/task-cards", json={
                "name": "c", "root": {"block_type": "group", "name": "Root",
                                      "body": [_lane(), _reader(text)]}}).json()
            return tc.post(f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch", json={})

        bad = launch('{{sibling("lane").outputs.finding.impact.per_unitt}}')
        assert bad.status_code == 422 and "per_unitt" in json.dumps(bad.json())
        good = launch('{{sibling("lane").outputs.finding.impact.per_unit}}')
        assert good.status_code != 422, good.text
