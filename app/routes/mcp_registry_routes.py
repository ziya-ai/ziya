"""
API routes for MCP Registry integration.

Only the endpoints that exist nowhere else live here. The bulk of the registry
API (providers, services listing, install, uninstall, tool search, favorites)
is in app.routes.mcp_routes under the same /api/mcp/registry prefix; that
router is included first in app.server, so a duplicate declared here would be
dead code that silently takes over on any include-order change.
"""

import shutil
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.mcp.registry.registry import get_provider_registry
from app.mcp.registry_manager import get_registry_manager
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/mcp/registry", tags=["mcp-registry"])


@router.get("/check-binary")
async def check_mcp_registry_binary():
    """Check if the registry CLI is available in PATH.

    'aim' is the rebranded 'mcp-registry' (same toolbox binary); the provider
    installs through whichever is present, so a machine with only 'aim' was
    wrongly reported here as having no registry CLI.
    """
    try:
        for name in ('mcp-registry', 'aim'):
            binary_path = shutil.which(name)
            if binary_path:
                return {"available": True, "path": binary_path, "binary": name}
        return {
            "available": False, "path": None, "binary": None,
        }
    except Exception as e:
        logger.error(f"Error checking mcp-registry binary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/services/uninstall/{server_name}")
async def uninstall_service_by_name(server_name: str):
    """Uninstall an MCP service by server name."""
    try:
        registry_manager = get_registry_manager()
        result = await registry_manager.uninstall_service(server_name)
        
        if result['status'] == 'error':
            raise HTTPException(status_code=400, detail=result['error'])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uninstalling service {server_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services/{service_id}/detail")  
async def get_service_detail(
    service_id: str,
    provider_id: Optional[str] = Query(default=None, description="Specific provider to query")
):
    """Get detailed information about a specific service."""
    try:
        provider_registry = get_provider_registry()
        
        provider = None
        if provider_id:
            provider = provider_registry.get_provider(provider_id)
        else:
            # Search all available providers
            for p in provider_registry.get_available_providers():
                if await p.validate_service(service_id):
                    provider = p
                    break
        
        if not provider:
            raise HTTPException(status_code=404, detail=f"Service {service_id} not found")
        
        service = await provider.get_service_detail(service_id)
        
        return {
            "serviceId": service.service_id,
            "serviceName": service.service_name,
            "serviceDescription": service.service_description,
            "version": service.version,
            "status": service.status.value,
            "supportLevel": service.support_level.value,
            "instructions": service.installation_instructions,
            "provider": {
                "id": provider.identifier,
                "name": provider.name,
                "isInternal": provider.is_internal
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting service detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/test-connection")
async def test_registry_connection():
    """Test connection to MCP Registry providers."""
    try:
        provider_registry = get_provider_registry()
        results = {}
        
        for provider in provider_registry.get_available_providers():
            if hasattr(provider, 'test_connection'):
                try:
                    result = await provider.test_connection()
                    results[provider.identifier] = {"success": result}
                except Exception as e:
                    results[provider.identifier] = {"success": False, "error": str(e)}
        
        return {"connection_tests": results}
        
    except Exception as e:
        logger.error(f"Error testing registry connections: {e}")
        raise HTTPException(status_code=500, detail=str(e))
