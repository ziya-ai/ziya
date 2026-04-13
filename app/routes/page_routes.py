"""
Page/UI routes (HTML pages, favicon, etc).

Extracted from server.py during Phase 3b refactoring.
"""
import os
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.utils.logging_utils import logger
from app.agents.models import ModelManager

router = APIRouter(tags=["pages"])

# Templates setup - reuse server.py's template directory
def _get_templates():
    """Lazy template loader to avoid circular imports."""
    from app.server import templates_dir
    return Jinja2Templates(directory=templates_dir)


@router.get("/")
async def root(request: Request):
    try:
        # Get formatter scripts from plugins
        formatter_scripts = []
        from app.plugins import get_active_config_providers
        for provider in get_active_config_providers():
            config = provider.get_defaults()
            if 'frontend' in config and 'formatters' in config['frontend']:
                formatter_scripts.extend(config['frontend']['formatters'])
        
        # Deduplicate while preserving order — duplicate script tags cause
        # fatal SyntaxError from const redeclaration in formatter JS files
        formatter_scripts = list(dict.fromkeys(formatter_scripts))
        # Log detailed information about templates
        logger.info(f"Rendering index.html using custom template loader")
        
        # Create the context for the template
        context = {
            "request": request,
            "diff_view_type": os.environ.get("ZIYA_DIFF_VIEW_TYPE", "unified"),
            "api_path": "/ziya",
            "formatter_scripts": formatter_scripts or []  # Ensure always a list, never None
        }
        
        # Try to render the template
        return _get_templates().TemplateResponse("index.html", context)
    except Exception as e:
        logger.error(f"Error rendering index.html: {str(e)}")
        # Return a simple HTML response as fallback
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Ziya</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                h1 { color: #333; }
                .container { max-width: 800px; margin: 0 auto; }
                .error { color: #721c24; background-color: #f8d7da; padding: 10px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Ziya</h1>
                <div class="error">
                    <p>Error loading template. Please check server logs.</p>
                    <p>Error details: """ + str(e).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') + """</p>
                </div>
                <p>Please ensure that the templates directory is properly included in the package.</p>
            </div>
        </body>
        </html>
        """
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_content)


@router.get("/render")
async def render_page(request: Request):
    """Serve the SPA shell for the /render route.

    React Router handles client-side routing to DiagramRenderPage.
    This catch-through is required so Playwright (and browsers navigating
    directly) get index.html instead of a 404.
    """
    return await root(request)


@router.get("/info")
async def info_page(request: Request):
    """Render the info page as part of the React app."""
    try:
        # Check if this is a request for the telemetry dashboard
        
        # Get formatter scripts from plugins
        formatter_scripts = []
        from app.plugins import get_active_config_providers
        for provider in get_active_config_providers():
            config = provider.get_defaults()
            if 'frontend' in config and 'formatters' in config['frontend']:
                formatter_scripts.extend(config['frontend']['formatters'])
        
        context = {"request": request, "formatter_scripts": formatter_scripts, "info_page": True}
        return _get_templates().TemplateResponse("index.html", context)
    except Exception as e:
        logger.error(f"Error rendering info page: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/debug2")
async def debug_page_old(request: Request):
    """Legacy route - renders full HTML info page."""
    try:
        import platform
        import sys
        from app.utils.version_util import get_current_version, get_build_info
        
        # Get all the system information
        edition = "Community Edition"
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            if _initialized:
                for provider in _config_providers:
                    if hasattr(provider, 'get_defaults'):
                        config = provider.get_defaults()
                        if 'branding' in config and 'edition' in config['branding']:
                            edition = config['branding']['edition']
                            break
        except Exception as e:
            logger.warning(f"Could not get edition info: {e}")
        
        # Build the HTML content
        html_parts = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '    <title>Ziya System Information</title>',
            '    <meta charset="UTF-8">',
            '    <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            '            <style>',
            '        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; line-height: 1.6; }',
            '        body { overflow: auto !important; position: static !important; height: auto !important; }',
            '        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }',
            '        h1 { color: #333; border-bottom: 3px solid #4a90e2; padding-bottom: 10px; margin-top: 0; }',
            '        h2 { color: #4a90e2; margin-top: 30px; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px; }',
            '        .info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 20px 0; }',
            '        .info-card { background: #f9f9f9; padding: 15px; border-radius: 5px; border-left: 4px solid #4a90e2; }',
            '        .info-card h3 { margin-top: 0; color: #333; font-size: 16px; }',
            '        .info-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #e0e0e0; }',
            '        .info-row:last-child { border-bottom: none; }',
            '        .info-label { font-weight: 600; color: #666; }',
            '        .info-value { color: #333; text-align: right; word-break: break-all; max-width: 60%; }',
            '        .status-badge { display: inline-block; padding: 3px 8px; border-radius: 3px; font-size: 12px; font-weight: 600; }',
            '        .status-valid { background: #d4edda; color: #155724; }',
            '        .status-error { background: #f8d7da; color: #721c24; }',
            '        .status-warning { background: #fff3cd; color: #856404; }',
            '        .plugin-list { list-style: none; padding: 0; }',
            '        .plugin-item { padding: 8px; margin: 5px 0; background: white; border-radius: 3px; display: flex; justify-content: space-between; align-items: center; }',
            '        .plugin-active { border-left: 3px solid #28a745; }',
            '        .env-vars { font-family: "Courier New", monospace; font-size: 14px; background: #2d2d2d; color: #f8f8f2; padding: 15px; border-radius: 5px; overflow-x: auto; }',
            '        .env-var { margin: 5px 0; }',
            '        .env-key { color: #66d9ef; }',
            '        .env-value { color: #a6e22e; }',
            '        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: "Courier New", monospace; }',
            '    </style>',
            '</head>',
            '<body>',
            '    <div class="container">',
            f'        <h1>🔧 Ziya System Information</h1>',
            f'        <p><strong>Edition:</strong> {edition} • <strong>Version:</strong> {get_current_version()}</p>',
        ]
        
        # Version Information
        html_parts.extend([
            '        <h2>📦 Version Information</h2>',
            '        <div class="info-grid">',
            '            <div class="info-card">',
            '                <h3>Runtime</h3>',
            f'                <div class="info-row"><span class="info-label">Python Version:</span><span class="info-value">{sys.version.split()[0]}</span></div>',
            f'                <div class="info-row"><span class="info-label">Python Executable:</span><span class="info-value"><code>{sys.executable}</code></span></div>',
            f'                <div class="info-row"><span class="info-label">Platform:</span><span class="info-value">{platform.platform()}</span></div>',
            '            </div>',
        ])
        
        # Directories
        html_parts.extend([
            '            <div class="info-card">',
            '                <h3>Directories</h3>',
            f'                <div class="info-row"><span class="info-label">Root Directory:</span><span class="info-value"><code>{os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())}</code></span></div>',
            f'                <div class="info-row"><span class="info-label">Working Directory:</span><span class="info-value"><code>{os.getcwd()}</code></span></div>',
            '            </div>',
            '        </div>',
        ])
        
        # Client Information
        html_parts.extend([
            '        <h2>💻 Client Information</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">User Agent:</span><span class="info-value">{request.headers.get("user-agent", "Unknown")}</span></div>',
            f'            <div class="info-row"><span class="info-label">Remote Address:</span><span class="info-value">{request.client.host if request.client else "Unknown"}</span></div>',
            '        </div>',
        ])
        
        # Model Configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
        # Get current model from ModelManager instead of just env var
        try:
            current_model = ModelManager.get_model_alias()
            model_id = ModelManager.get_model_id()
            if isinstance(model_id, dict):
                # Format multi-region model IDs nicely
                model_id_display = ', '.join(f"{k}: {v}" for k, v in model_id.items())
            else:
                model_id_display = str(model_id)
        except Exception as e:
            logger.warning(f"Could not get current model from ModelManager: {e}")
            current_model = os.environ.get("ZIYA_MODEL", "Not set")
            model_id_display = "Unknown"
        
        html_parts.extend([
            '        <h2>🤖 Model Configuration</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">Endpoint:</span><span class="info-value"><strong>{endpoint}</strong></span></div>',
            f'            <div class="info-row"><span class="info-label">Model:</span><span class="info-value"><strong>{current_model}</strong></span></div>',
            f'            <div class="info-row"><span class="info-label">Model ID:</span><span class="info-value"><code>{model_id_display}</code></span></div>',
        ])
        
        # AWS/Google Configuration
        if endpoint == "bedrock":
            import boto3
            profile = os.environ.get('ZIYA_AWS_PROFILE') or os.environ.get('AWS_PROFILE', 'default')
            region = os.environ.get('AWS_REGION', 'us-west-2')
            html_parts.extend([
                f'            <div class="info-row"><span class="info-label">AWS Profile:</span><span class="info-value">{profile}</span></div>',
                f'            <div class="info-row"><span class="info-label">AWS Region:</span><span class="info-value">{region}</span></div>',
            ])
            
            try:
                session = boto3.Session(profile_name=profile, region_name=region)
                credentials = session.get_credentials()
                if credentials:
                    try:
                        sts = session.client('sts', region_name=region)
                        identity = sts.get_caller_identity()
                        html_parts.append(f'            <div class="info-row"><span class="info-label">AWS Account:</span><span class="info-value">{identity["Account"]}</span></div>')
                        html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-valid">✓ Valid</span></span></div>')
                    except Exception as e:
                        error_msg = str(e)
                        if 'ExpiredToken' in error_msg:
                            html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Expired</span></span></div>')
                        else:
                            html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Error</span></span></div>')
                else:
                    html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Not found</span></span></div>')
            except Exception:
                html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Error</span></span></div>')
        elif endpoint == "google":
            api_key = os.environ.get('GOOGLE_API_KEY')
            status = '✓ Set' if api_key else '✗ Not set'
            badge_class = 'status-valid' if api_key else 'status-error'
            html_parts.append(f'            <div class="info-row"><span class="info-label">API Key:</span><span class="info-value"><span class="status-badge {badge_class}">{status}</span></span></div>')
        
        html_parts.append('        </div>')
        
        # MCP Information
        mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes")
        html_parts.extend([
            '        <h2>🔧 MCP Servers and Tools</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">MCP Enabled:</span><span class="info-value">{"✓ Yes" if mcp_enabled else "✗ No"}</span></div>',
        ])
        
        if mcp_enabled:
            try:
                from app.mcp.manager import get_mcp_manager
                mcp_manager = get_mcp_manager()
                
                if mcp_manager.is_initialized:
                    status = mcp_manager.get_server_status()
                    connected_servers = sum(1 for s in status.values() if s["connected"])
                    total_tools = sum(s["tools"] for s in status.values())
                    
                    html_parts.extend([
                        f'            <div class="info-row"><span class="info-label">Initialized:</span><span class="info-value"><span class="status-badge status-valid">✓ Yes</span></span></div>',
                        f'            <div class="info-row"><span class="info-label">Connected Servers:</span><span class="info-value"><strong>{connected_servers}</strong> / {len(status)}</span></div>',
                        f'            <div class="info-row"><span class="info-label">Total Tools:</span><span class="info-value"><strong>{total_tools}</strong></span></div>',
                    ])
                    
                    # List each server with its tools
                    html_parts.append('        </div>')
                    for server_name, server_info in status.items():
                        is_connected = server_info["connected"]
                        tool_count = server_info["tools"]
                        status_class = 'status-valid' if is_connected else 'status-error'
                        status_text = '✓ Connected' if is_connected else '✗ Disconnected'
                        
                        html_parts.extend([
                            '        <div class="info-card">',
                            f'            <h3>{server_name}</h3>',
                            f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge {status_class}">{status_text}</span></span></div>',
                            f'            <div class="info-row"><span class="info-label">Tools:</span><span class="info-value">{tool_count}</span></div>',
                            '        </div>',
                        ])
                else:
                    html_parts.append(f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge status-warning">Not initialized</span></span></div>')
                    html_parts.append('        </div>')
            except Exception as e:
                html_parts.append(f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge status-error">Error: {str(e)}</span></span></div>')
                html_parts.append('        </div>')
        else:
            html_parts.append(f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge status-warning">Disabled</span></span></div>')
            html_parts.append('        </div>')
        
        # Feature Flags
        ast_enabled = os.environ.get("ZIYA_ENABLE_AST", "false").lower() in ("true", "1", "yes")
        mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes")
        ephemeral = os.environ.get("ZIYA_EPHEMERAL_MODE", "false").lower() in ("true", "1", "yes")
        
        html_parts.extend([
            '        <h2>⚙️ Feature Flags</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">AST Analysis:</span><span class="info-value">{"✓ Enabled" if ast_enabled else "✗ Disabled"}</span></div>',
            f'            <div class="info-row"><span class="info-label">MCP Tools:</span><span class="info-value">{"✓ Enabled" if mcp_enabled else "✗ Disabled"}</span></div>',
            f'            <div class="info-row"><span class="info-label">Ephemeral Mode:</span><span class="info-value">{"✓ Enabled" if ephemeral else "✗ Disabled"}</span></div>',
            '        </div>',
        ])
        
        # Plugins
        html_parts.extend([
            '        <h2>🔌 Plugins</h2>',
        ])
        
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            
            if _initialized:
                active_auth = get_active_auth_provider()
                
                # Auth Providers
                html_parts.extend([
                    '        <div class="info-card">',
                    f'            <h3>Authentication Providers ({len(_auth_providers)})</h3>',
                    '            <ul class="plugin-list">',
                ])
                for p in _auth_providers:
                    provider_id = getattr(p, 'provider_id', 'unknown')
                    is_active = p == active_auth
                    active_class = ' plugin-active' if is_active else ''
                    active_badge = '<span class="status-badge status-valid">Active</span>' if is_active else ''
                    html_parts.append(f'                <li class="plugin-item{active_class}">{provider_id} {active_badge}</li>')
                html_parts.extend([
                    '            </ul>',
                    '        </div>',
                ])
                
                # Config Providers
                html_parts.extend([
                    '        <div class="info-card">',
                    f'            <h3>Configuration Providers ({len(_config_providers)})</h3>',
                    '            <ul class="plugin-list">',
                ])
                for p in _config_providers:
                    provider_id = getattr(p, 'provider_id', 'unknown')
                    html_parts.append(f'                <li class="plugin-item">{provider_id}</li>')
                html_parts.extend([
                    '            </ul>',
                    '        </div>',
                ])
                
                # Registry Providers
                html_parts.extend([
                    '        <div class="info-card">',
                    f'            <h3>Registry Providers ({len(_registry_providers)})</h3>',
                    '            <ul class="plugin-list">',
                ])
                for p in _registry_providers:
                    provider_id = getattr(p, 'identifier', 'unknown')
                    html_parts.append(f'                <li class="plugin-item">{provider_id}</li>')
                html_parts.extend([
                    '            </ul>',
                    '        </div>',
                ])
                
                # Formatter Providers (populated by JavaScript)
                html_parts.extend([
                    '        <div class="info-card">',
                    '            <h3>Formatter Providers <span id="formatter-count" style="opacity: 0.7;"></span></h3>',
                    '            <ul class="plugin-list" id="formatter-list">',
                    '                <li style="opacity: 0.6;">Loading...</li>',
                    '            </ul>',
                    '        </div>',
                ])
        except Exception as e:
            logger.warning(f"Could not get plugin info: {e}")
        # Environment Variables
        ziya_vars = {k: v for k, v in os.environ.items() if k.startswith('ZIYA_')}
        html_parts.extend([
            '        <h2>🌍 Environment Variables</h2>',
            '        <div class="env-vars">',
        ])
        for key, value in sorted(ziya_vars.items()):
            # Mask sensitive values
            if 'KEY' in key or 'SECRET' in key or 'TOKEN' in key:
                display_value = value[:8] + '...' if len(value) > 8 else '***'
            else:
                display_value = value
            html_parts.append(f'            <div class="env-var"><span class="env-key">{key}</span>=<span class="env-value">{display_value}</span></div>')
        
        html_parts.extend([
            '        </div>',
            '    <script>',
            '        // Populate formatter info from frontend registry',
            '        window.addEventListener("load", function() {',
            '            setTimeout(function() {',
            '                if (window.FormatterRegistry) {',
            '                    const formatters = window.FormatterRegistry.getAllFormatters();',
            '                    const countSpan = document.getElementById("formatter-count");',
            '                    const listEl = document.getElementById("formatter-list");',
            '                    ',
            '                    if (countSpan) countSpan.textContent = "(" + formatters.length + ")";',
            '                    ',
            '                    if (listEl) {',
            '                        listEl.innerHTML = "";',
            '                        formatters.forEach(function(f) {',
            '                            var li = document.createElement("li");',
            '                            li.className = "plugin-item";',
            '                            li.innerHTML = f.formatterId + " <span style=\\"opacity: 0.7; font-size: 11px;\\">(priority: " + f.priority + ")</span>";',
            '                            listEl.appendChild(li);',
            '                        });',
            '                        if (formatters.length === 0) {',
            '                            listEl.innerHTML = "<li style=\\"opacity: 0.6;\\">No formatters registered</li>";',
            '                        }',
            '                    }',
            '                } else {',
            '                    document.getElementById("formatter-list").innerHTML = "<li style=\\"opacity: 0.6; color: #ff4d4f;\\">FormatterRegistry not available</li>";',
            '                }',
            '            }, 100);',
            '        });',
            '    </script>',
            '    </div>',
            '</body>',
            '</html>',
        ])
        
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content='\n'.join(html_parts))
        
    except Exception as e:
        logger.error(f"Error rendering info page: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/debug1")

async def debug(request: Request):
    # Return the same app but with a query parameter to show debug mode
    return _get_templates().TemplateResponse("index.html", {
        "request": request,
        "debug_mode": True
    })


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Look for favicon in the templates directory
    try:
        from app.server import templates_dir
        favicon_path = os.path.join(templates_dir, "favicon.ico")
        if os.path.exists(favicon_path):
            logger.info(f"Serving favicon from: {favicon_path}")
            return FileResponse(favicon_path)
    except Exception as e:
        logger.warning(f"Error finding favicon: {e}")
    
    logger.warning("Favicon not found in any location")
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Favicon not found")

