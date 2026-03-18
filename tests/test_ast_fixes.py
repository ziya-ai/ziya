"""
Tests for AST system fixes.

Covers the 6 fixes applied to the AST system:
1. `callers` — name-based fallback when "calls" edges are missing
2. `dependencies` — import-node attribute fallback + module resolution
3. TypeScript callable detection (useCallback, arrow functions)
4. `context` action on ast_references (get_context_for_location)
5. Regex search in ast_search
6. edge_type_index for O(1) edge-type lookups

Each test class is standalone and uses synthetic ASTs or parser output
to avoid depending on a running server or full codebase indexing.
"""

import ast
import os
import textwrap

import pytest
import pytest_asyncio

from app.utils.ast_parser.unified_ast import UnifiedAST, Node, SourceLocation, Edge
from app.utils.ast_parser.query_engine import ASTQueryEngine
from app.utils.ast_parser.python_parser import PythonASTParser
from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


# ===================================================================
# Shared fixtures
# ===================================================================

SAMPLE_CALLER_CODE = textwrap.dedent("""\
    def helper():
        return 42

    def main():
        x = helper()
        y = helper()
        return x + y

    class Processor:
        def run(self):
            return helper()
""")

SAMPLE_CROSS_FILE_A = textwrap.dedent("""\
    def shared_util():
        return "util"

    class SharedClass:
        pass
""")

SAMPLE_CROSS_FILE_B = textwrap.dedent("""\
    from module_a import shared_util, SharedClass

    def consumer():
        result = shared_util()
        obj = SharedClass()
        return result
""")

SAMPLE_IMPORTS_CODE = textwrap.dedent("""\
    import os
    from typing import List, Optional
    from app.utils.logging_utils import logger
    from .sibling_module import helper_func
    from ..parent_module import ParentClass

    def do_work():
        logger.info("working")
""")

SAMPLE_CONTEXT_CODE = textwrap.dedent("""\
    class Outer:
        def method_a(self):
            x = 1
            return x

        def method_b(self, arg: str) -> str:
            return arg.upper()

    def standalone():
        pass
""")


@pytest.fixture
def python_parser():
    return PythonASTParser()


@pytest.fixture
def caller_ast(python_parser):
    """AST from SAMPLE_CALLER_CODE — has same-file calls."""
    native = python_parser.parse("caller.py", SAMPLE_CALLER_CODE)
    return python_parser.to_unified_ast(native, "caller.py")


@pytest.fixture
def caller_qe(caller_ast):
    return ASTQueryEngine(caller_ast)


@pytest.fixture
def cross_file_project_ast(python_parser):
    """Merged project AST from two files — cross-file calls have no edges."""
    ast_a = python_parser.to_unified_ast(
        python_parser.parse("module_a.py", SAMPLE_CROSS_FILE_A), "module_a.py"
    )
    ast_b = python_parser.to_unified_ast(
        python_parser.parse("module_b.py", SAMPLE_CROSS_FILE_B), "module_b.py"
    )
    ast_a.merge(ast_b)
    return ast_a


@pytest.fixture
def cross_file_qe(cross_file_project_ast):
    return ASTQueryEngine(cross_file_project_ast)


@pytest.fixture
def imports_ast(python_parser):
    native = python_parser.parse("imports_mod.py", SAMPLE_IMPORTS_CODE)
    return python_parser.to_unified_ast(native, "imports_mod.py")


@pytest.fixture
def imports_qe(imports_ast):
    return ASTQueryEngine(imports_ast)


@pytest.fixture
def context_ast(python_parser):
    native = python_parser.parse("context_mod.py", SAMPLE_CONTEXT_CODE)
    return python_parser.to_unified_ast(native, "context_mod.py")


@pytest.fixture
def context_qe(context_ast):
    return ASTQueryEngine(context_ast)


def _make_enhancer_with_codebase(tmp_path, monkeypatch, files: dict):
    """Helper: write files, index, register enhancer for MCP tools."""
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    (tmp_path / ".gitignore").write_text("__pycache__/\n")

    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    from app.utils.ast_parser.integration import _enhancers, _initialized_projects
    enhancer = ZiyaASTEnhancer(ast_resolution="medium")
    enhancer.process_codebase(str(tmp_path), max_depth=5)
    abs_path = os.path.abspath(str(tmp_path))
    _enhancers[abs_path] = enhancer
    _initialized_projects.add(abs_path)
    return enhancer


# ===================================================================
# 1. Fix: callers — name-based fallback
# ===================================================================

class TestCallersNameFallback:
    """
    get_function_calls() relied solely on "calls" edges, which only exist
    for same-file targets. The fix falls back to searching "call" nodes
    by name when edge-based lookup returns nothing.
    """

    def test_same_file_callers_found_via_edges(self, caller_qe):
        """Same-file calls should still work via the edge-based path."""
        calls = caller_qe.get_function_calls("helper")
        assert len(calls) >= 2, (
            f"Expected at least 2 call sites for helper(), got {len(calls)}"
        )

    def test_cross_file_callers_found_via_name_fallback(self, cross_file_qe):
        """Cross-file calls have no "calls" edges — fallback must find them."""
        calls = cross_file_qe.get_function_calls("shared_util")
        assert len(calls) >= 1, (
            "Cross-file call to shared_util() not found. "
            "The name-based fallback in get_function_calls is not working."
        )

    def test_attribute_style_call_matched(self, cross_file_qe):
        """Calls like self.foo() or module.foo() should match 'foo'."""
        # The Python parser records `SharedClass()` as a call node
        calls = cross_file_qe.get_function_calls("SharedClass")
        # Should find at least the call in module_b
        assert len(calls) >= 1, (
            "Call to SharedClass() not found via name fallback"
        )

    def test_nonexistent_function_returns_empty(self, caller_qe):
        """Searching for a function that doesn't exist should return []."""
        calls = caller_qe.get_function_calls("nonexistent_func")
        assert calls == []

    def test_case_insensitive_fallback(self, cross_file_qe):
        """Name fallback should be case-insensitive."""
        calls = cross_file_qe.get_function_calls("Shared_Util")
        # Should find shared_util via case-insensitive match
        assert len(calls) >= 1, (
            "Case-insensitive name fallback not working"
        )


# ===================================================================
# 2. Fix: dependencies — import attribute fallback
# ===================================================================

class TestDependenciesImportFallback:
    """
    get_dependencies() relied on "imports" edges which are never created.
    The fix falls back to reading import node attributes.
    """

    def test_dependencies_found_from_import_attributes(self, imports_qe):
        """Should find dependencies even without 'imports' edges."""
        deps = imports_qe.get_dependencies("imports_mod.py")
        assert len(deps) > 0, (
            "No dependencies found for imports_mod.py. "
            "The import-attribute fallback in get_dependencies is not working."
        )

    def test_stdlib_imports_included(self, imports_qe):
        """Standard library modules should appear as raw module names."""
        deps = imports_qe.get_dependencies("imports_mod.py")
        dep_strings = " ".join(deps)
        assert "os" in dep_strings or "typing" in dep_strings, (
            f"Standard library imports not found in dependencies: {deps}"
        )

    def test_dotted_imports_included(self, imports_qe):
        """Dotted imports like app.utils.logging_utils should be present."""
        deps = imports_qe.get_dependencies("imports_mod.py")
        dep_strings = " ".join(deps)
        assert "app.utils.logging_utils" in dep_strings or "logging_utils" in dep_strings, (
            f"Dotted import not found in dependencies: {deps}"
        )

    def test_file_with_no_imports_returns_empty(self):
        """A file with no imports should return []."""
        parser = PythonASTParser()
        code = "x = 42\n"
        uast = parser.to_unified_ast(parser.parse("no_imports.py", code), "no_imports.py")
        qe = ASTQueryEngine(uast)
        deps = qe.get_dependencies("no_imports.py")
        assert deps == []

    def test_resolve_module_to_indexed_file(self, python_parser):
        """_resolve_module_to_file should map a dotted module to an indexed path."""
        code_a = "def foo(): pass\n"
        code_b = "from app.utils.helpers import foo\ndef bar(): foo()\n"

        ast_a = python_parser.to_unified_ast(
            python_parser.parse("/project/app/utils/helpers.py", code_a),
            "/project/app/utils/helpers.py"
        )
        ast_b = python_parser.to_unified_ast(
            python_parser.parse("/project/consumer.py", code_b),
            "/project/consumer.py"
        )
        ast_a.merge(ast_b)
        qe = ASTQueryEngine(ast_a)

        resolved = qe._resolve_module_to_file("app.utils.helpers", "/project/consumer.py")
        assert resolved == "/project/app/utils/helpers.py", (
            f"Expected /project/app/utils/helpers.py, got {resolved}"
        )

    def test_resolve_unknown_module_returns_none(self, imports_qe):
        """Modules not in the index should return None."""
        result = imports_qe._resolve_module_to_file("totally.unknown.module", "imports_mod.py")
        assert result is None


# ===================================================================
# 3. Fix: edge_type_index for O(1) lookups
# ===================================================================

class TestEdgeTypeIndex:
    """
    Added edge_type_index dict to _build_indices for O(1) edge-type lookups
    instead of scanning all edges.
    """

    def test_edge_type_index_exists(self, caller_qe):
        """Query engine should have an edge_type_index attribute."""
        assert hasattr(caller_qe, 'edge_type_index'), (
            "ASTQueryEngine missing edge_type_index attribute"
        )

    def test_edge_type_index_populated(self, caller_qe):
        """The index should have entries for edge types present in the AST."""
        assert len(caller_qe.edge_type_index) > 0, (
            "edge_type_index is empty despite edges existing in the AST"
        )

    def test_contains_edges_indexed(self, caller_qe):
        """'contains' edges should be in the index."""
        assert "contains" in caller_qe.edge_type_index, (
            "'contains' edge type missing from edge_type_index"
        )
        assert len(caller_qe.edge_type_index["contains"]) > 0

    def test_calls_edges_indexed(self, caller_qe):
        """'calls' edges should be in the index (same-file calls)."""
        assert "calls" in caller_qe.edge_type_index, (
            "'calls' edge type missing from edge_type_index"
        )
        assert len(caller_qe.edge_type_index["calls"]) > 0

    def test_index_matches_linear_scan(self, caller_qe):
        """Edge type index should return the same edges as a linear scan."""
        for edge_type in caller_qe.edge_type_index:
            indexed_edges = set(id(e) for e in caller_qe.edge_type_index[edge_type])
            linear_edges = set(
                id(e) for e in caller_qe.ast.edges if e.edge_type == edge_type
            )
            assert indexed_edges == linear_edges, (
                f"Mismatch for edge type '{edge_type}': "
                f"indexed {len(indexed_edges)} vs linear {len(linear_edges)}"
            )


# ===================================================================
# 4. Fix: get_context_for_location
# ===================================================================

class TestContextForLocation:
    """
    get_context_for_location() returns semantic context at a cursor position.
    Tests cover the query engine method directly.
    """

    def test_context_at_function_body(self, context_qe):
        """Line inside method_a should return the method node."""
        # method_a starts around line 2, body at line 3
        ctx = context_qe.get_context_for_location("context_mod.py", 3, 1)
        assert ctx["type"] != "unknown", (
            f"No context found at context_mod.py:3:1. Got: {ctx}"
        )

    def test_context_at_class_definition(self, context_qe):
        """Line 1 (class Outer) should return the class node."""
        ctx = context_qe.get_context_for_location("context_mod.py", 1, 1)
        assert ctx["type"] != "unknown", f"No context at line 1. Got: {ctx}"
        # Should be either the class itself or the module
        assert ctx["name"] in ("Outer", "context_mod"), (
            f"Unexpected name at line 1: {ctx['name']}"
        )

    def test_context_returns_containing_scopes(self, context_qe):
        """Context inside a method should show containing class scope."""
        # method_a body is around line 3-4
        ctx = context_qe.get_context_for_location("context_mod.py", 3, 5)
        if ctx["type"] != "unknown" and "scopes" in ctx:
            scope_names = [s["name"] for s in ctx["scopes"]]
            # Should contain the class or method in the scope chain
            assert any(name in ("Outer", "method_a") for name in scope_names), (
                f"Expected Outer or method_a in scopes, got: {scope_names}"
            )

    def test_context_at_invalid_location(self, context_qe):
        """A location past the end of the file should return 'unknown'."""
        ctx = context_qe.get_context_for_location("context_mod.py", 9999, 1)
        assert ctx["type"] == "unknown"

    def test_context_wrong_file(self, context_qe):
        """A file not in the index should return 'unknown'."""
        ctx = context_qe.get_context_for_location("nonexistent.py", 1, 1)
        assert ctx["type"] == "unknown"


# ===================================================================
# 5. Fix: context action in MCP ast_references tool
# ===================================================================

class TestASTReferencesContextAction:
    """Test the 'context' action exposed via the MCP tool layer."""

    @pytest.fixture
    def enhancer(self, tmp_path, monkeypatch):
        return _make_enhancer_with_codebase(tmp_path, monkeypatch, {
            "context_test.py": SAMPLE_CONTEXT_CODE,
        })

    @pytest.mark.asyncio
    async def test_context_action_valid_file_line(self, enhancer, tmp_path, monkeypatch):
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="context_test.py:3", action="context")
        assert "error" not in result, f"Unexpected error: {result}"
        assert "Context at" in result["content"]

    @pytest.mark.asyncio
    async def test_context_action_valid_file_line_col(self, enhancer, tmp_path, monkeypatch):
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="context_test.py:1:1", action="context")
        assert "error" not in result, f"Unexpected error: {result}"
        assert "Context at" in result["content"]

    @pytest.mark.asyncio
    async def test_context_action_invalid_format(self, enhancer, tmp_path, monkeypatch):
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="just_a_name", action="context")
        assert result.get("error") is True
        assert "file_path:line" in result["message"]

    @pytest.mark.asyncio
    async def test_context_action_unknown_file(self, enhancer, tmp_path, monkeypatch):
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="nonexistent.py:1", action="context")
        assert result.get("error") is True
        assert "not in index" in result["message"]


# ===================================================================
# 6. Fix: regex search in ast_search
# ===================================================================

class TestRegexSearch:
    """
    ast_search now supports a `regex` flag for pattern-based symbol search.
    """

    @pytest.fixture
    def enhancer(self, tmp_path, monkeypatch):
        return _make_enhancer_with_codebase(tmp_path, monkeypatch, {
            "handlers.py": textwrap.dedent("""\
                def handleClick():
                    pass
                def handleSubmit():
                    pass
                def handleKeyPress():
                    pass
                def unrelated():
                    pass
            """),
        })

    @pytest.mark.asyncio
    async def test_regex_search_pattern(self, enhancer, tmp_path, monkeypatch):
        """Regex 'handle.*' should match handleClick, handleSubmit, handleKeyPress."""
        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="handle.*", regex=True)
        assert "error" not in result, f"Unexpected error: {result}"
        content = result["content"]
        assert "handleClick" in content
        assert "handleSubmit" in content
        assert "handleKeyPress" in content
        assert "unrelated" not in content

    @pytest.mark.asyncio
    async def test_regex_search_character_class(self, enhancer, tmp_path, monkeypatch):
        """Regex with character class should work."""
        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="handle[A-Z].*", regex=True)
        assert "error" not in result
        content = result["content"]
        assert "handleClick" in content or "handleSubmit" in content

    @pytest.mark.asyncio
    async def test_regex_search_invalid_pattern(self, enhancer, tmp_path, monkeypatch):
        """Invalid regex should return a clear error."""
        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="[invalid", regex=True)
        assert result.get("error") is True
        assert "Invalid regex" in result["message"]

    @pytest.mark.asyncio
    async def test_regex_false_uses_substring(self, enhancer, tmp_path, monkeypatch):
        """With regex=False, search should use substring matching as before."""
        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="handle", regex=False)
        assert "error" not in result
        content = result["content"]
        # Substring "handle" matches all three handlers
        assert "handleClick" in content

    @pytest.mark.asyncio
    async def test_regex_case_insensitive(self, enhancer, tmp_path, monkeypatch):
        """Regex search should be case-insensitive."""
        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="HANDLE.*CLICK", regex=True)
        assert "error" not in result
        content = result["content"]
        assert "handleClick" in content


# ===================================================================
# 7. Fix: TypeScript callable detection
# ===================================================================

class TestTypeScriptCallableDetection:
    """
    TypeScript variables initialized with arrow functions or React hooks
    (useCallback, useMemo) should be detected as callable and typed as
    'function' rather than 'variable'.
    """

    def _make_ts_node(self, node_type, name, children=None):
        """Helper to create a mock TS AST node dict."""
        return {
            'type': node_type,
            'kind': node_type,
            'name': name,
            'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 10, 'endColumn': 1},
            'children': children or [],
        }

    def test_arrow_function_detected(self):
        """const foo = () => { ... } should be detected as callable."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'myHandler', children=[
            self._make_ts_node('ArrowFunction', '', children=[])
        ])
        result = converter._detect_callable_variable(node)
        assert result is not None, "Arrow function not detected as callable"
        assert result.get('callable') is True
        assert result.get('is_arrow') is True

    def test_function_expression_detected(self):
        """const foo = function() { ... } should be detected as callable."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'myFunc', children=[
            self._make_ts_node('FunctionExpression', '', children=[])
        ])
        result = converter._detect_callable_variable(node)
        assert result is not None, "Function expression not detected as callable"
        assert result.get('callable') is True

    def test_use_callback_detected(self):
        """const foo = useCallback(() => {}, []) should be detected as callable."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'handleClick', children=[
            {
                'kind': 'CallExpression',
                'type': 'CallExpression',
                'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 50},
                'children': [
                    {'kind': 'Identifier', 'name': 'useCallback',
                     'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 12},
                     'children': []},
                    {'kind': 'ArrowFunction', 'type': 'ArrowFunction',
                     'pos': {'startLine': 1, 'startColumn': 13, 'endLine': 1, 'endColumn': 40},
                     'children': [],
                     'parameters': [{'name': 'e'}]},
                ]
            }
        ])
        result = converter._detect_callable_variable(node)
        assert result is not None, "useCallback not detected as callable"
        assert result.get('callable') is True
        assert result.get('is_hook') is True
        assert result.get('hook_name') == 'useCallback'

    def test_use_memo_detected(self):
        """const val = useMemo(() => compute(), [deps]) should be callable."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'computedValue', children=[
            {
                'kind': 'CallExpression',
                'type': 'CallExpression',
                'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 50},
                'children': [
                    {'kind': 'Identifier', 'name': 'useMemo',
                     'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 8},
                     'children': []},
                    {'kind': 'ArrowFunction', 'type': 'ArrowFunction',
                     'pos': {'startLine': 1, 'startColumn': 9, 'endLine': 1, 'endColumn': 40},
                     'children': []},
                ]
            }
        ])
        result = converter._detect_callable_variable(node)
        assert result is not None, "useMemo not detected as callable"
        assert result.get('hook_name') == 'useMemo'

    def test_regular_variable_not_callable(self):
        """const x = 42 should NOT be detected as callable."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'count', children=[
            self._make_ts_node('NumericLiteral', '42')
        ])
        result = converter._detect_callable_variable(node)
        assert result is None, "Plain variable incorrectly detected as callable"

    def test_property_access_hook_detected(self):
        """React.useCallback should be detected via PropertyAccessExpression."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'handler', children=[
            {
                'kind': 'CallExpression',
                'type': 'CallExpression',
                'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 50},
                'children': [
                    {
                        'kind': 'PropertyAccessExpression',
                        'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 20},
                        'children': [
                            {'kind': 'Identifier', 'name': 'React',
                             'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 6},
                             'children': []},
                            {'kind': 'Identifier', 'name': 'useCallback',
                             'pos': {'startLine': 1, 'startColumn': 7, 'endLine': 1, 'endColumn': 18},
                             'children': []},
                        ]
                    },
                    {'kind': 'ArrowFunction', 'type': 'ArrowFunction',
                     'pos': {'startLine': 1, 'startColumn': 21, 'endLine': 1, 'endColumn': 40},
                     'children': []},
                ]
            }
        ])
        result = converter._detect_callable_variable(node)
        assert result is not None, "React.useCallback not detected as callable"
        assert result.get('hook_name') == 'useCallback'

    def test_callable_variable_becomes_function_node(self):
        """When _detect_callable_variable returns truthy, the node type
        should be 'function' in the unified AST, not 'variable'."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        # Build a minimal SourceFile with a variable = ArrowFunction
        ts_ast = {
            'kind': 'SourceFile',
            'type': 'SourceFile',
            'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 5, 'endColumn': 1},
            'children': [
                {
                    'kind': 'variable',
                    'type': 'variable',
                    'name': 'myCallback',
                    'pos': {'startLine': 2, 'startColumn': 1, 'endLine': 4, 'endColumn': 1},
                    'children': [
                        {
                            'kind': 'ArrowFunction',
                            'type': 'ArrowFunction',
                            'pos': {'startLine': 2, 'startColumn': 20, 'endLine': 4, 'endColumn': 1},
                            'children': [],
                            'parameters': [{'name': 'e'}],
                        }
                    ],
                }
            ],
        }

        unified = converter.convert(ts_ast)
        # Find the node for myCallback
        callback_nodes = [n for n in unified.nodes.values() if n.name == 'myCallback']
        assert len(callback_nodes) >= 1, "myCallback node not found in unified AST"
        assert callback_nodes[0].node_type == 'function', (
            f"Expected node_type='function' for arrow-function variable, "
            f"got '{callback_nodes[0].node_type}'"
        )
        assert callback_nodes[0].attributes.get('callable') is True

    def test_hook_without_inner_function_still_callable(self):
        """useCallback without extractable inner function should still be marked callable."""
        from app.utils.ast_parser.typescript_parser import TypeScriptASTConverter
        converter = TypeScriptASTConverter("test.tsx")

        node = self._make_ts_node('variable', 'handler', children=[
            {
                'kind': 'CallExpression',
                'type': 'CallExpression',
                'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 50},
                'children': [
                    {'kind': 'Identifier', 'name': 'useCallback',
                     'pos': {'startLine': 1, 'startColumn': 1, 'endLine': 1, 'endColumn': 12},
                     'children': []},
                    # No ArrowFunction or FunctionExpression child
                    {'kind': 'SomeOtherNode', 'type': 'SomeOtherNode',
                     'pos': {'startLine': 1, 'startColumn': 13, 'endLine': 1, 'endColumn': 40},
                     'children': []},
                ]
            }
        ])
        result = converter._detect_callable_variable(node)
        assert result is not None, "useCallback without inner function not detected"
        assert result.get('callable') is True
        assert result.get('is_hook') is True


# ===================================================================
# 8. MCP tool layer: callers action
# ===================================================================

class TestASTReferencesCallersAction:
    """Test the 'callers' action via the MCP tool layer."""

    @pytest.fixture
    def enhancer(self, tmp_path, monkeypatch):
        return _make_enhancer_with_codebase(tmp_path, monkeypatch, {
            "module_a.py": SAMPLE_CROSS_FILE_A,
            "module_b.py": SAMPLE_CROSS_FILE_B,
        })

    @pytest.mark.asyncio
    async def test_callers_finds_cross_file_calls(self, enhancer, tmp_path, monkeypatch):
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="shared_util", action="callers")
        assert "error" not in result, f"Unexpected error: {result}"
        content = result["content"]
        assert "Found" in content
        # Should find at least one caller
        found_line = [l for l in content.split("\n") if "Found" in l]
        assert found_line, "No 'Found' line in callers output"
        # The found count should be >= 1
        assert "Found**: 0" not in content, (
            "Callers returned 0 results for cross-file call. "
            "The name-based fallback is not working in the MCP tool layer."
        )


# ===================================================================
# 9. MCP tool layer: dependencies action
# ===================================================================

class TestASTReferencesDependenciesAction:
    """Test the 'dependencies' action via the MCP tool layer."""

    @pytest.fixture
    def enhancer(self, tmp_path, monkeypatch):
        return _make_enhancer_with_codebase(tmp_path, monkeypatch, {
            "imports_test.py": SAMPLE_IMPORTS_CODE,
        })

    @pytest.mark.asyncio
    async def test_dependencies_returns_results(self, enhancer, tmp_path, monkeypatch):
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="imports_test.py", action="dependencies")
        assert "error" not in result, f"Unexpected error: {result}"
        content = result["content"]
        assert "Count" in content
        assert "Count**: 0" not in content, (
            "Dependencies returned 0 results. "
            "The import-attribute fallback is not working in the MCP tool layer."
        )


# ===================================================================
# 10. Query engine: _resolve_module_to_file edge cases
# ===================================================================

class TestResolveModuleToFile:
    """Test the module-to-file resolution helper in the query engine."""

    def _make_qe_with_files(self, file_paths):
        """Create a query engine with dummy nodes at the given file paths."""
        uast = UnifiedAST()
        for fp in file_paths:
            uast.add_node(
                "module", os.path.basename(fp),
                SourceLocation(fp, 1, 1, 1, 1)
            )
        return ASTQueryEngine(uast)

    def test_resolve_dotted_python_module(self):
        qe = self._make_qe_with_files([
            "/proj/app/utils/helpers.py",
            "/proj/app/main.py",
        ])
        result = qe._resolve_module_to_file("app.utils.helpers", "/proj/app/main.py")
        assert result == "/proj/app/utils/helpers.py"

    def test_resolve_init_file(self):
        qe = self._make_qe_with_files([
            "/proj/app/utils/__init__.py",
            "/proj/app/main.py",
        ])
        result = qe._resolve_module_to_file("app.utils", "/proj/app/main.py")
        assert result == "/proj/app/utils/__init__.py"

    def test_resolve_ts_module(self):
        qe = self._make_qe_with_files([
            "/proj/src/utils/helpers.ts",
            "/proj/src/main.ts",
        ])
        result = qe._resolve_module_to_file("src.utils.helpers", "/proj/src/main.ts")
        assert result == "/proj/src/utils/helpers.ts"

    def test_resolve_tsx_module(self):
        qe = self._make_qe_with_files([
            "/proj/src/components/Button.tsx",
            "/proj/src/App.tsx",
        ])
        result = qe._resolve_module_to_file("src.components.Button", "/proj/src/App.tsx")
        assert result == "/proj/src/components/Button.tsx"

    def test_resolve_nonexistent_returns_none(self):
        qe = self._make_qe_with_files(["/proj/app/main.py"])
        result = qe._resolve_module_to_file("totally.fake", "/proj/app/main.py")
        assert result is None

    def test_resolve_empty_module_returns_none(self):
        qe = self._make_qe_with_files(["/proj/app/main.py"])
        result = qe._resolve_module_to_file("", "/proj/app/main.py")
        assert result is None
