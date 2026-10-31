"""``min_units`` on gather contracts: "k of N units must emit".

Before: a Parallel whose lanes were each ``required: false`` still carried
a gather contract with ``per: child, min: 1``, so one silent lane failed
the whole container.  ``per: total, min: k`` was the workaround on offer,
but it counts PARTS across the container (one lane emitting three
satisfies ``min: 3``) and, on a multi-task Parallel, needs every lane
listed as a producer.  ``min_units`` keeps the per-unit judgement and
asks for a quorum of units instead of all of them.

The validator also warns about the original combination, so it is caught
at launch rather than from a failed run.
"""
from app.models.task_card import Block
from app.utils.task_card_validation import validate_card_tree
from tests.test_gather_contract_seam import (  # noqa: F401  (fixture)
    EMIT, GATHER, _Fake, _calls_for, _lanes, _run, _shortfalls, _t, proj,
)


def _silent(*words):
    return lambda instr, path, nth: [] if any(w in instr for w in words) else EMIT


def _reruns():
    return [e for e in _Fake.events if e.get("type") == "unit_rerun"]


# ── executor ──────────────────────────────────────────────────────────────

def test_one_silent_lane_is_tolerated_when_the_quorum_is_met(proj):
    art = _run(_lanes(min_units=2), proj, _silent("two"))
    # Positive: the silent lane ran.  Under a quorum it owes nothing on its
    # own, so no correction turn and no lane failure.
    assert len(_calls_for("two")) == 1
    assert not art.failed, art.decisions
    assert not _shortfalls()
    assert [s.get("status") for s in art.stages] == ["passed", "passed", "passed"]
    [d] = [d for d in art.decisions if "gather_contract" in d]
    assert "2 of 3" in d and "at least 2 required" in d and "c2 (0)" in d


def test_under_a_quorum_emitted_parts_are_still_validated(proj):
    bad = [dict(name="finding", part_type="data",
                data={"id": "T-X", "title": "t", "verdict": "maybe"})]
    art = _run(_lanes(min_units=2), proj,
               lambda instr, path, nth: bad if "two" in instr else EMIT)
    assert any("rejected by its output contract" in r for r in _Fake.replies)
    assert not art.failed, art.decisions


def test_min_units_equal_to_the_count_keeps_every_lane_required(proj):
    art = _run(_lanes(min_units=3), proj, _silent("two"))
    assert len(_calls_for("two")) == 2, "required push-down: correction turn"
    assert art.failed and art.signature == "gather_contract:finding"


def test_below_the_quorum_the_container_fails_and_says_so(proj):
    art = _run(_lanes(min_units=2), proj, _silent("two", "three"))
    assert art.failed and art.signature == "gather_contract:finding"
    assert any("1 met, at least 2 required" in d for d in art.decisions)
    [ev] = _shortfalls()
    assert {u["unit"] for u in ev["units"]} == {"c2", "c3"}


def test_without_min_units_every_child_is_still_required(proj):
    # Unchanged default: the old behaviour is what the new field relaxes.
    art = _run(_lanes(), proj, _silent("two"))
    assert art.failed and art.signature == "gather_contract:finding"


def test_a_met_quorum_skips_the_rerun_pass(proj):
    art = _run(_lanes(min_units=2, rerun_missing=1), proj, _silent("two"))
    assert not art.failed, art.decisions
    assert not _reruns()
    assert len(_calls_for("two")) == 1


def test_an_unmet_quorum_reruns_the_short_lanes(proj):
    # Two of three silent, quorum 2: the rerun pass fires, told why; the
    # second attempt of lane three emits, which meets the quorum.
    # The re-run prepends its note to the instructions, so key on the note
    # (``nth`` counts calls with identical instructions and resets).
    def behave(instr, path, nth):
        if "two" in instr or ("three" in instr and "[Re-run of" not in instr):
            return []
        return EMIT
    art = _run(_lanes(min_units=2, rerun_missing=1), proj, behave)
    assert _reruns() and set(_reruns()[0]["units"]) == {"c2", "c3"}
    assert len(_calls_for("three")) == 2
    assert not art.failed, art.decisions


def test_an_unmet_quorum_still_reruns(proj):
    # Control for the test above: the re-run pass itself still works.
    art = _run(_lanes(min_units=3, rerun_missing=1), proj, _silent("two"))
    assert _reruns() and _reruns()[0]["units"] == ["c2"]
    assert len(_calls_for("two")) > 2
    assert art.failed


# ── validator ─────────────────────────────────────────────────────────────

def _lane(id_, required=None):
    b = {"block_type": "task", "id": id_, "name": id_, "instructions": "find"}
    if required is not None:
        b["output_contract"] = [{"name": "finding", "schema": "ziya:finding/v1",
                                 "required": required}]
    return b


def _par(lanes, **g):
    return {"block_type": "parallel", "id": "lanes", "name": "Lanes",
            "gather_contract": [{**GATHER, **g}], "body": lanes}


def _v(*body):
    return validate_card_tree(Block(block_type="group", id="g", name="Root", body=list(body)))


def _msgs(res, kind):
    return [f.message for f in getattr(res, kind)]


def _optional_warnings(res):
    return [m for m in _msgs(res, "warnings") if "required: false" in m]


def test_optional_lanes_under_an_all_units_gather_warn():
    res = _v(_par([_lane("a", False), _lane("b", False), _lane("c", True)]))
    assert res.ok, res.summary()
    [w] = _optional_warnings(res)
    assert "a, b" in w and "c," not in w and "min_units" in w and "2 of 3" in w


def test_required_lanes_do_not_warn():
    res = _v(_par([_lane("a", True), _lane("b")]))
    assert res.ok and not _optional_warnings(res)


def test_min_units_below_the_count_silences_the_warning():
    res = _v(_par([_lane("a", False), _lane("b", False), _lane("c", False)], min_units=2))
    assert res.ok, res.summary()
    assert not _optional_warnings(res)


def test_min_units_equal_to_the_count_still_warns():
    res = _v(_par([_lane("a", False), _lane("b")], min_units=2))
    assert _optional_warnings(res)


def test_min_units_errors():
    lanes = [_lane("a"), _lane("b"), _lane("c")]
    cases = {
        "per 'total' has one": _par(lanes, per="total", min_units=2,
                                    producer=["a", "b", "c"]),
        "lets the container pass with nothing": _par(lanes, min_units=0),
        "exceeds the 3 unit(s)": _par(lanes, min_units=4),
    }
    for needle, block in cases.items():
        errs = _msgs(_v(block), "errors")
        assert any(needle in e for e in errs), (needle, errs)
    assert _v(_par(lanes, min_units=3)).ok


def test_a_non_integer_min_units_is_refused_when_the_card_is_parsed():
    # GatherContract.min_units is Optional[int]: the model rejects it before
    # the validator runs (which also guards it, for raw dict callers).
    import pydantic
    import pytest
    with pytest.raises(pydantic.ValidationError, match="min_units"):
        _v(_par([_lane("a"), _lane("b")], min_units="most"))
    # Positive: an integer string coerces, as for other int fields.
    assert _v(_par([_lane("a"), _lane("b")], min_units="2")).ok


def test_min_units_on_a_counted_repeat_is_bounded_by_its_count():
    rep = {"block_type": "repeat", "id": "r", "name": "R", "repeat_count": 2,
           "gather_contract": [{**GATHER, "per": "iteration", "min_units": 3}],
           "body": [_lane("x")]}
    assert any("exceeds the 2 unit(s)" in e for e in _msgs(_v(rep), "errors"))
    rep["gather_contract"][0]["min_units"] = 1
    assert _v(rep).ok, _v(rep).summary()
