"""
AST Parser module for Ziya.

This module provides language-agnostic Abstract Syntax Tree (AST) parsing capabilities
for Ziya, enabling deeper code understanding across multiple programming languages.
"""

from .registry import ParserRegistry
from .unified_ast import UnifiedAST, Node, Edge
from .query_engine import ASTQueryEngine
from .ziya_ast_enhancer import ZiyaASTEnhancer
from .integration import (
    initialize_ast_capabilities,
    is_ast_available,
    get_ast_context,
    get_ast_token_count,
    enhance_query_context
)

__all__ = [
    'ParserRegistry',
    'UnifiedAST',
    'Node',
    'Edge',
    'ASTQueryEngine',
    'ZiyaASTEnhancer',
    'initialize_ast_capabilities',
    'is_ast_available',
    'get_ast_context',
    'get_ast_token_count',
    'enhance_query_context',
]
