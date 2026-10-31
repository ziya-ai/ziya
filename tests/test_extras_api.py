"""
/api/extras (app/api/extras.py) -- the HTTP seam over extras_registry.

Pins the status codes the Settings page branches on: 200 list with every
registry key, 202 when a server-installable extra starts, 409 carrying the
paste command when the extra needs a terminal, 404 for an unknown key and for
progress on an install that was never started.  No pip, browser or script
runs: start_install is replaced by a fake run record.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import extras as api
from app.utils import extras_registry as er


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    monkeypatch.setattr(er, "_runs", {})
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_list_names_every_registry_extra(client):
    r = client.get("/api/extras")
    assert r.status_code == 200
    body = r.json()
    assert {x["key"] for x in body["extras"]} == set(er.BY_KEY)
    assert body["install_cmd"] == "ziya-install-extras"
    for x in body["extras"]:
        assert {"installed", "stale", "new_in_this_version", "server_installable",
                "command", "blocked", "missing"} <= set(x)


def test_install_starts_and_reports_progress(client, monkeypatch):
    run = er.InstallRun(key="data", lines=["$ fake"])
    monkeypatch.setattr(er, "start_install", lambda key: run)
    er._runs["data"] = run
    r = client.post("/api/extras/data/install")
    assert r.status_code == 202 and r.json()["done"] is False
    run.done, run.rc = True, 0
    p = client.get("/api/extras/data/install")
    assert p.status_code == 200 and p.json()["ok"] is True


def test_terminal_only_extra_is_refused_with_the_command(client, monkeypatch):
    monkeypatch.setattr(er.Extra, "needs_sudo", lambda self: True)
    r = client.post("/api/extras/latex/install")
    assert r.status_code == 409
    assert r.json()["detail"]["command"] == "ziya-install-extras --latex"


def test_unknown_key_is_404(client):
    assert client.post("/api/extras/nope/install").status_code == 404
    assert client.get("/api/extras/nope/install").status_code == 404
    assert client.get("/api/extras/data/install").status_code == 404, "no install started"


def test_reconcile_endpoint_returns_report(client, monkeypatch):
    monkeypatch.setattr(er.Extra, "missing", lambda self: None)
    r = client.post("/api/extras/reconcile")
    assert r.status_code == 200
    assert set(r.json()) == {"adopted", "refreshed", "notices", "new"}
    assert set(r.json()["adopted"]) == {x.key for x in er.REGISTRY if x.supported()}
