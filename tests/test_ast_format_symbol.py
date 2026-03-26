"""
Tests for _format_symbol in ast_tools.

Covers edge cases where AST node attributes contain None values
or unexpected types in list fields (bases, params).
"""

import pytest

from app.utils.ast_parser.unified_ast import Node, SourceLocation
from app.mcp.tools.ast_tools import _format_symbol


def _make_node(node_type: str, name: str, attributes: dict | None = None) -> Node:
    loc = SourceLocation(file_path="test.py", start_line=1, start_column=0, end_line=1, end_column=0)
    return Node(node_id="test-1", node_type=node_type, name=name,
                source_location=loc, attributes=attributes)


class TestFormatSymbolClass:
    """Class node formatting."""

    def test_class_no_bases(self):
        node = _make_node("class", "Foo")
        assert _format_symbol(node) == "class Foo"

    def test_class_with_bases(self):
        node = _make_node("class", "Foo", {"bases": ["Bar", "Baz"]})
        assert _format_symbol(node) == "class Foo(Bar, Baz)"

    def test_class_with_none_in_bases(self):
        """Regression: bases list containing None caused TypeError in join."""
        node = _make_node("class", "Foo", {"bases": [None]})
        assert _format_symbol(node) == "class Foo"

    def test_class_with_mixed_none_bases(self):
        node = _make_node("class", "Foo", {"bases": ["Bar", None, "Baz"]})
        assert _format_symbol(node) == "class Foo(Bar, Baz)"

    def test_class_bases_empty_list(self):
        node = _make_node("class", "Foo", {"bases": []})
        assert _format_symbol(node) == "class Foo"


class TestFormatSymbolFunction:
    """Function/method node formatting."""

    def test_function_no_params(self):
        node = _make_node("function", "do_stuff")
        assert _format_symbol(node) == "def do_stuff()"

    def test_function_with_params(self):
        node = _make_node("function", "add", {"params": ["a", "b"]})
        assert _format_symbol(node) == "def add(a, b)"

    def test_async_function(self):
        node = _make_node("function", "fetch", {"is_async": True, "params": ["url"]})
        assert _format_symbol(node) == "async def fetch(url)"

    def test_function_with_return_type(self):
        node = _make_node("function", "get_id", {"return_type": "int"})
        assert _format_symbol(node) == "def get_id() -> int"

    def test_function_with_dict_params(self):
        node = _make_node("function", "foo", {"params": [{"name": "x"}, {"name": "y"}]})
        assert _format_symbol(node) == "def foo(x, y)"

    def test_function_with_none_params(self):
        """Params containing None should not crash."""
        node = _make_node("function", "bar", {"params": [None]})
        assert "bar" in _format_symbol(node)


class TestFormatSymbolOther:
    def test_interface(self):
        node = _make_node("interface", "IFoo")
        assert _format_symbol(node) == "interface IFoo"

    def test_variable_with_type(self):
        node = _make_node("variable", "count", {"type": "int"})
        assert _format_symbol(node) == "variable count: int"
