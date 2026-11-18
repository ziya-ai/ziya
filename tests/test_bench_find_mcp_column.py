"""
Find view, MCP column: a server is found by name wherever it sits in the
catalogue, best name match first, with its registry and tags; registries the
user switched off are not asked.

Regression (2026-11-14): "aws-outlook-mcp" was the 3,524th of 6,603 services
in its registry; the registries' own search inspects only the first page or
so, so the column said "no match" while the registry panel found it.
"""
import asyncio
from types import SimpleNamespace

import app.mcp.registry_manager as rm
from app.services import bench_find
from app.services import skill_sources as ss


def _svc(sid, name=None, desc="", provider="reg-a", tags=None, available_in=None):
    meta = {"provider_id": provider}
    if available_in:
        meta["available_in"] = available_in
    return SimpleNamespace(service_id=sid, service_name=name or sid, service_description=desc,
                           tags=tags or [], support_level=SimpleNamespace(value="Supported"),
                           provider_metadata=meta)


def _tool_hit(svc, *tools):
    return SimpleNamespace(service=svc, matching_tools=[SimpleNamespace(tool_name=t) for t in tools])


def _registry(monkeypatch, catalog, tool_hits, calls=None, providers=("reg-a", "reg-b")):
    class RM:
        provider_registry = SimpleNamespace(get_provider=lambda pid: SimpleNamespace(name=pid.upper()))

        def get_available_providers(self):
            return list(providers)

        async def get_available_services(self, max_results=100, provider_filter=None):
            if calls is not None:
                calls.append(("catalog", max_results, provider_filter))
            if isinstance(catalog, Exception):
                raise catalog
            return catalog

        async def search_services_by_tools(self, query, provider_filter=None):
            if calls is not None:
                calls.append(("tools", provider_filter))
            if isinstance(tool_hits, Exception):
                raise tool_hits
            return tool_hits
    monkeypatch.setattr(rm, "get_registry_manager", lambda: RM())
    monkeypatch.setattr(ss, "configured_mcp_servers", lambda: {"aws-outlook-mcp"})


def _col(q, n=8, off=None):
    return asyncio.run(bench_find._mcp_column(q, n, off))


def test_a_server_deep_in_the_catalogue_is_found_by_name_and_ranked_first(monkeypatch):
    filler = [_svc(f"svc-{i}", desc="unrelated") for i in range(3000)]
    wanted = _svc("aws-outlook-mcp", "AWSOutlookMCP", provider="amazon-internal")
    shared = _svc("aws-outlook-shared-mailbox-mcp", desc="fork of aws-outlook-mcp", provider="amazon-internal")
    by_tool = _svc("mailer", desc="sends mail", provider="awesome-lists")
    calls = []
    _registry(monkeypatch, filler + [shared, wanted], [_tool_hit(by_tool, "send_outlook")], calls)

    col = _col("aws-outlook-mcp")
    assert ("catalog", bench_find.CATALOG_MAX, None) in calls     # the whole catalogue, unfiltered
    assert col["total"] == 3
    assert [h["id"] for h in col["hits"]] == ["aws-outlook-mcp", "mailer", "aws-outlook-shared-mailbox-mcp"]
    first = col["hits"][0]
    assert first["provider"] == "amazon-internal" and first["installed"] is True
    assert col["hits"][1]["matched"] == ["send_outlook"] and col["hits"][1]["provider"] == "awesome-lists"


def test_name_matches_survive_the_top_n_cut_ahead_of_description_matches(monkeypatch):
    noise = [_svc(f"n{i}", desc="mentions outlook somewhere") for i in range(50)]
    _registry(monkeypatch, noise + [_svc("outlook-mcp")], [])
    col = _col("outlook", n=3)
    assert col["total"] == 51 and len(col["hits"]) == 3
    assert col["hits"][0]["id"] == "outlook-mcp"


def test_a_tool_hit_also_in_the_catalogue_is_one_row_with_its_tools_tags_and_all_registries(monkeypatch):
    s = _svc("jira-mcp", provider="reg-a", available_in=["reg-a", "reg-b"], tags=["issues", "tracker"])
    _registry(monkeypatch, [s, _svc("other")], [_tool_hit(s, "search_issues")])
    col = _col("jira")
    assert col["total"] == 1
    hit = col["hits"][0]
    assert hit["matched"] == ["search_issues"] and hit["provider"] == "reg-a, reg-b"
    assert hit["tags"] == ["issues", "tracker"]


def test_one_half_failing_still_answers_and_both_failing_omits_the_column(monkeypatch):
    _registry(monkeypatch, RuntimeError("catalogue down"), [_tool_hit(_svc("jira-mcp"), "t")])
    assert [h["id"] for h in _col("jira")["hits"]] == ["jira-mcp"]
    _registry(monkeypatch, [_svc("jira-mcp")], RuntimeError("search down"))
    assert [h["id"] for h in _col("jira")["hits"]] == ["jira-mcp"]
    _registry(monkeypatch, RuntimeError("a"), RuntimeError("b"))
    assert _col("jira") is None


def test_registries_switched_off_are_not_asked_and_the_column_lists_them_all(monkeypatch):
    calls = []
    _registry(monkeypatch, [_svc("jira-mcp")], [], calls)

    col = _col("jira")                                   # nothing off: no filter at all
    assert ("catalog", bench_find.CATALOG_MAX, None) in calls and ("tools", None) in calls
    assert col["registries"] == [{"id": "reg-a", "name": "REG-A", "selected": True},
                                 {"id": "reg-b", "name": "REG-B", "selected": True}]

    calls.clear()
    col = _col("jira", off=["reg-b", "not-a-registry"])
    assert ("catalog", bench_find.CATALOG_MAX, ["reg-a"]) in calls and ("tools", ["reg-a"]) in calls
    assert [r["selected"] for r in col["registries"]] == [True, False]
    assert [h["id"] for h in col["hits"]] == ["jira-mcp"]         # the search still ran

    calls.clear()
    col = _col("jira", off=["reg-a", "reg-b"])           # all off: nothing asked, switches remain
    assert calls == []
    assert col["hits"] == [] and col["total"] == 0 and len(col["registries"]) == 2


def test_browse_route_hands_the_switched_off_registries_to_the_search(monkeypatch):
    from app.api.bench import get_browse
    seen = []

    async def fake_search(project_id, q, n=8, source=None, registries_off=None):
        seen.append(registries_off)
        return {"q": q, "per_source": []}
    monkeypatch.setattr(bench_find, "search", fake_search)

    asyncio.run(get_browse("p", "jira", None, 8, "reg-b, reg-c,"))
    asyncio.run(get_browse("p", "jira", None, 8, ""))
    asyncio.run(get_browse("p", "jira", None, 8))        # called directly, parameter left at its default
    assert seen == [["reg-b", "reg-c"], None, None]


def test_a_server_configured_under_another_name_is_installed_by_its_registry_id(monkeypatch):
    import app.mcp.manager as mgr_mod
    _registry(monkeypatch, [_svc("jira-mcp", "Jira MCP"), _svc("jira-other"), _svc("jira_cloud")], [])
    monkeypatch.setattr(ss, "configured_mcp_servers", lambda: {"myjira", "jira-cloud"})
    monkeypatch.setattr(mgr_mod, "get_mcp_manager", lambda: SimpleNamespace(
        server_configs={"myjira": {"service_id": "jira-mcp"}, "jira-cloud": {}}))
    installed = {h["id"]: h["installed"] for h in _col("jira")["hits"]}
    assert installed == {"jira-mcp": True,      # by the config entry's service_id
                         "jira_cloud": True,    # by name, punctuation ignored
                         "jira-other": False}
