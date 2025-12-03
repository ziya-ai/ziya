"""
Architecture Shapes MCP Tools

Provides on-demand access to architecture shape catalogs for diagram generation.
"""

from typing import Dict, Any
from typing import Optional, Literal
from pydantic import BaseModel, Field
from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.architecture_shapes.catalog_loader import get_catalog_registry
from app.utils.logging_utils import logger


# ============================================================================
# TOOL: List Shape Categories
# ============================================================================

class ListShapeCategoriesInput(BaseModel):
    """Input schema for listing shape categories."""
    pass  # No parameters needed


class ListShapeCategoriesTool(BaseMCPTool):
    """List available architecture shape categories."""
    
    name: str = "list_architecture_shape_categories"
    description: str = """List available architecture shape categories (compute, storage, database, etc.).

Returns:
- Available categories and their shape counts
- List of catalog providers (AWS, Generic, custom extensions)
- Total shape count across all catalogs

Use this to discover what types of components are available before generating diagrams.
Works with DrawIO, Mermaid, and Graphviz output formats."""
    
    InputSchema = ListShapeCategoriesInput
    
    @property
    def is_internal(self) -> bool:
        """Internal tool - output not shown to user."""
        return True
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        logger.info("=" * 80)
        logger.info("ListShapeCategoriesTool.execute() START")
        try:
            logger.info("ListShapeCategoriesTool.execute() called")
            registry = get_catalog_registry()
            logger.info(f"Got registry with {len(registry.catalogs)} catalogs")
            categories = registry.get_categories()
            logger.info(f"Got {len(categories)} categories")
            providers = registry.get_providers()
            logger.info(f"Got {len(providers)} providers")
            total_shapes = len(registry.get_all_shapes())
            logger.info(f"Total shapes: {total_shapes}")
            
            # Format nicely for LLM
            lines = ["# Architecture Shape Catalog\n"]
            
            # Show providers
            lines.append("## Available Providers")
            for provider in providers:
                lines.append(f"- **{provider['name']}**: {provider['shape_count']} shapes (id: `{provider['id']}`)")
            lines.append("")
            
            # Show categories
            lines.append("## Categories")
            for cat, shape_ids in sorted(categories.items()):
                sample = ', '.join(shape_ids[:3])
                more = f' + {len(shape_ids) - 3} more' if len(shape_ids) > 3 else ''
                lines.append(f"**{cat}**: {len(shape_ids)} shapes ({sample}{more})")
            
            lines.append(f"\n**Total**: {total_shapes} shapes across {len(providers)} provider(s)")
            lines.append(f"**Supported Formats**: DrawIO, Mermaid, Graphviz")
            lines.append("\n💡 Use `search_architecture_shapes` to find specific shapes by keyword.")
            lines.append("💡 Search works across all providers and supports multiple keywords.")
            
            return {"content": "\n".join(lines)}
            
        except Exception as e:
            import traceback
            logger.error(f"Error listing shape categories: {e}\n{traceback.format_exc()}")
            return {"error": True, "message": str(e)}


# ============================================================================
# TOOL: Search Shapes
# ============================================================================

class SearchShapesInput(BaseModel):
    """Input schema for searching shapes."""
    keyword: str = Field(
        description="Search term (e.g., 'database', 'lambda', 'queue', 'api', 'storage'). Searches across shape names, descriptions, and keywords."
    )
    category: Optional[Literal[
        "compute", "storage", "database", "networking", "security",
        "integration", "analytics", "management", "container", "generic",
        "ml", "iot", "developer_tools", "application"
    ]] = Field(
        default=None,
        description="Optional: Filter by category"
    )
    format: Literal["all", "drawio", "mermaid", "graphviz"] = Field(
        default="all",
        description="Return rendering hints for specific format only (default: all formats)"
    )
    provider: Optional[str] = Field(
        default=None,
        description="Optional: Filter by provider (aws, generic, or custom provider id)"
    )


class SearchShapesTool(BaseMCPTool):
    """Search for architecture shapes by keyword."""
    
    name: str = "search_architecture_shapes"
    description: str = """Search for architecture shapes by keyword or category.

Returns tool-agnostic shape definitions that can be rendered as DrawIO, Mermaid, or Graphviz.
Each shape includes:
- Unique shape ID (use this when building diagrams)
- Name and description
- Category and color palette
- Default dimensions
- Rendering hints for each diagram format (drawio, mermaid, graphviz)
- Keywords for search

Use this when:
- Generating diagrams and need to know available components
- Looking for specific AWS services or generic shapes
- Want to see how a shape renders in different formats

Example searches:
- "lambda" → finds AWS Lambda function
- "database" → finds DynamoDB, RDS, Aurora, etc.
- "queue" → finds SQS
- "api" → finds API Gateway"""
    
    InputSchema = SearchShapesInput
    
    @property
    def is_internal(self) -> bool:
        """Internal tool - output not shown to user."""
        return True
    
    async def execute(
        self, 
        keyword: str, 
        category: Optional[str] = None, 
        format: str = "all", 
        provider: Optional[str] = None, 
        **kwargs
    ) -> Dict[str, Any]:
        try:
            registry = get_catalog_registry()
            results = registry.search_all(keyword, provider)
            
            # Filter by category if specified
            if category:
                results = [r for r in results if r.get("category") == category]
            
            if not results:
                providers_info = registry.get_providers()
                provider_list = ', '.join([p['name'] for p in providers_info])
                
                return f"""No shapes found matching "{keyword}"{f' in category "{category}"' if category else ''}{f' from provider "{provider}"' if provider else ''}.

**Suggestions**:
- Try broader keywords: "database", "compute", "storage", "network"
- Use `list_architecture_shape_categories` to see available categories
- Search for AWS service names: "lambda", "s3", "dynamodb", "ec2"
- Try generic shapes: "rectangle", "diamond", "cylinder"

**Available providers**: {provider_list}
"""
            
            # Format results
            lines = [f"# Found {len(results)} shape(s) matching \"{keyword}\"\n"]
            
            for shape in results:
                provider_info = shape.get("provider", {})
                lines.append(f"## {shape['name']} ({shape['color']})")
                lines.append(f"- **ID**: `{shape['id']}`")
                lines.append(f"- **Category**: {shape['category']}")
                lines.append(f"- **Description**: {shape['description']}")
                lines.append(f"- **Provider**: {provider_info.get('name', 'Unknown')}")
                lines.append(f"- **Default Size**: {shape['defaultSize']['width']}x{shape['defaultSize']['height']}px")
                
                # Show rendering hints based on format
                render_hints = shape.get('renderHints', {})
                
                if format == "all" or format == "drawio":
                    drawio_hint = render_hints.get('drawio', {})
                    if drawio_hint.get('resIcon'):
                        lines.append(f"- **DrawIO**: `{drawio_hint['resIcon']}`")
                    else:
                        lines.append(f"- **DrawIO**: shape=`{drawio_hint.get('shape', 'rectangle')}`")
                
                if format == "all" or format == "mermaid":
                    mermaid_hint = render_hints.get('mermaid', {})
                    icon = mermaid_hint.get('icon', '')
                    if icon:
                        lines.append(f"- **Mermaid**: `{icon}`")
                
                if format == "all" or format == "graphviz":
                    graphviz_hint = render_hints.get('graphviz', {})
                    if graphviz_hint:
                        lines.append(f"- **Graphviz**: shape=`{graphviz_hint.get('shape', 'box')}`, style=`{graphviz_hint.get('style', 'filled')}`")
                
                lines.append("")
            
            # Add usage instructions
            lines.append("## Usage")
            lines.append("To generate a diagram, use the shape IDs above:")
            lines.append("")
            lines.append("**DrawIO Example**:")
            lines.append("```")
            lines.append('shapes = [{"shapeId": "' + results[0]['id'] + '", "label": "My Component", "x": 100, "y": 100}]')
            lines.append("```")
            lines.append("")
            lines.append("**Mermaid Example**:")
            lines.append("```")
            lines.append('shapes = [{"shapeId": "' + results[0]['id'] + '", "label": "My Component"}]')
            lines.append("```")
            
            return {"content": "\n".join(lines)}
            
        except Exception as e:
            logger.error(f"Error searching shapes: {e}")
            return {"error": True, "message": str(e)}


# ============================================================================
# TOOL: Get Diagram Template
# ============================================================================

class GetDiagramTemplateInput(BaseModel):
    """Input schema for getting diagram template."""
    format: Literal["drawio", "mermaid", "graphviz"] = Field(
        description="Diagram format to generate template for"
    )
    pattern: Literal["simple", "aws-serverless", "aws-network", "flowchart"] = Field(
        default="simple",
        description="Template pattern (default: simple)"
    )


class GetDiagramTemplateTool(BaseMCPTool):
    """Get code template for generating architecture diagrams."""
    
    name: str = "get_architecture_diagram_template"
    description: str = """Get a code template for generating architecture diagrams in DrawIO, Mermaid, or Graphviz format.

Returns ready-to-use example code showing:
1. How to define shapes using catalog shape IDs
2. How to create connections between shapes
3. How to generate the final diagram output

Templates available:
- **simple**: Basic 2-3 component diagram
- **aws-serverless**: Serverless architecture (API Gateway + Lambda + DynamoDB)
- **aws-network**: Network architecture (VPC, subnets, etc.)
- **flowchart**: Generic flowchart with decision points

Use this when you're ready to generate a diagram and need the boilerplate structure."""
    
    InputSchema = GetDiagramTemplateInput
    
    @property
    def is_internal(self) -> bool:
        """Internal tool - output not shown to user."""
        return True
    
    async def execute(self, format: str, pattern: str = "simple", **kwargs) -> Dict[str, Any]:
        templates = {
            "drawio": {
                "simple": """# DrawIO Simple Template

Use shape IDs from catalog to build your diagram:

\\`\\`\\`typescript
import { generateDrawIOFromCatalog } from './renderers/drawioRenderer';

const shapes = [
    {{ shapeId: "aws_lambda", label: "Handler", x: 100, y: 100 }},
    {{ shapeId: "aws_dynamodb", label: "Data", x: 300, y: 100 }},
];

const connections = [
    {{ sourceIndex: 0, targetIndex: 1, label: "writes" }}
];

const xml = generateDrawIOFromCatalog(shapes, connections, "My Architecture");
\\`\\`\\`

Output the xml in a \\`\\`\\`drawio code block.""",
                "aws-serverless": """# DrawIO AWS Serverless Template

\\`\\`\\`typescript
const shapes = [
    {{ shapeId: "aws_api_gateway", label: "REST API", x: 100, y: 200 }},
    {{ shapeId: "aws_lambda", label: "ProcessOrder", x: 300, y: 200 }},
    {{ shapeId: "aws_dynamodb", label: "Orders", x: 500, y: 200 }},
    {{ shapeId: "aws_s3", label: "Receipts", x: 300, y: 350 }},
];

const connections = [
    {{ sourceIndex: 0, targetIndex: 1, label: "POST /orders" }},
    {{ sourceIndex: 1, targetIndex: 2, label: "store order" }},
    {{ sourceIndex: 1, targetIndex: 3, label: "save receipt" }},
];

const xml = generateDrawIOFromCatalog(shapes, connections, "Serverless Order Processing");
\\`\\`\\`""",
            },
            "mermaid": {
                "simple": """# Mermaid Simple Template

\\`\\`\\`typescript
import { generateMermaidFromCatalog } from './renderers/mermaidRenderer';

const shapes = [
    {{ shapeId: "aws_lambda", label: "Handler" }},
    {{ shapeId: "aws_dynamodb", label: "Data" }},
];

const connections = [
    {{ sourceIndex: 0, targetIndex: 1, label: "writes" }}
];

const mermaid = generateMermaidFromCatalog(shapes, connections);
\\`\\`\\`

Output in a \\`\\`\\`mermaid code block.""",
            },
            "graphviz": {
                "simple": """# Graphviz Simple Template

\\`\\`\\`typescript
import { generateGraphvizFromCatalog } from './renderers/graphvizRenderer';

const shapes = [
    {{ shapeId: "aws_lambda", label: "Handler" }},
    {{ shapeId: "aws_dynamodb", label: "Data" }},
];

const connections = [
    {{ sourceIndex: 0, targetIndex: 1, label: "writes" }}
];

const dot = generateGraphvizFromCatalog(shapes, connections, "My Architecture");
\\`\\`\\`

Output in a \\`\\`\\`graphviz code block.""",
            },
        }
        
        try:
            template_dict = templates.get(format, templates["drawio"])
            template = template_dict.get(pattern, template_dict.get("simple", "Template not found"))
            return {"content": template}
        except Exception as e:
            logger.error(f"Error getting template: {e}")
            return {"error": True, "message": str(e)}
