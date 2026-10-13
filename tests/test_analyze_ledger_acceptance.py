"""Acceptance for the analytics + evidence-ledger seam, on synthetic data.

design/local-analytics.md (Acceptance) and design/evidence-ledger.md (step 9):
per-entity figures computed with ``analyze`` from raw CSVs are recorded as
attested ``file`` observations, ``analyze``-result (``dataset``) observations
and claims; the reconciliation table is then rebuilt from the ledger ALONE
(no recomputation) and compared with values worked out by hand below; every
claim re-hashes unchanged; a changed raw CSV drifts exactly the claims
derived from it; and a document citing the claims exports their values.

The fixture data is invented.  Hand-derived expectations:

  outages.csv (host_id, s, e) through ``interval_duration`` (overlaps merge):
      a: [0,10] U [5,15] = 15 s     b: [0,4] = 4 s
      c: [20,21] U [30,35] = 6 s    d: [0,2] = 2 s          total 27 s
  retries.csv (host_id, requests, errors) summed per host:
      a: 7+3 = 10   b: 4   c: 0   d: 6                       total 20
"""
import math
import shutil
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("sqlglot")

from app.utils.analytics.analyze import run_analyze  # noqa: E402
from app.utils.evidence import service, store as st  # noqa: E402

NS = "audit-synthetic"
UNIT = {"s1.outage": "s", "s2.errors": "errors"}
PROVENANCE = {"via": "test", "script": "tests/test_analyze_ledger_acceptance.py"}

RAW = {
    "outages": ("raw/outages.csv",
                "host_id,s,e\na,0,10\na,5,15\nb,0,4\nc,20,21\nc,30,35\nd,0,2\n"),
    "retries": ("raw/retries.csv",
                "host_id,requests,errors\na,100,7\na,50,3\nb,200,4\nc,80,0\nd,120,6\n"),
}

EXPECTED = {
    "s1.outage": {"n": 4, "mean": 6.75, "median": 5.0, "max": 15.0, "total": 27.0},
    "s2.errors": {"n": 4, "mean": 5.0, "median": 5.0, "max": 10.0, "total": 20.0},
}


def targets(ds):
    """Per-host figures with ``ds(raw alias) -> dataset id`` substituted."""
    return [
        dict(key="s1.outage", raw="outages", metric="total_s",
             cte=f"WITH g AS (SELECT entity AS host_id, total_s "
                 f"FROM interval_duration({ds('outages')}, 'host_id', 's', 'e'))",
             population="hosts with at least one outage interval"),
        dict(key="s2.errors", raw="retries", metric="errors",
             cte=f"WITH g AS (SELECT host_id, sum(errors) AS errors "
                 f"FROM {ds('retries')} GROUP BY host_id)",
             population="hosts that served requests"),
    ]


def distribution_sql(t):
    return f"{t['cte']} SELECT * FROM entity_distribution(g, 'host_id', '{t['metric']}')"


def total_sql(t):
    return f"{t['cte']} SELECT sum({t['metric']}) AS t FROM g"


class Replay:
    def __init__(self, evidence: Path):
        self.ev = evidence
        self.registered = {}

    def resolve(self, p):
        q = (self.ev / p).resolve()
        if self.ev.resolve() not in q.parents:
            raise ValueError(p)
        return q

    def run(self, sql):
        r = run_analyze(sql=sql, resolve_path=self.resolve, max_inline_rows=1000)
        assert not r.get("error"), (r, sql)
        return r

    def ds(self, alias):
        rel = RAW[alias][0]
        if alias not in self.registered:
            r = run_analyze(sql="SELECT count(*) AS n FROM x", inputs={"x": rel},
                            resolve_path=self.resolve)
            assert not r.get("error"), r
            self.registered[alias] = r["lineage"]["inputs"][0]["id"]
        return self.registered[alias]


def import_figures(evidence: Path, ledger_root: Path):
    """Record observations and claims; returns ``{key: pin}``."""
    rp = Replay(evidence)
    led = st.ledger_for(str(ledger_root))
    raw_obs = {alias: led.record_observation("file", path=rel, result_path=rp.resolve(rel),
                                             provenance=PROVENANCE)["id"]
               for alias, (rel, _) in RAW.items()}
    pins = {}
    for t in targets(rp.ds):
        sql = distribution_sql(t)
        res = rp.run(sql)
        (row,) = res["rows"]
        obs = led.record_observation("sql", query_text=sql, connector="analyze",
                                     dataset_id=res["id"], provenance=PROVENANCE)["id"]
        (tot,) = rp.run(total_sql(t))["rows"]
        c = led.record_claim(
            t["key"], tot["t"], namespace=NS, unit=UNIT[t["key"]],
            population={"description": t["population"], "n": row["n_entities"]},
            distribution={"n": row["n_entities"], "mean": row["mean"], "median": row["median"],
                          "max": row["max"], "total": row["total"]},
            derivation={"obs_ids": [raw_obs[t["raw"]], obs], "expression": sql,
                        "column": t["metric"]},
            provenance=PROVENANCE)
        pins[t["key"]] = f"{c['id']}@v{c['version']}"
    return pins


def rebuild_from_ledger(ledger_root: Path):
    """``[(key, field, want, got, ok)]`` from ledger claims only."""
    claims = {c["id"][len(NS) + 1:]: c for c in st.ledger_for(str(ledger_root)).find(NS)}
    out = []
    for key, want in EXPECTED.items():
        c = claims.get(key)
        if c is None:
            out.append((key, "claim", key, None, False))
            continue
        dist = c.get("distribution") or {}
        for k in ("n", "mean", "median", "max"):
            got = dist.get(k)
            out.append((key, k, want[k], got,
                        got is not None and math.isclose(got, want[k], abs_tol=1e-9)))
        out.append((key, "total", want["total"], c["value"],
                    math.isclose(c["value"], want["total"], abs_tol=1e-9)))
        # The distribution's own total must reconcile with the fleet total.
        out.append((key, "dist total = total", c["value"], dist.get("total"),
                    dist.get("total") is not None
                    and math.isclose(dist["total"], c["value"], abs_tol=1e-9)))
    return out


@pytest.fixture
def imported(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    st._ledgers.clear()
    ev = tmp_path / "evidence"
    for rel, body in RAW.values():
        (ev / rel).parent.mkdir(parents=True, exist_ok=True)
        (ev / rel).write_text(body)
    root = tmp_path / "proj"
    root.mkdir()
    pins = import_figures(ev, root)
    return {"ev": ev, "root": root, "pins": pins}


def test_table_rebuilt_from_ledger_claims_matches_hand_derived_figures(imported):
    results = rebuild_from_ledger(imported["root"])
    bad = [r for r in results if not r[4]]
    assert len(results) == 2 * 6 and not bad, bad


def test_empty_ledger_fails_the_rebuild(imported, tmp_path):
    # Negative control: the rebuild reads the ledger, it does not recompute.
    results = rebuild_from_ledger(tmp_path / "elsewhere")
    assert results and not any(r[4] for r in results)


def test_claims_carry_their_derivation_and_rehash_agrees(imported):
    led = st.ledger_for(str(imported["root"]))
    c = led.claim(f"{NS}.s2.errors")
    assert c["unit"] == "errors" and c["derivation"]["column"] == "errors"
    assert "entity_distribution" in c["derivation"]["expression"]
    kinds = [led.observation(o)["result"]["ref"]["kind"] for o in c["derivation"]["obs_ids"]]
    assert kinds == ["file", "dataset"]
    assert all(led.observation(o)["attested"] for o in c["derivation"]["obs_ids"])
    v = service.verify(project_root=str(imported["root"]), method="rehash", prefix=NS)
    assert v["counts"] == {"agrees": len(imported["pins"])}


def test_a_changed_raw_file_drifts_only_the_claims_derived_from_it(imported):
    ev, root = imported["ev"], imported["root"]
    p = ev / RAW["outages"][0]
    p.write_text(p.read_text() + "\n")
    v = service.verify(project_root=str(root), method="rehash",
                       refs=[f"{NS}.s1.outage", f"{NS}.s2.errors"])
    out = {r["claim"].split("@")[0].rsplit(".", 1)[-1]: r["outcome"] for r in v["results"]}
    assert out == {"outage": "drifted", "errors": "agrees"}


def test_a_document_cites_the_imported_claims(imported):
    from app.utils.evidence.citations import resolve_claim_citations
    body = ("Outages cost {{claim:s1.outage|fmt=,.0f|unit}} in total; "
            "the median host lost {{claim:s1.outage.distribution.median|fmt=.1f}} s.")
    out, report = resolve_claim_citations(
        body, {"evidence": {"namespace": NS, "policy": "strict"}},
        project_root=str(imported["root"]))
    assert report is not None and not report.missing
    assert "{{claim:" not in out and "⟦missing" not in out
    assert "27 s in total" in out and "median host lost 5.0 s." in out
