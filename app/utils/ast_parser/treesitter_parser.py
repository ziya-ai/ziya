"""
Tree-sitter based AST parser for C/C++, Rust, Go, Java, and other languages.

Uses the tree-sitter-languages package which bundles pre-built grammars
for ~40 languages.  Falls back gracefully if not installed.
"""

import logging
from typing import Any, Dict, List, Optional

from .registry import ASTParserPlugin
from .unified_ast import UnifiedAST, SourceLocation

logger = logging.getLogger(__name__)

try:
    from tree_sitter_languages import get_language, get_parser
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False
    logger.info("tree-sitter-languages not installed — C/C++/Rust/Go parsing disabled")

# Map tree-sitter node types to unified AST node types, per language.
# Each entry: ts_node_type -> (unified_type, name_child_field, extra_fields)
_LANG_CONFIGS: Dict[str, Dict[str, Any]] = {
    "cpp": {
        "extensions": [".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"],
        "ts_name": "cpp",
        "node_map": {
            "function_definition": "function",
            "declaration": "function",  # forward decls / prototypes
            "class_specifier": "class",
            "struct_specifier": "class",
            "enum_specifier": "class",
            "namespace_definition": "class",
            "preproc_include": "import",
            "using_declaration": "import",
        },
    },
    "rust": {
        "extensions": [".rs"],
        "ts_name": "rust",
        "node_map": {
            "function_item": "function",
            "impl_item": "class",
            "struct_item": "class",
            "enum_item": "class",
            "trait_item": "interface",
            "type_item": "class",
            "use_declaration": "import",
            "const_item": "variable",
            "static_item": "variable",
        },
    },
    "go": {
        "extensions": [".go"],
        "ts_name": "go",
        "node_map": {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_declaration": "class",
            "import_declaration": "import",
            "const_declaration": "variable",
            "var_declaration": "variable",
        },
    },
    "java": {
        "extensions": [".java"],
        "ts_name": "java",
        "node_map": {
            "method_declaration": "method",
            "constructor_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "class",
            "import_declaration": "import",
            "field_declaration": "variable",
        },
    },
}


class TreeSitterParser(ASTParserPlugin):
    """Generic tree-sitter based parser for multiple languages."""

    def __init__(self, lang_key: str):
        super().__init__()
        self._lang_key = lang_key
        cfg = _LANG_CONFIGS[lang_key]
        self._ts_name = cfg["ts_name"]
        self._node_map = cfg["node_map"]
        self._parser = get_parser(self._ts_name) if _TS_AVAILABLE else None

    @classmethod
    def get_file_extensions(cls) -> List[str]:
        # Collected from all configs — used only when registering
        exts: List[str] = []
        for cfg in _LANG_CONFIGS.values():
            exts.extend(cfg["extensions"])
        return exts

    def parse(self, file_path: str, file_content: str) -> Any:
        if self._parser is None:
            raise RuntimeError("tree-sitter not available")
        return self._parser.parse(file_content.encode("utf-8"))

    def to_unified_ast(self, native_ast: Any, file_path: str) -> UnifiedAST:
        from .treesitter_converter import TreeSitterConverter
        converter = TreeSitterConverter(file_path, self._node_map, self._ts_name)
        return converter.convert(native_ast.root_node)
