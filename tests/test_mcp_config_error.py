"""
Tests that MCPManager surfaces config parse errors instead of silently
falling back to built-in defaults.

When mcp_config.json contains a JSON syntax error:
- config_error should contain a descriptive message
- get_config_search_info() should include the error
- The error should clear after a successful reload
"""

import json
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from app.mcp.manager import MCPManager


@pytest.fixture
def bad_config_file(tmp_path):
    """Create a temporary mcp_config.json with invalid JSON."""
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    config = bad_dir / "mcp_config.json"
    config.write_text('{ "mcpServers": { INVALID JSON HERE } }')
    return str(config)


@pytest.fixture
def good_config_file(tmp_path):
    """Create a temporary mcp_config.json with valid JSON."""
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    config = good_dir / "mcp_config.json"
    config.write_text(json.dumps({
        "mcpServers": {
            "test-server": {
                "command": "echo",
                "args": ["hello"]
            }
        }
    }))
    return str(config)


def _make_manager(config_path):
    """Create an MCPManager with no builtin servers for cleaner testing."""
    manager = MCPManager(config_path=config_path)
    manager.builtin_server_definitions = {}
    return manager


async def _initialize_with_fixed_path(manager):
    """Initialize manager without letting refresh_config_path override the test path."""
    with patch.object(manager, 'refresh_config_path'):
        with patch.object(manager, '_connect_server', new_callable=AsyncMock, return_value=True):
            await manager.initialize()


class TestMCPConfigError:
    """Tests for config_error reporting on malformed mcp_config.json."""

    def test_config_error_initially_none(self):
        """config_error starts as None before any initialize() call."""
        manager = _make_manager("/nonexistent/path")
        assert manager.config_error is None

    @pytest.mark.asyncio
    async def test_config_error_set_on_bad_json(self, bad_config_file):
        """config_error is set with line/column details on JSONDecodeError."""
        manager = _make_manager(bad_config_file)
        await _initialize_with_fixed_path(manager)

        assert manager.config_error is not None
        assert "Syntax error" in manager.config_error
        assert bad_config_file in manager.config_error
        # Should include line/column info from JSONDecodeError
        assert "line" in manager.config_error.lower()
        assert "column" in manager.config_error.lower()

    @pytest.mark.asyncio
    async def test_config_error_in_search_info(self, bad_config_file):
        """get_config_search_info() includes config_error."""
        manager = _make_manager(bad_config_file)
        await _initialize_with_fixed_path(manager)

        info = manager.get_config_search_info()
        assert "config_error" in info
        assert info["config_error"] is not None
        assert "Syntax error" in info["config_error"]

    @pytest.mark.asyncio
    async def test_config_error_cleared_on_good_json(self, bad_config_file, good_config_file):
        """config_error clears when a valid config is loaded after a bad one."""
        manager = _make_manager(bad_config_file)
        await _initialize_with_fixed_path(manager)
        assert manager.config_error is not None

        # Now point to the good config and reinitialize
        manager.config_path = good_config_file
        await _initialize_with_fixed_path(manager)

        assert manager.config_error is None
        info = manager.get_config_search_info()
        assert info["config_error"] is None

    @pytest.mark.asyncio
    async def test_config_error_on_unreadable_file(self, tmp_path):
        """config_error is set for non-JSON read errors (e.g. permission denied)."""
        config = tmp_path / "mcp_config.json"
        config.write_text("valid content")

        manager = _make_manager(str(config))

        # Simulate a read error by patching open to raise PermissionError
        original_open = open

        def guarded_open(path, *args, **kwargs):
            if str(path) == str(config):
                raise PermissionError("Permission denied")
            return original_open(path, *args, **kwargs)

        with patch.object(manager, 'refresh_config_path'):
            with patch.object(manager, '_connect_server', new_callable=AsyncMock, return_value=True):
                with patch("builtins.open", side_effect=guarded_open):
                    await manager.initialize()

        assert manager.config_error is not None
        assert "Failed to read" in manager.config_error
        assert "Permission denied" in manager.config_error

    def test_config_error_absent_when_no_config_file(self):
        """config_error stays None when there's simply no config file."""
        manager = _make_manager(None)
        info = manager.get_config_search_info()
        assert info.get("config_error") is None
