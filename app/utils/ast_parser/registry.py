"""
Parser Registry for Ziya AST capabilities.

This module provides a registry system for language-specific AST parsers,
allowing dynamic loading of appropriate parsers based on file extensions.
"""

import os
from typing import Dict, List, Optional, Type, Any
import logging

logger = logging.getLogger(__name__)

class ASTParserPlugin:
    """Base class for AST parser plugins."""
    
    def __init__(self):
        """Initialize the parser plugin."""
        pass
    
    @classmethod
    def get_file_extensions(cls) -> List[str]:
        """
        Get the file extensions supported by this parser.
        
        Returns:
            List of supported file extensions
        """
        raise NotImplementedError("Subclasses must implement get_file_extensions")
    
    def parse(self, file_path: str, file_content: str) -> Any:
        """
        Parse a file into a native AST.
        
        Args:
            file_path: Path to the file
            file_content: Content of the file
            
        Returns:
            Native AST representation
        """
        raise NotImplementedError("Subclasses must implement parse")
    
    def to_unified_ast(self, native_ast: Any, file_path: str) -> 'UnifiedAST':
        """
        Convert a native AST to a unified AST.
        
        Args:
            native_ast: Native AST representation
            file_path: Path to the file
            
        Returns:
            Unified AST representation
        """
        raise NotImplementedError("Subclasses must implement to_unified_ast")


class ParserRegistry:
    """Registry for AST parsers."""
    
    def __init__(self):
        """Initialize the registry."""
        self.parsers = {}
        self.extension_map = {}
    
    def register_parser(self, parser_class: Type[ASTParserPlugin]) -> None:
        """
        Register a parser class.
        """
        try:
            # Get file extensions from the parser class
            extensions = parser_class.get_file_extensions()
            
            # Register the parser class
            self.parsers[parser_class.__name__] = parser_class
            
            # Map extensions to the parser class
            for ext in extensions:
                self.extension_map[ext] = parser_class
        except Exception as e:
            logger.error(f"Failed to register parser {parser_class.__name__}: {e}")
    
    def get_parser(self, file_path: str) -> Optional[Type[ASTParserPlugin]]:
        """
        Get a parser for a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Parser class for the file, or None if no parser is available
        """
        # Get file extension
        _, ext = os.path.splitext(file_path)
        
        # Return parser class for the extension
        return self.extension_map.get(ext)
