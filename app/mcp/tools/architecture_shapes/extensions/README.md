# Architecture Shapes Extensions

Add custom shape catalogs for your organization, cloud provider, or tools.

## Overview

This directory allows you to extend the architecture shapes catalog with:
- Company-specific service icons
- Additional cloud providers (Azure, GCP, etc.)
- Custom diagram components
- Internal tooling representations

Extensions are automatically discovered and loaded at startup.

---

## Quick Start

### Option 1: JSON File (Simplest)

Create a JSON file in this directory or any subdirectory:

**`mycompany_services.json`**:
```json
{
  "provider_id": "mycompany",
  "provider_name": "My Company Services",
  "version": "1.0",
  "shapes": [
    {
      "id": "mycompany_api_service",
      "name": "Internal API",
      "category": "compute",
      "color": "blue",
      "description": "Company's internal API service",
      "keywords": ["api", "internal", "service"],
      "defaultSize": {"width": 78, "height": 78},
      "renderHints": {
        "drawio": {
          "shape": "mxgraph.aws4.resourceIcon",
          "resIcon": "mxgraph.aws4.generic_saml_token"
        },
        "mermaid": {"icon": "🔧"},
        "graphviz": {"shape": "box", "style": "filled"}
      }
    }
  ]
}
```

### Option 2: Python Module (More Flexible)

Create a Python module for dynamic catalogs:

**`mycompany/__init__.py`**:
```python
from app.mcp.tools.architecture_shapes.catalog_loader import ShapeCatalog

class MyCompanyCatalog(ShapeCatalog):
    def __init__(self):
        super().__init__()
        self.provider_id = "mycompany"
        self.provider_name = "My Company Services"
        self.shapes = [
            {
                "id": "mycompany_api_service",
                "name": "Internal API",
                "category": "compute",
                "color": "blue",
                "description": "Company's internal API service",
                "keywords": ["api", "internal", "service"],
                "defaultSize": {"width": 78, "height": 78},
                "renderHints": {
                    "drawio": {
                        "shape": "mxgraph.aws4.resourceIcon",
                        "resIcon": "mxgraph.aws4.generic_saml_token"
                    },
                    "mermaid": {"icon": "🔧"},
                    "graphviz": {"shape": "box", "style": "filled"},
                },
            }
        ]

# Export as 'catalog'
catalog = MyCompanyCatalog()
```

---

## Shape Definition Reference

Each shape must have these fields:

```json
{
  "id": "unique_shape_id",              // Lowercase, alphanumeric + underscore
  "name": "Display Name",                // Human-readable name
  "category": "compute",                 // See categories below
  "color": "orange",                     // See color palettes below
  "description": "What this represents",
  "keywords": ["search", "terms"],       // For search functionality
  "defaultSize": {
    "width": 78,
    "height": 78
  },
  "renderHints": {
    "drawio": {
      "shape": "mxgraph.aws4.resourceIcon",  // DrawIO shape type
      "resIcon": "mxgraph.aws4.lambda_function"  // Icon reference
    },
    "mermaid": {
      "icon": "aws-lambda"                // Mermaid icon or emoji
    },
    "graphviz": {
      "shape": "box",                     // Graphviz node shape
      "style": "filled,rounded"           // Graphviz style attributes
    }
  }
}
```

### Available Categories

- `compute` - Compute services (VMs, functions, etc.)
- `container` - Container services
- `storage` - Storage services
- `database` - Database services
- `networking` - Networking services
- `security` - Security and identity services
- `integration` - Integration and messaging
- `analytics` - Analytics and big data
- `management` - Management and governance
- `developer_tools` - Developer tools
- `ml` - Machine learning and AI
- `iot` - Internet of Things
- `application` - Application services
- `generic` - Generic shapes (flowchart elements)

### Color Palettes

- `orange` - Compute (gradient #F78E04 → #D05C17)
- `green` - Storage (gradient #7AA116 → #759C3E)
- `blue` - Database (gradient #5294CF → #2E73B8)
- `purple` - Networking (gradient #945DF2 → #5A30B5)
- `red` - Security (gradient #DD344C → #C7131F)
- `pink` - Integration (gradient #F34482 → #BC1356)
- `neutral` - Generic shapes (gray)

---

## Example Extensions

### Azure Services

**`azure_services.json`**:
```json
{
  "provider_id": "azure",
  "provider_name": "Microsoft Azure",
  "shapes": [
    {
      "id": "azure_functions",
      "name": "Azure Functions",
      "category": "compute",
      "color": "blue",
      "description": "Azure serverless functions",
      "keywords": ["azure", "functions", "serverless", "faas"],
      "defaultSize": {"width": 78, "height": 78},
      "renderHints": {
        "drawio": {"shape": "mxgraph.azure.compute.function_app", "resIcon": ""},
        "mermaid": {"icon": "⚡"},
        "graphviz": {"shape": "box", "style": "filled,rounded"}
      }
    }
  ]
}
```

### GCP Services

**`gcp/__init__.py`**:
```python
from app.mcp.tools.architecture_shapes.catalog_loader import ShapeCatalog

class GCPCatalog(ShapeCatalog):
    def __init__(self):
        super().__init__()
        self.provider_id = "gcp"
        self.provider_name = "Google Cloud Platform"
        self.shapes = [
            {
                "id": "gcp_cloud_functions",
                "name": "Cloud Functions",
                "category": "compute",
                "color": "blue",
                "description": "GCP serverless functions",
                "keywords": ["gcp", "functions", "serverless", "google"],
                "defaultSize": {"width": 78, "height": 78},
                "renderHints": {
                    "drawio": {"shape": "mxgraph.gcp2.compute.cloud_functions", "resIcon": ""},
                    "mermaid": {"icon": "⚡"},
                    "graphviz": {"shape": "box", "style": "filled,rounded"},
                },
            }
        ]

catalog = GCPCatalog()
```

---

## Testing Your Extension

After adding your catalog, restart Ziya and test:

```python
# Use the list tool to verify your shapes are loaded
list_architecture_shape_categories()

# Search for your shapes
search_architecture_shapes(keyword="mycompany")
```

Your shapes should appear in the results!

---

## Best Practices

1. **Use clear keywords**: Make shapes easy to find
2. **Follow naming conventions**: Use `provider_servicename` format for IDs
3. **Choose appropriate shapes**: Match DrawIO shapes to your component type
4. **Test all formats**: Verify shapes render correctly in DrawIO, Mermaid, and Graphviz
5. **Document your catalog**: Add a README in your extension directory

---

## DrawIO Shape Reference

Common DrawIO shapes you can use:

- **AWS Icons**: `mxgraph.aws4.resourceIcon` with `resIcon=mxgraph.aws4.{service}`
- **Azure Icons**: `mxgraph.azure.{category}.{service}`
- **GCP Icons**: `mxgraph.gcp2.{category}.{service}`
- **Generic**: `rectangle`, `ellipse`, `cylinder`, `rhombus`, `hexagon`, `cloud`, `actor`

Browse available stencils at: https://www.diagrams.net/blog/shape-libraries
