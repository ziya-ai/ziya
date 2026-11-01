"""
Seam tests for the datasets-by-reference feature (design/dataset-store.md):

  dataset_* builtin tools  →  app.utils.dataset_store  →  /api/datasets routes

These assert the connections, not just the halves: the tool category is
registered and enabled, a registered dataset's URL round-trips through the
HTTP endpoint the frontend fetches, path validation is the same allowlist
file_read uses, and the endpoint cannot be used as a file reader.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class _IsolatedHome(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("ZIYA_HOME")
        os.environ["ZIYA_HOME"] = self._tmp.name
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("ZIYA_HOME", None)
        else:
            os.environ["ZIYA_HOME"] = self._old_home
        self._tmp.cleanup()


class TestCategoryRegistration(unittest.TestCase):
    def test_datasets_category_registered_and_enabled_by_default(self):
        from app.mcp.builtin_tools import (
            BUILTIN_TOOL_CATEGORIES, get_builtin_tools_for_category, is_builtin_category_enabled,
            invalidate_category_cache,
        )
        self.assertIn("datasets", BUILTIN_TOOL_CATEGORIES)
        os.environ.pop("ZIYA_ENABLE_DATASETS", None)
        invalidate_category_cache()
        self.assertTrue(is_builtin_category_enabled("datasets"))
        names = sorted(t().name for t in get_builtin_tools_for_category("datasets"))
        self.assertEqual(names, ["analyze", "dataset_describe", "dataset_list",
                                 "dataset_preview", "dataset_register"])

    def test_tools_surface_in_enabled_set(self):
        from app.mcp.builtin_tools import get_enabled_builtin_tools, invalidate_category_cache
        os.environ.pop("ZIYA_ENABLE_DATASETS", None)
        invalidate_category_cache()
        names = {t.name for t in get_enabled_builtin_tools()}
        self.assertIn("dataset_register", names)


class TestDatasetTools(_IsolatedHome):
    async def test_register_from_project_file_then_describe_preview_list(self):
        from app.mcp.tools.dataset_tools import (
            DatasetRegisterTool, DatasetDescribeTool, DatasetPreviewTool, DatasetListTool,
        )
        (self.project / "data.csv").write_text("a,b\n1,x\n2,y\n3,z\n")
        reg = await DatasetRegisterTool().execute(path="data.csv", _workspace_path=str(self.project))
        self.assertFalse(reg.get("error"), reg)
        did = reg["id"]
        self.assertEqual(reg["row_count"], 3)
        self.assertEqual(reg["vega_data"]["url"], f"ziya://dataset/{did}")
        self.assertEqual({c["name"]: c["vega_type"] for c in reg["columns"]},
                         {"a": "ordinal", "b": "nominal"})

        desc = await DatasetDescribeTool().execute(id=did, _workspace_path=str(self.project))
        self.assertEqual(desc["id"], did)
        self.assertEqual(desc["row_count"], 3)

        prev = await DatasetPreviewTool().execute(id=did, limit=2, offset=1, columns=["b"])
        self.assertEqual([r["b"] for r in prev["rows"]], ["y", "z"])
        self.assertEqual(prev["columns"], ["b"])

        lst = await DatasetListTool().execute(all_conversations=True)
        self.assertEqual(lst["count"], 1)
        self.assertEqual(lst["datasets"][0]["id"], did)
        self.assertEqual(lst["datasets"][0]["vega_data"]["url"], f"ziya://dataset/{did}")

    async def test_preview_limit_is_capped(self):
        from app.mcp.tools.dataset_tools import DatasetRegisterTool, DatasetPreviewTool
        reg = await DatasetRegisterTool().execute(records=[{"i": i} for i in range(400)])
        prev = await DatasetPreviewTool().execute(id=reg["id"], limit=5000)
        self.assertEqual(prev["returned"], 200)
        self.assertTrue(prev["truncated"])

    async def test_inline_sources(self):
        from app.mcp.tools.dataset_tools import DatasetRegisterTool
        r1 = await DatasetRegisterTool().execute(records=[{"k": "a", "v": 1}], name="from-tool")
        self.assertEqual(r1["name"], "from-tool")
        r2 = await DatasetRegisterTool().execute(csv_text="k\tv\na\t1\n")
        self.assertEqual([c["name"] for c in r2["columns"]], ["k", "v"])

    async def test_path_outside_allowlist_is_refused(self):
        """Ingest must go through the same allowlist file_read uses.

        The readable-prefix list is pinned to [] so the assertion is about
        routing through the validator, not about whether the host's temp
        dir happens to be under a safe-write path (/tmp on Linux is).
        """
        from unittest.mock import patch
        from app.mcp.tools.dataset_tools import DatasetRegisterTool
        outside = Path(self._tmp.name) / "secret.csv"
        outside.write_text("a\n1\n")
        with patch("app.mcp.tools.dataset_tools._get_all_readable_prefixes", return_value=[]):
            res = await DatasetRegisterTool().execute(path=str(outside), _workspace_path=str(self.project))
            self.assertTrue(res.get("error"), res)
            self.assertIn("outside", res["message"])
            res = await DatasetRegisterTool().execute(path="../secret.csv", _workspace_path=str(self.project))
            self.assertTrue(res.get("error"), res)
        # Positive control: with the prefix allowed, the same file ingests.
        with patch("app.mcp.tools.dataset_tools._get_all_readable_prefixes", return_value=[self._tmp.name]):
            res = await DatasetRegisterTool().execute(path=str(outside), _workspace_path=str(self.project))
            self.assertFalse(res.get("error"), res)
            self.assertEqual(res["row_count"], 1)

    async def test_error_paths_are_structured_not_raised(self):
        from app.mcp.tools.dataset_tools import (
            DatasetRegisterTool, DatasetDescribeTool, DatasetPreviewTool,
        )
        res = await DatasetRegisterTool().execute(_workspace_path=str(self.project))
        self.assertTrue(res.get("error"))
        res = await DatasetRegisterTool().execute(path="missing.csv", _workspace_path=str(self.project))
        self.assertTrue(res.get("error"))
        self.assertIn("Not a file", res["message"])
        res = await DatasetDescribeTool().execute(id="ds_0000000000000000")
        self.assertTrue(res.get("error"))
        res = await DatasetPreviewTool().execute(id="../../etc/passwd")
        self.assertTrue(res.get("error"))


class TestDatasetRoutes(_IsolatedHome):
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routes.dataset_routes import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    async def test_tool_url_roundtrips_through_http_rows_endpoint(self):
        """The exact seam the frontend depends on: tool → ziya:// url → /api/datasets/<id>/rows."""
        from app.mcp.tools.dataset_tools import DatasetRegisterTool
        from app.utils.dataset_store import parse_dataset_url
        reg = await DatasetRegisterTool().execute(records=[{"x": 1, "y": "a"}, {"x": 2, "y": "b"}])
        did = parse_dataset_url(reg["vega_data"]["url"])
        self.assertIsNotNone(did)
        c = self._client()

        r = c.get(f"/api/datasets/{did}/rows?limit=50000")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["rows"], [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}])
        self.assertFalse(body["truncated"])

        r = c.get(f"/api/datasets/{did}/rows?limit=1&offset=1&columns=y")
        self.assertEqual(r.json()["rows"], [{"y": "b"}])

        r = c.get(f"/api/datasets/{did}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["vega_data"]["url"], reg["vega_data"]["url"])

        r = c.get(f"/api/datasets/{did}/rows?format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertEqual(r.text.splitlines()[0], "x,y")

        r = c.get("/api/datasets")
        self.assertEqual(r.json()["count"], 1)

        r = c.delete(f"/api/datasets/{did}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get(f"/api/datasets/{did}").status_code, 404)

    def test_endpoint_is_not_a_file_reader(self):
        c = self._client()
        for bad in ("..%2F..%2Fetc%2Fpasswd", "ds_..", "meta.json", "ds_0123456789abcde", "ds_ZZZZZZZZZZZZZZZZ"):
            r = c.get(f"/api/datasets/{bad}/rows")
            # Malformed ids are refused by _validate_id (400). The encoded
            # traversal case is decoded by Starlette before routing, so it
            # fails to match the route at all (404) — also a refusal. Either
            # way the property under test is that no row data is served.
            self.assertIn(r.status_code, (400, 404), bad)
            self.assertNotIn("rows", r.json(), bad)
        # Well-formed but unknown id → 404, and nothing else on disk is consulted.
        r = c.get("/api/datasets/ds_0000000000000000/rows")
        self.assertEqual(r.status_code, 404)
        # A stray file placed inside the store dir is still not servable by name.
        store = Path(self._tmp.name) / "datasets"
        store.mkdir(exist_ok=True)
        (store / "leak.json").write_text(json.dumps({"rows": [1]}))
        self.assertEqual(c.get("/api/datasets/leak.json/rows").status_code, 400)

    def test_limit_above_hard_max_rejected(self):
        c = self._client()
        r = c.get("/api/datasets/ds_0000000000000000/rows?limit=999999")
        self.assertEqual(r.status_code, 422)

    def test_router_is_mounted_on_app(self):
        """server.py must include the router or the frontend fetch 404s."""
        src = Path(__file__).resolve().parents[2] / "app" / "server.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("from app.routes.dataset_routes import router as dataset_router", text)
        self.assertIn("app.include_router(dataset_router)", text)


if __name__ == "__main__":
    unittest.main()
