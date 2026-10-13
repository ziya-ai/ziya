"""Save-to-path export: the server writes the bundle where the user chose
and reports the absolute path, because a browser download never says where
the file went.

Helper tests run standalone.  The endpoint tests drive the real router with
``save_path`` and check the file on disk plus the path the response
reports.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils.memory_export import (
    default_export_dir, resolve_save_path, write_export_file,
)

FN = "ziya-memory-export-x.json"


# -- helpers -----------------------------------------------------------------

def test_directory_gets_filename_appended(tmp_path):
    assert resolve_save_path(str(tmp_path), FN) == tmp_path.resolve() / FN
    assert resolve_save_path(str(tmp_path) + "/", FN) == tmp_path.resolve() / FN


def test_file_path_kept_and_json_suffix_added(tmp_path):
    assert resolve_save_path(str(tmp_path / "mine.json"), FN) == tmp_path.resolve() / "mine.json"
    assert resolve_save_path(str(tmp_path / "mine"), FN) == tmp_path.resolve() / "mine.json"


def test_tilde_and_relative_resolve_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "out").mkdir()
    assert resolve_save_path("~/out", FN) == tmp_path.resolve() / "out" / FN
    assert resolve_save_path("out", FN) == tmp_path.resolve() / "out" / FN


def test_missing_folder_and_empty_value_rejected(tmp_path):
    with pytest.raises(ValueError, match="Folder does not exist"):
        resolve_save_path(str(tmp_path / "nope" / "a.json"), FN)
    with pytest.raises(ValueError):
        resolve_save_path("   ", FN)


def test_write_refuses_overwrite_unless_asked(tmp_path):
    p = tmp_path / "a.json"
    write_export_file(p, "one")
    assert p.read_text() == "one"
    with pytest.raises(FileExistsError):
        write_export_file(p, "two")
    assert p.read_text() == "one"
    write_export_file(p, "two", overwrite=True)
    assert p.read_text() == "two"
    assert not list(tmp_path.glob(".a.json*.tmp"))  # no temp left behind


def test_default_dir_prefers_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_export_dir() == str(tmp_path)
    (tmp_path / "Downloads").mkdir()
    assert default_export_dir() == str(tmp_path / "Downloads")


# -- endpoint seam -----------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from app.api.memory import router
    from app.models.memory import Memory
    from app.storage.memory import MemoryStorage
    from app.storage.proposals import ProposalsStore
    s = MemoryStorage(memory_dir=tmp_path / "memory")
    s.save_many([Memory(id="m_1", content="Bench uses a flat JSON store for runs.",
                        layer="architecture")])
    app = FastAPI()
    app.include_router(router)
    events = []
    with patch("app.storage.memory.get_memory_storage", return_value=s), \
         patch("app.storage.proposals.get_proposals_store",
               return_value=ProposalsStore(memory_dir=tmp_path / "memory")), \
         patch("app.utils.tool_audit_log.log_security_event",
               side_effect=lambda n, source_tool="", details=None: events.append((n, details or {}))):
        yield TestClient(app), events


def _preview(c):
    pv = c.post("/api/v1/memory/export/preview", json={})
    assert pv.status_code == 200
    return pv.json()


def test_options_and_preview_advertise_save_location(client):
    c, _ = client
    assert c.get("/api/v1/memory/export/options").json()["default_save_dir"]
    pv = _preview(c)
    assert pv["suggested_filename"].startswith("ziya-memory-export-")


def test_save_path_writes_file_and_reports_absolute_path(client, tmp_path):
    c, events = client
    pv = _preview(c)
    out = tmp_path / "out"
    out.mkdir()
    r = c.post("/api/v1/memory/export", json={
        "selection": {}, "fingerprint": pv["fingerprint"], "save_path": str(out)})
    assert r.status_code == 200, r.text
    body = r.json()
    saved = Path(body["path"])
    assert saved.is_absolute() and saved.parent == out.resolve()
    assert saved.name == pv["suggested_filename"] or saved.name.startswith("ziya-memory-export-")
    assert [m["id"] for m in json.loads(saved.read_text())["memories"]] == ["m_1"]
    assert body["total"] == 1
    exported = [d for n, d in events if n == "memory_exported"]
    assert exported and exported[0]["saved_to"] == str(saved)


def test_existing_file_is_409_file_exists_until_overwrite(client, tmp_path):
    c, _ = client
    pv = _preview(c)
    target = tmp_path / "taken.json"
    target.write_text("keep me")
    req = {"selection": {}, "fingerprint": pv["fingerprint"], "save_path": str(target)}
    r = c.post("/api/v1/memory/export", json=req)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "file_exists"
    assert target.read_text() == "keep me"
    r = c.post("/api/v1/memory/export", json={**req, "overwrite": True})
    assert r.status_code == 200
    assert json.loads(target.read_text())["memories"][0]["id"] == "m_1"


def test_missing_folder_is_422(client, tmp_path):
    c, _ = client
    pv = _preview(c)
    r = c.post("/api/v1/memory/export", json={
        "selection": {}, "fingerprint": pv["fingerprint"],
        "save_path": str(tmp_path / "nope" / "x.json")})
    assert r.status_code == 422
    assert "Folder does not exist" in r.json()["detail"]


def test_without_save_path_still_downloads(client):
    c, _ = client
    pv = _preview(c)
    r = c.post("/api/v1/memory/export", json={"selection": {}, "fingerprint": pv["fingerprint"]})
    assert r.status_code == 200
    assert "attachment;" in r.headers["content-disposition"]
