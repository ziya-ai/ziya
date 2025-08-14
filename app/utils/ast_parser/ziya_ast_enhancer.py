"""
Ziya AST Enhancer.

This module integrates the AST parsing capabilities with Ziya,
enhancing context generation with semantic code information.
"""

import os
import logging
from typing import Dict, List, Optional, Any, Set
import time

# Set up logging for this module
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from .registry import ParserRegistry
from .unified_ast import UnifiedAST
from .query_engine import ASTQueryEngine
from .python_parser import PythonASTParser
from .typescript_parser import TypeScriptASTParser
from .html_css_parser import HTMLCSSParser


class ZiyaASTEnhancer:
    """Enhancer for Ziya using AST capabilities."""
    
    def __init__(self, ast_resolution='medium'):
        """Initialize the AST enhancer."""
        try:
            self.parser_registry = ParserRegistry()
            self._register_parsers()
        except Exception as e:
            logger.error(f"Error initializing ZiyaASTEnhancer: {e}")
            raise
        
        # AST resolution settings
        self.ast_resolution = ast_resolution
        self.resolution_settings = {
            'disabled': {'symbols_per_file': 0, 'deps_per_file': 0},
            'minimal': {'symbols_per_file': 5, 'deps_per_file': 3},
            'medium': {'symbols_per_file': 20, 'deps_per_file': 10},
            'detailed': {'symbols_per_file': 50, 'deps_per_file': 20},
            'comprehensive': {'symbols_per_file': 100, 'deps_per_file': 50}
        }
        
        self.ast_cache = {}
        self.query_engines = {}
        self.project_ast = UnifiedAST()
        self.UnifiedAST = UnifiedAST  # Store reference for re-initialization
        self.resolution_estimates = {}
    
    def _register_parsers(self):
        """Register available parsers."""
        # Register Python parser
        self.parser_registry.register_parser(PythonASTParser)
        
        # Register TypeScript parser
        self.parser_registry.register_parser(TypeScriptASTParser)
        
        # Register HTML/CSS parser
        self.parser_registry.register_parser(HTMLCSSParser)
    
    def _process_directory(self, codebase_dir: str, should_ignore_fn, max_depth: int, progress_callback=None) -> None:
        """
        Process all files in the directory and build ASTs.
        
        Args:
            codebase_dir: Path to the codebase root
            should_ignore_fn: Function to check if a path should be ignored
            max_depth: Maximum directory depth to traverse
            progress_callback: Optional callback to report progress
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
            dirs[:] = [d for d in dirs if not should_ignore_fn(os.path.join(root, d))]
            
            # Count eligible files
            for file in files:
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, codebase_dir)
                
                # Skip excluded files
                if should_ignore_fn(file_path):
                    continue
                
                # Check if we have a parser for this file
                parser_class = self.parser_registry.get_parser(file_path)
                if parser_class:
                    files_total += 1
        
        # Report initial file count via callback
        if progress_callback:
            progress_callback(0, files_total, 0)
        
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
            dirs[:] = [d for d in dirs if not should_ignore_fn(os.path.join(root, d))]
            
            # Process files
            for file in files:
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, codebase_dir)
                
                # Skip excluded files
                if should_ignore_fn(file_path):
                    continue
                
                # Check if we have a parser for this file
                parser_class = self.parser_registry.get_parser(file_path)
                
                if not parser_class:
                    continue
                
                # Skip TypeScript files if in node_modules to avoid dependency issues
                if 'node_modules' in rel_file_path and parser_class.__name__ == 'TypeScriptASTParser':
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
                        # Report progress via callback
                        if progress_callback:
                            progress_percentage = int((files_processed / files_total) * 100) if files_total > 0 else 0
                            progress_callback(files_processed, files_total, progress_percentage)
                        progress = int((files_processed / files_total) * 100)
                        elapsed = time.time() - start_time
                        print(f"Processing: {progress}% complete ({files_processed}/{files_total} files) - {elapsed:.1f}s elapsed", end='\r')
                    
                except Exception as e:
                    logger.warning(f"Failed to parse {rel_file_path}: {str(e)}")
        
        # Create project-wide query engine
        if self.project_ast:
            self.query_engines['project'] = ASTQueryEngine(self.project_ast)
        
        print(f"\nCompleted AST indexing: {files_processed} files processed in {time.time() - start_time:.1f}s")
    
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
        # Import gitignore utilities
        from app.utils.gitignore_parser import parse_gitignore_patterns
        from app.utils.directory_util import get_ignored_patterns
        
        if ignored_patterns is None:
            ignored_patterns = []
        
        logger.info(f"Processing codebase at {codebase_dir} with ignored_patterns={ignored_patterns}, max_depth={max_depth}")
        
        start_time = time.time()
        print("\nIndexing codebase for AST analysis...")
        
        # Get proper gitignore patterns
        gitignore_patterns = get_ignored_patterns(codebase_dir)
        should_ignore_fn = parse_gitignore_patterns(gitignore_patterns)
        
        logger.info(f"Using {len(gitignore_patterns)} gitignore patterns for AST indexing")
        logger.debug(f"Gitignore patterns: {gitignore_patterns[:10]}...")  # Log first 10 patterns
        
        # Create a progress callback that updates the global status
        def progress_callback(processed: int, total: int, percentage: int):
            try:
                from app.utils.context_enhancer import _ast_indexing_status
                _ast_indexing_status.update({
                    'indexed_files': processed,
                    'total_files': total,
                    'completion_percentage': percentage,
                    'elapsed_seconds': time.time() - start_time
                })
                logger.debug(f"Progress update: {processed}/{total} files ({percentage}%)")
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
        
        try:
            # Process files
            self._process_directory(codebase_dir, should_ignore_fn, max_depth, progress_callback)
            
            # Generate AST context
            ast_context = self.generate_ast_context()
            
            # Ensure we have a token count
            if not ast_context:
                ast_context = "# AST Analysis\n\nNo files processed for AST analysis."
            
            # Calculate token count (rough estimate: 1 token ≈ 4.1 characters based on validation)
            token_count = int(len(ast_context) / 4.1)
            
            elapsed_time = time.time() - start_time
            files_processed = len(self.ast_cache)
            
            print(f"✅ AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            print(f"📊 AST context generated: {len(ast_context)} characters, ~{token_count} tokens")
            logger.info(f"AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            logger.info(f"AST context size: {len(ast_context)} chars, ~{token_count} tokens")
            
            # Return a properly formatted result dictionary
            return {
                "files_processed": files_processed,
                "ast_context": ast_context,
                "token_count": max(token_count, 1),  # Ensure at least 1 token
                "context_length": len(ast_context),
                "file_list": list(self.ast_cache.keys())
            }
        except Exception as e:
            logger.error(f"Error processing codebase: {e}")
            import traceback
            logger.error(f"AST processing traceback: {traceback.format_exc()}")
            
            # Return a minimal context to avoid breaking the application
            return {
                "files_processed": 0,
                "ast_context": f"# AST Analysis\n\nError processing codebase: {str(e)}",
                "token_count": 10,
                "file_list": [],
            "file_list": list(self.ast_cache.keys())
        }
    
    def calculate_resolution_estimates(self) -> Dict[str, Dict[str, Any]]:
        """
        Calculate estimated token counts for each resolution level.
        
        Returns:
            Dictionary mapping resolution levels to their estimated sizes
        """
        estimates = {}
        
        for resolution_name, settings in self.resolution_settings.items():
            # Temporarily change resolution
            original_resolution = self.ast_resolution
            self.ast_resolution = resolution_name
            
            # Handle disabled case
            if resolution_name == 'disabled':
                estimates[resolution_name] = {
                    'token_count': 0,
                    'context_length': 0,
                    'symbols_per_file': 0,
                    'deps_per_file': 0
                }
                continue
            
            # Generate context with this resolution
            context = self.generate_ast_context()
            token_count = len(context) // 4  # Rough token estimate
            
            estimates[resolution_name] = {
                'token_count': token_count,
                'context_length': len(context),
                'symbols_per_file': settings['symbols_per_file'],
                'deps_per_file': settings['deps_per_file']
            }
            
            # Restore original resolution
            self.ast_resolution = original_resolution
        
        self.resolution_estimates = estimates
        return estimates
    
    def generate_ast_context(self) -> str:
        """
        Generate a textual context from the AST.
        
        Returns:
            String representation of the AST context 
        """

        # Return empty context if disabled
        if self.ast_resolution == 'disabled':
            return ""

        if not self.ast_cache:
            logger.info("No AST cache available, returning empty context")
            return "# AST Analysis\n\nNo files have been processed for AST analysis."
            
        if not self.project_ast:
            return ""
            
        context_parts = []
        
        # Add file structure information
        context_parts.append("# Code Structure Summary")
        
        # Add statistics about file types processed
        file_types = {}
        for file_path in self.ast_cache.keys():
            ext = os.path.splitext(file_path)[1]
            file_types[ext] = file_types.get(ext, 0) + 1
        
        context_parts.append(f"\n## Files Processed by Type: {dict(sorted(file_types.items()))}")
        
        # Add key files and their relationships
        context_parts.append(f"\n## Total AST Statistics")
        context_parts.append(f"- Total nodes in project AST: {len(self.project_ast.nodes)}")
        context_parts.append(f"- Total edges in project AST: {len(self.project_ast.edges)}")
        context_parts.append("\n## Key Files and Dependencies")
        
        # For each file in the ast_cache
        settings = self.resolution_settings[self.ast_resolution]
        for file_path, ast in list(self.ast_cache.items()):
            # Get relative path for display
            rel_path = os.path.relpath(file_path)
            
            # Add file summary
            context_parts.append(f"\n### {rel_path}")
            context_parts.append(f"Nodes: {len(ast.nodes)}, Edges: {len(ast.edges)}")
            
            # Show node types in this file
            node_types = {}
            for node in list(ast.nodes.values()):
                node_types[node.node_type] = node_types.get(node.node_type, 0) + 1
            context_parts.append(f"Node types: {dict(sorted(node_types.items()))}")
            
            # Add key symbols defined in this file
            symbols = self._extract_key_symbols(ast)
            if symbols:
                context_parts.append("\nDefines:")
                # Limit symbols based on resolution settings
                symbol_list = list(symbols)[:settings['symbols_per_file']]
                for symbol in symbol_list:
                    context_parts.append(f"- {symbol}")
            
            # Add dependencies
            deps = self._extract_dependencies(ast)
            if deps:
                context_parts.append("\nDependencies:")
                # Limit dependencies based on resolution settings
                dep_list = list(deps)[:settings['deps_per_file']]
                for dep in dep_list:
                    context_parts.append(f"- {dep}")
        
        return "\n".join(context_parts)
    
    def _extract_key_symbols(self, ast: UnifiedAST) -> List[str]:
        """Extract key symbols defined in the AST."""
        symbols = []
        
        # Extract classes, functions, and variables
        for node in list(ast.nodes.values()):
            if node.node_type in ["class", "function", "method", "variable"]:
                symbols.append(f"{node.node_type} {node.name}")
        
        return symbols
    
    def _extract_dependencies(self, ast: UnifiedAST) -> List[str]:
        """Extract dependencies from the AST."""
        deps = []
        
        # Extract imports and references
        for node in list(ast.nodes.values()):
            if node.node_type == "import":
                deps.append(f"import {node.name}")
        
        return deps
    
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
        # Import gitignore utilities
        from app.utils.gitignore_parser import parse_gitignore_patterns
        from app.utils.directory_util import get_ignored_patterns
        
        if ignored_patterns is None:
            ignored_patterns = []
        
        logger.info(f"Processing codebase at {codebase_dir} with ignored_patterns={ignored_patterns}, max_depth={max_depth}")
        
        start_time = time.time()
        print("\nIndexing codebase for AST analysis...")
        
        # Get proper gitignore patterns
        gitignore_patterns = get_ignored_patterns(codebase_dir)
        should_ignore_fn = parse_gitignore_patterns(gitignore_patterns)
        
        logger.info(f"Using {len(gitignore_patterns)} gitignore patterns for AST indexing")
        logger.debug(f"Gitignore patterns: {gitignore_patterns[:10]}...")  # Log first 10 patterns
        
        # Create a progress callback that updates the global status
        def progress_callback(processed: int, total: int, percentage: int):
            try:
                from app.utils.context_enhancer import _ast_indexing_status
                _ast_indexing_status.update({
                    'indexed_files': processed,
                    'total_files': total,
                    'completion_percentage': percentage,
                    'elapsed_seconds': time.time() - start_time
                })
                logger.debug(f"Progress update: {processed}/{total} files ({percentage}%)")
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
        
        try:
            # Process files
            self._process_directory(codebase_dir, should_ignore_fn, max_depth, progress_callback)
            
            # Generate AST context
            ast_context = self.generate_ast_context()
            
            # Ensure we have a token count
            if not ast_context:
                ast_context = "# AST Analysis\n\nNo files processed for AST analysis."
            
            # Calculate token count (rough estimate: 1 token ≈ 4.1 characters based on validation)
            token_count = int(len(ast_context) / 4.1)
            
            elapsed_time = time.time() - start_time
            files_processed = len(self.ast_cache)
            
            print(f"✅ AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            print(f"📊 AST context generated: {len(ast_context)} characters, ~{token_count} tokens")
            logger.info(f"AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            logger.info(f"AST context size: {len(ast_context)} chars, ~{token_count} tokens")
            
            # Return a properly formatted result dictionary
            return {
                "files_processed": files_processed,
                "ast_context": ast_context,
                "token_count": max(token_count, 1),  # Ensure at least 1 token
                "context_length": len(ast_context),
                "file_list": list(self.ast_cache.keys())
            }
        except Exception as e:
            logger.error(f"Error processing codebase: {e}")
            import traceback
            logger.error(f"AST processing traceback: {traceback.format_exc()}")
            
            # Return a minimal context to avoid breaking the application
            return {
                "files_processed": 0,
                "ast_context": f"# AST Analysis\n\nError processing codebase: {str(e)}",
                "token_count": 10,
                "file_list": [],
            "file_list": list(self.ast_cache.keys())
        }
    
    def calculate_resolution_estimates(self) -> Dict[str, Dict[str, Any]]:
        """
        Calculate estimated token counts for each resolution level.
        
        Returns:
            Dictionary mapping resolution levels to their estimated sizes
        """
        estimates = {}
        
        for resolution_name, settings in self.resolution_settings.items():
            # Temporarily change resolution
            original_resolution = self.ast_resolution
            self.ast_resolution = resolution_name
            
            # Generate context with this resolution
            context = self.generate_ast_context()
            token_count = len(context) // 4  # Rough token estimate
            
            estimates[resolution_name] = {
                'token_count': token_count,
                'context_length': len(context),
                'symbols_per_file': settings['symbols_per_file'],
                'deps_per_file': settings['deps_per_file']
            }
            
            # Restore original resolution
            self.ast_resolution = original_resolution
        
        self.resolution_estimates = estimates
        return estimates
    
    def generate_ast_context(self) -> str:
        """
        Generate a textual context from the AST.
        
        Returns:
            String representation of the AST context 
        """

        # Return empty context if disabled
        if self.ast_resolution == 'disabled':
            return ""

        if not self.ast_cache:
            logger.info("No AST cache available, returning empty context")
            return "# AST Analysis\n\nNo files have been processed for AST analysis."
            
        if not self.project_ast:
            return ""
            
        context_parts = []
        
        # Add file structure information
        context_parts.append("# Code Structure Summary")
        
        # Add statistics about file types processed
        file_types = {}
        for file_path in self.ast_cache.keys():
            ext = os.path.splitext(file_path)[1]
            file_types[ext] = file_types.get(ext, 0) + 1
        
        context_parts.append(f"\n## Files Processed by Type: {dict(sorted(file_types.items()))}")
        
        # Add key files and their relationships
        context_parts.append(f"\n## Total AST Statistics")
        context_parts.append(f"- Total nodes in project AST: {len(self.project_ast.nodes)}")
        context_parts.append(f"- Total edges in project AST: {len(self.project_ast.edges)}")
        context_parts.append("\n## Key Files and Dependencies")
        
        # For each file in the ast_cache
        settings = self.resolution_settings[self.ast_resolution]
        for file_path, ast in list(self.ast_cache.items()):
            # Get relative path for display
            rel_path = os.path.relpath(file_path)
            
            # Add file summary
            context_parts.append(f"\n### {rel_path}")
            context_parts.append(f"Nodes: {len(ast.nodes)}, Edges: {len(ast.edges)}")
            
            # Show node types in this file
            node_types = {}
            for node in list(ast.nodes.values()):
                node_types[node.node_type] = node_types.get(node.node_type, 0) + 1
            context_parts.append(f"Node types: {dict(sorted(node_types.items()))}")
            
            # Add key symbols defined in this file
            symbols = self._extract_key_symbols(ast)
            if symbols:
                context_parts.append("\nDefines:")
                # Limit symbols based on resolution settings
                symbol_list = list(symbols)[:settings['symbols_per_file']]
                for symbol in symbol_list:
                    context_parts.append(f"- {symbol}")
            
            # Add dependencies
            deps = self._extract_dependencies(ast)
            if deps:
                context_parts.append("\nDependencies:")
                # Limit dependencies based on resolution settings
                dep_list = list(deps)[:settings['deps_per_file']]
                for dep in dep_list:
                    context_parts.append(f"- {dep}")
        
        return "\n".join(context_parts)
    
    def _extract_key_symbols(self, ast: UnifiedAST) -> List[str]:
        """Extract key symbols defined in the AST."""
        symbols = []
        
        # Extract classes, functions, and variables
        for node in list(ast.nodes.values()):
            if node.node_type in ["class", "function", "method", "variable"]:
                symbols.append(f"{node.node_type} {node.name}")
        
        return symbols
    
    def _extract_dependencies(self, ast: UnifiedAST) -> List[str]:
        """Extract dependencies from the AST."""
        deps = []
        
        # Extract imports and references
        for node in list(ast.nodes.values()):
            if node.node_type == "import":
                deps.append(f"import {node.name}")
        
        return deps
    
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
        # Import gitignore utilities
        from app.utils.gitignore_parser import parse_gitignore_patterns
        from app.utils.directory_util import get_ignored_patterns
        
        if ignored_patterns is None:
            ignored_patterns = []
        
        logger.info(f"Processing codebase at {codebase_dir} with ignored_patterns={ignored_patterns}, max_depth={max_depth}")
        
        start_time = time.time()
        print("\nIndexing codebase for AST analysis...")
        
        # Get proper gitignore patterns
        gitignore_patterns = get_ignored_patterns(codebase_dir)
        should_ignore_fn = parse_gitignore_patterns(gitignore_patterns)
        
        logger.info(f"Using {len(gitignore_patterns)} gitignore patterns for AST indexing")
        logger.debug(f"Gitignore patterns: {gitignore_patterns[:10]}...")  # Log first 10 patterns
        
        # Create a progress callback that updates the global status
        def progress_callback(processed: int, total: int, percentage: int):
            try:
                from app.utils.context_enhancer import _ast_indexing_status
                _ast_indexing_status.update({
                    'indexed_files': processed,
                    'total_files': total,
                    'completion_percentage': percentage,
                    'elapsed_seconds': time.time() - start_time
                })
                logger.debug(f"Progress update: {processed}/{total} files ({percentage}%)")
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
        
        try:
            # Process files
            self._process_directory(codebase_dir, should_ignore_fn, max_depth, progress_callback)
            
            # Generate AST context
            ast_context = self.generate_ast_context()
            
            # Ensure we have a token count
            if not ast_context:
                ast_context = "# AST Analysis\n\nNo files processed for AST analysis."
            
            # Calculate token count (rough estimate: 1 token ≈ 4.1 characters based on validation)
            token_count = int(len(ast_context) / 4.1)
            
            elapsed_time = time.time() - start_time
            files_processed = len(self.ast_cache)
            
            print(f"✅ AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            print(f"📊 AST context generated: {len(ast_context)} characters, ~{token_count} tokens")
            logger.info(f"AST indexing complete: {files_processed} files processed in {elapsed_time:.1f}s")
            logger.info(f"AST context size: {len(ast_context)} chars, ~{token_count} tokens")
            
            # Return a properly formatted result dictionary
            return {
                "files_processed": files_processed,
                "ast_context": ast_context,
                "token_count": max(token_count, 1),  # Ensure at least 1 token
                "context_length": len(ast_context),
                "file_list": list(self.ast_cache.keys())
            }
        except Exception as e:
            logger.error(f"Error processing codebase: {e}")
            import traceback
            logger.error(f"AST processing traceback: {traceback.format_exc()}")
            
            # Return a minimal context to avoid breaking the application
            return {
                "files_processed": 0,
                "ast_context": f"# AST Analysis\n\nError processing codebase: {str(e)}",
                "token_count": 10,
                "file_list": [],
            "file_list": list(self.ast_cache.keys())
        }
    
    def calculate_resolution_estimates(self) -> Dict[str, Dict[str, Any]]:
        """
        Calculate estimated token counts for each resolution level.
        
        Returns:
            Dictionary mapping resolution levels to their estimated sizes
        """
        estimates = {}
        
        for resolution_name, settings in self.resolution_settings.items():
            # Temporarily change resolution
            original_resolution = self.ast_resolution
            self.ast_resolution = resolution_name
            
            # Generate context with this resolution
            context = self.generate_ast_context()
            token_count = len(context) // 4  # Rough token estimate
            
            estimates[resolution_name] = {
                'token_count': token_count,
                'context_length': len(context),
                'symbols_per_file': settings['symbols_per_file'],
                'deps_per_file': settings['deps_per_file']
            }
            
            # Restore original resolution
            self.ast_resolution = original_resolution
        
        self.resolution_estimates = estimates
        return estimates
    
    def generate_ast_context(self) -> str:
        """
        Generate a textual context from the AST.
        
        Returns:
            String representation of the AST context 
        """

        # Return empty context if disabled
        if self.ast_resolution == 'disabled':
            return ""

        if not self.ast_cache:
            logger.info("No AST cache available, returning empty context")
            return "# AST Analysis\n\nNo files have been processed for AST analysis."
            
        if not self.project_ast:
            return ""
            
        context_parts = []
        
        # Add file structure information
        context_parts.append("# Code Structure Summary")
        
        # Add statistics about file types processed
        file_types = {}
        for file_path in self.ast_cache.keys():
            ext = os.path.splitext(file_path)[1]
            file_types[ext] = file_types.get(ext, 0) + 1
        
        context_parts.append(f"\n## Files Processed by Type: {dict(sorted(file_types.items()))}")
        
        # Add key files and their relationships
        context_parts.append(f"\n## Total AST Statistics")
        context_parts.append(f"- Total nodes in project AST: {len(self.project_ast.nodes)}")
        context_parts.append(f"- Total edges in project AST: {len(self.project_ast.edges)}")
        context_parts.append("\n## Key Files and Dependencies")
        
        # For each file in the ast_cache
        settings = self.resolution_settings[self.ast_resolution]
        for file_path, ast in list(self.ast_cache.items()):
            # Get relative path for display
            rel_path = os.path.relpath(file_path)
            
            # Add file summary
            context_parts.append(f"\n### {rel_path}")
            context_parts.append(f"Nodes: {len(ast.nodes)}, Edges: {len(ast.edges)}")
            
            # Show node types in this file
            node_types = {}
            for node in list(ast.nodes.values()):
                node_types[node.node_type] = node_types.get(node.node_type, 0) + 1
            context_parts.append(f"Node types: {dict(sorted(node_types.items()))}")
            
            # Add key symbols defined in this file
            symbols = self._extract_key_symbols(ast)
            if symbols:
                context_parts.append("\nDefines:")
                # Limit symbols based on resolution settings
                symbol_list = list(symbols)[:settings['symbols_per_file']]
                for symbol in symbol_list:
                    context_parts.append(f"- {symbol}")
            
            # Add dependencies
            deps = self._extract_dependencies(ast)
            if deps:
                context_parts.append("\nDependencies:")
                # Limit dependencies based on resolution settings
                dep_list = list(deps)[:settings['deps_per_file']]
                for dep in dep_list:
                    context_parts.append(f"- {dep}")
        
        return "\n".join(context_parts)
    
    def _extract_key_symbols(self, ast: UnifiedAST) -> List[str]:
        """Extract key symbols defined in the AST."""
        symbols = []
        
        # Extract classes, functions, and variables
        for node in list(ast.nodes.values()):
            if node.node_type in ["class", "function", "method", "variable"]:
                symbols.append(f"{node.node_type} {node.name}")
        
        return symbols
    
    def _extract_dependencies(self, ast: UnifiedAST) -> List[str]:
        """Extract dependencies from the AST."""
        deps = []
        
        # Extract imports and references
        for node in list(ast.nodes.values()):
            if node.node_type == "import":
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
