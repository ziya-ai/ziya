"""
Unified AST representation for Ziya.

This module provides a language-agnostic representation of Abstract Syntax Trees,
allowing consistent handling of code structures across different programming languages.
"""

import json
from typing import Dict, List, Optional, Any, Tuple, Set
import uuid
import logging

logger = logging.getLogger(__name__)

class SourceLocation:
    """Represents a location in source code."""
    
    def __init__(self, file_path: str, start_line: int, start_column: int, 
                 end_line: int, end_column: int):
        """
        Initialize a source location.
        
        Args:
            file_path: Path to the source file
            start_line: Starting line number (1-based)
            start_column: Starting column number (1-based)
            end_line: Ending line number (1-based)
            end_column: Ending column number (1-based)
        """
        self.file_path = file_path
        self.start_line = start_line
        self.start_column = start_column
        self.end_line = end_line
        self.end_column = end_column
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'file_path': self.file_path,
            'start_line': self.start_line,
            'start_column': self.start_column,
            'end_line': self.end_line,
            'end_column': self.end_column
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SourceLocation':
        """Create from dictionary representation."""
        return cls(
            file_path=data['file_path'],
            start_line=data['start_line'],
            start_column=data['start_column'],
            end_line=data['end_line'],
            end_column=data['end_column']
        )


class Node:
    """Represents a node in the unified AST."""
    
    def __init__(self, node_id: str, node_type: str, name: str, 
                 source_location: SourceLocation, attributes: Optional[Dict[str, Any]] = None):
        """
        Initialize an AST node.
        
        Args:
            node_id: Unique identifier for the node
            node_type: Type of the node (e.g., 'function', 'class', 'variable')
            name: Name of the node
            source_location: Location in source code
            attributes: Additional attributes for the node
        """
        self.node_id = node_id
        self.node_type = node_type
        self.name = name
        self.source_location = source_location
        self.attributes = attributes or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'node_id': self.node_id,
            'node_type': self.node_type,
            'name': self.name,
            'source_location': self.source_location.to_dict(),
            'attributes': self.attributes
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Node':
        """Create from dictionary representation."""
        return cls(
            node_id=data['node_id'],
            node_type=data['node_type'],
            name=data['name'],
            source_location=SourceLocation.from_dict(data['source_location']),
            attributes=data['attributes']
        )


class Edge:
    """Represents an edge between nodes in the unified AST."""
    
    def __init__(self, source_id: str, target_id: str, edge_type: str, 
                 attributes: Optional[Dict[str, Any]] = None):
        """
        Initialize an AST edge.
        
        Args:
            source_id: ID of the source node
            target_id: ID of the target node
            edge_type: Type of the edge (e.g., 'contains', 'calls', 'imports')
            attributes: Additional attributes for the edge
        """
        self.source_id = source_id
        self.target_id = target_id
        self.edge_type = edge_type
        self.attributes = attributes or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'source_id': self.source_id,
            'target_id': self.target_id,
            'edge_type': self.edge_type,
            'attributes': self.attributes
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Edge':
        """Create from dictionary representation."""
        return cls(
            source_id=data['source_id'],
            target_id=data['target_id'],
            edge_type=data['edge_type'],
            attributes=data['attributes']
        )


class UnifiedAST:
    """Language-agnostic unified AST representation."""
    
    def __init__(self):
        """Initialize an empty unified AST."""
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self.metadata: Dict[str, Any] = {
            'language': None,
            'file_path': None,
            'version': '1.0'
        }
    
    def add_node(self, node_type: str, name: str, source_location: SourceLocation, 
                 attributes: Optional[Dict[str, Any]] = None) -> str:
        """
        Add a node to the AST.
        
        Args:
            node_type: Type of the node
            name: Name of the node
            source_location: Location in source code
            attributes: Additional attributes
            
        Returns:
            ID of the created node
        """
        node_id = str(uuid.uuid4())
        node = Node(node_id, node_type, name, source_location, attributes)
        self.nodes[node_id] = node
        return node_id
    
    def add_edge(self, source_id: str, target_id: str, edge_type: str, 
                 attributes: Optional[Dict[str, Any]] = None) -> None:
        """
        Add an edge between nodes.
        
        Args:
            source_id: ID of the source node
            target_id: ID of the target node
            edge_type: Type of the edge
            attributes: Additional attributes
        """
        edge = Edge(source_id, target_id, edge_type, attributes)
        self.edges.append(edge)
    
    def set_metadata(self, key: str, value: Any) -> None:
        """
        Set metadata for the AST.
        
        Args:
            key: Metadata key
            value: Metadata value
        """
        self.metadata[key] = value
    
    def to_json(self) -> str:
        """
        Serialize to JSON format.
        
        Returns:
            JSON string representation
        """
        data = {
            'metadata': self.metadata,
            'nodes': {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            'edges': [edge.to_dict() for edge in self.edges]
        }
        return json.dumps(data, indent=2)
    
    @classmethod
    def from_json(cls, json_data: str) -> 'UnifiedAST':
        """
        Deserialize from JSON format.
        
        Args:
            json_data: JSON string representation
            
        Returns:
            UnifiedAST instance
        """
        data = json.loads(json_data)
        ast = cls()
        ast.metadata = data['metadata']
        
        for node_id, node_data in data['nodes'].items():
            node = Node.from_dict(node_data)
            ast.nodes[node_id] = node
        
        for edge_data in data['edges']:
            edge = Edge.from_dict(edge_data)
            ast.edges.append(edge)
        
        return ast
    
    def merge(self, other: 'UnifiedAST') -> None:
        """
        Merge another AST into this one.
        
        Args:
            other: Another UnifiedAST to merge
        """
        logger.debug(f"Merging AST: current has {len(self.nodes)} nodes, other has {len(other.nodes)} nodes")
        
        # Add nodes from other AST
        for node_id, node in other.nodes.items():
            if node_id not in self.nodes:
                self.nodes[node_id] = node
        
        logger.debug(f"After node merge: {len(self.nodes)} total nodes")
        
        # Add edges from other AST
        for edge in other.edges:
            # Check if both source and target nodes exist in this AST
            if edge.source_id in self.nodes and edge.target_id in self.nodes:
                self.edges.append(edge)
    
    def get_node_by_location(self, file_path: str, line: int, column: int) -> Optional[Node]:
        """
        Find a node at the given source location.
        
        Args:
            file_path: Path to the file
            line: Line number (1-based)
            column: Column number (1-based)
            
        Returns:
            Node at the location or None if not found
        """
        candidates = []
        
        for node in self.nodes.values():
            loc = node.source_location
            if (loc.file_path == file_path and
                loc.start_line <= line <= loc.end_line and
                (loc.start_line != line or loc.start_column <= column) and
                (loc.end_line != line or column <= loc.end_column)):
                candidates.append(node)
        
        if not candidates:
            return None
        
        # Return the most specific (smallest) node
        return min(candidates, key=lambda n: (
            (n.source_location.end_line - n.source_location.start_line),
            (n.source_location.end_column - n.source_location.start_column)
        ))
    
    def get_nodes_by_type(self, node_type: str) -> List[Node]:
        """
        Get all nodes of a specific type.
        
        Args:
            node_type: Type of nodes to find
            
        Returns:
            List of matching nodes
        """
        return [node for node in self.nodes.values() if node.node_type == node_type]
    
    def get_nodes_by_name(self, name: str) -> List[Node]:
        """
        Get all nodes with a specific name.
        
        Args:
            name: Name to search for
            
        Returns:
            List of matching nodes
        """
        return [node for node in self.nodes.values() if node.name == name]
