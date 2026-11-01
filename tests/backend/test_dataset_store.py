"""
Tests for app.utils.dataset_store — the session-level tabular dataset store
behind the ``dataset_*`` builtin tools and ``/api/datasets``.

Every test runs against an isolated ZIYA_HOME so nothing touches the real
~/.ziya/datasets.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class _IsolatedHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("ZIYA_HOME")
        os.environ["ZIYA_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("ZIYA_HOME", None)
        else:
            os.environ["ZIYA_HOME"] = self._old_home
        self._tmp.cleanup()

    def _write(self, name: str, text: str) -> Path:
        p = Path(self._tmp.name) / name
        p.write_text(text, encoding="utf-8")
        return p


class TestIdsAndUrls(_IsolatedHome):
    def test_new_id_shape_and_uniqueness(self):
        from app.utils.dataset_store import new_dataset_id, is_valid_dataset_id
        ids = {new_dataset_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)
        for i in ids:
            self.assertTrue(is_valid_dataset_id(i), i)

    def test_id_validation_rejects_traversal_and_junk(self):
        from app.utils.dataset_store import is_valid_dataset_id, _dataset_dir, DatasetError
        for bad in ("../etc", "ds_..", "ds_ZZZZZZZZZZZZZZZZ", "ds_0123456789abcde", "", None, 42):
            self.assertFalse(is_valid_dataset_id(bad), bad)
        with self.assertRaises(DatasetError):
            _dataset_dir("../../etc/passwd")

    def test_parse_dataset_url(self):
        from app.utils.dataset_store import parse_dataset_url, dataset_url, new_dataset_id
        did = new_dataset_id()
        self.assertEqual(parse_dataset_url(dataset_url(did)), did)
        self.assertIsNone(parse_dataset_url("http://localhost:6969/api/datasets/" + did))
        self.assertIsNone(parse_dataset_url("ziya://dataset/../x"))
        self.assertIsNone(parse_dataset_url(None))


class TestRegisterAndDescribe(_IsolatedHome):
    def test_register_accepts_str_path(self):
        """register_dataset is a public entry point; a plain str path must
        work, not just a pathlib.Path (regression: _read_path called
        .suffix on a str)."""
        from app.utils import dataset_store as ds
        p = self._write("s.csv", "a,b\n1,2\n")
        d = ds.register_dataset(path=str(p), conversation_id="c", project_root="/proj")
        self.assertEqual(d["row_count"], 1)
        self.assertEqual(d["source"]["path"], str(p))

    def test_csv_roundtrip_with_type_inference(self):
        from app.utils import dataset_store as ds
        p = self._write("sales.csv",
                        "region,revenue,day,rank\n"
                        "EU,10.5,2025-07-01,1\n"
                        "NA,20,2025-07-02,2\n"
                        "APAC,,2025-07-03,3\n")
        d = ds.register_dataset(path=p, conversation_id="conv-1", project_root="/proj")
        self.assertTrue(ds.is_valid_dataset_id(d["id"]))
        self.assertEqual(d["row_count"], 3)
        self.assertEqual(d["vega_data"], {"url": f"ziya://dataset/{d['id']}"})
        cols = {c["name"]: c for c in d["columns"]}
        self.assertEqual(cols["region"]["vega_type"], "nominal")
        # pandas' default NA set would null the literal "NA" (North America);
        # the store must keep it as a value while still nulling "" / "N/A".
        self.assertEqual(cols["region"]["distinct"], 3)
        self.assertEqual(cols["region"]["nulls"], 0)
        self.assertIn("NA", cols["region"]["sample"])
        self.assertEqual(cols["revenue"]["vega_type"], "quantitative")
        self.assertEqual(cols["revenue"]["nulls"], 1)
        self.assertEqual(cols["revenue"]["min"], 10.5)
        self.assertEqual(cols["day"]["vega_type"], "temporal")
        self.assertTrue(str(cols["day"]["min"]).startswith("2025-07-01"))
        self.assertEqual(cols["rank"]["vega_type"], "ordinal")
        self.assertEqual(len(d["head"]), 3)
        # NaN must serialise as null, not the string "nan"
        self.assertIsNone(d["head"][2]["revenue"])

        # On-disk layout
        ddir = Path(self._tmp.name) / "datasets" / d["id"]
        self.assertTrue((ddir / "meta.json").is_file())
        # Exactly one data file: Parquet when DuckDB is installed, else JSONL.
        from app.utils.analytics import available
        expected = "data.parquet" if available() else "data.jsonl"
        present = sorted(p.name for p in ddir.glob("data.*"))
        self.assertEqual(present, [expected])
        meta = json.loads((ddir / "meta.json").read_text())
        self.assertEqual(meta["storage"], expected.split(".")[1])
        self.assertEqual(len(meta["sha256"]), 64)
        self.assertEqual(meta["conversation_id"], "conv-1")
        self.assertEqual(meta["datetime_columns"], ["day"])

        # Read back: types restored, rows JSON-safe
        out = ds.read_rows(d["id"])
        self.assertEqual(out["row_count"], 3)
        self.assertEqual(out["returned"], 3)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["rows"][0]["region"], "EU")
        self.assertTrue(out["rows"][0]["day"].startswith("2025-07-01"))
        self.assertIsNone(out["rows"][2]["revenue"])

    def test_records_and_csv_text_sources(self):
        from app.utils import dataset_store as ds
        d1 = ds.register_dataset(records=[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], name="recs")
        self.assertEqual(d1["name"], "recs")
        self.assertEqual(d1["row_count"], 2)
        d2 = ds.register_dataset(csv_text="k;v\np;1\nq;2\n")
        self.assertEqual([c["name"] for c in d2["columns"]], ["k", "v"])
        self.assertEqual(d2["row_count"], 2)

    def test_exactly_one_source_required(self):
        from app.utils import dataset_store as ds
        with self.assertRaises(ds.DatasetError):
            ds.register_dataset()
        with self.assertRaises(ds.DatasetError):
            ds.register_dataset(records=[{"a": 1}], csv_text="a\n1")

    def test_excel_requires_sheet_when_ambiguous(self):
        try:
            import openpyxl  # noqa: F401
            import pandas as pd
        except ImportError:
            self.skipTest("openpyxl/pandas not installed")
        from app.utils import dataset_store as ds
        p = Path(self._tmp.name) / "book.xlsx"
        with pd.ExcelWriter(p) as w:
            pd.DataFrame({"x": [1, 2]}).to_excel(w, sheet_name="One", index=False)
            pd.DataFrame({"y": [3]}).to_excel(w, sheet_name="Two", index=False)
        with self.assertRaises(ds.DatasetError) as cm:
            ds.register_dataset(path=p)
        self.assertIn("Two", str(cm.exception))
        d = ds.register_dataset(path=p, sheet="Two")
        self.assertEqual(d["name"], "book.xlsx:Two")
        self.assertEqual(d["row_count"], 1)
        with self.assertRaises(ds.DatasetError):
            ds.register_dataset(path=p, sheet="Nope")

    def test_unsupported_extension_and_bad_parse(self):
        from app.utils import dataset_store as ds
        p = self._write("thing.pdf", "%PDF-1.4")
        with self.assertRaises(ds.DatasetError):
            ds.register_dataset(path=p)
        with self.assertRaises(ds.DatasetError):
            ds.register_dataset(records=[1, 2, 3])
        with self.assertRaises(ds.DatasetError):
            ds.register_dataset(csv_text="   ")

    def test_row_cap_enforced(self):
        from app.utils import dataset_store as ds
        old = ds.MAX_INGEST_ROWS
        ds.MAX_INGEST_ROWS = 5
        try:
            with self.assertRaises(ds.DatasetError) as cm:
                ds.register_dataset(records=[{"a": i} for i in range(6)])
            self.assertIn("ingest limit", str(cm.exception))
            ds.register_dataset(records=[{"a": i} for i in range(5)])  # at cap is fine
        finally:
            ds.MAX_INGEST_ROWS = old


class TestReadListDrop(_IsolatedHome):
    def _reg(self, n=10, **kw):
        from app.utils import dataset_store as ds
        return ds.register_dataset(records=[{"i": i, "sq": i * i} for i in range(n)], **kw)

    def test_paging_and_truncation_flag(self):
        from app.utils import dataset_store as ds
        d = self._reg(10)
        page = ds.read_rows(d["id"], limit=4, offset=8)
        self.assertEqual([r["i"] for r in page["rows"]], [8, 9])
        self.assertFalse(page["truncated"])
        page = ds.read_rows(d["id"], limit=3, offset=0)
        self.assertEqual(page["returned"], 3)
        self.assertTrue(page["truncated"])
        # limit is clamped to hard_max
        page = ds.read_rows(d["id"], limit=10_000, hard_max=2)
        self.assertEqual(page["returned"], 2)

    def test_column_projection_and_unknown_column(self):
        from app.utils import dataset_store as ds
        d = self._reg(3)
        out = ds.read_rows(d["id"], columns=["sq"])
        self.assertEqual(out["columns"], ["sq"])
        self.assertEqual(list(out["rows"][0].keys()), ["sq"])
        with self.assertRaises(ds.DatasetError):
            ds.read_rows(d["id"], columns=["nope"])

    def test_csv_export(self):
        from app.utils import dataset_store as ds
        d = self._reg(2)
        csv = ds.rows_as_csv(d["id"])
        self.assertEqual(csv.splitlines()[0], "i,sq")
        self.assertEqual(len(csv.splitlines()), 3)

    def test_list_filters_by_conversation_and_drop(self):
        from app.utils import dataset_store as ds
        a = self._reg(1, conversation_id="c1")
        b = self._reg(1, conversation_id="c2")
        c = self._reg(1)  # unscoped: visible to every conversation
        ids = {m["id"] for m in ds.list_datasets()}
        self.assertEqual(ids, {a["id"], b["id"], c["id"]})
        ids = {m["id"] for m in ds.list_datasets(conversation_id="c1")}
        self.assertEqual(ids, {a["id"], c["id"]})
        self.assertTrue(ds.drop_dataset(a["id"]))
        self.assertFalse(ds.drop_dataset(a["id"]))
        with self.assertRaises(ds.DatasetError):
            ds.load_meta(a["id"])

    def test_unknown_and_invalid_ids(self):
        from app.utils import dataset_store as ds
        with self.assertRaises(ds.DatasetError):
            ds.read_rows("ds_0000000000000000")
        with self.assertRaises(ds.DatasetError):
            ds.read_rows("../meta.json")
        # A stray non-dataset directory in the store is ignored, not served.
        (Path(self._tmp.name) / "datasets" / "junk").mkdir(parents=True)
        self.assertEqual(ds.list_datasets(), [])


if __name__ == "__main__":
    unittest.main()
