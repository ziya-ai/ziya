"""JSON shapes the model actually writes must register as datasets.

Before this, ``dataset_store._read_path`` handed every ``.json`` file to
``pd.read_json(orient="records")``, which accepts only a top-level array.
The two shapes task-card runs produce — an object of objects keyed by id,
and an object with scalars plus one array member — failed with
"All arrays must be of the same length", so ``analyze`` could not be used
on the very files it was meant to replace.  Every test here fails against
the old reader.

Runs against an isolated ZIYA_HOME.
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

    def _write_json(self, name: str, obj) -> Path:
        p = Path(self._tmp.name) / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p


class TestJsonObjectShapes(_IsolatedHome):
    def test_object_of_objects_becomes_rows_keyed_by_key_column(self):
        from app.utils import dataset_store as ds
        p = self._write_json("defects.json", {
            "D-001": {"engine": "mermaid", "status": "open"},
            "D-002": {"engine": "d3", "status": "fixed"},
        })
        meta = ds.register_dataset(path=p)
        self.assertEqual(meta["row_count"], 2)
        cols = {c["name"] for c in meta["columns"]}
        self.assertEqual(cols, {ds.KEY_COLUMN, "engine", "status"})
        rows = ds.read_rows(meta["id"])["rows"]
        self.assertEqual({r[ds.KEY_COLUMN] for r in rows}, {"D-001", "D-002"})
        self.assertNotIn("record_path", ds.load_meta(meta["id"])["source"])

    def test_single_array_member_is_selected_and_recorded(self):
        from app.utils import dataset_store as ds
        p = self._write_json("backlog.json", {
            "schema": 2,
            "facts": {"bundle": "abc"},
            "defects": [{"id": "D-1", "sev": 2}, {"id": "D-2", "sev": 1}],
        })
        meta = ds.register_dataset(path=p)
        self.assertEqual(meta["row_count"], 2)
        self.assertEqual(ds.load_meta(meta["id"])["source"]["record_path"], "defects")
        self.assertTrue(meta["name"].endswith("#defects"), meta["name"])

    def test_several_array_members_require_record_path(self):
        from app.utils import dataset_store as ds
        p = self._write_json("multi.json", {
            "defects": [{"id": 1}], "merges": [{"id": 2}], "schema": 2,
        })
        with self.assertRaises(ds.DatasetError) as cm:
            ds.register_dataset(path=p)
        self.assertIn("record_path", str(cm.exception))
        self.assertIn("defects", str(cm.exception))
        self.assertIn("merges", str(cm.exception))
        meta = ds.register_dataset(path=p, record_path="merges")
        self.assertEqual(ds.read_rows(meta["id"])["rows"], [{"id": 2}])

    def test_dotted_record_path_reaches_a_nested_member(self):
        from app.utils import dataset_store as ds
        p = self._write_json("nested.json", {
            "run": {"items": [{"k": "a"}, {"k": "b"}, {"k": "c"}]},
        })
        meta = ds.register_dataset(path=p, record_path="run.items")
        self.assertEqual(meta["row_count"], 3)
        self.assertEqual(ds.load_meta(meta["id"])["source"]["record_path"], "run.items")

    def test_missing_record_path_names_available_keys(self):
        from app.utils import dataset_store as ds
        p = self._write_json("x.json", {"a": [{"k": 1}], "b": 3})
        with self.assertRaises(ds.DatasetError) as cm:
            ds.register_dataset(path=p, record_path="zz")
        self.assertIn("'zz'", str(cm.exception))
        self.assertIn("'a'", str(cm.exception))

    def test_object_without_any_rows_is_an_error_not_a_one_row_frame(self):
        from app.utils import dataset_store as ds
        p = self._write_json("scalars.json", {"schema": 2, "count": 5})
        with self.assertRaises(ds.DatasetError) as cm:
            ds.register_dataset(path=p)
        self.assertIn("no array-of-objects member", str(cm.exception))

    def test_top_level_array_still_takes_the_pandas_path(self):
        from app.utils import dataset_store as ds
        p = self._write_json("arr.json", [{"n": 1}, {"n": 2}])
        meta = ds.register_dataset(path=p)
        self.assertEqual(meta["row_count"], 2)
        self.assertNotIn("record_path", ds.load_meta(meta["id"])["source"])


if __name__ == "__main__":
    unittest.main()
