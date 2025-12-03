"""
Architecture Shapes Catalog System

Provides tool-agnostic architecture component definitions for diagram generation.
"""

from app.mcp.tools.architecture_shapes.catalog_loader import get_catalog_registry
from app.mcp.tools.architecture_shapes.tools import ListShapeCategoriesTool, SearchShapesTool, GetDiagramTemplateTool

__all__ = ['get_catalog_registry', 'ListShapeCategoriesTool', 'SearchShapesTool', 'GetDiagramTemplateTool']
