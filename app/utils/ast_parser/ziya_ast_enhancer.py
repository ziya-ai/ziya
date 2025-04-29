"""
Ziya AST Enhancer.

This module integrates the AST parsing capabilities with Ziya,
enhancing context generation with semantic code information.
"""

import os
import logging
from typing import Dict, List, Optional, Any, Set
import fnmatch
import time

from .registry import ParserRegistry
from .unified_ast import UnifiedAST
from .query_engine import ASTQueryEngine
from .python_parser import PythonASTParser
from .typescript_parser import TypeScriptASTParser
from .html_css_parser import HTMLCSSParser

logger = logging.getLogger(__name__)


class ZiyaASTEnhancer:
    """Enhancer for Ziya using AST capabilities."""
    
    def __init__(self):
        """Initialize the AST enhancer."""
        self.parser_registry = ParserRegistry()
        self._register_parsers()
        
        self.ast_cache = {}
        self.query_engines = {}
        self.project_ast = UnifiedAST()
    
    def _register_parsers(self):
        """Register available parsers."""
        # Register Python parser
        self.parser_registry.register_parser(PythonASTParser)
        
        # Register TypeScript parser
        self.parser_registry.register_parser(TypeScriptASTParser)
        
        # Register HTML/CSS parser
        self.parser_registry.register_parser(HTMLCSSParser)
    
    def _process_directory(self, codebase_dir: str, exclude_patterns: List[str], max_depth: int) -> None:
        """
        Process all files in the directory and build ASTs.
        
        Args:
            codebase_dir: Path to the codebase root
            exclude_patterns: Patterns to exclude
            max_depth: Maximum directory depth to traverse
        """
        files_processed = 0
        files_total = 0
        start_time = time.time()
        
        # First, count total eligible files for progress reporting
        for root, dirs, files in os.walk(codebase_dir):
            # Check depth
            rel_path = os.path.relpath(root, codebase_dir)
            if rel_path == '.':
                depth = 0
            else:
                depth = rel_path.count(os.sep) + 1
            
            if depth >= max_depth:
                dirs.clear()  # Don't go deeper
                continue
            
            # Filter directories based on exclude patterns
            dirs[:] = [d for d in dirs if not self._is_excluded(os.path.join(rel_path, d), exclude_patterns)]
            
            # Count eligible files
            for file in files:
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, codebase_dir)
                
                # Skip excluded files
                if self._is_excluded(rel_file_path, exclude_patterns):
                    continue
                
                # Check if we have a parser for this file
                parser_class = self.parser_registry.get_parser(file_path)
                if parser_class:
                    files_total += 1
        
        # Now process the files with progress reporting
        print(f"Indexing {files_total} files for AST analysis...")
        
        for root, dirs, files in os.walk(codebase_dir):
            # Check depth
            rel_path = os.path.relpath(root, codebase_dir)
            if rel_path == '.':
                depth = 0
            else:
                depth = rel_path.count(os.sep) + 1
            
            if depth >= max_depth:
                dirs.clear()  # Don't go deeper
                continue
            
            # Filter directories based on exclude patterns
            dirs[:] = [d for d in dirs if not self._is_excluded(os.path.join(rel_path, d), exclude_patterns)]
            
            # Process files
            for file in files:
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, codebase_dir)
                
                # Skip excluded files
                if self._is_excluded(rel_file_path, exclude_patterns):
                    continue
                
                # Check if we have a parser for this file
                parser_class = self.parser_registry.get_parser(file_path)
                if not parser_class:
                    continue
                
                try:
                    # Read file content
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        file_content = f.read()
                    
                    # Parse file
                    parser = parser_class()
                    native_ast = parser.parse(file_path, file_content)
                    unified_ast = parser.to_unified_ast(native_ast, file_path)
                    
                    # Cache the AST
                    self.ast_cache[file_path] = unified_ast
                    
                    # Create query engine
                    self.query_engines[file_path] = ASTQueryEngine(unified_ast)
                    
                    # Merge into project AST
                    self.project_ast.merge(unified_ast)
                    
                    # Update progress
                    files_processed += 1
                    if files_processed % 10 == 0 or files_processed == files_total:
                        progress = int((files_processed / files_total) * 100)
                        elapsed = time.time() - start_time
                        print(f"Processing: {progress}% complete ({files_processed}/{files_total} files) - {elapsed:.1f}s elapsed", end='\r')
                    
                    logger.info(f"Processed file: {rel_file_path}")
                except Exception as e:
                    logger.error(f"Error processing file {rel_file_path}: {str(e)}")
        
        # Create project-wide query engine
        if self.project_ast:
            self.query_engines['project'] = ASTQueryEngine(self.project_ast)
        
        print(f"\nCompleted AST indexing: {files_processed} files processed in {time.time() - start_time:.1f}s")
    
    def _is_excluded(self, path: str, exclude_patterns: List[str]) -> bool:
        """
        Check if a path matches any exclude pattern.
        
        Args:
            path: Path to check
            exclude_patterns: Patterns to exclude
            
        Returns:
            True if the path should be excluded, False otherwise
        """
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False
    
    def process_codebase(self, codebase_dir: str, ignored_patterns: Optional[List[str]] = None, max_depth: int = 15) -> Dict[str, Any]:
        """
        Process the entire codebase and build AST representations.
        
        Args:
            codebase_dir: Path to the codebase directory
            ignored_patterns: List of patterns to ignore
            max_depth: Maximum depth for folder traversal
            
        Returns:
            Dict containing AST processing results
        """
        if ignored_patterns is None:
            ignored_patterns = []
        
        logger.info(f"Processing codebase at {codebase_dir} with ignored_patterns={ignored_patterns}, max_depth={max_depth}")
        
        start_time = time.time()
        print("\nIndexing codebase for AST analysis...")
        
        try:
            # Process files
            self._process_directory(codebase_dir, ignored_patterns, max_depth)
            
            # Generate AST context
            ast_context = self.generate_ast_context()
            
            # Calculate token count (rough estimate: 1 token ≈ 4 characters)
            token_count = len(ast_context) // 4
            
            elapsed_time = time.time() - start_time
            files_processed = len(self.ast_cache)
            
            print(f"✅ AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            logger.info(f"AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            logger.info(f"AST context size: {len(ast_context)} chars, ~{token_count} tokens")
            
            # Return a properly formatted result dictionary
            return {
                "files_processed": files_processed,
                "ast_context": ast_context,
                "token_count": token_count,
                "file_list": list(self.ast_cache.keys())
            }
        except Exception as e:
            logger.error(f"Error processing codebase: {e}")
            import traceback
            logger.error(f"AST processing traceback: {traceback.format_exc()}")
            
            # Return a minimal context to avoid breaking the application
            return {
                "files_processed": 0,
                "ast_context": "# AST Analysis\n\nError processing codebase.",
                "token_count": 10,
                "file_list": [],
                "error": str(e)
            }
        
    def generate_ast_context(self) -> str:
        """
        Generate a textual context from the AST.
        
        Returns:
            String representation of the AST context
        """
        if not self.project_ast:
            return ""
            
        context_parts = []
        
        # Add file structure information
        context_parts.append("# Code Structure Summary")
        
        # Add key files and their relationships
        context_parts.append("\n## Key Files and Dependencies")
        
        # For each file in the ast_cache
        for file_path, ast in self.ast_cache.items():
            # Get relative path for display
            rel_path = os.path.relpath(file_path)
            
            # Add file summary
            context_parts.append(f"\n### {rel_path}")
            
            # Add key symbols defined in this file
            symbols = self._extract_key_symbols(ast)
            if symbols:
                context_parts.append("\nDefines:")
                for symbol in symbols[:10]:  # Limit to top 10 symbols
                    context_parts.append(f"- {symbol}")
            
            # Add dependencies
            deps = self._extract_dependencies(ast)
            if deps:
                context_parts.append("\nDependencies:")
                for dep in deps[:5]:  # Limit to top 5 dependencies
                    context_parts.append(f"- {dep}")
        
        return "\n".join(context_parts)
    
    def _extract_key_symbols(self, ast: UnifiedAST) -> List[str]:
        """Extract key symbols defined in the AST."""
        symbols = []
        
        # Extract classes, functions, and variables
        for node in ast.nodes:
            if node.type in ["class", "function", "method", "variable"]:
                symbols.append(f"{node.type} {node.name}")
        
        return symbols
    
    def _extract_dependencies(self, ast: UnifiedAST) -> List[str]:
        """Extract dependencies from the AST."""
        deps = []
        
        # Extract imports and references
        for node in ast.nodes:
            if node.type == "import":
                deps.append(f"import {node.name}")
        
        return deps
    
    def enhance_query_context(self, query: str, file_context: Optional[str] = None) -> Dict[str, Any]:
        """
        Add semantic information to the query context.
        
        Args:
            query: User query
            file_context: Optional file context
            
        Returns:
            Enhanced context information
        """
        context = {
            'semantic_context': {},
            'code_structures': {}
        }
        
        # If we have a specific file context, use its AST
        if file_context and file_context in self.query_engines:
            query_engine = self.query_engines[file_context]
            
        return context
