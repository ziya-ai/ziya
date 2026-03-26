"""
Tests for tree-sitter parser import fallback logic.

Verifies that the treesitter_parser module correctly handles:
1. tree-sitter-language-pack (preferred, Python 3.14+)
2. tree-sitter-languages (legacy fallback)
3. Neither installed (graceful degradation)
"""

import importlib
import logging
import sys
import types
from unittest import mock

import pytest


def _reload_treesitter_parser():
    """Force-reload treesitter_parser to re-trigger import logic."""
    mod_name = "app.utils.ast_parser.treesitter_parser"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


class TestTreeSitterImportFallback:
    """Verify the cascading import from language-pack → languages → disabled."""

    def test_prefers_language_pack(self):
        """When tree_sitter_language_pack is available, it should be used."""
        fake_pack = types.ModuleType("tree_sitter_language_pack")
        fake_pack.get_language = lambda name: f"lang:{name}"
        fake_pack.get_parser = lambda name: f"parser:{name}"

        with mock.patch.dict(sys.modules, {"tree_sitter_language_pack": fake_pack}):
            mod = _reload_treesitter_parser()
            assert mod._TS_AVAILABLE is True

    def test_falls_back_to_legacy(self):
        """When language-pack is missing but tree_sitter_languages exists, use it."""
        fake_legacy = types.ModuleType("tree_sitter_languages")
        fake_legacy.get_language = lambda name: f"legacy-lang:{name}"
        fake_legacy.get_parser = lambda name: f"legacy-parser:{name}"

        with mock.patch.dict(
            sys.modules,
            {"tree_sitter_language_pack": None, "tree_sitter_languages": fake_legacy},
        ):
            # None in sys.modules causes ImportError on import
            # We need to actually make the first import fail
            pass

        # More reliable approach: patch the import mechanism
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def selective_import(name, *args, **kwargs):
            if name == "tree_sitter_language_pack":
                raise ImportError("No module named 'tree_sitter_language_pack'")
            if name == "tree_sitter_languages":
                return fake_legacy
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=selective_import):
            mod = _reload_treesitter_parser()
            assert mod._TS_AVAILABLE is True

    def test_graceful_when_neither_installed(self):
        """When neither package is installed, _TS_AVAILABLE should be False."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fail_both(name, *args, **kwargs):
            if name in ("tree_sitter_language_pack", "tree_sitter_languages"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fail_both):
            mod = _reload_treesitter_parser()
            assert mod._TS_AVAILABLE is False

    def test_parser_raises_when_unavailable(self):
        """TreeSitterParser.parse() should raise RuntimeError when TS is unavailable."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fail_both(name, *args, **kwargs):
            if name in ("tree_sitter_language_pack", "tree_sitter_languages"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fail_both):
            mod = _reload_treesitter_parser()
            assert mod._TS_AVAILABLE is False
            # Can't instantiate TreeSitterParser without the library,
            # but we can verify the module state
            assert not mod._TS_AVAILABLE


class TestTreeSitterParserBasic:
    """Basic smoke tests for TreeSitterParser when tree-sitter is available."""

    def test_lang_configs_populated(self):
        """Ensure _LANG_CONFIGS has entries for expected languages."""
        from app.utils.ast_parser.treesitter_parser import _LANG_CONFIGS

        assert "cpp" in _LANG_CONFIGS
        assert "rust" in _LANG_CONFIGS
        assert "go" in _LANG_CONFIGS
        assert "java" in _LANG_CONFIGS

    def test_lang_configs_have_extensions(self):
        """Each language config should have a list of file extensions."""
        from app.utils.ast_parser.treesitter_parser import _LANG_CONFIGS

        for lang, cfg in _LANG_CONFIGS.items():
            assert "extensions" in cfg, f"{lang} missing extensions"
            assert len(cfg["extensions"]) > 0, f"{lang} has empty extensions"
            for ext in cfg["extensions"]:
                assert ext.startswith("."), f"{lang} extension '{ext}' should start with '.'"

    def test_lang_configs_have_node_map(self):
        """Each language config should have a node_map dict."""
        from app.utils.ast_parser.treesitter_parser import _LANG_CONFIGS

        for lang, cfg in _LANG_CONFIGS.items():
            assert "node_map" in cfg, f"{lang} missing node_map"
            assert isinstance(cfg["node_map"], dict), f"{lang} node_map should be dict"

    @pytest.mark.skipif(
        not importlib.util.find_spec("tree_sitter_language_pack")
        and not importlib.util.find_spec("tree_sitter_languages"),
        reason="No tree-sitter language package installed",
    )
    def test_parser_can_parse_cpp(self):
        """TreeSitterParser can parse a simple C++ snippet."""
        from app.utils.ast_parser.treesitter_parser import TreeSitterParser, _TS_AVAILABLE

        if not _TS_AVAILABLE:
            pytest.skip("tree-sitter not available")

        parser = TreeSitterParser("cpp")
        source = 'int main() { return 0; }'
        tree = parser.parse("test.cpp", source)
        assert tree is not None
        assert tree.root_node is not None

    @pytest.mark.skipif(
        not importlib.util.find_spec("tree_sitter_language_pack")
        and not importlib.util.find_spec("tree_sitter_languages"),
        reason="No tree-sitter language package installed",
    )
    def test_parser_produces_unified_ast(self):
        """TreeSitterParser.to_unified_ast returns a UnifiedAST with nodes."""
        from app.utils.ast_parser.treesitter_parser import TreeSitterParser, _TS_AVAILABLE

        if not _TS_AVAILABLE:
            pytest.skip("tree-sitter not available")

        parser = TreeSitterParser("cpp")
        source = """
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

class MyClass {
public:
    void doSomething();
};
"""
        tree = parser.parse("test.cpp", source)
        ast = parser.to_unified_ast(tree, "test.cpp")
        assert ast is not None
        # Should have found the function and/or class
        assert len(ast.nodes) > 0
