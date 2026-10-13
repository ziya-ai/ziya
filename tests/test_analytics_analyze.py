"""``analyze`` logic (app/utils/analytics/analyze.py), below the MCP layer.

The MCP seam (execute_single_tool -> AnalyzeTool) is covered in
tests/test_analyze_tool_seam.py.
"""
import json
from pathlib import Path

import pytest

import app.utils.analytics as analytics
from app.utils.analytics.analyze import run_analyze

OUTAGES = "host_id,s,e\na,0,10\na,5,15\nb,0,4\nc,0,1\n"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    return tmp_path


def _resolver(root: Path):
    def resolve(p: str) -> Path:
        q = (root / p).resolve()
        if root.resolve() not in q.parents:
            raise ValueError(p)
        return q
    return resolve


def _call(root, sql, **kw):
    return run_analyze(sql=sql, resolve_path=_resolver(root), **kw)


duckdb = pytest.importorskip("duckdb")
pytest.importorskip("sqlglot")

from app.utils import dataset_store as ds  # noqa: E402


@pytest.fixture
def registered(home):
    src = home / "out.csv"
    src.write_text(OUTAGES)
    return ds.register_dataset(path=src)


def test_result_is_a_dataset_with_lineage_and_matching_rows(home, registered):
    did = registered["id"]
    sql = f"SELECT entity, total_s FROM interval_duration({did}, 'host_id', 's', 'e') ORDER BY entity"
    r = _call(home, sql)
    assert not r.get("error"), r
    assert r["id"] != did and r["row_count"] == 3
    lin = r["lineage"]
    assert lin["sql"] == sql and lin["engine_version"] == duckdb.__version__
    assert lin["inputs"] == [{"alias": did, "id": did, "sha256": ds.load_meta(did)["sha256"]}]
    assert r["rows"] == [{"entity": "a", "total_s": 15.0}, {"entity": "b", "total_s": 4.0},
                         {"entity": "c", "total_s": 1.0}]
    # Same rows as reading the stored result back.
    assert ds.read_rows(r["id"])["rows"] == r["rows"]
    assert ds.data_file(r["id"]).suffix == ".parquet"


def test_path_input_is_registered_first(home):
    (home / "proj").mkdir()
    (home / "proj" / "o.csv").write_text(OUTAGES)
    r = _call(home / "proj", "SELECT count(*) AS n FROM o", inputs={"o": "o.csv"})
    assert r["rows"] == [{"n": 4}]
    (entry,) = r["lineage"]["inputs"]
    assert entry["alias"] == "o" and entry["path"].endswith("o.csv")
    assert ds.load_meta(entry["id"])["source"]["kind"] == "file"


def test_path_outside_allowed_dirs_is_refused(home):
    (home / "proj").mkdir()
    r = _call(home / "proj", "SELECT 1", inputs={"x": "../out.csv"})
    assert r["error"] and r["kind"] == "input" and "outside" in r["message"]


def test_unknown_dataset_id_in_inputs(home):
    r = _call(home, "SELECT 1", inputs={"x": "ds_0123456789abcdef"})
    assert r["kind"] == "input" and "unknown dataset" in r["message"]


def test_entity_result_carries_distribution(home, registered):
    did = registered["id"]
    r = _call(home, f"SELECT host_id, count(*) AS n FROM {did} GROUP BY host_id")
    dist = r["distribution"]
    assert dist["entity"] == "host_id"
    assert dist["metrics"]["n"]["n_entities"] == 3
    assert dist["metrics"]["n"]["total"] == 4
    assert dist["metrics"]["n"]["max"] == 2


def test_no_entity_column_no_distribution(home, registered):
    r = _call(home, f"SELECT count(*) AS n FROM {registered['id']}")
    assert "distribution" not in r and not r.get("error")


def test_explicit_entity_overrides_heuristic(home, registered):
    did = registered["id"]
    r = _call(home, f"SELECT host_id AS who, s FROM {did}", entity="who")
    assert r["distribution"]["entity"] == "who"
    assert r["distribution"]["metrics"]["s"]["total"] == 5
    r2 = _call(home, f"SELECT host_id AS who, s FROM {did}")
    assert "distribution" not in r2
    r3 = _call(home, f"SELECT host_id, s FROM {did}", entity="nope")
    assert "distribution" not in r3 and "nope" in r3["distribution_note"]


def test_large_result_is_not_inlined(home):
    r = _call(home, "SELECT range AS x FROM range(500)")
    assert r["row_count"] == 500 and "rows" not in r
    assert r["id"] in r["rows_note"]
    assert r["vega_data"]["url"] == f"ziya://dataset/{r['id']}"
    small = _call(home, "SELECT range AS x FROM range(500)", max_inline_rows=1000)
    assert len(small["rows"]) == 500


def test_wide_rows_respect_byte_cap(home):
    r = _call(home, "SELECT repeat('x', 1000) AS s FROM range(50)")
    assert "rows" not in r


def test_binder_error_lists_columns(home, registered):
    r = _call(home, f"SELECT nope FROM {registered['id']}")
    assert r["kind"] == "binder"
    assert r["columns"] == {registered["id"]: ["host_id", "s", "e"]}


def test_rejection_and_result_is_json_safe(home, registered):
    assert _call(home, "DROP TABLE x")["kind"] == "rejected"
    r = _call(home, f"SELECT host_id, avg(s) AS m, NULL AS z, DATE '2026-01-01' AS d "
                    f"FROM {registered['id']} GROUP BY host_id ORDER BY host_id")
    json.dumps(r)
    assert r["rows"][0]["d"].startswith("2026-01-01")


def test_empty_result_is_stored(home, registered):
    r = _call(home, f"SELECT * FROM {registered['id']} WHERE false")
    assert not r.get("error"), r
    assert r["row_count"] == 0 and r["rows"] == []
