"""
Verify that app.mcp.tools resolves to the package (not the dead file)
and that all symbols imported by server.py and enhanced_tools.py are available.

Regression guard: a bare tools.py file next to the tools/ package shadows it,
making all imports from the package fail silently.
"""

import os
import unittest


class TestMCPToolsPackageImports(unittest.TestCase):
    """Ensure all app.mcp.tools imports resolve to the package."""

    def test_resolves_to_package(self):
        """app.mcp.tools must resolve to the tools/ package, not a .py file."""
        import app.mcp.tools as tools_mod
        self.assertTrue(
            hasattr(tools_mod, '__path__'),
            "app.mcp.tools is not a package — a tools.py file may be shadowing it"
        )
        self.assertTrue(
            tools_mod.__file__.endswith('__init__.py'),
            f"Expected __init__.py, got {tools_mod.__file__}"
        )

    def test_no_shadow_file(self):
        """The dead app/mcp/tools.py must not exist alongside the package."""
        import app.mcp as mcp_pkg
        shadow = os.path.join(os.path.dirname(mcp_pkg.__file__), 'tools.py')
        self.assertFalse(
            os.path.exists(shadow),
            "app/mcp/tools.py still exists and shadows the tools/ package. Delete it."
        )

    def test_parse_tool_call_importable(self):
        """server.py imports parse_tool_call from app.mcp.tools."""
        from app.mcp.tools import parse_tool_call
        self.assertTrue(callable(parse_tool_call))

    def test_create_mcp_tools_importable(self):
        """enhanced_tools.py imports create_mcp_tools from app.mcp.tools."""
        from app.mcp.tools import create_mcp_tools
        self.assertTrue(callable(create_mcp_tools))

    def test_debug_state_vars_importable(self):
        """server.py debug_mcp_state imports tracking variables."""
        from app.mcp.tools import (
            _tool_execution_counter,
            _consecutive_timeouts,
            _conversation_tool_states,
        )
        self.assertIsInstance(_tool_execution_counter, int)
        self.assertIsInstance(_consecutive_timeouts, int)
        self.assertIsInstance(_conversation_tool_states, dict)

    def test_parse_tool_call_xml_format(self):
        """Smoke test: parse_tool_call handles XML tool format."""
        from app.mcp.tools import parse_tool_call
        content = '<TOOL_SENTINEL><n>test_tool</n><arguments>{"key": "val"}</arguments></TOOL_SENTINEL>'
        result = parse_tool_call(content)
        self.assertIsNotNone(result)
        self.assertEqual(result['tool_name'], 'test_tool')
        self.assertEqual(result['arguments'], {'key': 'val'})

    def test_parse_tool_call_returns_none_for_garbage(self):
        """parse_tool_call returns None for non-tool content."""
        from app.mcp.tools import parse_tool_call
        self.assertIsNone(parse_tool_call("just some text"))
