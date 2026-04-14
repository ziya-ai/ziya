"""
Debug and telemetry routes.

Extracted from server.py during Phase 3b refactoring.
"""
import os
import time
import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any

from app.utils.logging_utils import logger
from app.agents.models import ModelManager

router = APIRouter(tags=["debug"])

@router.get('/api/debug/mcp-state')
async def debug_mcp_state():
    """Debug endpoint to check MCP connection and tool execution state."""
    try:
        from app.mcp.manager import get_mcp_manager
        from app.mcp.tools import _tool_execution_counter, _consecutive_timeouts, _conversation_tool_states
        
        mcp_manager = get_mcp_manager()
        
        # Check manager state
        manager_state = {
            "is_initialized": mcp_manager.is_initialized,
            "client_count": len(mcp_manager.clients),
            "clients": {}
        }
        
        # Check each client's state
        for server_name, client in mcp_manager.clients.items():
            try:
                # Check process health
                process_healthy = client._is_process_healthy() if hasattr(client, '_is_process_healthy') else True
                
                manager_state["clients"][server_name] = {
                    "is_connected": client.is_connected,
                    "process_healthy": process_healthy,
                    "process_running": client.process and client.process.poll() is None,
                    "tools_count": len(client.tools),
                    "last_successful_call": getattr(client, '_last_successful_call', 0)
                }
            except Exception as e:
                manager_state["clients"][server_name] = {"error": str(e)}
        
        return {
            "manager": manager_state,
            "global_tool_counter": _tool_execution_counter,
            "consecutive_timeouts": _consecutive_timeouts,
            "conversation_states": _conversation_tool_states
        }
    except Exception as e:
        logger.error(f"Error getting MCP debug state: {e}")
        return {"error": str(e)}

@router.get('/api/info')
async def get_system_info(request: Request):
    """Get comprehensive system information and configuration for debugging."""
    try:
        import platform
        import sys
        from app.utils.version_util import get_current_version
        from app.utils.version_util import get_build_info
        
        info = {}
        
        # Edition from plugins
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
        
        # Version and platform information
        info['version'] = {
            'edition': edition,
            'ziya_version': get_current_version(),
            'build_info': get_build_info(),
            'python_version': sys.version.split()[0],
            'python_executable': sys.executable,
            'platform': platform.platform()
        }
        
        # Root directory information
        info['directories'] = {
            'root': os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd()),
            'templates': os.environ.get("ZIYA_TEMPLATES_DIR", "Not set"),
            'current_working_directory': os.getcwd()
        }
        
        # User agent from request headers
        info['client'] = {
            'user_agent': request.headers.get('user-agent', 'Unknown'),
            'remote_addr': request.client.host if request.client else 'Unknown'
        }
        
        # Plugin information
        info['plugins'] = {}
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            
            if _initialized:
                # Auth providers
                info['plugins']['auth_providers'] = {
                    'count': len(_auth_providers),
                    'providers': []
                }
                active_auth = get_active_auth_provider()
                for p in _auth_providers:
                    provider_id = getattr(p, 'provider_id', 'unknown')
                    is_active = p == active_auth
                    info['plugins']['auth_providers']['providers'].append({
                        'id': provider_id,
                        'active': is_active
                    })
                
                # Config providers
                info['plugins']['config_providers'] = {
                    'count': len(_config_providers),
                    'providers': [getattr(p, 'provider_id', 'unknown') for p in _config_providers]
                }
                
                # Registry providers
                info['plugins']['registry_providers'] = {
                    'count': len(_registry_providers),
                    'providers': [getattr(p, 'identifier', 'unknown') for p in _registry_providers]
                }
                
                # Check for enterprise formatter files
                import glob
                static_dir = os.path.join(os.path.dirname(__file__), 'templates', 'static', 'js')
                formatter_files = glob.glob(os.path.join(static_dir, '*[Ff]ormatter*.js')) if os.path.exists(static_dir) else []
                info['plugins']['enterprise_formatters'] = {
                    'count': len(formatter_files),
                    'files': [os.path.basename(f) for f in formatter_files]
                }
        except Exception as e:
            logger.warning(f"Could not get plugin info: {e}")
            info['plugins']['error'] = str(e)
        
        # Frontend Formatter Registry (from plugins)
        info['formatters'] = {}
        try:
            from app.plugins import get_formatter_providers
            
            formatter_providers = get_formatter_providers()
            info['formatters'] = {
                'count': len(formatter_providers),
                'providers': [{'id': p.formatter_id, 'priority': p.priority} for p in formatter_providers]
            }
        except Exception as e:
            logger.warning(f"Could not get formatter info: {e}")
            info['formatters']['error'] = str(e)
        
        # Endpoint and model configuration
        info['model'] = {
            'endpoint': os.environ.get("ZIYA_ENDPOINT", "bedrock"),
            'model': os.environ.get("ZIYA_MODEL", "Not set"),
            'model_id_override': os.environ.get("ZIYA_MODEL_ID_OVERRIDE")
        }
        
        # Get current model details
        try:
            model_alias = ModelManager.get_model_alias()
            model_id = ModelManager.get_model_id()
            info['model']['current_alias'] = model_alias
            info['model']['current_id'] = model_id
        except Exception as e:
            logger.warning(f"Could not get current model details: {e}")
        
        # AWS configuration (if using Bedrock)
        if info['model']['endpoint'] == "bedrock":
            import boto3
            info['aws'] = {
                'profile': os.environ.get('ZIYA_AWS_PROFILE') or os.environ.get('AWS_PROFILE', 'default'),
                'region': os.environ.get('AWS_REGION', 'us-west-2')
            }
            
            try:
                session = boto3.Session(
                    profile_name=info['aws']['profile'],
                    region_name=info['aws']['region']
                )
                credentials = session.get_credentials()
                if credentials:
                    try:
                        sts = session.client('sts', region_name=info['aws']['region'])
                        identity = sts.get_caller_identity()
                        info['aws']['account_id'] = identity['Account']
                        info['aws']['access_key'] = credentials.access_key[:8] + '...'
                        info['aws']['status'] = 'Valid'
                    except Exception as sts_error:
                        error_msg = str(sts_error)
                        if 'ExpiredToken' in error_msg:
                            info['aws']['access_key'] = credentials.access_key[:8] + '...'
                            info['aws']['status'] = 'Expired'
                        elif 'InvalidClientTokenId' in error_msg:
                            info['aws']['status'] = 'Invalid credentials'
                        else:
                            info['aws']['status'] = f'Error: {error_msg[:80]}'
                else:
                    info['aws']['status'] = 'No credentials found'
            except Exception as e:
                info['aws']['status'] = f'Error: {str(e)[:80]}'
        
        # Google configuration (if using Google)
        elif info['model']['endpoint'] == "google":
            api_key = os.environ.get('GOOGLE_API_KEY')
            info['google'] = {
                'api_key_set': bool(api_key),
                'api_key_masked': api_key[:8] + '...' if api_key else None
            }
        
        # Feature flags
        info['features'] = {
            'ast_enabled': os.environ.get("ZIYA_ENABLE_AST", "false").lower() in ("true", "1", "yes"),
            'ast_resolution': os.environ.get("ZIYA_AST_RESOLUTION", "medium"),
            'mcp_enabled': os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"),
            'ephemeral_mode': os.environ.get("ZIYA_EPHEMERAL_MODE", "false").lower() in ("true", "1", "yes")
        }
        
        # MCP Registry status
        if info['features']['mcp_enabled']:
            try:
                import subprocess
                result = subprocess.run(['which', 'mcp-registry'], capture_output=True, text=True)
                info['features']['mcp_registry_installed'] = result.returncode == 0
            except Exception:
                info['features']['mcp_registry_installed'] = False
        
        # ZIYA environment variables
        ziya_vars = {k: v for k, v in os.environ.items() if k.startswith('ZIYA_')}
        info['environment_variables'] = {}
        for key, value in sorted(ziya_vars.items()):
            # Mask sensitive values
            if 'KEY' in key or 'SECRET' in key or 'TOKEN' in key:
                info['environment_variables'][key] = value[:8] + '...' if len(value) > 8 else '***'
            else:
                info['environment_variables'][key] = value
        
        return info
        
    except Exception as e:
        logger.error(f"Error getting system info: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post('/api/debug/reset-mcp')
async def reset_mcp_state(request: Request):
    """Reset MCP state to recover from stuck tool execution."""
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id")
        
        from app.mcp.manager import get_mcp_manager
        from app.mcp.enhanced_tools import _reset_counter_async
        
        mcp_manager = get_mcp_manager()
        
        # Reset global state (includes conversation-specific state)
        await _reset_counter_async()
        
        # Force reconnection to all MCP servers
        for server_name, client in mcp_manager.clients.items():
            if not client._is_process_healthy():
                logger.info(f"Reconnecting unhealthy MCP server: {server_name}")
                asyncio.create_task(mcp_manager._ensure_client_healthy(client))
        
        return {"status": "success", "message": "MCP state reset initiated"}
        
    except Exception as e:
        logger.error(f"Error resetting MCP state: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get('/api/telemetry/cache-health')
async def get_cache_health_telemetry():
    """Get real-time cache health and efficiency metrics."""
    try:
        from app.streaming_tool_executor import get_global_usage_tracker
        
        tracker = get_global_usage_tracker()
        
        # Get current conversation metrics
        current_conversation = None
        
        # Get all tracked conversations
        all_conversations = tracker.get_all_conversations()
        
        # Calculate global statistics
        global_stats = {
            'total_conversations': len(all_conversations),
            'total_fresh_tokens': 0,
            'total_cached_tokens': 0,
            'total_output_tokens': 0,
            'total_throttle_events': 0,
            'conversations_with_cache_issues': 0
        }
        
        conversation_details = []
        
        for conv_id, usages in all_conversations.items():
            if not usages:
                continue
            
            # Aggregate metrics for this conversation
            conv_metrics = {
                'conversation_id': conv_id,
                'iteration_count': len(usages),
                'fresh_tokens': sum(u.input_tokens for u in usages),
                'cached_tokens': sum(u.cache_read_tokens for u in usages),
                'output_tokens': sum(u.output_tokens for u in usages),
                'cache_created': sum(u.cache_write_tokens for u in usages),
                'throttle_count': sum(1 for u in usages if getattr(u, 'was_throttled', False)),
                'timestamp': max(getattr(u, 'timestamp', 0) for u in usages) if usages else 0
            }
            
            # Calculate efficiency
            total_input = conv_metrics['fresh_tokens'] + conv_metrics['cached_tokens']
            conv_metrics['cache_efficiency'] = (
                (conv_metrics['cached_tokens'] / total_input * 100) if total_input > 0 else 0
            )
            
            # Detect issues
            cache_issue = (
                len(usages) > 1 and  # Multi-iteration conversation
                conv_metrics['cached_tokens'] == 0 and  # No cache reads
                conv_metrics['fresh_tokens'] > 50000  # Significant token usage
            )
            
            conv_metrics['has_cache_issue'] = cache_issue
            
            # Update global stats
            global_stats['total_fresh_tokens'] += conv_metrics['fresh_tokens']
            global_stats['total_cached_tokens'] += conv_metrics['cached_tokens']
            global_stats['total_output_tokens'] += conv_metrics['output_tokens']
            global_stats['total_throttle_events'] += conv_metrics['throttle_count']
            if cache_issue:
                global_stats['conversations_with_cache_issues'] += 1
            
            conversation_details.append(conv_metrics)
        
        # Calculate global cache efficiency
        total_global_input = global_stats['total_fresh_tokens'] + global_stats['total_cached_tokens']
        global_stats['overall_cache_efficiency'] = (
            (global_stats['total_cached_tokens'] / total_global_input * 100) 
            if total_global_input > 0 else 0
        )
        
        # Calculate cost savings
        global_stats['estimated_cost_savings_pct'] = global_stats['overall_cache_efficiency']
        
        # Sort conversations by most recent
        conversation_details.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return {
            'status': 'success',
            'timestamp': int(time.time() * 1000),
            'global_stats': global_stats,
            'conversations': conversation_details[:20],  # Most recent 20
            'health_summary': {
                'cache_working': global_stats['conversations_with_cache_issues'] == 0,
                'issues_detected': global_stats['conversations_with_cache_issues'],
                'throttle_pressure': 'high' if global_stats['total_throttle_events'] > 5 else 
                                    'medium' if global_stats['total_throttle_events'] > 0 else 'low'
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting cache health telemetry: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get('/api/telemetry/current-conversation')
async def get_current_conversation_telemetry(conversation_id: str):
    """Get detailed telemetry for a specific conversation."""
    try:
        from app.streaming_tool_executor import get_global_usage_tracker
        
        tracker = get_global_usage_tracker()
        usages = tracker.get_conversation_usages(conversation_id)
        
        if not usages:
            return {'status': 'no_data', 'conversation_id': conversation_id}
        
        # Build detailed iteration breakdown
        iterations = []
        for i, usage in enumerate(usages):
            iterations.append({
                'iteration': i,
                'fresh_tokens': usage.input_tokens,
                'cached_tokens': usage.cache_read_tokens,
                'output_tokens': usage.output_tokens,
                'cache_efficiency': f"{usage.cache_hit_rate * 100:.1f}%",
                'was_throttled': getattr(usage, 'was_throttled', False),
                'timestamp': getattr(usage, 'timestamp', 0)
            })
        
        # Calculate trends
        cache_trend = []
        for usage in usages:
            if usage.cache_read_tokens + usage.input_tokens > 0:
                cache_trend.append(usage.cache_hit_rate)
        
        return {
            'status': 'success',
            'conversation_id': conversation_id,
            'iterations': iterations,
            'cache_trend': cache_trend,
            'summary': {
                'total_iterations': len(usages),
                'average_cache_efficiency': sum(cache_trend) / len(cache_trend) if cache_trend else 0
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting conversation telemetry: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

