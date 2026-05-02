"""
Regression tests for MCPManager.call_tool dispatching builtin [DIRECT] tools.

Before the fix: builtin tools (ast_get_tree, file_read, nova_web_search, ...)
are local Python wrappers that are NOT attached to any MCP client/server.
MCPManager.call_tool iterated self.clients looking for the tool, failed, and
logged "Tool 'X' not found in any connected server", returning None. Any
wrapper that routed through the manager (google_direct, anthropic_direct,
openai_direct, etc.) saw the tool fail.

After the fix: the manager recognizes builtin tool names, applies the same
permission check and result-signing semantics as the dynamic-tool branch,
and returns a normalized response.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.mcp.manager import MCPManager


class _FakeBuiltinTool:
    def __init__(self, name="ast_get_tree", result=None, raises=None):
        self.name = name
        self._result = result if result is not None else {"content": "tree-dump"}
        self._raises = raises
        self.called_with = None

    async def execute(self, **kwargs):
        self.called_with = kwargs
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture
def manager():
    """A manager with no real clients — forces the code path through the
    builtin-dispatch branch (or the 'not found' fallback)."""
    m = MCPManager()
    m.clients = {}
    m.server_configs = {}
    return m


@pytest.fixture
def _perms_allow_all():
    """Patch permissions to default-enabled with no server entries."""
    fake_mgr = MagicMock()
    fake_mgr.get_permissions.return_value = {
        "defaults": {"tool": "enabled"},
        "servers": {},
    }
    with patch("app.mcp.permissions.get_permissions_manager", return_value=fake_mgr):
        yield fake_mgr


@pytest.fixture
def _perms_disable_builtin_tool():
    fake_mgr = MagicMock()
    fake_mgr.get_permissions.return_value = {
        "defaults": {"tool": "enabled"},
        "servers": {
            "builtin": {
                "tools": {
                    "ast_get_tree": {"permission": "disabled"},
                }
            }
        },
    }
    with patch("app.mcp.permissions.get_permissions_manager", return_value=fake_mgr):
        yield fake_mgr


class TestBuiltinDispatch:
    def test_builtin_tool_is_dispatched(self, manager, _perms_allow_all):
        fake = _FakeBuiltinTool(name="ast_get_tree")
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(
                manager.call_tool("ast_get_tree", {"path": "x.py"})
            )
        assert result is not None, (
            "builtin tool dispatch should return a value (not None / 'not found')"
        )
        assert fake.called_with == {"path": "x.py"}

    def test_mcp_prefixed_name_is_stripped(self, manager, _perms_allow_all):
        """The wrappers call mcp_ast_get_tree; manager strips the prefix to
        find the builtin 'ast_get_tree' entry."""
        fake = _FakeBuiltinTool(name="ast_get_tree")
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(manager.call_tool("mcp_ast_get_tree", {}))
        assert result is not None
        assert fake.called_with == {}

    def test_result_normalized_to_content_list(self, manager, _perms_allow_all):
        """Wrappers' _extract_text_from_mcp_result expects
        {"content": [{"type":"text","text":"..."}]}. A builtin returning
        {"content": "string"} must be normalized to that shape."""
        fake = _FakeBuiltinTool(result={"content": "hello world"})
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(manager.call_tool("ast_get_tree", {}))
        assert isinstance(result, dict)
        content = result.get("content")
        assert isinstance(content, list), f"expected list, got {type(content).__name__}"
        assert content and content[0].get("text") == "hello world"

    def test_raw_string_result_is_wrapped(self, manager, _perms_allow_all):
        fake = _FakeBuiltinTool(result="raw-output")
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(manager.call_tool("ast_get_tree", {}))
        assert result["content"][0]["text"] == "raw-output"

    def test_result_is_signed(self, manager, _perms_allow_all):
        """Parity with the dynamic-tool branch: builtin results must carry
        the HMAC signature metadata so downstream verification passes."""
        fake = _FakeBuiltinTool(result={"content": "x"})
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(manager.call_tool("ast_get_tree", {}))
        assert "_signature" in result, "builtin result must be signed"
        assert result.get("_tool_name") == "ast_get_tree"

    def test_disabled_builtin_returns_error(self, manager, _perms_disable_builtin_tool):
        fake = _FakeBuiltinTool(name="ast_get_tree")
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(manager.call_tool("ast_get_tree", {}))
        assert isinstance(result, dict) and result.get("error") is True
        assert "disabled" in result.get("message", "").lower()
        # The tool's execute() must not have been called when disabled
        assert fake.called_with is None

    def test_execute_exception_returns_error_dict(self, manager, _perms_allow_all):
        fake = _FakeBuiltinTool(raises=RuntimeError("boom"))
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[fake],
        ):
            result = asyncio.run(manager.call_tool("ast_get_tree", {}))
        assert isinstance(result, dict) and result.get("error") is True
        assert "boom" in result.get("message", "")

    def test_unknown_tool_still_returns_none(self, manager, _perms_allow_all):
        """Non-builtin, non-server tool names must still fall through to the
        original 'not found' path and return None — dispatch must not
        accidentally swallow unknown names."""
        with patch(
            "app.mcp.builtin_tools.get_enabled_builtin_tools",
            return_value=[_FakeBuiltinTool(name="something_else")],
        ):
            result = asyncio.run(manager.call_tool("totally_unknown_tool", {}))
        assert result is None
