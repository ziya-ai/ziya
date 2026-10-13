"""Isolated DuckDB execution (app/utils/analytics/engine.py).

The engine-layer tests bypass the guard on purpose: they prove that the
DuckDB lock holds by itself, so a guard bug cannot become a file-read bug.
Each refusal has a positive control showing the registered input still reads.
"""
import json
import os
import time

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("sqlglot")

from app.utils.analytics.engine import AnalyticsError, run_query  # noqa: E402


@pytest.fixture
def files(tmp_path):
    pq = tmp_path / "in.parquet"
    duckdb.sql(f"COPY (SELECT range AS x, range % 2 AS k FROM range(10)) TO '{pq}' (FORMAT parquet)")
    csv = tmp_path / "in.csv"
    csv.write_text("ct,v\na,1\nb,2\na,3\n")
    jl = tmp_path / "in.jsonl"
    jl.write_text("\n".join(json.dumps({"ct": c, "v": v}) for c, v in [("a", 1), ("b", 5)]) + "\n")
    secret = tmp_path / "secret.csv"
    secret.write_text("s\nTOPSECRET\n")
    return {"pq": pq, "csv": csv, "jl": jl, "secret": secret, "dir": tmp_path}


def test_reads_parquet_csv_and_jsonl_inputs(files):
    r = run_query(
        "SELECT (SELECT sum(x) FROM p) AS a, (SELECT sum(v) FROM c) AS b, (SELECT sum(v) FROM j) AS c",
        {"p": files["pq"], "c": files["csv"], "j": files["jl"]},
    )
    assert r.columns == ["a", "b", "c"]
    assert r.rows == [(45, 6, 6)]
    assert r.engine_version == duckdb.__version__


@pytest.mark.parametrize("sql_tpl", [
    "SELECT * FROM read_csv('{secret}')",
    "SELECT * FROM '{secret}'",
    "SELECT * FROM glob('{dir}/*')",
    "COPY (SELECT 1) TO '{dir}/evil.csv'",
    "ATTACH '{dir}/x.duckdb'",
    "INSTALL httpfs",
    "SET enable_external_access=true",
])
def test_engine_lock_holds_without_guard(files, sql_tpl):
    sql = sql_tpl.format(secret=files["secret"], dir=files["dir"])
    with pytest.raises(AnalyticsError):
        run_query(sql, {"p": files["pq"]}, skip_guard=True)
    assert not (files["dir"] / "evil.csv").exists()
    # Positive control: the same locked connection shape still reads its input.
    assert run_query("SELECT count(*) FROM p", {"p": files["pq"]}, skip_guard=True).rows == [(10,)]


def test_guard_rejection_is_reported_as_rejected(files):
    with pytest.raises(AnalyticsError) as e:
        run_query(f"SELECT * FROM read_csv('{files['secret']}')", {"p": files["pq"]})
    assert e.value.kind == "rejected"


def test_binder_error_lists_columns_of_referenced_inputs(files):
    with pytest.raises(AnalyticsError) as e:
        run_query("SELECT nope FROM c", {"c": files["csv"], "p": files["pq"]})
    assert e.value.kind == "binder"
    assert e.value.columns == {"c": ["ct", "v"]}


def test_timeout_interrupts_the_query(files):
    t0 = time.monotonic()
    with pytest.raises(AnalyticsError) as e:
        run_query("SELECT count(*) FROM range(10000000000) a, range(1000) b", {}, timeout_s=0.5)
    assert e.value.kind == "timeout"
    assert time.monotonic() - t0 < 10


def test_row_cap(files):
    with pytest.raises(AnalyticsError) as e:
        run_query("SELECT * FROM range(11)", {}, max_rows=10)
    assert e.value.kind == "too_large"
    assert run_query("SELECT * FROM range(10)", {}, max_rows=10).row_count == 10


def test_macros_are_installed_and_callable(files):
    r = run_query(
        "SELECT * FROM twice(p)",
        {"p": files["pq"]},
        macros=["CREATE MACRO twice(tbl) AS TABLE SELECT x * 2 AS y FROM query_table(tbl::VARCHAR)"],
        macro_names={"twice"},
    )
    assert sorted(v for (v,) in r.rows) == [x * 2 for x in range(10)]


def test_scratch_directory_is_removed(files, monkeypatch):
    import tempfile
    made = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, **k)
        made.append(d)
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    run_query("SELECT 1", {})
    assert made and not os.path.exists(made[0])


def test_invalid_alias_and_missing_file_and_bad_format(files):
    with pytest.raises(AnalyticsError, match="alias"):
        run_query("SELECT 1", {"bad name": files["pq"]})
    with pytest.raises(AnalyticsError, match="not found"):
        run_query("SELECT 1", {"p": files["dir"] / "missing.parquet"})
    odd = files["dir"] / "x.xlsx"
    odd.write_text("x")
    with pytest.raises(AnalyticsError, match="Unsupported"):
        run_query("SELECT 1", {"p": odd})
