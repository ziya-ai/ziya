"""Parquet storage for datasets (design/local-analytics.md, Storage format).

A phase-1 JSONL dataset is converted on demand; the rows API must return
identical JSON before and after, and the JSONL must survive a failed
verification.
"""
import json

import pytest

pytest.importorskip("duckdb")

import app.utils.analytics as analytics  # noqa: E402
import app.utils.dataset_store as ds  # noqa: E402

CSV = (
    "region,day,units,revenue,ok,note\n"
    "EU,2025-07-01,3,10.5,true,a\n"
    "US,2025-07-02,,7.25,false,\n"
    "EU,2025-07-03,5,,true,c\n"
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def phase1(home, monkeypatch):
    """A dataset registered as a phase-1 install would have: JSONL only."""
    src = home / "in.csv"
    src.write_text(CSV)
    with monkeypatch.context() as m:
        m.setattr(analytics, "available", lambda: False)
        d = ds.register_dataset(path=src)
    ddir = home / "datasets" / d["id"]
    assert sorted(p.name for p in ddir.glob("data.*")) == ["data.jsonl"]
    return d["id"], ddir


def test_migration_keeps_rows_json_identical(phase1):
    did, ddir = phase1
    before = json.dumps(ds.read_rows(did), sort_keys=True)
    assert ds.ensure_parquet(did) == ddir / "data.parquet"
    assert sorted(p.name for p in ddir.glob("data.*")) == ["data.parquet"]
    after = json.dumps(ds.read_rows(did), sort_keys=True)
    assert after == before
    meta = ds.load_meta(did)
    assert meta["storage"] == "parquet"
    assert meta["sha256"] == ds._file_sha256(ddir / "data.parquet")


def test_migration_is_idempotent(phase1):
    did, ddir = phase1
    first = ds.ensure_parquet(did)
    sha = ds.load_meta(did)["sha256"]
    assert ds.ensure_parquet(did) == first
    assert ds.load_meta(did)["sha256"] == sha


def test_failed_verification_keeps_jsonl(phase1, monkeypatch):
    did, ddir = phase1
    monkeypatch.setattr(ds, "_parquet_matches", lambda *a: False)
    assert ds.ensure_parquet(did) == ddir / "data.jsonl"
    assert sorted(p.name for p in ddir.iterdir()) == ["data.jsonl", "meta.json"]
    assert ds.load_meta(did)["storage"] == "jsonl"
    assert ds.read_rows(did)["row_count"] == 3


def test_without_engine_ensure_parquet_is_a_noop(phase1, monkeypatch):
    did, ddir = phase1
    monkeypatch.setattr(analytics, "available", lambda: False)
    assert ds.ensure_parquet(did) == ddir / "data.jsonl"
    assert not (ddir / "data.parquet").exists()


def test_new_dataset_is_parquet_and_records_lineage(home):
    import pandas as pd
    lineage = {"sql": "SELECT 1", "inputs": [{"id": "ds_0000000000000000", "sha256": "x"}]}
    d = ds.register_dataframe(pd.DataFrame({"a": [1, 2]}), name="r",
                              source={"kind": "analyze"}, lineage=lineage)
    assert d["lineage"] == lineage
    assert d["sha256"] == ds._file_sha256(ds.data_file(d["id"]))
    assert ds.data_file(d["id"]).suffix == ".parquet"
    assert ds.read_rows(d["id"])["rows"] == [{"a": 1}, {"a": 2}]


def test_describe_omits_lineage_for_ingested_data(phase1):
    did, _ = phase1
    assert "lineage" not in ds.public_describe(ds.load_meta(did))
