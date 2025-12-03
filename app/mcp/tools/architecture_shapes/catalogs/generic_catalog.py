"""
Generic Shapes Catalog

Standard flowchart and diagram shapes that work with any cloud provider.
"""

from app.mcp.tools.architecture_shapes.catalog_loader import ShapeCatalog


class GenericShapeCatalog(ShapeCatalog):
    """Generic diagram shapes catalog."""
    
    def __init__(self):
        super().__init__()
        self.provider_id = "generic"
        self.provider_name = "Generic Shapes"
        self.shapes = GENERIC_SHAPES


GENERIC_SHAPES = [
    {
        "id": "generic_rectangle",
        "name": "Rectangle",
        "category": "generic",
        "color": "neutral",
        "description": "Basic rectangle shape for general components",
        "keywords": ["rectangle", "box", "square", "component", "process"],
        "defaultSize": {"width": 120, "height": 60},
        "renderHints": {
            "drawio": {"shape": "rectangle", "resIcon": ""},
            "mermaid": {"icon": ""},
            "graphviz": {"shape": "box", "style": "filled"},
        },
    },
    {
        "id": "generic_rounded_rectangle",
        "name": "Rounded Rectangle",
        "category": "generic",
        "color": "neutral",
        "description": "Rounded rectangle for start/end nodes",
        "keywords": ["rounded", "pill", "start", "end", "terminal", "begin", "finish"],
        "defaultSize": {"width": 120, "height": 40},
        "renderHints": {
            "drawio": {"shape": "rectangle", "resIcon": ""},
            "mermaid": {"icon": ""},
            "graphviz": {"shape": "box", "style": "filled,rounded"},
        },
    },
    {
        "id": "generic_ellipse",
        "name": "Ellipse",
        "category": "generic",
        "color": "neutral",
        "description": "Ellipse or circle shape",
        "keywords": ["ellipse", "circle", "oval", "state", "round"],
        "defaultSize": {"width": 100, "height": 100},
        "renderHints": {
            "drawio": {"shape": "ellipse", "resIcon": ""},
            "mermaid": {"icon": ""},
            "graphviz": {"shape": "ellipse", "style": "filled"},
        },
    },
    {
        "id": "generic_diamond",
        "name": "Diamond",
        "category": "generic",
        "color": "neutral",
        "description": "Diamond shape for decision points",
        "keywords": ["diamond", "rhombus", "decision", "condition", "if", "choice", "branch"],
        "defaultSize": {"width": 100, "height": 100},
        "renderHints": {
            "drawio": {"shape": "rhombus", "resIcon": ""},
            "mermaid": {"icon": ""},
            "graphviz": {"shape": "diamond", "style": "filled"},
        },
    },
    {
        "id": "generic_cylinder",
        "name": "Cylinder",
        "category": "generic",
        "color": "neutral",
        "description": "Cylinder shape for generic databases or storage",
        "keywords": ["cylinder", "database", "storage", "data", "disk", "db"],
        "defaultSize": {"width": 80, "height": 100},
        "renderHints": {
            "drawio": {"shape": "cylinder", "resIcon": ""},
            "mermaid": {"icon": "🗄️"},
            "graphviz": {"shape": "cylinder", "style": "filled"},
        },
    },
    {
        "id": "generic_parallelogram",
        "name": "Parallelogram",
        "category": "generic",
        "color": "neutral",
        "description": "Parallelogram for input/output operations",
        "keywords": ["parallelogram", "input", "output", "io", "data", "read", "write"],
        "defaultSize": {"width": 120, "height": 60},
        "renderHints": {
            "drawio": {"shape": "parallelogram", "resIcon": ""},
            "mermaid": {"icon": ""},
            "graphviz": {"shape": "parallelogram", "style": "filled"},
        },
    },
    {
        "id": "generic_hexagon",
        "name": "Hexagon",
        "category": "generic",
        "color": "neutral",
        "description": "Hexagon shape for processing or preparation steps",
        "keywords": ["hexagon", "preparation", "processing", "transform"],
        "defaultSize": {"width": 100, "height": 80},
        "renderHints": {
            "drawio": {"shape": "hexagon", "resIcon": ""},
            "mermaid": {"icon": ""},
            "graphviz": {"shape": "hexagon", "style": "filled"},
        },
    },
    {
        "id": "generic_actor",
        "name": "Actor/Person",
        "category": "generic",
        "color": "neutral",
        "description": "Actor/person shape for users or external actors",
        "keywords": ["actor", "person", "user", "human", "stick figure", "external"],
        "defaultSize": {"width": 40, "height": 60},
        "renderHints": {
            "drawio": {"shape": "actor", "resIcon": ""},
            "mermaid": {"icon": "👤"},
            "graphviz": {"shape": "box", "style": "filled,rounded"},
        },
    },
    {
        "id": "generic_cloud",
        "name": "Cloud",
        "category": "generic",
        "color": "neutral",
        "description": "Cloud shape for cloud services or external systems",
        "keywords": ["cloud", "service", "external", "third party"],
        "defaultSize": {"width": 120, "height": 80},
        "renderHints": {
            "drawio": {"shape": "cloud", "resIcon": ""},
            "mermaid": {"icon": "☁️"},
            "graphviz": {"shape": "ellipse", "style": "filled,dashed"},
        },
    },
    {
        "id": "generic_document",
        "name": "Document",
        "category": "generic",
        "color": "neutral",
        "description": "Document shape for files or reports",
        "keywords": ["document", "file", "report", "paper"],
        "defaultSize": {"width": 100, "height": 80},
        "renderHints": {
            "drawio": {"shape": "document", "resIcon": ""},
            "mermaid": {"icon": "📄"},
            "graphviz": {"shape": "note", "style": "filled"},
        },
    },
]


# Create catalog instance
generic_catalog = GenericShapeCatalog()
