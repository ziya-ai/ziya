"""
API routes for MCP Registry integration.
"""

import shutil
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.mcp.registry.registry import get_provider_registry
from app.mcp.registry_manager import get_registry_manager
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/mcp/registry", tags=["mcp-registry"])


class ServiceInstallRequest(BaseModel):
    model_config = {"extra": "allow"}
    service_id: str = Field(..., description="The service ID to install")
    provider_id: Optional[str] = Field(None, description="Optional provider ID")
    

class ServiceUninstallRequest(BaseModel):
    model_config = {"extra": "allow"}
    server_name: str = Field(..., description="The server name to uninstall")


class ToolSearchRequest(BaseModel):
    model_config = {"extra": "allow"}
    query: str = Field(..., description="Natural language query for tool search")
    max_tools: int = Field(default=10, description="Maximum number of tools to return")
    providers: Optional[List[str]] = Field(None, description="Limit search to specific providers")


@router.get("/check-binary")
async def check_mcp_registry_binary():
    """Check if mcp-registry binary is available in PATH."""
    try:
        binary_path = shutil.which('mcp-registry')
        return {
            "available": binary_path is not None,
            "path": binary_path
        }
    except Exception as e:
        logger.error(f"Error checking mcp-registry binary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers")
async def list_providers():
    """Get list of available registry providers."""
    try:
        registry_manager = get_registry_manager()
        provider_ids = registry_manager.get_available_providers()
        
        provider_registry = get_provider_registry()
        providers = []
        for provider_id in provider_ids:
            provider = provider_registry.get_provider(provider_id)
            if provider:
                providers.append({
                    "id": provider.identifier,
                    "name": provider.name,
                    "isInternal": provider.is_internal,
                    "supportsSearch": provider.supports_search
                })
        
        return {"providers": providers}
        
    except Exception as e:
        logger.error(f"Error listing providers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services")
async def list_available_services(
    max_results: int = Query(default=50, le=100),
    next_token: Optional[str] = Query(default=None),
    providers: Optional[str] = Query(default=None, description="Comma-separated provider IDs")
):
    """Get list of available MCP services from the registry."""
    try:
        registry_manager = get_registry_manager()
        
        provider_filter = None
        if providers:
            provider_filter = [p.strip() for p in providers.split(',')]
        
        services = await registry_manager.get_available_services(max_results, provider_filter)
        
        # Convert to API response format
        service_list = []
        for service in services:
            service_list.append({
                "serviceId": service.service_id,
                "serviceName": service.service_name,
                "serviceDescription": service.service_description,
                "supportLevel": service.support_level.value,
                "status": service.status.value,
                "version": service.version,
                "createdAt": service.created_at.isoformat(),
                "lastUpdatedAt": service.last_updated_at.isoformat(),
                "securityReviewLink": service.security_review_url,
                "instructions": service.installation_instructions,
                "provider": {
                    "id": service.provider_metadata.get('provider_id'),
                    "name": service.provider_metadata.get('provider_name'),
                    "isInternal": service.provider_metadata.get('is_internal', False)
                },
                "tags": service.tags,
                "author": service.author,
                "repositoryUrl": service.repository_url,
                "homepageUrl": service.homepage_url,
                "license": service.license
            })
        
        return {
            "services": service_list,
            "total": len(service_list)
        }
        
    except Exception as e:
        logger.error(f"Error listing available services: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services/installed")
async def list_installed_services():
    """Get list of currently installed registry services."""
    try:
        registry_manager = get_registry_manager()
        installed = await registry_manager.get_installed_services()
        
        return {
            "services": installed,
            "total": len(installed)
        }
        
    except Exception as e:
        logger.error(f"Error listing installed services: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/services/install")
async def install_service(request: ServiceInstallRequest):
    """Install an MCP service from the registry."""
    try:
        registry_manager = get_registry_manager()
        result = await registry_manager.install_service(request.service_id, request.provider_id)
        
        if result['status'] == 'error':
            raise HTTPException(status_code=400, detail=result['error'])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error installing service: {e}")
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


@router.post("/services/uninstall")
async def uninstall_service(request: ServiceUninstallRequest):
    """Uninstall an MCP service."""
    try:
        registry_manager = get_registry_manager()
        result = await registry_manager.uninstall_service(request.server_name)
        
        if result['status'] == 'error':
            raise HTTPException(status_code=400, detail=result['error'])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uninstalling service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tools/search")
async def search_tools(request: ToolSearchRequest):
    """Search for tools and their associated services."""
    try:
        registry_manager = get_registry_manager()
        results = await registry_manager.search_services_by_tools(
            request.query, request.providers
        )
        
        # Format response
        formatted_results = []
        for result in results:
            formatted_results.append({
                "service": {
                    "serviceId": result.service.service_id,
                    "serviceName": result.service.service_name,
                    "serviceDescription": result.service.service_description,
                    'supportLevel': result.service.support_level.value,
                    'status': result.service.status.value,
                    'provider': {
                        'id': result.service.provider_metadata.get('provider_id'),
                        'name': result.service.provider_metadata.get('provider_name'),
                        'isInternal': result.service.provider_metadata.get('is_internal', False)
                    },
                    'availableIn': result.service.provider_metadata.get('available_in', [result.service.provider_metadata.get('provider_name', 'Unknown')])
                },
                "matchingTools": [
                    {
                        "toolName": tool.tool_name,
                        "serviceId": tool.service_id,
                        "description": tool.description,
                        "category": tool.category
                    }
                    for tool in result.matching_tools
                ]
            })
        
        return {
            "results": formatted_results,
            "query": request.query,
            "total": len(formatted_results)
        }
        
    except Exception as e:
        logger.error(f"Error searching tools: {e}")
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
