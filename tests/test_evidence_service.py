"""ledger_record / ledger_query / ledger_verify logic (app/utils/evidence/service.py).

The MCP seam is tests/test_ledger_tool_seam.py.
"""
from pathlib import Path

import pytest

from app.utils.evidence import service as svc
from app.utils.evidence import store as st


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    st._ledgers.clear()


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    (p / "evidence").mkdir(parents=True)
    (p / "evidence" / "s1.csv").write_text("errors,requests\n22464,120000\n")
    return p


def _resolver(root: Path):
    def resolve(p: str) -> Path:
        q = (root / p).resolve()
        if root.resolve() not in q.parents:
            raise ValueError(p)
        return q
    return resolve


def rec(proj, **kw):
    return svc.record(project_root=str(proj), resolve_path=_resolver(proj),
                      provenance={"via": "test"}, **kw)


def test_observation_and_claim_in_one_call_are_linked(proj):
    r = rec(proj,
            observation={"source": "sql", "query_text": "SELECT ...", "result_path": "evidence/s1.csv"},
            claim={"key": "s1.error_rate", "namespace": "audit", "value": 0.1872, "unit": "ratio",
                   "population": {"description": "retried requests", "n": 120000},
                   "derivation": {"column": "cut", "expression": "sum(cut)/sum(paths)"}})
    assert not r.get("error"), r
    assert r["observation"]["attested"] is True
    c = r["claim"]
    assert c["id"] == "audit.s1.error_rate" and c["status"] == "recorded"
    assert c["derivation"]["obs_ids"] == [r["observation"]["id"]]
    led = st.ledger_for(str(proj))
    assert led.claim("audit.s1.error_rate")["provenance"] == {"via": "test"}


def test_result_path_outside_project_is_refused(proj):
    r = rec(proj, observation={"source": "file", "result_path": "../../etc/passwd"})
    assert r["error"] and "outside" in r["message"]
    assert st.ledger_for(str(proj)).read("observation") == []


def test_claim_refusal_keeps_and_reports_the_observation(proj):
    r = rec(proj, observation={"source": "tool", "inline": {"x": 1}},
            claim={"key": "bad id with spaces", "value": 1})
    assert r["error"] and "Invalid claim id" in r["message"]
    assert r["observation"]["attested"] is False


def test_inline_null_is_a_result(proj):
    r = rec(proj, observation={"source": "tool", "inline": None})
    assert not r.get("error"), r


def test_query_by_ref_field_and_prefix(proj):
    o = rec(proj, observation={"source": "tool", "inline": [1]})["observation"]["id"]
    for key, val in (("a", 1), ("a", 2), ("b.per_unit", 3)):
        rec(proj, claim={"key": key, "namespace": "ns", "value": val,
                         "distribution": {"median": 9.5}, "derivation": {"obs_ids": [o]}})
    q = svc.query(project_root=str(proj), ref="b.per_unit.distribution.median", namespace="ns")
    assert q["value"] == 9.5 and q["claim"]["id"] == "ns.b.per_unit"
    h = svc.query(project_root=str(proj), ref="ns.a", history=True)
    assert [v["status"] for v in h["history"]] == ["superseded", "recorded"]
    lst = svc.query(project_root=str(proj), prefix="ns")
    assert lst["count"] == 2 and [c["value"] for c in lst["claims"]] == [2, 3]
    assert svc.query(project_root=str(proj), ref="ns.zz")["error"]


def test_verify_by_prefix_reports_each_claim(proj):
    o = rec(proj, observation={"source": "sql", "result_path": "evidence/s1.csv"})["observation"]["id"]
    rec(proj, claim={"key": "ns.errors", "value": 22464, "derivation": {"obs_ids": [o]}})
    rec(proj, claim={"key": "ns.rate", "value": 22464 / 120000,
                     "derivation": {"inputs": {"c": "ns.errors"}, "expression": "c / 120000"}})
    v = svc.verify(project_root=str(proj), method="rehash", prefix="ns")
    assert v["counts"] == {"agrees": 2}
    v = svc.verify(project_root=str(proj), method="computed", prefix="ns")
    # ns.errors has no expression: reported per claim, the other still verified.
    assert v["counts"] == {"agrees": 1, "error": 1}
    (proj / "evidence" / "s1.csv").write_text("changed\n")
    assert svc.verify(project_root=str(proj), method="rehash", refs=["ns.errors"])["counts"] == {"drifted": 1}
