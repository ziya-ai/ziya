"""[external] folders are read only when the user opens them.

External trees can be huge.  They used to be walked in full (10,000
entries, 15 s, a token estimate per file that opens every PDF) when a path
was added and again for every saved path on each server start, which held
up the first /api/folders response for minutes.  Refresh never re-read them,
so files copied into an added folder stayed hidden until a restart.

Now: startup reads nothing, expanding a folder lists that one level, Refresh
re-lists only folders already opened, and a bounded full walk happens only
when the user wants a whole folder (checking it, or adding it to context).

Seams: restore_external_paths_for_project / load_external_dir /
refresh_external_listings -> _folder_cache['[external]'] (what /api/folders
serves), and the /api/external-dir, /api/folders and /api/add-explicit-paths
routes.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import folder_service as fs
from app.utils import external_tree as xt

LAZY = "_lazy"


def _node(proj, abs_path):
    return xt.get_node(fs._folder_cache[str(proj)]["data"]["[external]"], str(abs_path))


@pytest.fixture
def env(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    ext = tmp_path / "Vendor Specs"
    (ext / "Aldrin 3XL").mkdir(parents=True)
    (ext / "Aldrin 3XL" / "errata.pdf").write_text("x")
    html = ext / "CPSS" / "html"
    html.mkdir(parents=True)
    for i in range(5):
        (html / f"p{i}.html").write_text("x")
    (ext / "CPSS" / "README.md").write_text("x")
    (ext / "Roadmap.pdf").write_text("x")
    (ext / "README.md").write_text("x")

    estimated = []

    def estimate(p):
        estimated.append(p)
        return 7

    project = SimpleNamespace(id="p1", settings=SimpleNamespace(externalPaths=[str(ext)]))

    class FakeStorage:
        def __init__(self, *_a, **_k):
            pass

        def get_by_path(self, path):
            return project if path == str(proj) else None

    import app.context as ctx
    import app.storage.projects as projects
    monkeypatch.setattr(ctx, "get_project_root", lambda: str(proj))
    monkeypatch.setattr(projects, "ProjectStorage", FakeStorage)
    monkeypatch.setattr("app.utils.directory_util.estimate_tokens_fast", estimate)
    monkeypatch.setattr(fs, "_folder_cache", {})
    monkeypatch.setattr(fs, "_explicit_external_paths", set())
    monkeypatch.setattr(fs, "_restored_projects", set())
    monkeypatch.setattr(fs, "_schedule_broadcast", lambda *a, **k: None)
    return SimpleNamespace(proj=proj, ext=ext, estimated=estimated)


def test_startup_restore_reads_nothing(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("restore must not list external folders")
    monkeypatch.setattr(xt, "list_dir", boom)
    monkeypatch.setattr(xt, "scan_tree", boom)

    fs.restore_external_paths_for_project(str(env.proj))

    node = _node(env.proj, env.ext)
    assert node is not None and node.get(LAZY) is True
    assert node["children"] == {}
    assert env.estimated == []
    # Still on the allowlist, so it can be opened.
    assert fs.is_under_external_path(str(env.ext / "CPSS"))


def test_expanding_lists_one_level(env):
    fs.restore_external_paths_for_project(str(env.proj))
    node, complete = fs.load_external_dir(str(env.ext))

    assert complete is True
    assert set(node["children"]) == {"Aldrin 3XL", "CPSS", "README.md", "Roadmap.pdf"}
    assert node["children"]["CPSS"].get(LAZY) is True
    assert node["children"]["CPSS"]["children"] == {}
    assert LAZY not in node
    # Only this level's files were estimated.
    assert sorted(p.rsplit("/", 1)[1] for p in env.estimated) == ["README.md", "Roadmap.pdf"]
    # The cache (what /api/folders serves) holds the same listing.
    assert set(_node(env.proj, env.ext)["children"]) == set(node["children"])


def test_refresh_relists_opened_folders_only(env):
    fs.restore_external_paths_for_project(str(env.proj))
    fs.load_external_dir(str(env.ext))
    fs.load_external_dir(str(env.ext / "CPSS"))

    # The original report: PDFs copied in beside a subfolder after it was added.
    (env.ext / "Aldrin3M_Functional_Datasheet.pdf").write_text("x")
    (env.ext / "CPSS" / "new.txt").write_text("x")
    (env.ext / "Aldrin 3XL" / "unopened.pdf").write_text("x")
    env.estimated.clear()

    assert fs.refresh_external_listings(str(env.proj)) == 2

    top = _node(env.proj, env.ext)
    assert "Aldrin3M_Functional_Datasheet.pdf" in top["children"]
    assert "new.txt" in top["children"]["CPSS"]["children"]
    assert top["children"]["CPSS"]["children"]["html"].get(LAZY) is True
    # Never opened: still lazy, and nothing inside it was read.
    assert top["children"]["Aldrin 3XL"].get(LAZY) is True
    assert not any("Aldrin 3XL" in p or "/html/" in p for p in env.estimated)


def test_relisting_keeps_what_was_opened_below(env):
    fs.restore_external_paths_for_project(str(env.proj))
    fs.load_external_dir(str(env.ext))
    fs.load_external_dir(str(env.ext / "CPSS"))
    (env.ext / "Roadmap.pdf").unlink()

    node, _ = fs.load_external_dir(str(env.ext))
    assert "Roadmap.pdf" not in node["children"]
    assert set(node["children"]["CPSS"]["children"]) == {"README.md", "html"}


def test_whole_folder_walk_marks_unfinished_folders_lazy(env):
    real = str(env.proj.resolve())
    full, complete = xt.scan_tree(str(env.ext), real, lambda p: 1)
    assert complete is True
    assert len(full["children"]["CPSS"]["children"]["html"]["children"]) == 5
    assert LAZY not in full

    cut, complete = xt.scan_tree(str(env.ext), real, lambda p: 1, max_entries=6)
    assert complete is False
    # Sorted walk: Aldrin 3XL(1) errata(2) CPSS(3) README(4) html(5) p0(6), then stop.
    html = cut["children"]["CPSS"]["children"]["html"]
    assert html.get(LAZY) is True and set(html["children"]) == {"p0.html"}
    assert cut["children"]["CPSS"].get(LAZY) is True
    assert cut.get(LAZY) is True
    assert "Aldrin 3XL" in cut["children"] and LAZY not in cut["children"]["Aldrin 3XL"]


def test_placeholder_inside_a_later_added_folder_becomes_openable(env):
    parent = env.ext.parent / "Docs"
    child = parent / "Vendor Docs" / "Specs"
    child.mkdir(parents=True)
    (child / "a.pdf").write_text("x")
    (parent / "Vendor Docs" / "other.pdf").write_text("x")

    fs._explicit_external_paths.add(str(child))
    assert fs.add_external_path_to_cache(str(child), mode="shallow")
    # Not under any added path yet: a plain placeholder.
    assert LAZY not in _node(env.proj, parent / "Vendor Docs")

    fs._explicit_external_paths.add(str(parent))
    assert fs.add_external_path_to_cache(str(parent), mode="shallow")
    vd = _node(env.proj, parent / "Vendor Docs")
    assert vd.get(LAZY) is True                      # its other.pdf is unlisted
    assert "a.pdf" in vd["children"]["Specs"]["children"]

    node, _ = fs.load_external_dir(str(parent / "Vendor Docs"))
    assert set(node["children"]) == {"Specs", "other.pdf"}
    assert "a.pdf" in node["children"]["Specs"]["children"]


# ── routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    from app.routes import folder_routes as fr
    monkeypatch.setattr(fr, "get_project_root", lambda: str(env.proj))
    app = FastAPI()
    app.include_router(fr.router)
    return TestClient(app), fr


def test_external_dir_route(env, client):
    c, _fr = client
    fs.restore_external_paths_for_project(str(env.proj))

    r = c.get("/api/external-dir", params={"path": str(env.ext / "CPSS")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == str(env.ext / "CPSS") and body["complete"] is True
    assert set(body["node"]["children"]) == {"README.md", "html"}

    r = c.get("/api/external-dir", params={"path": str(env.ext), "recursive": "true"})
    assert len(r.json()["node"]["children"]["CPSS"]["children"]["html"]["children"]) == 5

    # Outside every added path, including the project itself: refused.
    assert c.get("/api/external-dir", params={"path": str(env.ext.parent)}).status_code == 403
    assert c.get("/api/external-dir", params={"path": str(env.proj)}).status_code == 403
    assert c.get("/api/external-dir",
                 params={"path": str(env.ext / "Roadmap.pdf")}).status_code == 404


def test_folders_refresh_relists_external(env, client, monkeypatch):
    c, fr = client
    calls = []
    monkeypatch.setattr(fr, "refresh_external_listings", lambda root: calls.append(root) or 0)
    monkeypatch.setattr(fr, "get_cached_folder_structure", lambda *a, **k: {})
    monkeypatch.setattr(fr, "get_ignored_patterns", lambda d: [])

    c.get("/api/folders", params={"project_path": str(env.proj)})
    assert calls == []
    c.get("/api/folders", params={"project_path": str(env.proj), "refresh": "true"})
    assert calls == [str(env.proj)]


def test_adding_a_folder_lists_one_level_unless_it_goes_into_context(env, client):
    c, _fr = client
    fs._folder_cache[str(env.proj)] = {"timestamp": 0, "data": {}}

    r = c.post("/api/add-explicit-paths", json={"paths": [str(env.ext)], "add_to_context": False})
    assert r.status_code == 200 and r.json()["added_count"] == 1
    node = _node(env.proj, env.ext)
    assert node["children"]["CPSS"].get(LAZY) is True
    # README lookup reads only the folder's own level.
    assert r.json()["context_keys"] == ["[external]" + str(env.ext / "README.md")]

    r = c.post("/api/add-explicit-paths", json={"paths": [str(env.ext)], "add_to_context": True})
    node = _node(env.proj, env.ext)
    assert len(node["children"]["CPSS"]["children"]["html"]["children"]) == 5
    assert "[external]" + str(env.ext / "CPSS" / "html" / "p0.html") in r.json()["context_keys"]
