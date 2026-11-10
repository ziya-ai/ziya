"""POST /api/v1/drag/open opens or reveals a project file on the server's
machine (app/api/drag_helper.py, app/utils/drag_file_source.py)."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import drag_helper
from app.utils import drag_file_source as dfs


@pytest.fixture
def root(tmp_path, monkeypatch):
    docs = tmp_path / ".ziya" / "documents"
    docs.mkdir(parents=True)
    (docs / "report.md").write_text("# report\n")
    monkeypatch.setattr("app.mcp.tools.fileio._get_all_readable_prefixes", lambda: [])
    monkeypatch.setattr("app.context.get_project_root_or_none", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def launched(monkeypatch):
    """Capture the argv the route would spawn; nothing is actually opened."""
    calls = []
    monkeypatch.setattr(drag_helper.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    # Pin the opener so the assertions do not depend on the test host's OS.
    monkeypatch.setattr(drag_helper, "open_argv",
                        lambda p, reveal: ["fake-open", "-R" if reveal else "-o", str(p)])
    return calls


def client(host="127.0.0.1"):
    app = FastAPI()
    app.include_router(drag_helper.router)
    return TestClient(app, client=(host, 40000))


def test_open_launches_the_default_app_and_returns_the_file_uri(root, launched):
    r = client().post("/api/v1/drag/open", json={"path": ".ziya/documents/report.md"})
    assert r.status_code == 200
    body = r.json()
    resolved = (root / ".ziya" / "documents" / "report.md").resolve()
    assert body["opened"] is True and body["supported"] is True
    assert body["path"] == str(resolved) and body["uri"] == resolved.as_uri()
    assert launched == [["fake-open", "-o", str(resolved)]]


def test_reveal_selects_the_file_instead(root, launched):
    r = client().post("/api/v1/drag/open",
                      json={"path": ".ziya/documents/report.md", "reveal": True})
    assert r.status_code == 200 and r.json()["reveal"] is True
    assert launched[0][1] == "-R"


def test_absolute_path_inside_the_project_is_accepted(root, launched):
    abs_path = str(root / ".ziya" / "documents" / "report.md")
    assert client().post("/api/v1/drag/open", json={"path": abs_path}).status_code == 200
    assert len(launched) == 1


@pytest.mark.parametrize("path,status", [
    ("", 400), ("../outside.md", 403), ("/etc/passwd", 403),
    (".ziya/documents/missing.md", 404), (".ziya/documents", 404),
])
def test_refusals_spawn_nothing(root, launched, path, status):
    assert client().post("/api/v1/drag/open", json={"path": path}).status_code == status
    assert launched == []


def test_open_has_no_size_cap_unlike_drag(root, launched, monkeypatch):
    monkeypatch.setattr(dfs, "MAX_DRAG_FILE_BYTES", 1)
    with pytest.raises(dfs.DragFileError) as e:
        dfs.resolve_drag_file(".ziya/documents/report.md", str(root), max_bytes=dfs.MAX_DRAG_FILE_BYTES)
    assert e.value.status == 413
    assert client().post("/api/v1/drag/open", json={"path": ".ziya/documents/report.md"}).status_code == 200


def test_remote_browser_gets_the_uri_but_nothing_is_opened(root, launched):
    r = client(host="10.0.0.5").post("/api/v1/drag/open", json={"path": ".ziya/documents/report.md"})
    assert r.status_code == 200
    body = r.json()
    assert body["opened"] is False and body["supported"] is False
    assert body["uri"].startswith("file://") and "browser" in body["reason"]
    assert launched == []


def test_host_without_an_opener_reports_unsupported(root, launched, monkeypatch):
    monkeypatch.setattr(drag_helper, "open_argv", lambda p, reveal: None)
    body = client().post("/api/v1/drag/open", json={"path": ".ziya/documents/report.md"}).json()
    assert body["opened"] is False and body["supported"] is False and launched == []


@pytest.mark.parametrize("host,local", [
    ("127.0.0.1", True), ("::1", True), ("localhost", True), (None, True),
    ("10.0.0.5", False), ("testclient", False),
])
def test_is_local_client(host, local):
    assert dfs.is_local_client(host) is local


def test_open_argv_per_platform(monkeypatch):
    p = Path("/proj/.ziya/documents/report.md")
    assert dfs.open_argv(p, False, "darwin") == ["open", str(p)]
    assert dfs.open_argv(p, True, "darwin") == ["open", "-R", str(p)]
    assert dfs.open_argv(p, False, "win32") == ["cmd", "/c", "start", "", str(p)]
    assert dfs.open_argv(p, True, "win32") == ["explorer", f"/select,{p}"]
    monkeypatch.setattr(dfs.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert dfs.open_argv(p, False, "linux") == ["xdg-open", str(p)]
    assert dfs.open_argv(p, True, "linux") == ["xdg-open", str(p.parent)]
    monkeypatch.setattr(dfs.shutil, "which", lambda name: None)
    assert dfs.open_argv(p, False, "linux") is None
