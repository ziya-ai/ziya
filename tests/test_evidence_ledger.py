"""Evidence ledger store, verification and citation refs (app/utils/evidence).

design/evidence-ledger.md, Tests: concurrency, supersession, volatile rerun,
write policy.  The export and shell seams are tested with their steps.
"""
import json
import multiprocessing as mp
import os

import pytest

from app.utils.evidence import store as st
from app.utils.evidence.refs import format_value, resolve
from app.utils.evidence.store import Ledger, LedgerError
from app.utils.evidence.verify import evaluate, verify_claim


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    yield
    st._key_cache.clear()


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture
def led(proj):
    return Ledger.for_project(str(proj))


def _obs(led, value=1):
    return led.record_observation("tool", inline={"v": value})["id"]


# -- records and signing -----------------------------------------------------

def test_records_land_signed_under_project_ledger(led, proj):
    oid = _obs(led)
    c = led.record_claim("s1.rate", 0.1872, namespace="ns", unit="ratio",
                         derivation={"obs_ids": [oid], "column": "errors", "expression": "x"})
    assert c["id"] == "ns.s1.rate" and c["version"] == 1 and c["supersedes"] is None
    lines = (proj / ".ziya/ledger/claims.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["sig"]
    assert (proj / ".ziya/ledger/observations.jsonl").is_file()
    assert oct(os.stat(st._key_path()).st_mode & 0o777) == "0o600"


def test_tampered_or_unsigned_line_is_skipped(led, proj):
    oid = _obs(led)
    led.record_claim("ns.a", 1, derivation={"obs_ids": [oid]})
    path = proj / ".ziya/ledger/claims.jsonl"
    rec = json.loads(path.read_text())
    rec["value"] = 999                       # edited by hand, signature now wrong
    forged = dict(rec, id="ns.forged")
    forged.pop("sig")                        # written without the key
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n" + json.dumps(forged) + "\n")
    assert [c["value"] for c in led.read("claim")] == [1]
    assert led.stats()["claim"] == {"records": 1, "rejected": 2}


def test_claim_needs_an_existing_source(led):
    with pytest.raises(LedgerError, match="needs a source"):
        led.record_claim("ns.a", 1)
    with pytest.raises(LedgerError, match="Unknown observation"):
        led.record_claim("ns.a", 1, derivation={"obs_ids": ["obs_0000000000000000"]})
    with pytest.raises(LedgerError, match="Unknown input claim"):
        led.record_claim("ns.a", 1, derivation={"inputs": ["ns.nope"]})
    with pytest.raises(LedgerError, match="Invalid claim id"):
        led.record_claim("nodot", 1, derivation={"obs_ids": [_obs(led)]})
    assert led.read("claim") == []


def test_attestation_follows_who_computed_the_hash(led, proj):
    f = proj / "r.csv"
    f.write_text("a\n1\n")
    filed = led.record_observation("sql", query_text="SELECT 1", result_path=f)
    inline = led.record_observation("tool", inline=[1, 2])
    assert filed["attested"] is True and filed["result"]["sha256"] == st.sha256_file(f)
    assert inline["attested"] is False
    with pytest.raises(LedgerError, match="exactly one"):
        led.record_observation("sql", result_path=f, inline=1)
    with pytest.raises(LedgerError, match="limit"):
        led.record_observation("tool", inline="x" * (st.MAX_INLINE_BYTES + 1))


def test_dataset_observation_uses_the_stores_hash(led, proj):
    from app.utils import dataset_store as ds
    d = ds.register_dataset(csv_text="a,b\n1,2\n")
    o = led.record_observation("sql", dataset_id=d["id"])
    assert o["attested"] and o["result"]["sha256"] == ds.load_meta(d["id"])["sha256"]
    assert o["result"]["columns"] == ["a", "b"]


# -- supersession and status -------------------------------------------------

def test_new_value_supersedes_and_status_is_derived(led):
    oid = _obs(led)
    v1 = led.record_claim("ns.s2.impact", 310_000, derivation={"obs_ids": [oid]})
    v2 = led.record_claim("ns.s2.impact", 250_000, derivation={"obs_ids": [oid]})
    assert (v2["version"], v2["supersedes"]) == (2, "ns.s2.impact@v1")
    assert led.claim("ns.s2.impact")["value"] == 250_000
    assert led.claim("ns.s2.impact@v1")["value"] == 310_000
    assert led.view(v1)["status"] == "superseded"
    assert led.view(v2)["status"] == "recorded" and led.view(v2)["versions"] == 2
    led.record_verdict("ns.s2.impact", method="rehash", outcome="refuted", detail="x")
    assert led.view(led.claim("ns.s2.impact"))["status"] == "refuted"
    # A verdict on v2 says nothing about v1.
    assert led.verdicts("ns.s2.impact@v1") == []


def test_find_by_namespace_returns_latest_versions(led):
    oid = _obs(led)
    for k in ("ns.a", "ns.b.c", "ns.a", "nsx.a"):
        led.record_claim(k, 1, derivation={"obs_ids": [oid]})
    found = led.find("ns")
    assert [(c["id"], c["version"]) for c in found] == [("ns.a", 2), ("ns.b.c", 1)]


# -- concurrency -------------------------------------------------------------

def _worker(args):
    root, home, i, n = args
    os.environ["ZIYA_HOME"] = home
    st._key_cache.clear()
    led = Ledger(root)
    oid = led.record_observation("tool", inline=i)["id"]
    for j in range(n):
        led.record_claim(f"ns.w{i}.c{j}", j, derivation={"obs_ids": [oid]})
        led.record_claim("ns.shared", i * 1000 + j, derivation={"obs_ids": [oid]})


def test_parallel_processes_append_cleanly(tmp_path):
    root = tmp_path / "proj/.ziya/ledger"
    home = os.environ["ZIYA_HOME"]
    n_proc, n = 10, 100
    ctx = mp.get_context("spawn")
    with ctx.Pool(n_proc) as pool:
        pool.map(_worker, [(str(root), home, i, n) for i in range(n_proc)])
    lines = (root / "claims.jsonl").read_text().splitlines()
    assert len(lines) == 2 * n_proc * n
    assert all(json.loads(x)["sig"] for x in lines)
    led = Ledger(root)
    assert len(led.read("claim")) == 2 * n_proc * n            # every line verifies
    shared = [c["version"] for c in led.claim_versions("ns.shared")]
    assert sorted(shared) == list(range(1, n_proc * n + 1))    # no duplicate versions


# -- verification ------------------------------------------------------------

def test_rehash_agrees_then_drifts_when_the_file_changes(led, proj):
    f = proj / "r.csv"
    f.write_text("x\n1\n")
    oid = led.record_observation("sql", result_path=f)["id"]
    led.record_claim("ns.a", 1, derivation={"obs_ids": [oid]})
    assert verify_claim(led, "ns.a", "rehash")["outcome"] == "agrees"
    assert led.view(led.claim("ns.a"))["status"] == "verified"
    f.write_text("x\n2\n")
    v = verify_claim(led, "ns.a", "rehash")
    assert v["outcome"] == "drifted" and "changed" in v["detail"]
    assert led.view(led.claim("ns.a"))["status"] == "drifted"
    f.unlink()
    assert verify_claim(led, "ns.a", "rehash")["outcome"] == "not_testable"


def test_rehash_of_volatile_source_is_not_testable(led, proj):
    f = proj / "live.csv"
    f.write_text("1")
    oid = led.record_observation("sql", result_path=f, volatile=True)["id"]
    led.record_claim("ns.a", 1, derivation={"obs_ids": [oid]})
    f.write_text("2")
    assert verify_claim(led, "ns.a", "rehash")["outcome"] == "not_testable"
    assert led.view(led.claim("ns.a"))["status"] == "recorded"


def test_computed_claim_within_and_outside_tolerance(led):
    # design: "A computed claim over a changed input passes within tolerance
    # and drifts outside it."
    oid = _obs(led)
    led.record_claim("ns.errors", 48000, derivation={"obs_ids": [oid]})
    led.record_claim("ns.cost_s", 48000 * 2.5,
                     derivation={"inputs": ["ns.errors"], "expression": "errors * 2.5"},
                     tolerance={"rel": 0.01})
    v = verify_claim(led, "ns.cost_s", "computed")
    assert v["outcome"] == "agrees"
    assert led.view(led.claim("ns.cost_s"))["attested"] is True
    led.record_claim("ns.errors", 48000 * 1.005, derivation={"obs_ids": [oid]})   # +0.5 %
    assert verify_claim(led, "ns.cost_s", "computed")["outcome"] == "agrees"
    led.record_claim("ns.errors", 48000 * 1.05, derivation={"obs_ids": [oid]})    # +5 %
    v = verify_claim(led, "ns.cost_s", "computed")
    assert v["outcome"] == "drifted" and "now v3" in v["detail"]
    # rehash sees the input has moved on, too.
    assert verify_claim(led, "ns.cost_s", "rehash")["outcome"] == "drifted"


def test_named_inputs_and_binding_collisions(led):
    oid = _obs(led)
    led.record_claim("ns.a.n", 3, derivation={"obs_ids": [oid]})
    led.record_claim("ns.b.n", 4, derivation={"obs_ids": [oid]})
    with pytest.raises(LedgerError, match="bind the name 'n'"):
        led.record_claim("ns.sum", 7, derivation={"inputs": ["ns.a.n", "ns.b.n"], "expression": "n"})
    led.record_claim("ns.sum", 7, derivation={"inputs": {"x": "ns.a.n", "y": "ns.b.n"},
                                              "expression": "x + y"})
    assert verify_claim(led, "ns.sum", "computed")["outcome"] == "agrees"


def test_rerun_is_refused_until_its_runner_exists(led):
    led.record_claim("ns.a", 1, derivation={"obs_ids": [_obs(led)]})
    with pytest.raises(LedgerError, match="not built yet"):
        verify_claim(led, "ns.a", "rerun")
    assert led.read("verdict") == []


@pytest.mark.parametrize("expr", ["__import__('os')", "x.real", "[1][0]", "open('f')",
                                  "2 ** 1000", "y"])
def test_expression_evaluator_allows_arithmetic_only(expr):
    with pytest.raises(LedgerError):
        evaluate(expr, {"x": 2})
    assert evaluate("round(sqrt(x) * 10 + -1, 2)", {"x": 2}) == round(2 ** 0.5 * 10 - 1, 2)


# -- citation refs -----------------------------------------------------------

def test_ref_resolves_longest_claim_prefix_then_field(led):
    oid = _obs(led)
    led.record_claim("ns.s1", 5, derivation={"obs_ids": [oid]})
    led.record_claim("ns.s1.per_unit", 1234.5, derivation={"obs_ids": [oid]},
                     distribution={"n": 640, "median": 418.37}, population={"n": 640})
    r = resolve(led, "s1.per_unit.distribution.median", namespace="ns")
    assert (r["claim"]["id"], r["field"], r["value"]) == ("ns.s1.per_unit", "distribution.median", 418.37)
    assert resolve(led, "ns.s1")["value"] == 5
    assert resolve(led, "s1.per_unit.population.n", "ns")["value"] == 640
    with pytest.raises(LedgerError, match="no field"):
        resolve(led, "s1.per_unit.distribution.p99", "ns")
    with pytest.raises(LedgerError, match="Unknown claim"):
        resolve(led, "s9.x", "ns")


def test_ref_pins_a_version(led):
    oid = _obs(led)
    led.record_claim("ns.a", 1, derivation={"obs_ids": [oid]})
    led.record_claim("ns.a", 2, derivation={"obs_ids": [oid]})
    assert resolve(led, "a@v1", "ns")["value"] == 1
    assert resolve(led, "a@v1", "ns")["claim"]["status"] == "superseded"
    assert resolve(led, "a", "ns")["value"] == 2
    with pytest.raises(LedgerError, match="no version 7"):
        resolve(led, "ns.a@v7")


def test_format_value():
    assert format_value(0.1872, ".2%") == "18.72%"
    assert format_value(250000.3, ",.0f", "host-s") == "250,000 host-s"
    assert format_value("n/a", ".2%") == "n/a"


# -- write policy ------------------------------------------------------------

def test_tools_cannot_write_the_ledger_but_the_server_can(led, proj):
    from app.config.write_policy import WritePolicyManager
    pm = WritePolicyManager()
    pm._project_root = str(proj)
    ok, reason = pm.check_write(".ziya/ledger/claims.jsonl", str(proj))
    assert not ok and "server" in reason.lower()
    led.record_claim("ns.a", 1, derivation={"obs_ids": [_obs(led)]})   # positive control
    assert led.claim("ns.a")["value"] == 1
