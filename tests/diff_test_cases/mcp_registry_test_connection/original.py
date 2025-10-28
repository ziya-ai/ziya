"""
API routes for MCP Registry integration.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.mcp.registry.registry import get_provider_registry
from app.mcp.registry_manager import get_registry_manager
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/mcp/registry", tags=["mcp-registry"])


class ServiceInstallRequest(BaseModel):
    service_id: str = Field(..., description="The service ID to install")
    

class ServiceUninstallRequest(BaseModel):
    server_name: str = Field(..., description="The server name to uninstall")


class ToolSearchRequest(BaseModel):
    query: str = Field(..., description="Natural language query for tool search")
    max_tools: int = Field(default=10, description="Maximum number of tools to return")
    providers: Optional[List[str]] = Field(None, description="Limit search to specific providers")


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


@router.post("/install")
async def install_service(request: ServiceInstallRequest):
    """Install a service from the registry."""
    try:
        registry_manager = get_registry_manager()
        result = await registry_manager.install_service(request.service_id)
        
        return {
            "success": result.success,
            "message": result.message,
            "serverName": result.server_name,
            "configPath": result.config_path
        }
        
    except Exception as e:
        logger.error(f"Error installing service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/uninstall")
async def uninstall_service(request: ServiceUninstallRequest):
    """Uninstall a service from the registry."""
    try:
        registry_manager = get_registry_manager()
        result = await registry_manager.uninstall_service(request.server_name)
        
        return {
            "success": result.success,
            "message": result.message
        }
        
    except Exception as e:
        logger.error(f"Error uninstalling service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services")
async def list_services(
    provider_id: Optional[str] = Query(default=None, description="Filter by provider ID"),
    category: Optional[str] = Query(default=None, description="Filter by category")
):
    """List available services from all providers."""
    try:
        provider_registry = get_provider_registry()
        
        if provider_id:
            provider = provider_registry.get_provider(provider_id)
            if not provider:
                raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
            providers = [provider]
        else:
            providers = provider_registry.get_available_providers()
        
        all_services = []
        for provider in providers:
            services = await provider.list_services(category=category)
            for service in services:
                all_services.append({
                    "serviceId": service.service_id,
                    "serviceName": service.service_name,
                    "serviceDescription": service.service_description,
                    "version": service.version,
                    "status": service.status.value,
                    "supportLevel": service.support_level.value,
                    "provider": {
                        "id": provider.identifier,
                        "name": provider.name,
                        "isInternal": provider.is_internal
                    }
                })
        
        return {"services": all_services}
        
    except Exception as e:
        logger.error(f"Error listing services: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/categories")
async def list_categories():
    """Get list of available service categories."""
    try:
        provider_registry = get_provider_registry()
        categories = set()
        
        for provider in provider_registry.get_available_providers():
            services = await provider.list_services()
            for service in services:
                if service.category:
                    categories.add(service.category)
        
        return {"categories": sorted(list(categories))}
        
    except Exception as e:
        logger.error(f"Error listing categories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search-tools")
async def search_tools(request: ToolSearchRequest):
    """Search for tools across registry providers using natural language."""
    try:
        provider_registry = get_provider_registry()
        
        # Get providers to search
        if request.providers:
            providers = [provider_registry.get_provider(p) for p in request.providers]
            providers = [p for p in providers if p is not None]
        else:
            providers = [p for p in provider_registry.get_available_providers() if p.supports_search]
        
        # Search each provider
        all_results = []
        for provider in providers:
            results = await provider.search_tools(request.query, max_results=request.max_tools)
            all_results.extend(results)
        
        # Sort by relevance score
        all_results.sort(key=lambda x: x.relevance_score, reverse=True)
        
        # Limit to max_tools
        all_results = all_results[:request.max_tools]
        
        # Format results
        formatted_results = []
        for result in all_results:
            formatted_results.append({
                "serviceId": result.service_id,
                "serviceName": result.service_name,
                "relevanceScore": result.relevance_score,
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
