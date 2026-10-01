"""
Skill sources (design/capabilities-hub.md, slice 8a): folder and git providers,
install with provenance, update detection, requires, and the bench routes.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.services import bench as svc
from app.services import skill_sources as ss
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage

PID = "proj1"


def _skill(root: Path, name: str, body: str = "Do the thing.", requires: str = "") -> Path:
    d = root / name
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "run.sh").write_text("echo hi\n")
    fm = f"---\nname: {name}\ndescription: {name} desc\n" + (f"requires: {requires}\n" if requires else "") + "---\n"
    (d / "SKILL.md").write_text(fm + body + "\n")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    storage = SkillStorage(tmp_path / "home" / "projects" / PID, TokenService())
    monkeypatch.setattr(svc, "_skill_storage", lambda pid: storage)
    monkeypatch.setattr(svc, "_mcp_items", lambda: [])
    monkeypatch.setattr(svc, "_builtin_items", lambda: [])
    src = tmp_path / "src"
    _skill(src, "alpha")
    _skill(src, "beta", requires="[mcp:jira, mcp:slack]")
    ss.add_source({"id": "local", "kind": "folder", "path": str(src), "name": "Local"})
    return tmp_path, src, storage


def test_folder_source_lists_details_and_requires(env):
    _, src, _ = env
    s = ss.get_source("local")
    assert s and s.kind == "folder"
    names = {l.id: l for l in s.list()}
    assert set(names) == {"alpha", "beta"}
    assert names["beta"].requires == ["mcp:jira", "mcp:slack"] and names["alpha"].requires == []
    assert names["alpha"].version == s.current_version("alpha") and names["alpha"].version
    b = s.fetch_bundle("alpha")
    assert set(b.files) == {"SKILL.md", "scripts/run.sh"}
    with pytest.raises(KeyError):
        s.fetch_bundle("nope")


def test_install_writes_bundle_and_provenance_and_discovery_sees_it(env):
    tmp, src, storage = env
    root = ss.install_root(None, "user")
    rec = ss.install_skill("local", "alpha", root)
    dest = root / "alpha"
    assert (dest / "SKILL.md").is_file() and (dest / "scripts" / "run.sh").is_file()
    prov = json.loads((dest / ss.PROVENANCE_FILE).read_text())
    assert prov["source"] == "local" and prov["remote_id"] == "alpha" and prov["version"] == rec["version"]
    # the bench now lists it, as a user-tier skill, with its provenance on the detail
    items = {i.spec.key: i for i in svc.collect_items(PID)}
    assert "skill:alpha" in items and items["skill:alpha"].provenance == "user"
    d = svc.item_detail(PID, "skill:alpha")
    assert d["provenance_record"]["source"] == "local" and d["update_available"] is False
    # project layer lands under <root>/.ziya/skills
    proj = tmp / "repo"
    rec2 = ss.install_skill("local", "beta", ss.install_root(str(proj), "project"))
    assert Path(rec2["path"]) == proj / ".ziya" / "skills" / "beta"
    with pytest.raises(ValueError):
        ss.install_root(None, "project")


def test_update_detected_when_source_changes(env):
    _, src, _ = env
    ss.install_skill("local", "alpha", ss.install_root(None, "user"))
    prov = ss.read_provenance(str(ss.install_root(None, "user") / "alpha"))
    assert ss.update_available(prov) is False
    (src / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: v2\n---\nNew body.\n")
    assert ss.update_available(prov) is True
    assert ss.update_available(None) is False


def test_git_source_versions_by_head(env, tmp_path):
    repo = tmp_path / "gitsrc"
    _skill(repo, "gamma")
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True,
                                    env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                                         "GIT_COMMITTER_EMAIL": "t@x", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    run("init", "-q", "-b", "main"); run("add", "."); run("commit", "-qm", "one")
    ss.add_source({"id": "g", "kind": "git", "url": str(repo), "ref": "main", "name": "G"})
    g = ss.get_source("g")
    assert g.kind == "git"
    assert [l.id for l in g.list()] == ["gamma"]
    v1 = g.current_version("gamma")
    assert v1 and len(v1) == 40
    rec = ss.install_skill("g", "gamma", ss.install_root(None, "user"))
    assert rec["version"] == v1
    (repo / "gamma" / "SKILL.md").write_text("---\nname: gamma\ndescription: two\n---\nv2\n")
    run("commit", "-qam", "two")
    g.list()                                                     # refresh
    assert g.current_version("gamma") != v1
    assert ss.update_available(ss.read_provenance(rec["path"])) is True


def test_browse_and_install_routes(env, monkeypatch):
    from fastapi import HTTPException
    from app.api.bench import InstallRequest, SourceRequest, get_browse, get_sources, post_install, post_source
    import app.mcp.manager as mm
    import app.mcp.registry_manager as rm
    from types import SimpleNamespace
    monkeypatch.setattr(mm, "get_mcp_manager", lambda: SimpleNamespace(server_configs={"jira": {}}))
    def no_registry():
        raise RuntimeError("no registry here")
    monkeypatch.setattr(rm, "get_registry_manager", no_registry)

    assert [s["id"] for s in asyncio.run(get_sources(PID))["sources"]] == ["local"]
    # a query answers per source; an unavailable MCP registry is no column at all
    cols = {c["source"]: c for c in asyncio.run(get_browse(PID, "BETA", None, 8))["per_source"]}
    assert set(cols) == {"local"}
    assert [h["id"] for h in cols["local"]["hits"]] == ["beta"] and cols["local"]["total"] == 1
    assert cols["local"]["hits"][0]["installed"] is False
    assert cols["local"]["hits"][0]["missing_requires"] == ["mcp:slack"]      # jira is configured, slack is not
    col = asyncio.run(get_browse(PID, "a", "local", 1))["per_source"][0]
    assert col["total"] == 2 and len(col["hits"]) == 1                         # top-n; the total says the rest

    r = asyncio.run(post_install(PID, InstallRequest(source="local", id="alpha", layer="user", state="always")))
    assert r["installed"]["name"] == "alpha" and r["missing_requires"] == []
    item = next(i for i in r["view"]["items"] if i["key"] == "skill:alpha")
    assert item["placements"]["user"] == "always" and item["effective"] == "always"
    hits = asyncio.run(get_browse(PID, "alpha", None, 8))["per_source"][0]["hits"]
    assert {h["id"]: h["installed"] for h in hits} == {"alpha": True}

    with pytest.raises(HTTPException) as ei:
        asyncio.run(post_install(PID, InstallRequest(source="nope", id="alpha")))
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        asyncio.run(post_source(PID, SourceRequest(id="bad", kind="git")))     # no url
    assert ei.value.status_code == 400
    asyncio.run(post_source(PID, SourceRequest(id="f2", kind="folder", path="/nonexistent")))
    assert asyncio.run(get_browse(PID, "x", "f2", 8))["per_source"] == [
        {"source": "f2", "name": "f2", "kind": "folder", "total": 0, "hits": []}]   # empty, not an error


def test_package_bundle_installs_one_dir_per_skill_where_discovery_looks(env, monkeypatch):
    """An AIM capability (or a git repo of skills) is a PACKAGE: several
    ``<skill>/SKILL.md``.  Each must land directly under the root."""
    class Pkg(ss.SkillSourceProvider):
        kind = "pkg"
        identifier = property(lambda self: "pkg")
        name = property(lambda self: "Pkg")
        def list(self): return [ss.SkillListing("pkg", "SomeSkillSet", "SomeSkillSet", "d", "1.0")]
        def current_version(self, sid): return "1.0"
        def fetch_bundle(self, sid):
            return ss.SkillBundle(self.list()[0], {
                "one/SKILL.md": b"---\nname: one\ndescription: a\n---\nA\n",
                "two/SKILL.md": b"---\nname: two\ndescription: b\nrequires: mcp:x\n---\nB\n",
                "two/scripts/go.sh": b"echo\n", "README.md": b"ignored\n"})
    monkeypatch.setattr(ss, "list_sources", lambda: [Pkg()])
    root = ss.install_root(None, "user")
    rec = ss.install_skill("pkg", "SomeSkillSet", root)
    assert sorted(Path(p).name for p in rec["installed"]) == ["one", "two"]
    assert not (root / "SomeSkillSet").exists()
    assert json.loads((root / "two" / ss.PROVENANCE_FILE).read_text())["requires"] == ["mcp:x"]
    assert (root / "two" / "scripts" / "go.sh").is_file()
    assert {i.spec.key for i in svc.collect_items(PID)} >= {"skill:one", "skill:two"}


# -- find (8b): browse is a query, not a shelf ------------------------------

def _mgr(monkeypatch, cfgs):
    from types import SimpleNamespace
    import app.mcp.manager as mm
    monkeypatch.setattr(mm, "get_mcp_manager", lambda: SimpleNamespace(server_configs=cfgs))


def test_find_home_is_derived_from_this_bench_only(env, monkeypatch):
    from app.services import bench_find
    _, src, _ = env
    cfgs = {"jira": {}, "slack": {}}
    _mgr(monkeypatch, cfgs)
    # nothing installed: no needs, no updates; beta's requires are all on hand -> pairs with
    h = bench_find.home(PID)
    assert h["needs"] == [] and h["updates"] == []
    assert [p["id"] for p in h["pairs_with"]] == ["beta"] and h["pairs_with"][0]["installed"] is False
    # install beta, then lose slack from the config: it NEEDS slack and leaves pairs-with
    ss.install_skill("local", "beta", ss.install_root(None, "user"))
    del cfgs["slack"]
    h = bench_find.home(PID)
    assert h["needs"] == [{"key": "skill:beta", "name": "beta", "missing": ["mcp:slack"]}]
    assert h["pairs_with"] == []
    # the source moves on: beta is listed under updates with both versions
    (src / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: v2\nrequires: [mcp:jira, mcp:slack]\n---\nNew.\n")
    h = bench_find.home(PID)
    assert [u["name"] for u in h["updates"]] == ["beta"]
    u = h["updates"][0]
    assert u["source"] == "local" and u["id"] == "beta" and u["current_version"] != u["installed_version"]


def test_find_home_reads_requires_from_frontmatter_without_provenance(env, monkeypatch):
    from app.services import bench_find
    _mgr(monkeypatch, {})
    _skill(ss.install_root(None, "user"), "handmade", requires="mcp:confluence")
    assert bench_find.home(PID)["needs"] == [{"key": "skill:handmade", "name": "handmade", "missing": ["mcp:confluence"]}]


def test_find_search_fans_out_per_source_and_never_lists_a_registry(env, monkeypatch):
    from types import SimpleNamespace
    from app.services import bench_find
    import app.mcp.registry_manager as rm
    _mgr(monkeypatch, {"jira": {}})
    calls = []

    class Registry(ss.SkillSourceProvider):
        kind = "registry"
        enumerable = False
        identifier = property(lambda self: "reg")
        name = property(lambda self: "Reg")
        def list(self):
            calls.append("list"); return []
        def current_version(self, sid): return "1"
        def fetch_bundle(self, sid): raise KeyError(sid)
        def search(self, q, limit=8):
            calls.append(("search", q, limit))
            return ss.SearchResult(hits=[ss.SkillListing("reg", "Pkg", "Pkg", "d", "1.0", ["mcp:jira"], "Supported", ["x-tool"])], total=1451)
    local = ss.get_source("local")
    monkeypatch.setattr(ss, "list_sources", lambda: [local, Registry()])

    svc_ = SimpleNamespace(service_id="svc-1", service_name="jira-mcp", service_description="Jira",
                           support_level=SimpleNamespace(value="Supported"), provider_metadata={"provider_id": "amazon"})
    hit = SimpleNamespace(service=svc_, matching_tools=[SimpleNamespace(tool_name="search_issues")])
    class RM:
        async def search_services_by_tools(self, query, provider_filter=None): return [hit, hit]
    monkeypatch.setattr(rm, "get_registry_manager", lambda: RM())

    assert bench_find.home(PID)["pairs_with"] == []              # beta needs slack; the registry is not listed
    assert "list" not in calls
    cols = {c["source"]: c for c in asyncio.run(bench_find.search(PID, "x", 1))["per_source"]}
    assert set(cols) == {"local", "reg", "mcp-registry"}
    assert ("search", "x", 1) in calls and "list" not in calls
    reg = cols["reg"]["hits"][0]
    assert cols["reg"]["total"] == 1451 and reg["matched"] == ["x-tool"] and reg["support_level"] == "Supported"
    assert reg["missing_requires"] == [] and reg["installed"] is False
    mcp = cols["mcp-registry"]
    assert mcp["kind"] == "mcp" and mcp["total"] == 2 and len(mcp["hits"]) == 1
    assert mcp["hits"][0] == {**mcp["hits"][0], "id": "svc-1", "name": "jira-mcp", "installed": False,
                              "matched": ["search_issues"], "support_level": "Supported"}
    # a source that fails is an error column, not a failed request
    def down(self, q, limit=8):
        raise RuntimeError("down")
    monkeypatch.setattr(Registry, "search", down)
    cols = {c["source"]: c for c in asyncio.run(bench_find.search(PID, "x", 1))["per_source"]}
    assert cols["reg"]["hits"] == [] and "down" in cols["reg"]["error"]
    # a single-source query asks only that source
    assert [c["source"] for c in asyncio.run(bench_find.search(PID, "x", 1, "local"))["per_source"]] == ["local"]
