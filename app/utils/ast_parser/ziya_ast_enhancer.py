"""
Ziya AST Enhancer.

This module integrates the AST parsing capabilities with Ziya,
enhancing context generation with semantic code information.
"""

import os
import logging
from typing import Dict, List, Optional, Any, Set
import fnmatch

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
        self.project_ast = None
    
    def _register_parsers(self):
        """Register available parsers."""
        # Register Python parser
        self.parser_registry.register_parser(PythonASTParser)
        
        # Register TypeScript parser
        self.parser_registry.register_parser(TypeScriptASTParser)
        
        # Register HTML/CSS parser
        self.parser_registry.register_parser(HTMLCSSParser)
    
    def process_codebase(self, codebase_path: str, exclude_patterns: Optional[List[str]] = None, 
                        max_depth: int = 15) -> None:
        """
        Process all files in the codebase and build ASTs.
        
        Args:
            codebase_path: Path to the codebase root
            exclude_patterns: Patterns to exclude
            max_depth: Maximum directory depth to traverse
        """
        exclude_patterns = exclude_patterns or []
        self.ast_cache = {}
        self.query_engines = {}
        self.project_ast = UnifiedAST()
        
        # Walk through the codebase
        for root, dirs, files in os.walk(codebase_path):
            # Check depth
            rel_path = os.path.relpath(root, codebase_path)
            if rel_path == '.':
                depth = 0
            else:
                depth = rel_path.count(os.sep) + 1
            
            if depth >= max_depth:
                dirs.clear()  # Don't go deeper
                continue
            
            # Filter directories based on exclude patterns
            dirs[:] = [d for d in dirs if not self._is_excluded(d, exclude_patterns)]
            
            # Process files
            for file in files:
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, codebase_path)
                
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
                    
                    logger.info(f"Processed file: {rel_file_path}")
                except Exception as e:
                    logger.error(f"Error processing file {rel_file_path}: {str(e)}")
        
        # Create project-wide query engine
        if self.project_ast:
            self.query_engines['project'] = ASTQueryEngine(self.project_ast)
    
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
            
            # Get file summary
            context['semantic_context']['file_summary'] = query_engine.generate_summary(file_context)
            
            # Extract key structures
            context['code_structures']['functions'] = [
                {'name': func.name, 'attributes': func.attributes}
                for func in query_engine.find_functions()
            ]
            
            context['code_structures']['classes'] = [
                {'name': cls.name, 'attributes': cls.attributes}
                for cls in query_engine.find_classes()
            ]
            
            # Get dependencies
            context['semantic_context']['dependencies'] = query_engine.get_dependencies(file_context)
        
        # Otherwise use project-wide context
        elif 'project' in self.query_engines:
            query_engine = self.query_engines['project']
            
            # Get top-level structures
            context['code_structures']['top_functions'] = [
                {'name': func.name, 'file': func.source_location.file_path}
                for func in query_engine.find_functions()[:10]  # Limit to avoid context explosion
            ]
            
            context['code_structures']['top_classes'] = [
                {'name': cls.name, 'file': cls.source_location.file_path}
                for cls in query_engine.find_classes()[:10]  # Limit to avoid context explosion
            ]
        
        return context
    
    def generate_code_summaries(self) -> Dict[str, Any]:
        """
        Generate high-level summaries of code structures.
        
        Returns:
            Dictionary with code summaries
        """
        summaries = {
            'files': {},
            'project_overview': {
                'function_count': 0,
                'class_count': 0,
                'file_count': len(self.ast_cache)
            }
        }
        
        # Generate file summaries
        for file_path, query_engine in self.query_engines.items():
            if file_path == 'project':
                continue
                
            try:
                summary = query_engine.generate_summary(file_path)
                summaries['files'][file_path] = summary
                
                # Update project counts
                summaries['project_overview']['function_count'] += len(summary.get('top_level_definitions', []))
            except Exception as e:
                logger.error(f"Error generating summary for {file_path}: {str(e)}")
        
        # If we have a project-wide query engine, get overall stats
        if 'project' in self.query_engines:
            query_engine = self.query_engines['project']
            summaries['project_overview']['class_count'] = len(query_engine.find_classes())
            summaries['project_overview']['function_count'] = len(query_engine.find_functions())
        
        return summaries
    
    def find_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        """
        Find all references to a symbol across the codebase.
        
        Args:
            symbol_name: Name of the symbol to find
            
        Returns:
            List of references
        """
        if 'project' not in self.query_engines:
            return []
        
        query_engine = self.query_engines['project']
        
        # Find definitions
        definitions = query_engine.find_definitions(symbol_name)
        
        references = []
        for definition in definitions:
            # Find references to this definition
            refs = query_engine.find_references(definition.node_id)
            
            for ref in refs:
                references.append({
                    'file': ref.source_location.file_path,
                    'line': ref.source_location.start_line,
                    'column': ref.source_location.start_column,
                    'type': ref.node_type,
                    'context': self._get_context_snippet(ref.source_location.file_path, 
                                                       ref.source_location.start_line)
                })
        
        return references
    
    def _get_context_snippet(self, file_path: str, line: int, context_lines: int = 2) -> str:
        """
        Get a code snippet around a line.
        
        Args:
            file_path: Path to the file
            line: Line number
            context_lines: Number of context lines before and after
            
        Returns:
            Code snippet
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            start = max(0, line - context_lines - 1)
            end = min(len(lines), line + context_lines)
            
            return ''.join(lines[start:end])
        except Exception:
            return ""
    
    def analyze_dependencies(self) -> Dict[str, List[str]]:
        """
        Analyze dependencies between files.
        
        Returns:
            Dictionary mapping files to their dependencies
        """
        dependencies = {}
        
        for file_path, query_engine in self.query_engines.items():
            if file_path == 'project':
                continue
                
            try:
                file_deps = query_engine.get_dependencies(file_path)
                dependencies[file_path] = file_deps
            except Exception as e:
                logger.error(f"Error analyzing dependencies for {file_path}: {str(e)}")
        
        return dependencies
