"""Read-only classification of external (``mcp_*``) MCP tools.

After the shell classifier fix (test_tool_batch_classifier_quotes.py) the
remaining serial cost in a research turn was the external tools: every
``mcp_*`` call -- wiki search, code search, Jira, Confluence, web fetch, the
slow network ones -- was a barrier because ``is_read_only_call`` knew only
builtin names.  Classification now comes from, in order: ``permissions.json``
(``parallel.read_only_tools`` / ``parallel.serial_tools``), the server's own
``readOnlyHint`` annotation, a write-target argument carrying a value, and
finally the tool's name, where any mutating word beats any observing word.

The annotation has to survive four hops to reach the classifier; each hop is
asserted here so a half-wired change fails CI.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import tool_batch
from app.tool_batch import (
    external_tool_is_read_only, is_read_only_call, plan_groups,
)


def _no_config():
    return patch.object(tool_batch, "_parallel_config", lambda: {})


# ── the name heuristic ────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "mcp_InternalSearch", "mcp_InternalCodeSearch", "mcp_ReadInternalWebsites",
    "mcp_brave_web_search", "mcp_fetch", "mcp_get_jira_issue", "mcp_search_jira_issues",
    "mcp_list_jira_projects", "mcp_kuiper_confluence_search_pages",
    "mcp_kuiper_confluence_get_page_content", "mcp_get_messages", "mcp_get_thread",
    "mcp_lookup_user", "mcp_list_my_channels", "mcp_pippin_get_artifact",
    "mcp_pippin_list_artifacts", "mcp_pippin_read_cell", "mcp_quip_get_thread_html",
    "mcp_quip_search", "mcp_GetPipelineHealth", "mcp_ApolloReadActions",
    "mcp_TicketingReadActions", "mcp_WorkspaceSearch", "mcp_get_dashboard",
    "mcp_get_panel_data", "mcp_get_diagnostics", "mcp_kuiper_confluence_page_exists",
    # the executor hands the classifier the mcp_-stripped name
    "get_messages", "InternalSearch",
])
def test_observing_external_tools_overlap(name):
    with _no_config():
        assert is_read_only_call(name, {}) is True


@pytest.mark.parametrize("name", [
    "mcp_post_message", "mcp_set_status", "mcp_TaskeiUpdateTask", "mcp_SimAddComment",
    "mcp_delete_list_item", "mcp_upload_file", "mcp_MechanicRunTool", "mcp_CrCheckout",
    "mcp_CRRevisionCreator", "mcp_TicketingWriteActions", "mcp_pippin_write_cell",
    "mcp_pippin_sync_project_to_local", "mcp_download_file", "mcp_export_list",
    "mcp_kuiper_confluence_export_page_as_pdf", "mcp_refresh_jira_auth",
    "mcp_review_local_code_leolens", "mcp_pin_message", "mcp_share_canvas",
    "mcp_quip_edit_document", "mcp_set_last_read",
    # a mutating word wins even next to an observing one
    "mcp_ReadRemoteTestRun", "mcp_add_jira_watcher", "mcp_get_list_form_and_delete",
    # no observing word: unknown stays a barrier
    "mcp_BrazilBuildAnalyzerTool", "mcp_ask_leolens", "mcp_sequentialthinking",
    "mcp_health_check", "mcp_QuipEditor", "mcp_some_external_tool",
])
def test_mutating_or_unknown_external_tools_stay_serial(name):
    with _no_config():
        assert is_read_only_call(name, {}) is False


def test_a_write_target_argument_with_a_value_makes_a_reader_serial():
    with _no_config():
        assert is_read_only_call("mcp_kuiper_confluence_get_page_storage", {"page_id": "1"}) is True
        assert is_read_only_call("mcp_kuiper_confluence_get_page_storage",
                                 {"page_id": "1", "output_file": "/tmp/x.xml"}) is False
        assert is_read_only_call("mcp_get_messages", {"savePath": ""}) is True, "empty: not a write"
        assert is_read_only_call("mcp_get_messages", {"outputDirectory": "/tmp"}) is False
        assert is_read_only_call("mcp_get_messages", {"dest_dir": "/tmp"}) is False


def test_builtins_not_on_the_list_never_fall_through_to_the_name_heuristic():
    """``context_add_file`` and ``dataset_register`` contain no mutating
    word and ``memory_save`` has an observing-looking shape elsewhere; a
    Ziya builtin that is not in _READ_ONLY_BUILTINS is a barrier, period."""
    with _no_config():
        for name in ("context_add_file", "dataset_register", "memory_save", "file_write",
                     "job_start", "shadow_send", "handoff_write", "task_card_write"):
            assert is_read_only_call(name, {}) is False, name
        builtin = SimpleNamespace(name="get_time", tool_instance=object(), metadata={})
        assert is_read_only_call("get_time", {}, builtin) is False, "DirectMCPTool wrapper: builtin"


# ── annotations and configuration beat the name ───────────────────────────

def _tool(name, **ann):
    return SimpleNamespace(name=name, metadata={"annotations": ann} if ann else {})


def test_read_only_hint_wins_over_the_name_in_both_directions():
    with _no_config():
        assert external_tool_is_read_only("mcp_do_the_thing", {}, _tool("mcp_do_the_thing", readOnlyHint=True)) is True
        assert external_tool_is_read_only("mcp_get_messages", {}, _tool("mcp_get_messages", readOnlyHint=False)) is False
        # destructiveHint contradicts readOnlyHint: believe the scarier one.
        assert external_tool_is_read_only("mcp_x", {}, _tool("mcp_x", readOnlyHint=True, destructiveHint=True)) is False
        # No readOnlyHint key at all: the name decides.
        assert external_tool_is_read_only("mcp_get_messages", {}, _tool("mcp_get_messages", title="Messages")) is True
        assert external_tool_is_read_only("mcp_get_messages", {}, _tool("mcp_get_messages")) is True


def test_permissions_lists_win_over_annotations_and_names():
    cfg = {"read_only_tools": ["mcp_ReadRemoteTestRun", "ask_leolens"],
           "serial_tools": ["get_messages"]}
    with patch.object(tool_batch, "_parallel_config", lambda: cfg):
        assert is_read_only_call("ReadRemoteTestRun", {}) is True
        assert is_read_only_call("mcp_ask_leolens", {}) is True
        assert is_read_only_call("mcp_get_messages", {}, _tool("mcp_get_messages", readOnlyHint=True)) is False


def test_a_broken_permissions_read_falls_back_to_the_other_signals():
    def boom():
        raise RuntimeError("no permissions")
    from app.mcp import permissions as perms_mod
    with patch.object(perms_mod, "get_permissions_manager", boom):
        assert tool_batch._parallel_config() == {}
        assert is_read_only_call("mcp_get_messages", {}) is True


# ── the hops ──────────────────────────────────────────────────────────────

def test_plan_groups_resolves_the_tool_object_from_the_context():
    """The seam into the batch runner: ``ctx.all_tools`` holds the
    advertised tool (``mcp_`` name) while ``actual_tool_name`` is stripped;
    the annotation must still be found and still win."""
    tools = [_tool("mcp_get_messages", readOnlyHint=False), _tool("mcp_frobnicate", readOnlyHint=True)]

    def ctx(i, actual, tool_name=None):
        return SimpleNamespace(tool_id=f"t{i}", tool_name=tool_name or f"mcp_{actual}",
                               actual_tool_name=actual, args={}, all_tools=tools)
    with _no_config():
        groups = plan_groups([
            ctx(1, "frobnicate"),            # annotated read-only
            ctx(2, "InternalSearch"),        # by name
            ctx(3, "get_messages"),          # annotated NOT read-only despite the name
            ctx(4, "search_jira_issues"),
        ])
    assert [[c.tool_id for c in g] for g in groups] == [["t1", "t2"], ["t3"], ["t4"]]


def test_mcptool_carries_annotations_through_the_tools_list_field_filter():
    """client._load_server_capabilities keeps exactly ``fields(MCPTool)`` of
    each tools/list entry; the dataclass field is what lets annotations in."""
    from dataclasses import fields
    from app.mcp.client import MCPTool
    assert "annotations" in {f.name for f in fields(MCPTool)}
    t = MCPTool(name="x", description="", inputSchema={}, annotations={"readOnlyHint": True})
    assert t.annotations == {"readOnlyHint": True}
    assert MCPTool(name="x", description="", inputSchema={}).annotations is None


def test_secure_mcp_tool_keeps_annotations_in_metadata_and_the_factory_passes_them():
    from app.mcp.enhanced_tools import SecureMCPTool
    t = SecureMCPTool(name="mcp_x", description="d", mcp_tool_name="x",
                      annotations={"readOnlyHint": True, "title": "X"})
    assert t.metadata["annotations"] == {"readOnlyHint": True, "title": "X"}
    assert external_tool_is_read_only("x", {}, t) is True
    bare = SecureMCPTool(name="mcp_y", description="d", mcp_tool_name="y")
    assert "annotations" not in bare.metadata

    import inspect
    from app.mcp import enhanced_tools
    src = inspect.getsource(enhanced_tools.create_secure_mcp_tools)
    assert 'annotations=getattr(tool, "annotations", None)' in src, \
        "create_secure_mcp_tools must hand MCPTool.annotations to SecureMCPTool"
