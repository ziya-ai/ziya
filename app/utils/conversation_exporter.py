"""
Conversation Export Utility

Exports conversations to formats suitable for paste services (GitHub Gist and others)
with full preservation of formatting, code blocks, diffs, and visualizations.
"""

import base64
import re
import json
from typing import List, Dict, Any, Optional
from datetime import datetime


def export_conversation_for_paste(
    messages: List[Dict[str, Any]],
    format_type: str = 'markdown',
    target: str = 'public',  # Target paste service ID (extensible via plugins)
    captured_diagrams: Optional[List[Dict[str, Any]]] = None,
    version: str = '0.3.8',
    model: str = 'unknown',
    provider: str = 'unknown'
) -> Dict[str, Any]:
    """
    Export a conversation in a format suitable for paste services.
    
    Args:
        messages: List of conversation messages
        format_type: 'markdown' or 'html'
        target: Target paste service ID (extensible via plugins)
        captured_diagrams: List of captured visualization data URIs with metadata
        version: Ziya version
        model: Model name/alias
        provider: Provider name (bedrock, google, etc.)
        
    Returns:
        Dictionary with exported content and metadata
    """
    # Create diagram lookup for easy access
    diagram_by_index = {}
    if captured_diagrams:
        for diagram in captured_diagrams:
            diagram_by_index[diagram.get('index', -1)] = diagram
    
    if format_type == 'html':
        content = _export_as_html(messages, target, version, model, provider, diagram_by_index)
        filename = f"ziya_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    else:
        content = _export_as_markdown(messages, target, version, model, provider, diagram_by_index)
        filename = f"ziya_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    
    return {
        "content": content,
        "filename": filename,
        "format": format_type,
        "target": target,
        "size": len(content),
        "message_count": len(messages),
        "diagrams_count": len(captured_diagrams) if captured_diagrams else 0
    }

def _clean_tool_blocks(content: str) -> str:
    """
    Replace HTML comment tool blocks with formatted output.
    Converts: <!-- TOOL_BLOCK_START:mcp_tool|Header -->...<!-- TOOL_BLOCK_END:mcp_tool -->
    To: Formatted markdown section with tool output
    """
    import re

    # Pattern to match tool blocks
    pattern = r'<!-- TOOL_BLOCK_START:(mcp_\w+)\|(.+?) -->\s*(.*?)\s*<!-- TOOL_BLOCK_END:\1 -->'

    def replace_tool_block(match):
        tool_name = match.group(1)
        display_header = match.group(2).strip()
        tool_content = match.group(3).strip()

        # Format as a nice section
        # Remove "mcp_" prefix and format tool name
        clean_tool_name = tool_name.replace('mcp_', '').replace('_', ' ').title()

        # Create formatted output
        formatted = f"\n**🔧 {display_header}**\n\n"

        # Add content in a subtle box
        formatted += f"<details>\n<summary>Tool Output</summary>\n\n```\n{tool_content}\n```\n\n</details>\n"

        return formatted

    cleaned = re.sub(pattern, replace_tool_block, content, flags=re.DOTALL)
    return cleaned


def _clean_thinking_blocks(content: str) -> str:
    """
    Replace thinking code blocks with formatted sections.
    Converts: ```thinking:step-N ... ``` to formatted thinking sections
    """
    import re

    # Pattern to match thinking blocks
    pattern = r'```thinking:step-(\d+)\n(.*?)```'

    def replace_thinking_block(match):
        step_number = match.group(1)
        thinking_content = match.group(2).strip()

        # Remove the "🤔 **Thought N/M**" header if present (we'll add our own)
        thinking_content = re.sub(r'^🤔 \*\*Thought \d+/\d+\*\*\n+', '', thinking_content)

        # Remove status suffixes like "_Continuing..._" or "_✅ Complete._"
        thinking_content = re.sub(r'\n+_.*?_\s*$', '', thinking_content)

        # Format as a collapsible section
        formatted = f"\n<details>\n<summary>💭 Reasoning (Step {step_number})</summary>\n\n{thinking_content}\n\n</details>\n"

        return formatted

    cleaned = re.sub(pattern, replace_thinking_block, content, flags=re.DOTALL)
    return cleaned


def _process_content_for_export(content: str) -> str:
    """Process content to clean up tool and thinking blocks for export."""
    content = _clean_tool_blocks(content)
    content = _clean_thinking_blocks(content)
    return content



def _export_as_markdown(
    messages: List[Dict[str, Any]],
    target: str,
    version: str,
    model: str,
    provider: str,
    diagram_by_index: Dict[int, Dict[str, Any]]
) -> str:
    """Export conversation as Markdown with embedded visualizations."""
    lines = []
    
    # Add header
    lines.append("# Ziya Conversation Export")
    lines.append("")
    lines.append(f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if diagram_by_index:
        lines.append(f"**Visualizations:** {len(diagram_by_index)} diagram(s) embedded")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Process each message
    diagram_counter = 0
    for i, msg in enumerate(messages):
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        
        # Skip empty messages
        if not content or not content.strip():
            continue
        
        # Add message header
        if role == 'human':
            lines.append(f"## 👤 User")
        elif role == 'assistant':
            lines.append(f"## 🤖 AI Assistant")
        elif role == 'system':
            # Skip system messages in export
            continue
        else:
            lines.append(f"## {role.title()}")
        
        lines.append("")

        # Process content to clean up tool blocks before embedding diagrams
        content = _process_content_for_export(content)
        
        # Process content to handle and embed visualizations
        processed_content, diagrams_used = _embed_diagrams_in_markdown(
            content, 
            diagram_by_index,
            diagram_counter
        )
        diagram_counter += diagrams_used
        
        lines.append(processed_content)
        
        lines.append("")
        lines.append("---")
        lines.append("")
    
    # Add footer with metadata
    lines.append(_create_footer(target, version, model, provider, 'markdown'))
    
    return "\n".join(lines)


def _embed_diagrams_in_markdown(
    content: str,
    diagram_by_index: Dict[int, Dict[str, Any]],
    start_index: int
) -> tuple[str, int]:
    """
    Embed captured diagrams in markdown content.
    
    Returns:
        Tuple of (processed_content, number_of_diagrams_used)
    """
    diagrams_used = 0
    
    # Find visualization code blocks and replace with embedded versions
    viz_pattern = r'```(graphviz|mermaid|vega-lite|d3|joint|circuitikz)\n(.*?)```'
    
    def embed_diagram(match):
        nonlocal diagrams_used
        viz_type = match.group(1)
        source_code = match.group(2)
        
        # Get the corresponding captured diagram
        diagram_idx = start_index + diagrams_used
        diagram = diagram_by_index.get(diagram_idx)
        
        diagrams_used += 1
        
        if diagram and diagram.get('dataUri'):
            # Create markdown with embedded image AND source code
            data_uri = diagram['dataUri']
            
            return f"""### 📊 {viz_type.title()} Visualization

![{viz_type} diagram](data:image/svg+xml;base64,{data_uri.split(',')[1] if ',' in data_uri else data_uri})

<details>
<summary>View {viz_type} Source Code</summary>

```{viz_type}
{source_code}
```

</details>

"""
        else:
            # No captured diagram available, keep original code block with note
            return f"""```{viz_type}
{source_code}
```

> ⚠️ *Visualization not captured. This is the source code - paste into a {viz_type} renderer to view.*

"""
    
    processed = re.sub(viz_pattern, embed_diagram, content, flags=re.DOTALL)
    
    return processed, diagrams_used


def _export_as_html(
    messages: List[Dict[str, Any]],
    target: str,
    version: str,
    model: str,
    provider: str,
    diagram_by_index: Dict[int, Dict[str, Any]]
) -> str:
    """Export conversation as standalone HTML with embedded styles and visualizations."""
    
    html_parts = []
    
    # HTML header with styles
    html_parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ziya Conversation Export</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background: #ffffff;
            color: #24292e;
        }
        @media (prefers-color-scheme: dark) {
            body {
                background: #0d1117;
                color: #c9d1d9;
            }
            .message.user { background: #161b22; border-left-color: #58a6ff; }
            .message.assistant { background: #161b22; border-left-color: #3fb950; }
            pre { background: #161b22; border-color: #30363d; }
            code { background: #161b22; color: #e6edf3; }
            .visualization { background: #161b22; border-color: #30363d; }
        }
        .header {
            border-bottom: 2px solid #e1e4e8;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }
        .message {
            margin: 20px 0;
            padding: 15px;
            border-left: 4px solid #0969da;
            background: #f6f8fa;
            border-radius: 6px;
        }
        .message.user { border-left-color: #0969da; }
        .message.assistant { border-left-color: #1a7f37; }
        .message-header {
            font-weight: 600;
            font-size: 16px;
            margin-bottom: 10px;
            color: #0969da;
        }
        .message.assistant .message-header { color: #1a7f37; }
        pre {
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
        }
        code {
            background: #f6f8fa;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 13px;
        }
        .diff-view {
            border: 1px solid #d0d7de;
            border-radius: 6px;
            overflow: hidden;
            margin: 16px 0;
        }
        .visualization {
            border: 1px solid #d0d7de;
            border-radius: 6px;
            padding: 16px;
            margin: 16px 0;
            text-align: center;
            background: #f6f8fa;
        }
        .visualization img,
        .visualization svg {
            max-width: 100%;
            height: auto;
            display: block;
            margin: 0 auto;
        }
        .viz-caption {
            font-size: 14px;
            color: #57606a;
            margin-top: 12px;
            font-style: italic;
        }
        details {
            margin-top: 12px;
        }
        summary {
            cursor: pointer;
            padding: 8px;
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-radius: 4px;
            user-select: none;
        }
        summary:hover {
            background: #e1e4e8;
        }
        .footer {
            margin-top: 40px;
            padding-top: 20px;
            border-top: 2px solid #e1e4e8;
            font-size: 14px;
            color: #57606a;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎯 Ziya Conversation Export</h1>
        <p><strong>Exported:</strong> """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</p>
    </div>
""")
    
    # Process each message
    diagram_counter = 0
    for msg in messages:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        
        # Skip empty or system messages
        if not content or not content.strip() or role == 'system':
            continue
        
        role_class = 'user' if role == 'human' else 'assistant'
        role_emoji = '👤' if role == 'human' else '🤖'
        role_name = 'User' if role == 'human' else 'AI Assistant'
        
        html_parts.append(f'''
    <div class="message {role_class}">
        <div class="message-header">{role_emoji} {role_name}</div>
        <div class="message-content">
''')
        
        # Process content (convert markdown to HTML, embed visualizations)
        processed_content, diagrams_used = _embed_diagrams_in_html(
            content,
            diagram_by_index,
            diagram_counter
        )
        diagram_counter += diagrams_used
        
        html_parts.append(processed_content)
        
        html_parts.append("""
        </div>
    </div>
""")
    
    # Add footer
    html_parts.append(_create_footer(target, version, model, provider, 'html'))
    
    # Close HTML
    html_parts.append("""
</body>
</html>
""")
    
    return "".join(html_parts)


def _embed_diagrams_in_html(
    content: str,
    diagram_by_index: Dict[int, Dict[str, Any]],
    start_index: int
) -> tuple[str, int]:
    """
    Embed captured diagrams directly in HTML content.
    
    Returns:
        Tuple of (processed_content, number_of_diagrams_used)
    """
    diagrams_used = 0
    
    # Convert basic markdown to HTML first
    html = _markdown_to_html_basic(content)
    
    # Find visualization code blocks and replace with embedded diagrams
    viz_pattern = r'<pre><code class="language-(graphviz|mermaid|vega-lite|d3|joint|circuitikz)">(.*?)</code></pre>'
    
    def embed_diagram(match):
        nonlocal diagrams_used
        viz_type = match.group(1)
        source_code = match.group(2)
        
        # Get the corresponding captured diagram
        diagram_idx = start_index + diagrams_used
        diagram = diagram_by_index.get(diagram_idx)
        
        diagrams_used += 1
        
        if diagram and diagram.get('dataUri'):
            data_uri = diagram['dataUri']
            width = diagram.get('width', 600)
            height = diagram.get('height', 400)
            
            # For SVG, we can embed inline for better quality
            if diagram.get('type') == 'svg' and ',' in data_uri:
                # Extract base64 data and decode
                try:
                    svg_base64 = data_uri.split(',')[1]
                    svg_content = base64.b64decode(svg_base64).decode('utf-8')
                    
                    return f'''
<div class="visualization">
    <div class="viz-caption">📊 {viz_type.title()} Visualization</div>
    {svg_content}
    <details>
        <summary>View {viz_type} Source Code</summary>
        <pre><code class="language-{viz_type}">{source_code}</code></pre>
    </details>
</div>
'''
                except Exception as e:
                    # Fallback to image tag if inline fails
                    pass
            
            # Fallback: use img tag with data URI
            return f'''
<div class="visualization">
    <div class="viz-caption">📊 {viz_type.title()} Visualization</div>
    <img src="{data_uri}" alt="{viz_type} diagram" width="{width}" height="{height}"/>
    <details>
        <summary>View {viz_type} Source Code</summary>
        <pre><code class="language-{viz_type}">{source_code}</code></pre>
    </details>
</div>
'''
        else:
            # No captured diagram, show source with warning
            return f'''
<div class="visualization">
    <div class="viz-caption">⚠️ {viz_type.title()} Visualization (not captured)</div>
    <pre><code class="language-{viz_type}">{source_code}</code></pre>
    <p style="font-size: 12px; color: #57606a;">Paste this code into a {viz_type} renderer to view.</p>
</div>
'''
    
    html = re.sub(viz_pattern, embed_diagram, html, flags=re.DOTALL)
    
    return html, diagrams_used


def _markdown_to_html_basic(markdown: str) -> str:
    """Basic markdown to HTML conversion."""
    html = markdown
    
    # Convert code blocks
    def convert_code_block(match):
        lang = match.group(1) or 'text'
        code = match.group(2)
        # Escape HTML in code
        code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'<pre><code class="language-{lang}">{code}</code></pre>'
    
    html = re.sub(r'```(\w+)?\n(.*?)```', convert_code_block, html, flags=re.DOTALL)
    
    # Convert inline code
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    # Convert bold
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    
    # Convert italic
    html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
    
    # Convert links
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    
    # Convert headers
    html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    
    # Convert paragraphs
    paragraphs = html.split('\n\n')
    html = ''.join(
        f'<p>{p.replace(chr(10), "<br>")}</p>\n' 
        if p.strip() and not p.strip().startswith('<') 
        else p + '\n' 
        for p in paragraphs
    )
    
    return html


def _process_visualizations_for_markdown(content: str) -> str:
    """
    Process content to embed visualizations in markdown-compatible format.
    
    For SVG diagrams, we convert to base64 data URIs.
    For other visualizations, we preserve the code blocks.
    """
    # Look for code blocks with visualization types
    viz_pattern = r'```(graphviz|mermaid|vega-lite|d3|joint)\n(.*?)```'
    
    def replace_viz(match):
        viz_type = match.group(1)
        viz_code = match.group(2)
        
        # Keep the code block for reproducibility
        # Add a note that this is a visualization
        return f"```{viz_type}\n{viz_code}\n```\n\n> 📊 *This is a {viz_type} visualization. Paste into a markdown viewer that supports {viz_type} rendering.*\n"
    
    content = re.sub(viz_pattern, replace_viz, content, flags=re.DOTALL)
    
    return content


def _process_content_for_html(content: str) -> str:
    """
    Process markdown content and convert to HTML.
    Handles code blocks, diffs, and inline formatting.
    """
    # Simple markdown to HTML conversion
    # This is a basic implementation - you may want to use a library like markdown2
    
    html = content
    
    # Convert code blocks
    def convert_code_block(match):
        lang = match.group(1) or 'text'
        code = match.group(2)
        # Escape HTML in code
        code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'<pre><code class="language-{lang}">{code}</code></pre>'
    
    html = re.sub(r'```(\w+)?\n(.*?)```', convert_code_block, html, flags=re.DOTALL)
    
    # Convert inline code
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    # Convert bold
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    
    # Convert italic
    html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
    
    # Convert links
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    
    # Convert paragraphs (double newlines)
    paragraphs = html.split('\n\n')
    html = ''.join(f'<p>{p.strip()}</p>\n' if p.strip() and not p.strip().startswith('<') else p + '\n' for p in paragraphs)
    
    # Process visualizations
    html = _embed_visualizations_in_html(html)
    
    return html


def _embed_visualizations_in_html(html: str) -> str:
    """
    Embed visualizations directly in HTML.
    For SVGs and other diagrams, we embed them inline or as data URIs.
    """
    # Look for visualization code blocks and convert them to embedded SVGs
    # This is a placeholder - actual implementation would render the viz
    
    viz_pattern = r'<pre><code class="language-(graphviz|mermaid|vega-lite)">(.*?)</code></pre>'
    
    def embed_viz(match):
        viz_type = match.group(1)
        viz_code = match.group(2)
        
        # For now, keep as code block but add a note
        # In production, you'd want to render these server-side
        return f'''
<div class="visualization">
    <p><em>📊 {viz_type.title()} Visualization</em></p>
    <details>
        <summary>View {viz_type} Source</summary>
        <pre><code class="language-{viz_type}">{viz_code}</code></pre>
    </details>
</div>
'''
    
    html = re.sub(viz_pattern, embed_viz, html, flags=re.DOTALL)
    
    return html


def _create_footer(
    target: str,
    version: str,
    model: str,
    provider: str,
    format_type: str
) -> str:
    """Create footer with metadata and links."""
    
    # Default to public URLs
    ziya_url = "https://github.com/ziya-ai/ziya"
    repo_url = "https://github.com/ziya-ai/ziya"
    
    # Try to get URLs from active config provider (allows internal customization)
    try:
        from app.plugins import get_active_config_providers
        from app.utils.logging_utils import logger
        
        config_providers = get_active_config_providers()
        for provider in config_providers:
            try:
                provider_defaults = provider.get_defaults()
                if 'urls' in provider_defaults:
                    urls = provider_defaults['urls']
                    if 'ziya_url' in urls:
                        ziya_url = urls['ziya_url']
                    if 'repo_url' in urls:
                        repo_url = urls['repo_url']
                    logger.debug(f"Using URLs from {provider.provider_id} config provider")
                    break
            except Exception as e:
                logger.debug(f"Error getting URLs from provider: {e}")
    except ImportError:
        # Plugin system not available, use defaults
        ziya_url = "https://github.com/ziya-ai/ziya"
        repo_url = ziya_url
    except Exception as e:
        logger.debug(f"Could not get URLs from config providers: {e}")
    
    if format_type == 'html':
        return f'''
    <div class="footer">
        <p><strong>Generated by Ziya v{version}</strong></p>
        <p>Model: <code>{model}</code> | Provider: <code>{provider}</code></p>
        <p>Learn more: <a href="{ziya_url}">{ziya_url}</a></p>
        <p><em>This conversation was exported from Ziya, an AI-powered code assistant.</em></p>
    </div>
'''
    else:  # markdown
        return f"""
---

## 📋 Export Metadata

**Generated by:** Ziya v{version}  
**Model:** `{model}`  
**Provider:** `{provider}`  
**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Learn more about Ziya:** [{ziya_url}]({ziya_url})

*This conversation was exported from Ziya, an AI-powered code assistant that helps developers write, understand, and modify code with context-aware intelligence.*
"""


def _process_visualizations_for_markdown(content: str) -> str:
    """
    Process content to embed visualizations in markdown.
    
    Strategy:
    1. Keep original code blocks for reproducibility
    2. Add rendering hints for paste services
    """
    
    # Detect visualization code blocks
    viz_types = ['graphviz', 'mermaid', 'vega-lite', 'd3', 'joint', 'circuitikz']
    
    for viz_type in viz_types:
        pattern = f'```{viz_type}\\n(.*?)```'
        
        def add_viz_note(match):
            code = match.group(1)
            return f"""```{viz_type}
{code}
```

> 📊 **Visualization:** This is a {viz_type} diagram. To view:
> - GitHub Gist: Will render automatically if supported
> - Other viewers: Copy the code above to a {viz_type} renderer

"""
        
        content = re.sub(pattern, add_viz_note, content, flags=re.DOTALL)
    
    return content


def extract_svg_from_content(content: str) -> List[str]:
    """Extract SVG elements from content."""
    svg_pattern = r'<svg[^>]*>.*?</svg>'
    return re.findall(svg_pattern, content, flags=re.DOTALL)


def svg_to_data_uri(svg_content: str) -> str:
    """Convert SVG to data URI for embedding."""
    # Encode SVG as base64
    svg_bytes = svg_content.encode('utf-8')
    svg_base64 = base64.b64encode(svg_bytes).decode('utf-8')
    return f"data:image/svg+xml;base64,{svg_base64}"


def _process_content_for_html(content: str) -> str:
    """
    Process markdown content and convert to HTML with embedded visualizations.
    """
    # Convert markdown to HTML (basic implementation)
    html = content
    
    # Extract and embed SVGs
    svgs = extract_svg_from_content(content)
    for svg in svgs:
        # Keep SVG inline for HTML export
        html = html.replace(svg, f'<div class="visualization">{svg}</div>')
    
    # Convert code blocks with syntax highlighting hints
    def convert_code_block(match):
        lang = match.group(1) or 'text'
        code = match.group(2)
        
        # Check if this is a diff
        if lang == 'diff' or code.strip().startswith('diff --git'):
            escaped_code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            return f'<div class="diff-view"><pre><code class="language-diff">{escaped_code}</code></pre></div>'
        
        # Check if this is a visualization
        if lang in ['graphviz', 'mermaid', 'vega-lite', 'd3', 'joint']:
            escaped_code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            return f'''
<div class="visualization">
    <p><em>📊 {lang.title()} Visualization</em></p>
    <details>
        <summary>View Source Code</summary>
        <pre><code class="language-{lang}">{escaped_code}</code></pre>
    </details>
</div>
'''
        
        escaped_code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'<pre><code class="language-{lang}">{escaped_code}</code></pre>'
    
    html = re.sub(r'```(\w+)?\n(.*?)```', convert_code_block, html, flags=re.DOTALL)
    
    # Convert inline code
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    # Convert bold
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    
    # Convert italic  
    html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
    
    # Convert links
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    
    # Convert headers
    html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    
    # Convert line breaks to <br> for single newlines, <p> for double
    paragraphs = html.split('\n\n')
    html = ''.join(
        f'<p>{p.replace(chr(10), "<br>")}</p>\n' 
        if p.strip() and not p.strip().startswith('<') 
        else p + '\n' 
        for p in paragraphs
    )
    
    return html
