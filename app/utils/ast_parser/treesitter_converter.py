"""Tree-sitter AST node → UnifiedAST converter."""

import logging
from typing import Any, Dict, List, Optional

from .unified_ast import UnifiedAST, SourceLocation

logger = logging.getLogger(__name__)


class TreeSitterConverter:
    """Walks a tree-sitter CST and produces a UnifiedAST."""

    def __init__(self, file_path: str, node_map: Dict[str, str], lang: str):
        self.file_path = file_path
        self.node_map = node_map
        self.lang = lang
        self.ast = UnifiedAST()
        self.ast.set_metadata("language", lang)
        self.ast.set_metadata("file_path", file_path)

    def convert(self, root) -> UnifiedAST:
        self._walk(root, parent_id=None)
        return self.ast

    # ------------------------------------------------------------------
    def _walk(self, node, parent_id: Optional[str]):
        unified_type = self.node_map.get(node.type)
        node_id: Optional[str] = None

        if unified_type:
            name = self._extract_name(node)
            if name:
                loc = SourceLocation(
                    self.file_path,
                    node.start_point[0] + 1,
                    node.start_point[1] + 1,
                    node.end_point[0] + 1,
                    node.end_point[1] + 1,
                )
                attrs = self._extract_attrs(node, unified_type)
                node_id = self.ast.add_node(unified_type, name, loc, attrs)
                if parent_id:
                    self.ast.add_edge(parent_id, node_id, "contains")

        for child in node.children:
            self._walk(child, node_id or parent_id)

    # ------------------------------------------------------------------
    def _extract_name(self, node) -> Optional[str]:
        """Pull a human-readable name from a tree-sitter node."""
        # Most declarations have a direct `name` child
        for field in ("name", "declarator"):
            child = node.child_by_field_name(field)
            if child:
                # The declarator may itself have a nested name
                inner = child.child_by_field_name("name") or \
                        child.child_by_field_name("declarator")
                if inner:
                    return inner.text.decode("utf-8", errors="replace")
                return child.text.decode("utf-8", errors="replace")

        # Imports: use the whole node text (trimmed)
        if node.type in ("preproc_include", "use_declaration",
                         "import_declaration", "using_declaration"):
            txt = node.text.decode("utf-8", errors="replace").strip()
            # Trim to first line / reasonable length
            return txt.split("\n")[0][:120]

        return None

    # ------------------------------------------------------------------
    def _extract_attrs(self, node, unified_type: str) -> Dict[str, Any]:
        attrs: Dict[str, Any] = {}

        if unified_type in ("function", "method"):
            params = node.child_by_field_name("parameters")
            if params:
                attrs["params"] = self._param_names(params)
            ret = node.child_by_field_name("return_type") or \
                  node.child_by_field_name("type")
            if ret:
                attrs["return_type"] = ret.text.decode("utf-8", errors="replace")

        if unified_type == "class":
            # Gather base classes / trait bounds
            bases: List[str] = []
            for child in node.children:
                if child.type in ("base_class_clause", "superclass",
                                  "type_parameters", "where_clause"):
                    bases.append(child.text.decode("utf-8", errors="replace"))
            if bases:
                attrs["bases"] = bases

        return attrs

    @staticmethod
    def _param_names(params_node) -> List[str]:
        names: List[str] = []
        for child in params_node.children:
            name_node = child.child_by_field_name("name") or \
                        child.child_by_field_name("pattern")
            if name_node:
                names.append(name_node.text.decode("utf-8", errors="replace"))
        return names
