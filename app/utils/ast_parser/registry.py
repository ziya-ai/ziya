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
    
    def __init__(self, file_extensions: List[str]):
        """
        Initialize a parser plugin.
        
        Args:
            file_extensions: List of file extensions this parser can handle (e.g., ['.py', '.pyi'])
        """
        self.file_extensions = file_extensions
    
    def parse(self, file_path: str, file_content: str) -> Any:
        """
        Parse file content and return a native AST.
        
        Args:
            file_path: Path to the file being parsed
            file_content: Content of the file to parse
            
        Returns:
            Native AST representation for the specific language
        """
        raise NotImplementedError("Parser plugins must implement parse method")
    
    def to_unified_ast(self, native_ast: Any, file_path: str) -> 'UnifiedAST':
        """
        Convert native AST to unified AST format.
        
        Args:
            native_ast: Native AST from the parser
            file_path: Path to the file that was parsed
            
        Returns:
            UnifiedAST representation
        """
        raise NotImplementedError("Parser plugins must implement to_unified_ast method")
    
    @classmethod
    def can_parse(cls, file_path: str) -> bool:
        """
        Check if this parser can handle the given file.
        
        Args:
            file_path: Path to the file to check
            
        Returns:
            True if this parser can handle the file, False otherwise
        """
        _, ext = os.path.splitext(file_path)
        return ext.lower() in cls.file_extensions


class ParserRegistry:
    """Registry for AST parsers."""
    
    def __init__(self):
        """Initialize an empty parser registry."""
        self.parsers: Dict[str, Type[ASTParserPlugin]] = {}
    
    def register_parser(self, parser_class: Type[ASTParserPlugin]) -> None:
        """
        Register a parser plugin.
        
        Args:
            parser_class: Parser class to register
        """
        for ext in parser_class.file_extensions:
            if ext in self.parsers:
                logger.warning(f"Overriding existing parser for extension {ext}")
            self.parsers[ext] = parser_class
        logger.info(f"Registered parser {parser_class.__name__} for extensions {parser_class.file_extensions}")
    
    def get_parser(self, file_path: str) -> Optional[Type[ASTParserPlugin]]:
        """
        Get appropriate parser for a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Parser plugin class or None if no suitable parser is found
        """
        _, ext = os.path.splitext(file_path)
        return self.parsers.get(ext.lower())
    
    def parse_file(self, file_path: str, file_content: str) -> Optional['UnifiedAST']:
        """
        Parse a file and return unified AST.
        
        Args:
            file_path: Path to the file
            file_content: Content of the file
            
        Returns:
            UnifiedAST or None if no suitable parser is found
        """
        parser_class = self.get_parser(file_path)
        if not parser_class:
            logger.warning(f"No parser found for file: {file_path}")
            return None
        
        try:
            parser = parser_class()
            native_ast = parser.parse(file_path, file_content)
            unified_ast = parser.to_unified_ast(native_ast, file_path)
            return unified_ast
        except Exception as e:
            logger.error(f"Error parsing file {file_path}: {str(e)}")
            return None
