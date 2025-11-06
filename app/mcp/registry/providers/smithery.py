"""
Smithery Registry Provider (smithery.ai).

Note: Smithery doesn't have a public API yet, so we scrape their website.
In the future, they may provide a REST API similar to the official registry.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import httpx
from bs4 import BeautifulSoup
import re

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel, InstallationType
)
from app.utils.logging_utils import logger


class SmitheryRegistryProvider(RegistryProvider):
    """Provider for Smithery registry (smithery.ai)."""
    
    def __init__(self, base_url: str = "https://smithery.ai"):
        self.base_url = base_url
        self._cache = {}
        self._cache_time = None
        self._http_client: Optional[httpx.AsyncClient] = None
    
    @property
    def name(self) -> str:
        return "Smithery"
    
    @property
    def identifier(self) -> str:
        return "smithery"
    
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
                headers={'User-Agent': 'Ziya-Smithery-Client/1.0'}
            )
        return self._http_client
    
    def _should_refresh_cache(self) -> bool:
        """Check if cache should be refreshed (1 hour TTL)."""
        if not self._cache_time:
            return True
        
        from datetime import timedelta
        return datetime.now() - self._cache_time > timedelta(hours=1)
    
    async def _fetch_servers_from_web(self) -> List[Dict[str, Any]]:
        """
        Scrape server list from Smithery website.
        
        Smithery displays servers in a browsable UI at /servers.
        We'll scrape the main page and individual server pages.
        """
        try:
            client = self._get_http_client()
            response = await client.get(f"{self.base_url}/servers")
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            servers = []
            
            # Updated approach: Find servers by looking for /server/ links directly
            # This is more reliable than trying to guess CSS classes
            
            # Method 1: Find all links that go to /server/ pages
            server_links = soup.find_all('a', href=lambda x: x and x.startswith('/server/'))
            logger.info(f"Found {len(server_links)} direct server links on Smithery")
            
            # Use direct server links as primary method, containers for context
            elements_to_process = server_links
            
            # Extract unique server IDs
            seen_ids = set()
            
            # Process each server link directly
            for link in elements_to_process:
                if not link or link.name != 'a':
                    continue
                    
                href = link.get('href', '')
                if not href or not href.startswith('/server/'):
                    continue
                
                # Extract server ID from href
                match = re.search(r'/server/([\w\-_.]+)', href)  
                if not match:
                    logger.debug(f"Could not extract server ID from: {href}")
                    continue
                
                server_id = match.group(1)
                
                # Skip if already processed
                if server_id in seen_ids:
                    continue
                
                seen_ids.add(server_id)
                        
                # Get server name from link text or use server_id
                server_name = link.get_text(strip=True) or server_id
                
                # If link text is empty, try to find it in a parent container
                if not server_name or server_name == server_id:
                    # Look for the server name in parent containers
                    parent = link.parent
                    while parent and server_name == server_id:
                        # Look for headings or emphasized text in parent
                        name_elem = (parent.find(['h1', 'h2', 'h3', 'h4', 'strong']) or
                                   parent.find(attrs={'class': re.compile(r'font-semibold|title|name', re.I)}))
                        if name_elem and name_elem != link:
                            potential_name = name_elem.get_text(strip=True)
                            if potential_name and len(potential_name) < 100:  # Sanity check
                                server_name = potential_name
                                break
                        parent = parent.parent
                
                # Clean server name
                server_name = server_name.strip()
                
                # Fallback to server_id if name is empty
                if not server_name:
                    server_name = server_id
                
                # Look for description in parent containers
                description = ""
                parent = link.parent
                while parent and not description:
                    # Find any paragraph in the parent (avoid complex class matching)
                    desc_elem = parent.find('p')
                    if desc_elem and desc_elem != link:
                        description = desc_elem.get_text(strip=True)
                        if len(description) > 10:  # Only use substantial descriptions
                            break
                    parent = parent.parent
                
                # Fallback description
                if not description:
                    description = f"Server: {server_name}"
                
                logger.debug(f"Extracted: ID='{server_id}', Name='{server_name}', Desc='{description[:50]}'")
                
                # Validate that we have minimum required data
                if not server_name or not server_id:
                    logger.warning(f"Skipping server with missing data: id='{server_id}', name='{server_name}'")
                    continue
                
                servers.append({
                    'id': server_id,
                    'name': server_name,
                    'description': description or f"Server: {server_name}",
                    'url': f"{self.base_url}{href}",
                    'source': 'smithery-web'
                })
                
                logger.debug(f"Successfully added server: {server_id}")
            
            logger.info(f"Scraped {len(servers)} unique servers from Smithery")
            
            return servers
            
        except Exception as e:
            logger.error(f"Error fetching Smithery servers: {e}")
            return []
    
    async def _fetch_server_details(self, server_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed information for a specific server from its page."""
        try:
            client = self._get_http_client()
            response = await client.get(f"{self.base_url}/server/{server_id}")
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract details from the page
            details = {
                'id': server_id,
                'name': server_id
            }
            
            # Look for common elements
            title = soup.find('h1')
            if title:
                details['name'] = title.get_text(strip=True)
            
            # Look for description
            desc = soup.find('meta', attrs={'name': 'description'})
            if desc:
                details['description'] = desc.get('content', '')
            else:
                # Fallback to first paragraph
                p = soup.find('p')
                if p:
                    details['description'] = p.get_text(strip=True)
            
            # Look for GitHub link
            github_link = soup.find('a', href=re.compile(r'github\.com'))
            if github_link:
                details['repository'] = github_link.get('href')
            
            # Look for installation command
            code_blocks = soup.find_all('code')
            for code in code_blocks:
                text = code.get_text(strip=True)
                if 'npx' in text or 'npm install' in text:
                    details['install_command'] = text
                    break
            
            return details
            
        except Exception as e:
            logger.warning(f"Error fetching Smithery server details for {server_id}: {e}")
            return None
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from Smithery."""
        try:
            # Refresh cache if needed
            if self._should_refresh_cache():
                servers = await self._fetch_servers_from_web()
                self._cache = {'servers': servers}
                self._cache_time = datetime.now()
            
            servers = self._cache.get('servers', [])
            
            # Parse into standard format
            services = []
            for server_data in servers:
                try:
                    service = self._parse_smithery_server(server_data)
                    services.append(service)
                except Exception as e:
                    logger.warning(f"Failed to parse Smithery server: {e}")
                    continue
            
            # Apply pagination
            start_idx = int(next_token or 0)
            end_idx = min(start_idx + max_results, len(services))
            paginated_services = services[start_idx:end_idx]
            
            return {
                'services': paginated_services,
                'next_token': str(end_idx) if end_idx < len(services) else None
            }
            
        except Exception as e:
            logger.error(f"Error listing Smithery services: {e}")
            raise
    
    def _parse_smithery_server(self, server_data: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse Smithery server data into standard format."""
        service_id = f"smithery.{server_data['id']}"
        
        # Infer installation type from available data
        install_instructions = server_data.get('installation', {})
        if server_data.get('install_command'):
            # Try to parse install command for more info
            if 'npm' in server_data['install_command']:
                install_instructions['type'] = 'npm'
        
        return RegistryServiceInfo(
            service_id=service_id,
            service_name=server_data.get('name', 'Unknown'),
            service_description=server_data.get('description', 'No description'),
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions=install_instructions,
            installation_type=InstallationType.UNKNOWN,
            tags=server_data.get('tags', []),
            repository_url=server_data.get('repository'),
            homepage_url=server_data.get('url'),
            provider_metadata={
                'provider_id': 'smithery',
                'smithery_url': server_data.get('url'),
                'install_command': server_data.get('install_command')
            }
        )
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information from Smithery."""
        services_result = await self.list_services(max_results=10000)
        service = next(
            (s for s in services_result['services'] if s.service_id == service_id),
            None
        )
        
        if not service:
            raise ValueError(f"Service {service_id} not found in Smithery")
        
        return service
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search Smithery for tools."""
        try:
            # Use list_services with search filter
            services_result = await self.list_services(max_results=max_results * 2)
            
            results = []
            query_lower = query.lower()
            
            for service in services_result['services']:
                relevance_score = 0
                
                if query_lower in service.service_name.lower():
                    relevance_score += 50
                if query_lower in service.service_description.lower():
                    relevance_score += 20
                
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
            logger.error(f"Error searching Smithery: {e}")
            return []
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """
        Install using Smithery CLI if available.
        
        Smithery has npm install -g @smithery/cli
        Usage: smithery install <server-name>
        """
        # Check if smithery CLI is installed
        import shutil
        if shutil.which('smithery'):
            logger.info(f"Smithery CLI detected, but auto-install not implemented yet")
        
        return InstallationResult(
            success=False,
            service_id=service_id,
            server_name="",
            error_message=f"Smithery installation not yet automated. Install via: npm install -g @smithery/cli && smithery install {service_id}"
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
