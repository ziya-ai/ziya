"""
AST system regression tests.

Covers parser registration, Python parsing, query engine operations,
the enhancer pipeline, and the MCP tool layer.

The root-cause bug that motivated this suite: ZiyaASTEnhancer._register_parsers()
only registered HTMLCSSParser — PythonASTParser and TypeScriptASTParser were
imported but never registered.  That meant .py/.ts/.js files were silently
skipped during indexing, producing an empty/useless AST for any non-HTML project.
"""

import ast
import os
import textwrap
import tempfile
import shutil

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from app.utils.ast_parser.registry import ParserRegistry, ASTParserPlugin
from app.utils.ast_parser.python_parser import PythonASTParser
from app.utils.ast_parser.typescript_parser import TypeScriptASTParser
from app.utils.ast_parser.html_css_parser import HTMLCSSParser
from app.utils.ast_parser.unified_ast import UnifiedAST, Node, SourceLocation, Edge
from app.utils.ast_parser.query_engine import ASTQueryEngine
from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


# ===================================================================
# Fixtures
# ===================================================================

SAMPLE_PYTHON = textwrap.dedent("""\
    \"\"\"Sample module docstring.\"\"\"

    import os
    from typing import List, Optional

    CONSTANT = 42

    class Greeter:
        \"\"\"A greeting class.\"\"\"

        def __init__(self, name: str):
            self.name = name

        def greet(self) -> str:
            return f"Hello, {self.name}"

    def add(a: int, b: int) -> int:
        \"\"\"Add two numbers.\"\"\"
        return a + b

    async def fetch_data(url: str) -> Optional[str]:
        pass

    result = add(1, 2)
""")

SAMPLE_PYTHON_B = textwrap.dedent("""\
    from sample_a import add

    def multiply(x: int, y: int) -> int:
        return x * y

    total = add(3, 4)
""")


SAMPLE_PYTHON_COMPLEX_BASES = textwrap.dedent("""\
    from typing import Generic, TypeVar

    T = TypeVar("T")

    class Base:
        pass

    class Child(Base):
        pass

    class GenericChild(Generic[T]):
        \"\"\"Class whose base is a subscript expression — parser may yield None.\"\"\"
        pass
""")


@pytest.fixture
def python_parser():
    return PythonASTParser()


@pytest.fixture
def sample_ast(python_parser):
    """Parse SAMPLE_PYTHON and return the UnifiedAST."""
    native = python_parser.parse("sample.py", SAMPLE_PYTHON)
    return python_parser.to_unified_ast(native, "sample.py")


@pytest.fixture
def sample_query_engine(sample_ast):
    return ASTQueryEngine(sample_ast)


@pytest.fixture
def tmp_codebase(tmp_path):
    """Create a minimal filesystem codebase for enhancer tests."""
    (tmp_path / "sample_a.py").write_text(SAMPLE_PYTHON)
    (tmp_path / "sample_b.py").write_text(SAMPLE_PYTHON_B)
    (tmp_path / "page.html").write_text("<html><body><h1>Hi</h1></body></html>")
    (tmp_path / "style.css").write_text("body { margin: 0; }")
    # A non-parseable file — should be silently skipped
    (tmp_path / "data.json").write_text('{"key": "value"}')
    # Create .gitignore so directory_util doesn't choke
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    # File with complex base classes that can produce None in bases list
    (tmp_path / "complex_bases.py").write_text(SAMPLE_PYTHON_COMPLEX_BASES)
    return tmp_path


# ===================================================================
# 1. Parser Registry
# ===================================================================

class TestParserRegistry:
    """Verify that the registry correctly maps extensions to parsers."""

    def test_register_and_lookup_python(self):
        reg = ParserRegistry()
        reg.register_parser(PythonASTParser)
        assert reg.get_parser("foo.py") is PythonASTParser
        assert reg.get_parser("foo.pyi") is PythonASTParser

    def test_register_and_lookup_typescript(self):
        reg = ParserRegistry()
        reg.register_parser(TypeScriptASTParser)
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            assert reg.get_parser(f"file{ext}") is TypeScriptASTParser, f"missing {ext}"

    def test_register_and_lookup_html_css(self):
        reg = ParserRegistry()
        reg.register_parser(HTMLCSSParser)
        for ext in (".html", ".htm", ".css"):
            assert reg.get_parser(f"file{ext}") is HTMLCSSParser, f"missing {ext}"

    def test_unknown_extension_returns_none(self):
        reg = ParserRegistry()
        reg.register_parser(PythonASTParser)
        assert reg.get_parser("data.json") is None


# ===================================================================
# 2. Enhancer parser registration — the root-cause regression test
# ===================================================================

class TestEnhancerRegistration:
    """
    The original bug: _register_parsers only registered HTMLCSSParser.
    These tests ensure all three parsers are registered.
    """

    def test_python_parser_is_registered(self):
        enhancer = ZiyaASTEnhancer()
        parser_cls = enhancer.parser_registry.get_parser("example.py")
        assert parser_cls is not None, (
            "PythonASTParser not registered — .py files will be silently skipped"
        )
        assert parser_cls is PythonASTParser

    def test_typescript_parser_is_registered(self):
        enhancer = ZiyaASTEnhancer()
        parser_cls = enhancer.parser_registry.get_parser("component.tsx")
        assert parser_cls is not None, (
            "TypeScriptASTParser not registered — .ts/.tsx files will be silently skipped"
        )
        assert parser_cls is TypeScriptASTParser

    def test_html_css_parser_is_registered(self):
        enhancer = ZiyaASTEnhancer()
        parser_cls = enhancer.parser_registry.get_parser("page.html")
        assert parser_cls is HTMLCSSParser

    def test_all_core_extensions_covered(self):
        """Every core extension should map to a parser."""
        enhancer = ZiyaASTEnhancer()
        expected = {
            ".py": PythonASTParser,
            ".pyi": PythonASTParser,
            ".ts": TypeScriptASTParser,
            ".tsx": TypeScriptASTParser,
            ".js": TypeScriptASTParser,
            ".jsx": TypeScriptASTParser,
            ".html": HTMLCSSParser,
            ".htm": HTMLCSSParser,
            ".css": HTMLCSSParser,
        }
        for ext, expected_cls in expected.items():
            actual = enhancer.parser_registry.get_parser(f"file{ext}")
            assert actual is expected_cls, (
                f"Extension {ext}: expected {expected_cls.__name__}, "
                f"got {actual.__name__ if actual else 'None'}"
            )


# ===================================================================
# 3. Python parser — node extraction
# ===================================================================

class TestPythonParser:
    """Verify the Python parser produces the expected AST nodes."""

    def test_parse_returns_ast_module(self, python_parser):
        native = python_parser.parse("test.py", SAMPLE_PYTHON)
        assert isinstance(native, ast.Module)

    def test_unified_ast_has_nodes(self, sample_ast):
        assert len(sample_ast.nodes) > 0

    def test_class_detected(self, sample_ast):
        classes = sample_ast.get_nodes_by_type("class")
        names = {n.name for n in classes}
        assert "Greeter" in names

    def test_functions_detected(self, sample_ast):
        functions = sample_ast.get_nodes_by_type("function")
        names = {n.name for n in functions}
        assert "add" in names
        assert "greet" in names
        assert "__init__" in names
        assert "fetch_data" in names

    def test_async_function_flagged(self, sample_ast):
        functions = sample_ast.get_nodes_by_type("function")
        fetch = [n for n in functions if n.name == "fetch_data"]
        assert len(fetch) == 1
        assert fetch[0].attributes.get("is_async") is True

    def test_imports_detected(self, sample_ast):
        imports = sample_ast.get_nodes_by_type("import")
        names = {n.name for n in imports}
        assert "os" in names
        # from typing import List, Optional → two import nodes
        assert "List" in names
        assert "Optional" in names

    def test_variable_detected(self, sample_ast):
        variables = sample_ast.get_nodes_by_type("variable")
        names = {n.name for n in variables}
        assert "CONSTANT" in names

    def test_function_params_captured(self, sample_ast):
        functions = sample_ast.get_nodes_by_type("function")
        add_fn = [n for n in functions if n.name == "add"][0]
        params = add_fn.attributes.get("params", [])
        param_names = [p["name"] for p in params]
        assert "a" in param_names
        assert "b" in param_names

    def test_return_type_captured(self, sample_ast):
        functions = sample_ast.get_nodes_by_type("function")
        add_fn = [n for n in functions if n.name == "add"][0]
        assert add_fn.attributes.get("return_type") == "int"

    def test_class_bases_captured(self, sample_ast):
        classes = sample_ast.get_nodes_by_type("class")
        greeter = [c for c in classes if c.name == "Greeter"][0]
        # Greeter has no explicit base
        assert greeter.attributes.get("bases") == []

    def test_source_locations_are_sane(self, sample_ast):
        for node in sample_ast.nodes.values():
            loc = node.source_location
            assert loc.start_line >= 1
            assert loc.end_line >= loc.start_line
            assert loc.file_path == "sample.py"

    def test_edges_exist(self, sample_ast):
        """The parser should produce containment and call edges."""
        assert len(sample_ast.edges) > 0
        edge_types = {e.edge_type for e in sample_ast.edges}
        assert "contains" in edge_types


# ===================================================================
# 4. Query Engine
# ===================================================================

class TestQueryEngine:

    def test_find_definitions_by_name(self, sample_query_engine):
        defs = sample_query_engine.find_definitions("add")
        assert len(defs) >= 1
        assert any(d.node_type == "function" for d in defs)

    def test_find_definitions_unknown(self, sample_query_engine):
        assert sample_query_engine.find_definitions("nonexistent") == []

    def test_find_functions(self, sample_query_engine):
        funcs = sample_query_engine.find_functions()
        names = {f.name for f in funcs}
        assert "add" in names

    def test_find_classes(self, sample_query_engine):
        classes = sample_query_engine.find_classes()
        names = {c.name for c in classes}
        assert "Greeter" in names

    def test_generate_summary(self, sample_query_engine):
        summary = sample_query_engine.generate_summary("sample.py")
        assert "function" in summary["type_counts"]
        assert "class" in summary["type_counts"]
        top_names = {d["name"] for d in summary["top_level_definitions"]}
        assert "add" in top_names
        assert "Greeter" in top_names

    def test_indices_populated(self, sample_query_engine):
        """Name, type, and file indices should all be non-empty."""
        assert len(sample_query_engine.name_index) > 0
        assert len(sample_query_engine.type_index) > 0
        assert len(sample_query_engine.file_index) > 0


# ===================================================================
# 5. UnifiedAST merge
# ===================================================================

class TestUnifiedASTMerge:

    def test_merge_combines_nodes(self, python_parser):
        ast_a = python_parser.to_unified_ast(
            python_parser.parse("a.py", SAMPLE_PYTHON), "a.py"
        )
        ast_b = python_parser.to_unified_ast(
            python_parser.parse("b.py", SAMPLE_PYTHON_B), "b.py"
        )
        count_before = len(ast_a.nodes)
        ast_a.merge(ast_b)
        assert len(ast_a.nodes) > count_before

    def test_merge_is_idempotent_for_same_ids(self):
        """Merging the same AST twice should not duplicate nodes."""
        a = UnifiedAST()
        loc = SourceLocation("f.py", 1, 1, 1, 10)
        nid = a.add_node("function", "foo", loc)

        b = UnifiedAST()
        # same node_id won't happen in practice (UUIDs), but if it does:
        b.nodes[nid] = a.nodes[nid]

        a.merge(b)
        assert len(a.nodes) == 1  # no duplicate

    def test_serialization_roundtrip(self, sample_ast):
        json_str = sample_ast.to_json()
        restored = UnifiedAST.from_json(json_str)
        assert len(restored.nodes) == len(sample_ast.nodes)
        assert len(restored.edges) == len(sample_ast.edges)


# ===================================================================
# 6. ZiyaASTEnhancer end-to-end (filesystem)
# ===================================================================

class TestEnhancerEndToEnd:
    """Test that the enhancer indexes real files from a temp codebase."""

    def test_process_codebase_indexes_python(self, tmp_codebase, monkeypatch):
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        result = enhancer.process_codebase(str(tmp_codebase), max_depth=5)

        assert result["files_processed"] >= 2, (
            f"Expected at least 2 Python files indexed, got {result['files_processed']}. "
            f"Indexed: {list(enhancer.ast_cache.keys())}"
        )

        # Verify Python files specifically are in the cache
        cached_exts = {os.path.splitext(fp)[1] for fp in enhancer.ast_cache}
        assert ".py" in cached_exts, (
            f"No .py files in AST cache. Extensions found: {cached_exts}. "
            "PythonASTParser is likely not registered."
        )

    def test_project_query_engine_created(self, tmp_codebase, monkeypatch):
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        enhancer.process_codebase(str(tmp_codebase), max_depth=5)

        assert "project" in enhancer.query_engines

    def test_project_ast_has_cross_file_nodes(self, tmp_codebase, monkeypatch):
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        enhancer.process_codebase(str(tmp_codebase), max_depth=5)

        all_names = {n.name for n in enhancer.project_ast.nodes.values()}
        # from sample_a.py
        assert "Greeter" in all_names
        assert "add" in all_names
        # from sample_b.py
        assert "multiply" in all_names

    def test_generate_ast_context_not_empty(self, tmp_codebase, monkeypatch):
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        enhancer.process_codebase(str(tmp_codebase), max_depth=5)

        ctx = enhancer.generate_ast_context()
        assert "def add" in ctx or "def multiply" in ctx, (
            f"AST context lacks Python function symbols. Context preview: {ctx[:500]}"
        )

    def test_disabled_resolution_returns_empty(self, tmp_codebase, monkeypatch):
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="disabled")
        enhancer.process_codebase(str(tmp_codebase), max_depth=5)
        assert enhancer.generate_ast_context() == ""

    def test_extract_key_symbols(self, tmp_codebase, monkeypatch):
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        enhancer.process_codebase(str(tmp_codebase), max_depth=5)

        # Pick a cached Python file and check symbol extraction
        py_files = [fp for fp in enhancer.ast_cache if fp.endswith(".py")]
        assert py_files, "No .py files in cache"
        symbols = enhancer._extract_key_symbols(enhancer.ast_cache[py_files[0]])
        assert len(symbols) > 0, "No symbols extracted from Python AST"

    def test_extract_key_symbols_with_none_bases(self, python_parser):
        """
        Regression: _extract_key_symbols crashed with
        'sequence item 0: expected str instance, NoneType found'
        when a class had None entries in its bases list (e.g. Generic[T]).
        """
        native = python_parser.parse("complex.py", SAMPLE_PYTHON_COMPLEX_BASES)
        unified = python_parser.to_unified_ast(native, "complex.py")

        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        # This must not raise TypeError
        symbols = enhancer._extract_key_symbols(unified)
        class_symbols = [s for s in symbols if "class " in s]
        assert len(class_symbols) >= 2, (
            f"Expected at least Base and Child classes, got: {class_symbols}"
        )

    def test_generate_context_with_none_bases(self, tmp_codebase, monkeypatch):
        """
        End-to-end regression: process_codebase + generate_ast_context must
        not crash when the codebase contains classes with unparseable bases.
        """
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        # process_codebase calls generate_ast_context internally;
        # pre-fix this raised TypeError
        result = enhancer.process_codebase(str(tmp_codebase), max_depth=5)
        assert result["files_processed"] >= 3, (
            f"Expected >=3 files (sample_a, sample_b, complex_bases), "
            f"got {result['files_processed']}"
        )
        ctx = enhancer.generate_ast_context()
        assert "class Child" in ctx or "class Base" in ctx, (
            f"Complex-bases file not in context. Preview: {ctx[:500]}"
        )


# ===================================================================
# 7. MCP tool layer (ast_tools.py)
# ===================================================================

class TestASTToolLayer:
    """Test the MCP tool wrappers in ast_tools.py."""

    def _make_enhancer(self, tmp_codebase, monkeypatch):
        """Create and register an enhancer for tmp_codebase."""
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_codebase))
        from app.utils.ast_parser.integration import (
            _enhancers, _initialized_projects,
        )
        enhancer = ZiyaASTEnhancer(ast_resolution="medium")
        enhancer.process_codebase(str(tmp_codebase), max_depth=5)
        abs_path = os.path.abspath(str(tmp_codebase))
        _enhancers[abs_path] = enhancer
        _initialized_projects.add(abs_path)
        return enhancer

    @pytest.mark.asyncio
    async def test_ast_get_tree_overview(self, tmp_codebase, monkeypatch):
        self._make_enhancer(tmp_codebase, monkeypatch)
        from app.mcp.tools.ast_tools import ASTGetTreeTool
        tool = ASTGetTreeTool()
        result = await tool.execute(path=None)
        assert "error" not in result
        content = result["content"]
        assert "Indexed files" in content

    @pytest.mark.asyncio
    async def test_ast_search_finds_python_symbol(self, tmp_codebase, monkeypatch):
        self._make_enhancer(tmp_codebase, monkeypatch)
        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="Greeter")
        assert "error" not in result
        assert "Greeter" in result["content"]

    @pytest.mark.asyncio
    async def test_ast_references_definitions(self, tmp_codebase, monkeypatch):
        self._make_enhancer(tmp_codebase, monkeypatch)
        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="add", action="definitions")
        assert "error" not in result
        assert "add" in result["content"]
