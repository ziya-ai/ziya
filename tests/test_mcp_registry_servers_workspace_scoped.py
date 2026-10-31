"""Registry-installed MCP servers default to workspace (per-project) scoping.

Servers installed via the Ziya MCP Registry Manager carry a
``registry_provider`` marker in their config entry. Their tools frequently
resolve relative file paths against the subprocess cwd and refuse anything
outside it, so a single global instance started in the first project rejects
files from every other project. ``MCPManager._is_workspace_scoped`` must
therefore treat such entries as workspace-scoped unless the user says
otherwise explicitly.
"""
import pytest

from app.mcp.manager import MCPManager


@pytest.fixture
def manager():
    m = MCPManager.__new__(MCPManager)
    m.server_configs = {}
    return m


def _scoped(manager, name, cfg):
    manager.server_configs = {name: cfg}
    return manager._is_workspace_scoped(name)


def test_registry_installed_server_is_workspace_scoped_by_default(manager):
    # Mirrors a real registry entry: no filesystem keyword in name or command.
    cfg = {
        "enabled": True,
        "registry_provider": "amazon-internal",
        "service_id": "pippin-mcp",
        "command": "/Users/u/.toolbox/bin/pippin-mcp-server",
    }
    assert _scoped(manager, "pippin_mcp_server", cfg) is True


def test_same_entry_without_registry_marker_falls_to_keyword_heuristic(manager):
    # Positive control for the test above: with the marker removed, the
    # keyword heuristic must NOT scope this server, proving the marker is
    # what changed the verdict.
    cfg = {
        "enabled": True,
        "command": "/Users/u/.toolbox/bin/pippin-mcp-server",
    }
    assert _scoped(manager, "pippin_mcp_server", cfg) is False


def test_explicit_false_overrides_registry_default(manager):
    cfg = {
        "registry_provider": "amazon-internal",
        "workspace_scoped": False,
        "command": "/x/bin/some-server",
    }
    assert _scoped(manager, "some_server", cfg) is False


def test_remote_registry_server_is_never_workspace_scoped(manager):
    # Remote servers have no subprocess cwd to manage.
    cfg = {
        "registry_provider": "amazon-internal",
        "url": "https://example.invalid/mcp",
    }
    assert _scoped(manager, "remote_server", cfg) is False
