"""
Tree-sitter based AST parser supporting 25+ languages via tree-sitter-language-pack.

Uses the tree-sitter-language-pack package which bundles pre-built grammars
for ~40 languages.  Falls back gracefully if not installed.
"""

import logging
from typing import Any, Dict, List, Optional

from .registry import ASTParserPlugin
from .unified_ast import UnifiedAST, SourceLocation

logger = logging.getLogger(__name__)

try:
    from tree_sitter_language_pack import get_language, get_parser
    _TS_AVAILABLE = True
except ImportError:
    # Fall back to the old (unmaintained) package for existing installations
    try:
        from tree_sitter_languages import get_language, get_parser
        _TS_AVAILABLE = True
        logger.info("Using legacy tree-sitter-languages; consider upgrading to tree-sitter-language-pack")
    except ImportError:
        _TS_AVAILABLE = False
        logger.info("tree-sitter-language-pack not installed — C/C++/Rust/Go parsing disabled")

# Map tree-sitter node types to unified AST node types, per language.
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
    "c_sharp": {
        "extensions": [".cs"],
        "ts_name": "c_sharp",
        "node_map": {
            "method_declaration": "method",
            "constructor_declaration": "method",
            "class_declaration": "class",
            "struct_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "class",
            "record_declaration": "class",
            "namespace_declaration": "class",
            "using_directive": "import",
            "field_declaration": "variable",
            "property_declaration": "variable",
            "delegate_declaration": "function",
        },
    },
    "kotlin": {
        "extensions": [".kt", ".kts"],
        "ts_name": "kotlin",
        "node_map": {
            "function_declaration": "function",
            "class_declaration": "class",
            "object_declaration": "class",
            "interface_declaration": "interface",
            "import_header": "import",
            "property_declaration": "variable",
        },
    },
    "swift": {
        "extensions": [".swift"],
        "ts_name": "swift",
        "node_map": {
            "function_declaration": "function",
            "class_declaration": "class",
            "struct_declaration": "class",
            "enum_declaration": "class",
            "protocol_declaration": "interface",
            "import_declaration": "import",
            "property_declaration": "variable",
            "typealias_declaration": "class",
        },
    },
    "ruby": {
        "extensions": [".rb", ".rake", ".gemspec"],
        "ts_name": "ruby",
        "node_map": {
            "method": "function",
            "singleton_method": "method",
            "class": "class",
            "module": "class",
            "call": "import",  # require/include are calls in Ruby
        },
    },
    "php": {
        "extensions": [".php"],
        "ts_name": "php",
        "node_map": {
            "function_definition": "function",
            "method_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "trait_declaration": "class",
            "enum_declaration": "class",
            "namespace_use_declaration": "import",
            "property_declaration": "variable",
            "const_declaration": "variable",
        },
    },
    "scala": {
        "extensions": [".scala", ".sc"],
        "ts_name": "scala",
        "node_map": {
            "function_definition": "function",
            "class_definition": "class",
            "object_definition": "class",
            "trait_definition": "interface",
            "import_declaration": "import",
            "val_definition": "variable",
            "var_definition": "variable",
        },
    },
    "lua": {
        "extensions": [".lua"],
        "ts_name": "lua",
        "node_map": {
            "function_declaration": "function",
            "local_function": "function",
            "variable_declaration": "variable",
            "local_variable_declaration": "variable",
        },
    },
    "perl": {
        "extensions": [".pl", ".pm"],
        "ts_name": "perl",
        "node_map": {
            "function_definition": "function",
            "package_statement": "class",
            "use_statement": "import",
            "require_statement": "import",
        },
    },
    "r": {
        "extensions": [".r", ".R"],
        "ts_name": "r",
        "node_map": {
            "function_definition": "function",
            "left_assignment": "variable",
            "call": "import",  # library() / require() are calls in R
        },
    },
    "elixir": {
        "extensions": [".ex", ".exs"],
        "ts_name": "elixir",
        "node_map": {
            "call": "function",  # def/defp/defmodule are macro calls
        },
    },
    "haskell": {
        "extensions": [".hs"],
        "ts_name": "haskell",
        "node_map": {
            "function": "function",
            "signature": "function",
            "type_alias": "class",
            "newtype": "class",
            "adt": "class",  # algebraic data types
            "class": "interface",
            "import": "import",
        },
    },
    "dart": {
        "extensions": [".dart"],
        "ts_name": "dart",
        "node_map": {
            "function_signature": "function",
            "method_signature": "method",
            "class_definition": "class",
            "enum_declaration": "class",
            "mixin_declaration": "class",
            "import_or_export": "import",
            "initialized_variable_definition": "variable",
        },
    },
    "zig": {
        "extensions": [".zig"],
        "ts_name": "zig",
        "node_map": {
            "FnProto": "function",
            "VarDecl": "variable",
            "ContainerDecl": "class",
            "TestDecl": "function",
        },
    },
    "ocaml": {
        "extensions": [".ml", ".mli"],
        "ts_name": "ocaml",
        "node_map": {
            "let_binding": "function",
            "value_definition": "function",
            "type_definition": "class",
            "module_definition": "class",
            "module_type_definition": "interface",
            "open_statement": "import",
        },
    },
    "julia": {
        "extensions": [".jl"],
        "ts_name": "julia",
        "node_map": {
            "function_definition": "function",
            "short_function_definition": "function",
            "struct_definition": "class",
            "abstract_definition": "interface",
            "module_definition": "class",
            "import_statement": "import",
            "using_statement": "import",
            "const_statement": "variable",
        },
    },
    "bash": {
        "extensions": [".sh", ".bash", ".zsh"],
        "ts_name": "bash",
        "node_map": {
            "function_definition": "function",
            "variable_assignment": "variable",
        },
    },
    "hcl": {
        "extensions": [".tf", ".hcl"],
        "ts_name": "hcl",
        "node_map": {
            "block": "class",
            "attribute": "variable",
        },
    },
    "sql": {
        "extensions": [".sql"],
        "ts_name": "sql",
        "node_map": {
            "create_function_statement": "function",
            "create_table_statement": "class",
            "create_view_statement": "class",
            "create_index_statement": "class",
            "create_type_statement": "class",
        },
    },
    "toml": {
        "extensions": [".toml"],
        "ts_name": "toml",
        "node_map": {
            "table": "class",
            "pair": "variable",
        },
    },
    "yaml": {
        "extensions": [".yml", ".yaml"],
        "ts_name": "yaml",
        "node_map": {
            "block_mapping_pair": "variable",
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
