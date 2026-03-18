"""
AST Query Engine for Ziya.

This module provides capabilities for querying and analyzing unified ASTs,
enabling semantic-aware code search and analysis.
"""

from typing import Dict, List, Optional, Set, Any, Tuple
import re

from .unified_ast import UnifiedAST, Node, Edge


class ASTQueryEngine:
    """Engine for querying and analyzing unified ASTs."""
    
    def __init__(self, unified_ast: UnifiedAST):
        """
        Initialize the query engine.
        
        Args:
            unified_ast: The unified AST to query
        """
        self.ast = unified_ast
        self._build_indices()
    
    def _build_indices(self) -> None:
        """Build indices for efficient querying."""
        # Build name index
        self.name_index: Dict[str, List[str]] = {}
        for node_id, node in self.ast.nodes.items():
            if node.name not in self.name_index:
                self.name_index[node.name] = []
            self.name_index[node.name].append(node_id)
        
        # Build type index
        self.type_index: Dict[str, List[str]] = {}
        for node_id, node in self.ast.nodes.items():
            if node.node_type not in self.type_index:
                self.type_index[node.node_type] = []
            self.type_index[node.node_type].append(node_id)
        
        # Build file index
        self.file_index: Dict[str, List[str]] = {}
        for node_id, node in self.ast.nodes.items():
            file_path = node.source_location.file_path
            if file_path not in self.file_index:
                self.file_index[file_path] = []
            self.file_index[file_path].append(node_id)
        
        # Build edge indices
        self.outgoing_edges: Dict[str, List[Edge]] = {}
        self.incoming_edges: Dict[str, List[Edge]] = {}
        self.edge_type_index: Dict[str, List[Edge]] = {}
        
        for edge in self.ast.edges:
            if edge.source_id not in self.outgoing_edges:
                self.outgoing_edges[edge.source_id] = []
            self.outgoing_edges[edge.source_id].append(edge)
            
            if edge.target_id not in self.incoming_edges:
                self.incoming_edges[edge.target_id] = []
            self.incoming_edges[edge.target_id].append(edge)

            if edge.edge_type not in self.edge_type_index:
                self.edge_type_index[edge.edge_type] = []
            self.edge_type_index[edge.edge_type].append(edge)
    
    def find_definitions(self, name: str) -> List[Node]:
        """
        Find all definitions of a symbol.
        
        Args:
            name: Name of the symbol
            
        Returns:
            List of nodes defining the symbol
        """
        node_ids = self.name_index.get(name, [])
        return [self.ast.nodes[node_id] for node_id in node_ids]
    
    def find_references(self, node_id: str) -> List[Node]:
        """
        Find all references to a node.
        
        Args:
            node_id: ID of the node
            
        Returns:
            List of nodes referencing the node
        """
        result = []
        
        # Find edges with "references" type pointing to this node
        for edge in self.ast.edges:
            if edge.target_id == node_id and edge.edge_type == "references":
                if edge.source_id in self.ast.nodes:
                    result.append(self.ast.nodes[edge.source_id])
        
        return result
    
    def find_patterns(self, pattern_template: Dict[str, Any]) -> List[Dict[str, Node]]:
        """
        Find code matching a specific pattern.
        
        Args:
            pattern_template: Pattern specification
            
        Returns:
            List of pattern matches, each a mapping from pattern variables to nodes
        """
        # This is a simplified implementation
        # A real implementation would use a more sophisticated pattern matching algorithm
        
        results = []
        
        # Start with nodes matching the root pattern
        root_type = pattern_template.get('node_type')
        root_name = pattern_template.get('name')
        
        candidates = set()
        
        # Filter by type if specified
        if root_type:
            candidates = set(self.type_index.get(root_type, []))
        else:
            candidates = set(self.ast.nodes.keys())
        
        # Filter by name if specified
        if root_name:
            name_matches = set(self.name_index.get(root_name, []))
            candidates = candidates.intersection(name_matches)
        
        # For each candidate, try to match the complete pattern
        for node_id in candidates:
            match = {'root': self.ast.nodes[node_id]}
            if self._match_pattern(node_id, pattern_template, match):
                results.append(match)
        
        return results
    
    def _match_pattern(self, node_id: str, pattern: Dict[str, Any], 
                      match: Dict[str, Node]) -> bool:
        """
        Recursively match a pattern starting from a node.
        
        Args:
            node_id: ID of the current node
            pattern: Pattern specification
            match: Current match state
            
        Returns:
            True if pattern matches, False otherwise
        """
        node = self.ast.nodes[node_id]
        
        # Check node type
        if 'node_type' in pattern and node.node_type != pattern['node_type']:
            return False
        
        # Check node name
        if 'name' in pattern and node.name != pattern['name']:
            return False
        
        # Check attributes
        if 'attributes' in pattern:
            for key, value in pattern['attributes'].items():
                if key not in node.attributes or node.attributes[key] != value:
                    return False
        
        # Check children patterns
        if 'children' in pattern:
            for child_pattern in pattern['children']:
                child_type = child_pattern.get('edge_type', 'contains')
                child_matched = False
                
                # Find edges of the specified type
                for edge in self.outgoing_edges.get(node_id, []):
                    if edge.edge_type == child_type:
                        # Try to match the child pattern
                        child_match = match.copy()
                        if self._match_pattern(edge.target_id, child_pattern, child_match):
                            match.update(child_match)
                            child_matched = True
                            break
                
                if not child_matched:
                    return False
        
        return True
    
    def get_context_for_location(self, file_path: str, line: int, column: int) -> Dict[str, Any]:
        """
        Get semantic context for a specific location.
        
        Args:
            file_path: Path to the file
            line: Line number (1-based)
            column: Column number (1-based)
            
        Returns:
            Dictionary with context information
        """
        node = self.ast.get_node_by_location(file_path, line, column)
        if not node:
            return {'type': 'unknown', 'context': 'No code context found'}
        
        context = {
            'type': node.node_type,
            'name': node.name,
            'location': {
                'file': node.source_location.file_path,
                'start_line': node.source_location.start_line,
                'start_column': node.source_location.start_column,
                'end_line': node.source_location.end_line,
                'end_column': node.source_location.end_column
            },
            'attributes': node.attributes
        }
        
        # Add containing scope information
        containing_scopes = self._get_containing_scopes(node.node_id)
        if containing_scopes:
            context['scopes'] = [
                {'type': scope.node_type, 'name': scope.name}
                for scope in containing_scopes
            ]
        
        return context
    
    def _get_containing_scopes(self, node_id: str) -> List[Node]:
        """
        Get all scopes containing a node.
        
        Args:
            node_id: ID of the node
            
        Returns:
            List of containing scope nodes, from innermost to outermost
        """
        scopes = []
        current_id = node_id
        
        # Follow "contained_by" edges to find containing scopes
        while True:
            containing_edges = [
                edge for edge in self.incoming_edges.get(current_id, [])
                if edge.edge_type == "contains"
            ]
            
            if not containing_edges:
                break
                
            # Get the containing scope
            parent_id = containing_edges[0].source_id
            if parent_id in self.ast.nodes:
                parent_node = self.ast.nodes[parent_id]
                scopes.append(parent_node)
                current_id = parent_id
            else:
                break
        
        return scopes
    
    def find_functions(self) -> List[Node]:
        """
        Find all function definitions.
        
        Returns:
            List of function nodes
        """
        return self.ast.get_nodes_by_type("function")
    
    def find_classes(self) -> List[Node]:
        """
        Find all class definitions.
        
        Returns:
            List of class nodes
        """
        return self.ast.get_nodes_by_type("class")
    
    def find_variables(self) -> List[Node]:
        """
        Find all variable definitions.
        
        Returns:
            List of variable nodes
        """
        return self.ast.get_nodes_by_type("variable")
    
    def get_function_calls(self, function_name: str) -> List[Node]:
        """
        Find all calls to a specific function.
        
        Args:
            function_name: Name of the function
            
        Returns:
            List of nodes representing call sites
        """
        # Strategy 1: Edge-based lookup (same-file calls where "calls" edges exist)
        calls = []
        function_node_ids = {
            node.node_id for node in self.ast.nodes.values()
            if node.node_type == "function" and node.name == function_name
        }

        if function_node_ids:
            for edge in self.edge_type_index.get("calls", []):
                if (edge.target_id in function_node_ids and
                        edge.source_id in self.ast.nodes):
                    calls.append(self.ast.nodes[edge.source_id])

        # Strategy 2: Name-based fallback — find "call" nodes whose name matches.
        # Parsers always create call nodes even for cross-file targets, but the
        # "calls" edge only links to definitions in the same file scope.
        if not calls:
            seen_ids: Set[str] = set()
            name_lower = function_name.lower()
            for node in self.ast.nodes.values():
                if node.node_type == "call" and node.node_id not in seen_ids:
                    # Exact match or attribute-style match (e.g. "self.foo" matches "foo")
                    node_name = node.name
                    if (node_name == function_name or
                            node_name.lower() == name_lower or
                            node_name.endswith("." + function_name)):
                        calls.append(node)
                        seen_ids.add(node.node_id)

        return calls
    
    def get_dependencies(self, file_path: str) -> List[str]:
        """
        Get all files that the given file depends on.
        
        Args:
            file_path: Path to the file
            
        Returns:
            List of file paths that the file depends on
        """
        # Find all nodes in the file
        file_nodes = [
            node_id for node_id in self.file_index.get(file_path, [])
        ]
        
        # Strategy 1: Edge-based lookup (imports edges)
        dependencies = set()
        for node_id in file_nodes:
            for edge in self.outgoing_edges.get(node_id, []):
                if edge.edge_type == "imports" and edge.target_id in self.ast.nodes:
                    target_node = self.ast.nodes[edge.target_id]
                    target_file = target_node.source_location.file_path
                    if target_file != file_path:
                        dependencies.add(target_file)
        
        # Strategy 2: Fall back to import node attributes when no edges found.
        # Parsers create import nodes with 'module' attributes but don't always
        # create "imports" edges to the target file's module node.
        if not dependencies:
            for node_id in file_nodes:
                node = self.ast.nodes.get(node_id)
                if not node or node.node_type != "import":
                    continue
                attrs = node.attributes or {}
                module_name = attrs.get("module") or node.name
                if module_name:
                    # Try to resolve the module to an indexed file path
                    resolved = self._resolve_module_to_file(module_name, file_path)
                    if resolved and resolved != file_path:
                        dependencies.add(resolved)
                    elif module_name:
                        # Keep the raw module name as a dependency even if
                        # we can't resolve it to a local file (stdlib, etc.)
                        dependencies.add(module_name)

        return list(dependencies)

    def _resolve_module_to_file(self, module_name: str, source_file: str) -> Optional[str]:
        """Try to resolve a module name to an indexed file path."""
        if not module_name:
            return None

        # Convert dotted module path to file path fragments
        # e.g. "app.utils.logging_utils" -> ["app", "utils", "logging_utils"]
        parts = module_name.replace("-", "_").split(".")

        # Check indexed files for a suffix match
        import os
        for indexed_path in self.file_index:
            normalized = indexed_path.replace(os.sep, "/")
            # Build candidate suffixes: "app/utils/logging_utils.py",
            # "app/utils/logging_utils/__init__.py", etc.
            suffix_py = "/".join(parts) + ".py"
            suffix_init = "/".join(parts) + "/__init__.py"
            suffix_ts = "/".join(parts) + ".ts"
            suffix_tsx = "/".join(parts) + ".tsx"
            if (normalized.endswith(suffix_py) or
                    normalized.endswith(suffix_init) or
                    normalized.endswith(suffix_ts) or
                    normalized.endswith(suffix_tsx)):
                return indexed_path

        # Relative import: try resolving from the source file's directory
        if not module_name.startswith("."):
            return None
        source_dir = os.path.dirname(source_file)
        # Strip leading dots and resolve
        stripped = module_name.lstrip(".")
        levels_up = len(module_name) - len(stripped)
        for _ in range(levels_up - 1):
            source_dir = os.path.dirname(source_dir)
        rel_parts = stripped.split(".") if stripped else []
        candidate_base = os.path.join(source_dir, *rel_parts)
        for ext in [".py", "/__init__.py", ".ts", ".tsx"]:
            candidate = candidate_base + ext
            normalized_candidate = candidate.replace(os.sep, "/")
            for indexed_path in self.file_index:
                if indexed_path.replace(os.sep, "/").endswith(normalized_candidate.lstrip("/")):
                    return indexed_path

        return None
    
    def get_reverse_dependencies(self, file_path: str) -> List[str]:
        """
        Get all files that depend on (import from) the given file.

        This is the inverse of get_dependencies — answers "who imports me?"

        Args:
            file_path: Path to the file

        Returns:
            List of file paths that import from the given file
        """
        importers = set()

        # Strategy 1: Edge-based — find "imports" edges pointing INTO this file's nodes
        file_node_ids = set(self.file_index.get(file_path, []))
        if file_node_ids:
            for edge in self.edge_type_index.get("imports", []):
                if edge.target_id in file_node_ids and edge.source_id in self.ast.nodes:
                    source_file = self.ast.nodes[edge.source_id].source_location.file_path
                    if source_file != file_path:
                        importers.add(source_file)

        # Strategy 2: Scan all import nodes across all files for module name match.
        # This catches cross-file imports where no "imports" edge was created.
        if not importers:
            import os
            basename = os.path.splitext(os.path.basename(file_path))[0]

            for other_file, node_ids in self.file_index.items():
                if other_file == file_path:
                    continue
                for node_id in node_ids:
                    node = self.ast.nodes.get(node_id)
                    if not node or node.node_type != "import":
                        continue
                    module_name = (node.attributes or {}).get("module") or node.name
                    if not module_name:
                        continue
                    # Match: module ends with the basename, or resolves to the file
                    if (module_name.endswith(basename) or
                            module_name.endswith("." + basename) or
                            module_name == basename):
                        importers.add(other_file)
                        break  # one match per file is enough
                    # Also try full resolution
                    resolved = self._resolve_module_to_file(module_name, other_file)
                    if resolved == file_path:
                        importers.add(other_file)
                        break

        return sorted(importers)

    def generate_summary(self, file_path: str) -> Dict[str, Any]:
        """
        Generate a summary of a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary with summary information
        """
        # Find all nodes in the file
        file_nodes = [
            self.ast.nodes[node_id] for node_id in self.file_index.get(file_path, [])
        ]
        
        # Count different types of nodes
        type_counts = {}
        for node in file_nodes:
            if node.node_type not in type_counts:
                type_counts[node.node_type] = 0
            type_counts[node.node_type] += 1
        
        # Get top-level definitions
        top_level = [
            node for node in file_nodes
            if node.node_type in ("function", "class", "variable")
        ]
        
        # Generate summary
        summary = {
            'file_path': file_path,
            'type_counts': type_counts,
            'top_level_definitions': [
                {'type': node.node_type, 'name': node.name}
                for node in top_level
            ],
            'dependencies': self.get_dependencies(file_path)
        }
        
        return summary
