"""
Tests for AST system enhancements #5 (structural search filters) and #6 (reverse dependencies).

These cover:
- get_reverse_dependencies() in the query engine
- "importers" action in ast_references MCP tool
- has_decorator / is_async / base_class filters in ast_search
- Empty query + filter combinations
- Combined filter scenarios
"""

import os
import textwrap

import pytest
import pytest_asyncio

from app.utils.ast_parser.python_parser import PythonASTParser
from app.utils.ast_parser.unified_ast import UnifiedAST, SourceLocation
from app.utils.ast_parser.query_engine import ASTQueryEngine
from app.utils.ast_parser.ziya_ast_enhancer import ZiyaASTEnhancer


# ===================================================================
# Sample code for testing
# ===================================================================

SAMPLE_MODULE_A = textwrap.dedent("""\
    \"\"\"Module A — the one others import.\"\"\"

    def add(a: int, b: int) -> int:
        return a + b

    def subtract(a: int, b: int) -> int:
        return a - b
""")

SAMPLE_MODULE_B = textwrap.dedent("""\
    \"\"\"Module B — imports from A.\"\"\"
    from sample_a import add

    def multiply(x: int, y: int) -> int:
        return x * y

    total = add(3, 4)
""")

SAMPLE_MODULE_C = textwrap.dedent("""\
    \"\"\"Module C — also imports from A.\"\"\"
    import sample_a

    result = sample_a.add(1, 2)
""")

SAMPLE_MODULE_D = textwrap.dedent("""\
    \"\"\"Module D — imports nothing.\"\"\"

    def standalone():
        pass
""")

# Code with decorators and inheritance for structural filter tests.
# Note: Python parser only captures simple Name decorators (like @staticmethod)
# and Call decorators with Name func (like @decorator(args)).
# Attribute decorators like @pytest.fixture are NOT captured by the current parser.
SAMPLE_DECORATORS = textwrap.dedent("""\
    class BaseHandler:
        pass

    class ChildHandler(BaseHandler):
        pass

    @staticmethod
    def helper():
        pass

    async def fetch_data(url: str) -> str:
        pass

    def sync_handler():
        pass

    class APIClient(BaseHandler):
        \"\"\"Another child of BaseHandler.\"\"\"

        @staticmethod
        def get_instance():
            pass

        async def do_request(self):
            pass
""")


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def python_parser():
    return PythonASTParser()


@pytest.fixture
def multi_file_project_ast(python_parser):
    """Build a merged project AST from four sample modules."""
    files = {
        "sample_a.py": SAMPLE_MODULE_A,
        "sample_b.py": SAMPLE_MODULE_B,
        "sample_c.py": SAMPLE_MODULE_C,
        "sample_d.py": SAMPLE_MODULE_D,
    }
    merged = UnifiedAST()
    for fname, src in files.items():
        native = python_parser.parse(fname, src)
        file_ast = python_parser.to_unified_ast(native, fname)
        merged.merge(file_ast)
    return merged


@pytest.fixture
def multi_file_qe(multi_file_project_ast):
    return ASTQueryEngine(multi_file_project_ast)


def _make_enhancer(tmp_path, python_source: str, filename: str = "decorators.py"):
    """Create a real enhancer for a single-file codebase."""
    (tmp_path / filename).write_text(python_source)
    (tmp_path / ".gitignore").write_text("__pycache__/\n")

    enhancer = ZiyaASTEnhancer(ast_resolution="medium")
    enhancer.process_codebase(str(tmp_path), max_depth=5)
    return enhancer


def _register_enhancer(enhancer, tmp_path, monkeypatch):
    """Register the enhancer so MCP tools can find it."""
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    from app.utils.ast_parser.integration import _enhancers, _initialized_projects
    abs_path = os.path.abspath(str(tmp_path))
    _enhancers[abs_path] = enhancer
    _initialized_projects.add(abs_path)


def _make_multi_file_enhancer(tmp_path):
    """Create an enhancer with the four-module codebase."""
    files = {
        "sample_a.py": SAMPLE_MODULE_A,
        "sample_b.py": SAMPLE_MODULE_B,
        "sample_c.py": SAMPLE_MODULE_C,
        "sample_d.py": SAMPLE_MODULE_D,
    }
    for fname, src in files.items():
        (tmp_path / fname).write_text(src)
    (tmp_path / ".gitignore").write_text("__pycache__/\n")

    enhancer = ZiyaASTEnhancer(ast_resolution="medium")
    enhancer.process_codebase(str(tmp_path), max_depth=5)
    return enhancer


# ===================================================================
# 1. Reverse dependencies (query engine)
# ===================================================================

class TestReverseDependencies:
    """Test get_reverse_dependencies() on merged project AST."""

    def test_file_with_importers(self, multi_file_qe):
        """sample_a.py is imported by sample_b.py and sample_c.py."""
        importers = multi_file_qe.get_reverse_dependencies("sample_a.py")
        assert len(importers) >= 2
        importer_set = set(importers)
        assert "sample_b.py" in importer_set
        assert "sample_c.py" in importer_set

    def test_file_with_no_importers(self, multi_file_qe):
        """sample_d.py is not imported by anyone."""
        importers = multi_file_qe.get_reverse_dependencies("sample_d.py")
        assert importers == []

    def test_does_not_include_self(self, multi_file_qe):
        """sample_a.py should not list itself as an importer."""
        importers = multi_file_qe.get_reverse_dependencies("sample_a.py")
        assert "sample_a.py" not in importers

    def test_nonexistent_file_returns_empty(self, multi_file_qe):
        importers = multi_file_qe.get_reverse_dependencies("nonexistent.py")
        assert importers == []

    def test_importers_are_sorted(self, multi_file_qe):
        importers = multi_file_qe.get_reverse_dependencies("sample_a.py")
        assert importers == sorted(importers)


# ===================================================================
# 2. Importers action (MCP tool)
# ===================================================================

class TestASTReferencesImportersAction:

    @pytest.mark.asyncio
    async def test_importers_action_returns_results(self, tmp_path, monkeypatch):
        enhancer = _make_multi_file_enhancer(tmp_path)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="sample_a.py", action="importers")
        assert "error" not in result
        content = result["content"]
        assert "Importers" in content
        assert "sample_b" in content
        assert "sample_c" in content

    @pytest.mark.asyncio
    async def test_importers_action_unknown_file(self, tmp_path, monkeypatch):
        enhancer = _make_multi_file_enhancer(tmp_path)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="nonexistent.py", action="importers")
        assert result.get("error") is True

    @pytest.mark.asyncio
    async def test_importers_action_no_importers(self, tmp_path, monkeypatch):
        enhancer = _make_multi_file_enhancer(tmp_path)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTReferencesTool
        tool = ASTReferencesTool()
        result = await tool.execute(name="sample_d.py", action="importers")
        assert "error" not in result
        assert "Count**: 0" in result["content"]


# ===================================================================
# 3. Decorator filter (ast_search)
# ===================================================================

class TestDecoratorFilter:

    @pytest.mark.asyncio
    async def test_filter_by_staticmethod(self, tmp_path, monkeypatch):
        """@staticmethod is a simple Name decorator — parser captures it."""
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="function", has_decorator="staticmethod")
        content = result["content"]
        assert "helper" in content, f"Expected helper function with @staticmethod, got: {content}"

    @pytest.mark.asyncio
    async def test_filter_by_attribute_decorator(self, tmp_path, monkeypatch):
        """Attribute decorators like @pytest.fixture should be captured
        by the Python parser (e.g. 'pytest.fixture')."""
        code = textwrap.dedent("""\
            import pytest

            @pytest.fixture
            def sample_data():
                return [1, 2, 3]

            @staticmethod
            def other_func():
                pass
        """)
        enhancer = _make_enhancer(tmp_path, code, "test_decorators.py")
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="function", has_decorator="pytest.fixture")
        content = result["content"]
        assert "sample_data" in content, (
            f"Expected sample_data with @pytest.fixture decorator, got: {content}"
        )
        # Should NOT include other_func (which has @staticmethod, not @pytest.fixture)
        assert "other_func" not in content

    @pytest.mark.asyncio
    async def test_decorator_filter_no_match(self, tmp_path, monkeypatch):
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="function", has_decorator="nonexistent_decorator")
        content = result["content"]
        assert "Results**: 0" in content


# ===================================================================
# 4. Async filter (ast_search)
# ===================================================================

class TestAsyncFilter:

    @pytest.mark.asyncio
    async def test_filter_async_only(self, tmp_path, monkeypatch):
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="function", is_async=True)
        content = result["content"]
        # Should find async functions
        assert "fetch_data" in content or "do_request" in content, \
            f"Expected async functions, got: {content}"
        # Should NOT find sync functions
        assert "sync_handler" not in content

    @pytest.mark.asyncio
    async def test_filter_sync_only(self, tmp_path, monkeypatch):
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="function", is_async=False)
        content = result["content"]
        # Should find sync functions
        assert "helper" in content or "sync_handler" in content
        # Should NOT find async functions (fetch_data, do_request)
        assert "async def fetch_data" not in content
        assert "async def do_request" not in content


# ===================================================================
# 5. Base class filter (ast_search)
# ===================================================================

class TestBaseClassFilter:

    @pytest.mark.asyncio
    async def test_filter_by_base_class(self, tmp_path, monkeypatch):
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="class", base_class="BaseHandler")
        content = result["content"]
        # Should find ChildHandler and APIClient (both extend BaseHandler)
        assert "ChildHandler" in content, f"Expected ChildHandler, got: {content}"
        assert "APIClient" in content, f"Expected APIClient, got: {content}"
        # Count results — should be exactly 2 (not BaseHandler itself)
        assert "Results**: 2" in content, f"Expected exactly 2 subclasses, got: {content}"

    @pytest.mark.asyncio
    async def test_base_class_no_match(self, tmp_path, monkeypatch):
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="class", base_class="NonexistentBase")
        content = result["content"]
        assert "Results**: 0" in content


# ===================================================================
# 6. Empty query with filters
# ===================================================================

class TestEmptyQueryWithFilters:

    @pytest.mark.asyncio
    async def test_empty_query_with_type_filter(self, tmp_path, monkeypatch):
        """Empty query + node_type=class should return all classes."""
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="class")
        content = result["content"]
        # Should find BaseHandler, ChildHandler, and APIClient
        assert "BaseHandler" in content
        assert "ChildHandler" in content

    @pytest.mark.asyncio
    async def test_empty_query_no_filters_returns_all(self, tmp_path, monkeypatch):
        """Empty query with no filters should return all symbols."""
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="")
        content = result["content"]
        # Should return results (not 0)
        assert "Results**: 0" not in content


# ===================================================================
# 7. Combined filters
# ===================================================================

class TestCombinedFilters:

    @pytest.mark.asyncio
    async def test_async_plus_decorator(self, tmp_path, monkeypatch):
        """Combining is_async=True + has_decorator should find async decorated functions."""
        code = textwrap.dedent("""\
            import pytest

            @pytest.mark.asyncio
            async def test_fetch():
                pass

            @staticmethod
            def sync_helper():
                pass

            async def plain_async():
                pass
        """)
        enhancer = _make_enhancer(tmp_path, code, "combined.py")
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query="", node_type="function",
                                    is_async=True, has_decorator="pytest.mark.asyncio")
        content = result["content"]
        assert "test_fetch" in content, f"Expected test_fetch, got: {content}"
        # plain_async is async but doesn't have the decorator
        assert "plain_async" not in content
        # sync_helper has a decorator but is not async
        assert "sync_helper" not in content

    @pytest.mark.asyncio
    async def test_regex_plus_type_filter(self, tmp_path, monkeypatch):
        enhancer = _make_enhancer(tmp_path, SAMPLE_DECORATORS)
        _register_enhancer(enhancer, tmp_path, monkeypatch)

        from app.mcp.tools.ast_tools import ASTSearchTool
        tool = ASTSearchTool()
        result = await tool.execute(query=".*Handler", regex=True, node_type="class")
        content = result["content"]
        assert "BaseHandler" in content or "ChildHandler" in content
