"""
PulseMCP Registry Provider (pulsemcp.com).

Note: PulseMCP appears to mirror the official registry API format.
We'll use their public API once documented, for now use web scraping.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import httpx
import json

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel, InstallationType
)
from app.mcp.registry.installation_helper import InstallationHelper
from app.utils.logging_utils import logger


class PulseMCPRegistryProvider(RegistryProvider):
    """Provider for PulseMCP registry (pulsemcp.com)."""
    
    def __init__(self, base_url: str = "https://www.pulsemcp.com"):
        self.base_url = base_url
        # According to docs, PulseMCP will mirror official registry API
        self.api_base = "https://registry.modelcontextprotocol.io"
        self._cache = {}
        self._cache_time = None
        self._http_client: Optional[httpx.AsyncClient] = None
    
    @property
    def name(self) -> str:
        return "PulseMCP"
    
    @property
    def identifier(self) -> str:
        return "pulsemcp"
    
    @property
    def is_internal(self) -> bool:
        return False
    
    @property
    def supports_search(self) -> bool:
        return True
    
    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                headers={'User-Agent': 'Ziya-PulseMCP-Client/1.0'}
            )
        return self._http_client
    
    def _should_refresh_cache(self) -> bool:
        """Check if cache should be refreshed (1 hour TTL)."""
        if not self._cache_time:
            return True
        
        from datetime import timedelta
        return datetime.now() - self._cache_time > timedelta(hours=1)
    
    async def _fetch_servers_from_official_registry(self) -> List[Dict[str, Any]]:
        """
        Fetch servers from the official registry.
        
        PulseMCP maintains mirrors of popular servers in the official registry
        until server maintainers publish there themselves.
        """
        try:
            client = self._get_http_client()
            url = f"{self.api_base}/v0/servers"
            
            # Fetch with latest version filter
            params = {'version': 'latest'}
            response = await client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            servers = data.get('servers', [])
            
            logger.info(f"PulseMCP: Fetched {len(servers)} servers from official registry")
            return servers
            
        except Exception as e:
            logger.error(f"Error fetching from official registry for PulseMCP: {e}")
            return []
    
    async def _fetch_pulsemcp_specific_servers(self) -> List[Dict[str, Any]]:
        """
        Fetch PulseMCP-specific server data if they have an API.
        For now, returns empty as API is not yet documented.
        """
        try:
            # TODO: Implement when PulseMCP exposes their own API
            # For now, they mirror official registry so we use that
            logger.info("PulseMCP-specific API not yet implemented")
            return []
            
        except Exception as e:
            logger.error(f"Error fetching PulseMCP-specific servers: {e}")
            return []
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from PulseMCP."""
        try:
            # Refresh cache if needed
            if self._should_refresh_cache():
                # Fetch from official registry (where PulseMCP mirrors)
                servers = await self._fetch_servers_from_official_registry()
                
                # Optionally fetch PulseMCP-specific additions
                pulsemcp_servers = await self._fetch_pulsemcp_specific_servers()
                servers.extend(pulsemcp_servers)
                
                self._cache = {'servers': servers}
                self._cache_time = datetime.now()
            
            servers = self._cache.get('servers', [])
            
            # Parse into standard format
            services = []
            for server_data in servers:
                try:
                    # Ensure provider_id is set early for error handling
                    if 'server' in server_data:
                        server_data.setdefault('_provider_id', 'pulsemcp')
                    
                    service = self._parse_pulsemcp_server(server_data)
                    services.append(service)
                except Exception as e:
                    logger.warning(f"Failed to parse PulseMCP server: {e}")
                    continue
            
            # Apply pagination
            start_idx = int(next_token or 0)
            end_idx = min(start_idx + max_results, len(services))
            paginated_services = services[start_idx:end_idx]
            
            logger.info(f"Fetched {len(paginated_services)} services from PulseMCP (total: {len(services)})")
            
            return {
                'services': paginated_services,
                'next_token': str(end_idx) if end_idx < len(services) else None
            }
            
        except Exception as e:
            logger.error(f"Error listing PulseMCP services: {e}")
            raise
    
    def _parse_pulsemcp_server(self, server_data: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse PulseMCP server data into standard format."""
        # PulseMCP uses official registry format
        server = server_data.get('server', server_data)
        meta = server_data.get('_meta', {}).get('io.modelcontextprotocol.registry/official', {})
        
        # Extract repository info
        repo_data = server.get('repository', {})
        repo_url = repo_data.get('url')
        
        # Parse timestamps
        published_at = meta.get('publishedAt')
        updated_at = meta.get('updatedAt')
        
        created_datetime = datetime.fromisoformat(published_at.replace('Z', '+00:00')) if published_at else datetime.now()
        updated_datetime = datetime.fromisoformat(updated_at.replace('Z', '+00:00')) if updated_at else created_datetime
        
        # Build installation instructions
        instructions = self._build_installation_instructions(server)
        install_type = InstallationHelper.detect_installation_type(instructions)
        
        return RegistryServiceInfo(
            service_id=server['name'],
            service_name=server['name'],
            service_description=server.get('description', 'No description'),
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=created_datetime,
            last_updated_at=updated_datetime,
            installation_instructions=instructions,
            installation_type=install_type,
            tags=self._extract_tags(server),
            repository_url=repo_url,
            homepage_url=repo_url,
            provider_metadata={
                'provider_id': 'pulsemcp',
                'schema': server.get('$schema'),
                'is_latest': meta.get('isLatest', False)
            }
        )
    
    def _build_installation_instructions(self, server: Dict[str, Any]) -> Dict[str, Any]:
        """Build installation instructions from server definition."""
        instructions = {'type': 'unknown', 'steps': []}
        
        # Check for packages
        packages = server.get('packages', [])
        if packages:
            package = packages[0]
            registry_type = package.get('registryType', '')
            
            if registry_type == 'npm':
                instructions = {
                    'type': 'npm',
                    'package': package.get('identifier'),
                    'version': package.get('version'),
                    'runtime_hint': package.get('runtimeHint', 'npx')
                }
            elif registry_type == 'pypi':
                instructions = {
                    'type': 'pypi',
                    'package': package.get('identifier'),
                    'version': package.get('version')
                }
            elif registry_type == 'oci':
                instructions = {
                    'type': 'docker',
                    'image': package.get('identifier')
                }
        
        # Check for remotes
        remotes = server.get('remotes', [])
        if remotes:
            remote = remotes[0]
            instructions = {
                'type': 'remote',
                'url': remote.get('url'),
                'transport': remote.get('type')
            }
        
        return instructions
    
    def _extract_tags(self, server: Dict[str, Any]) -> List[str]:
        """Extract tags from server metadata."""
        tags = []
        name = server.get('name', '').lower()
        description = server.get('description', '').lower()
        
        # Basic category detection
        categories = {
            'database': ['database', 'sql', 'postgres'],
            'web': ['web', 'http', 'browser'],
            'files': ['file', 'filesystem'],
            'cloud': ['aws', 'azure', 'gcp']
        }
        
        for category, keywords in categories.items():
            if any(kw in name or kw in description for kw in keywords):
                tags.append(category)
        
        return tags
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        # Use search for efficiency
        services_result = await self.list_services(max_results=10)
        service = next(
            (s for s in services_result['services'] if s.service_id == service_id),
            None
        )
        
        if not service:
            raise ValueError(f"Service {service_id} not found in PulseMCP")
        
        return service
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search PulseMCP for tools."""
        try:
            services_result = await self.list_services(max_results=max_results * 2)
            
            results = []
            query_lower = query.lower()
            
            for service in services_result['services']:
                relevance_score = 0
                
                if query_lower in service.service_name.lower():
                    relevance_score += 50
                if query_lower in service.service_description.lower():
                    relevance_score += 20
                for tag in service.tags:
                    if query_lower in tag.lower():
                        relevance_score += 30
                
                if relevance_score > 0:
                    tools = [RegistryTool(
                        tool_name=f"{service.service_name}_tool",
                        service_id=service.service_id
                    )]
                    
                    results.append(ToolSearchResult(
                        service=service,
                        matching_tools=tools,
                        relevance_score=relevance_score
                    ))
            
            results.sort(key=lambda x: x.relevance_score or 0, reverse=True)
            return results[:max_results]
            
        except Exception as e:
            logger.error(f"Error searching PulseMCP: {e}")
            return []
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service from PulseMCP."""
        try:
            service = await self.get_service_detail(service_id)
            
            # Since PulseMCP mirrors official registry,
            # installation works the same way
            from app.mcp.registry.providers.official_mcp import OfficialMCPRegistryProvider
            
            official_provider = OfficialMCPRegistryProvider()
            result = await official_provider.install_service(service_id, config_path)
            
            # Update provider in result
            if result.success and result.config_entries:
                result.config_entries['registry_provider'] = self.identifier
            
            return result
            
        except Exception as e:
            logger.error(f"Error installing PulseMCP service: {e}")
            return InstallationResult(
                success=False,
                service_id=service_id,
                server_name="",
                error_message=str(e)
            )
    
    async def validate_service(self, service_id: str) -> bool:
        """Validate service availability."""
        try:
            await self.get_service_detail(service_id)
            return True
        except Exception:
            return False
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
