"""
Architecture Shapes Catalog Loader

Discovers and loads shape catalogs from multiple sources:
- Built-in catalogs (AWS, Generic)
- JSON catalog files
- Extension catalogs (in extensions/ directory)
"""

from typing import Dict, List, Any, Optional
import importlib
import json
from pathlib import Path
from app.utils.logging_utils import logger


class ShapeCatalog:
    """Base class for shape catalog providers."""
    
    def __init__(self):
        self.provider_id: str = ""
        self.provider_name: str = ""
        self.shapes: List[Dict[str, Any]] = []
    
    def get_shapes(self) -> List[Dict[str, Any]]:
        """Return all shapes in this catalog."""
        return self.shapes
    
    def search(self, keyword: str) -> List[Dict[str, Any]]:
        """Search shapes by keyword."""
        keyword_lower = keyword.lower()
        results = []
        
        for shape in self.shapes:
            # Search in keywords, name, and description
            if any(keyword_lower in kw.lower() for kw in shape.get("keywords", [])) or \
               keyword_lower in shape.get("name", "").lower() or \
               keyword_lower in shape.get("description", "").lower() or \
               keyword_lower in shape.get("id", "").lower():
                results.append(shape)
        
        return results


class CatalogRegistry:
    """Registry for all shape catalogs."""
    
    def __init__(self):
        self.catalogs: Dict[str, ShapeCatalog] = {}
        self._load_builtin_catalogs()
        self._load_extension_catalogs()
    
    def _load_builtin_catalogs(self):
        """Load built-in catalogs (AWS, Generic)."""
        try:
            # Load JSON catalogs from catalogs/ directory
            catalogs_dir = Path(__file__).parent / "catalogs"
            if catalogs_dir.exists():
                for json_file in catalogs_dir.glob("*.json"):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            catalog_data = json.load(f)
                        catalog = self._create_catalog_from_json(catalog_data)
                        self.register(catalog)
                        logger.info(f"Loaded JSON catalog: {json_file.stem} ({len(catalog.shapes)} shapes)")
                    except Exception as e:
                        logger.warning(f"Could not load JSON catalog {json_file}: {e}")
            
            # Load Python catalogs (for dynamic catalogs)
            try:
                from app.mcp.tools.architecture_shapes.catalogs.generic_catalog import generic_catalog
                self.register(generic_catalog)
                logger.info(f"Loaded Python catalog: generic ({len(generic_catalog.shapes)} shapes)")
            except ImportError as e:
                logger.warning(f"Could not load generic catalog: {e}")
                
        except Exception as e:
            logger.error(f"Error loading built-in catalogs: {e}")
    
    def _load_extension_catalogs(self):
        """Auto-discover extension catalogs."""
        extensions_path = Path(__file__).parent / "extensions"
        
        if not extensions_path.exists():
            logger.debug("No extensions directory found for shape catalogs")
            return
        
        # Look for JSON files first (easiest to add)
        for json_file in extensions_path.rglob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    catalog_data = json.load(f)
                catalog = self._create_catalog_from_json(catalog_data)
                self.register(catalog)
                logger.info(f"Loaded extension catalog: {json_file.stem} ({len(catalog.shapes)} shapes)")
            except Exception as e:
                logger.debug(f"Could not load extension JSON {json_file}: {e}")
        
        # Look for Python modules
        for item in extensions_path.iterdir():
            if item.is_dir() and (item / "__init__.py").exists():
                try:
                    module_name = f"app.mcp.tools.architecture_shapes.extensions.{item.name}"
                    module = importlib.import_module(module_name)
                    
                    if hasattr(module, "catalog"):
                        self.register(module.catalog)
                        logger.info(f"Loaded extension Python catalog: {item.name}")
                except Exception as e:
                    logger.debug(f"Could not load extension module {item.name}: {e}")
    
    def _create_catalog_from_json(self, data: Dict[str, Any]) -> ShapeCatalog:
        """Create a catalog from JSON data."""
        catalog = ShapeCatalog()
        catalog.provider_id = data.get("provider_id", "unknown")
        catalog.provider_name = data.get("provider_name", "Unknown")
        catalog.shapes = data.get("shapes", [])
        return catalog
    
    def register(self, catalog: ShapeCatalog):
        """Register a catalog."""
        self.catalogs[catalog.provider_id] = catalog
        logger.debug(f"Registered catalog: {catalog.provider_name} ({len(catalog.shapes)} shapes)")
    
    def get_all_shapes(self) -> List[Dict[str, Any]]:
        """Get all shapes from all catalogs."""
        all_shapes = []
        for catalog in self.catalogs.values():
            shapes = catalog.get_shapes()
            # Add provider info to each shape
            for shape in shapes:
                if "provider" not in shape:
                    shape["provider"] = {
                        "id": catalog.provider_id,
                        "name": catalog.provider_name
                    }
            all_shapes.extend(shapes)
        return all_shapes
    
    def search_all(self, keyword: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search across all catalogs or specific provider."""
        results = []
        
        catalogs_to_search = (
            [self.catalogs[provider]] if provider and provider in self.catalogs 
            else self.catalogs.values()
        )
        
        for catalog in catalogs_to_search:
            shapes = catalog.search(keyword)
            # Add provider info
            for shape in shapes:
                if "provider" not in shape:
                    shape["provider"] = {
                        "id": catalog.provider_id,
                        "name": catalog.provider_name
                    }
            results.extend(shapes)
        
        return results
    
    def get_categories(self) -> Dict[str, List[str]]:
        """Get all categories with shape IDs."""
        categories: Dict[str, List[str]] = {}
        
        for shape in self.get_all_shapes():
            cat = shape.get("category", "unknown")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(shape["id"])
        
        return categories
    
    def get_providers(self) -> List[Dict[str, Any]]:
        """Get list of available providers."""
        return [
            {
                "id": catalog.provider_id,
                "name": catalog.provider_name,
                "shape_count": len(catalog.shapes),
            }
            for catalog in self.catalogs.values()
        ]


# Global registry instance
_catalog_registry: Optional[CatalogRegistry] = None


def get_catalog_registry() -> CatalogRegistry:
    """Get or create the global catalog registry."""
    global _catalog_registry
    if _catalog_registry is None:
        _catalog_registry = CatalogRegistry()
    return _catalog_registry
