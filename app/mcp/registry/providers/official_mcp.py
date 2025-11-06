"""
Official MCP Registry Provider (registry.modelcontextprotocol.io).
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import httpx

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel, InstallationType
)
from app.mcp.registry.installation_helper import InstallationHelper
from app.utils.logging_utils import logger


class OfficialMCPRegistryProvider(RegistryProvider):
    """Provider for official MCP registry at registry.modelcontextprotocol.io."""
    
    def __init__(self, base_url: str = "https://registry.modelcontextprotocol.io"):
        self.base_url = base_url
        self.api_version = "v0"
        self._http_client: Optional[httpx.AsyncClient] = None
    
    @property
    def name(self) -> str:
        return "Official MCP Registry"
    
    @property
    def identifier(self) -> str:
        return "official-mcp"
    
    @property
    def is_internal(self) -> bool:
        return False
    
    @property
    def supports_search(self) -> bool:
        return True  # Official API has search parameter
    
    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with proper configuration."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={'User-Agent': 'Ziya-MCP-Registry-Client/1.0'}
            )
        return self._http_client
    
    def _map_status(self, status_str: str) -> ServiceStatus:
        """Map registry status to our enum."""
        status_map = {
            'active': ServiceStatus.ACTIVE,
            'pending': ServiceStatus.PENDING,
            'deleted': ServiceStatus.DELETED,
            'deprecated': ServiceStatus.DEPRECATED
        }
        return status_map.get(status_str.lower(), ServiceStatus.ACTIVE)
    
    def _infer_support_level(self, server_data: Dict[str, Any]) -> SupportLevel:
        """Infer support level from registry data."""
        # Official registry doesn't have explicit support levels yet
        # We can infer based on repository ownership or other metadata
        
        name = server_data.get('name', '')
        
        # Check if it's from official modelcontextprotocol org
        if 'io.modelcontextprotocol' in name:
            return SupportLevel.RECOMMENDED
        
        # Check repository ownership
        repo_url = server_data.get('repository', {}).get('url', '')
        if 'github.com/modelcontextprotocol/' in repo_url:
            return SupportLevel.RECOMMENDED
        elif 'github.com/anthropic' in repo_url or 'github.com/smithery-ai' in repo_url:
            return SupportLevel.SUPPORTED
        
        # Default to community for third-party servers
        return SupportLevel.COMMUNITY
    
    def _parse_server_entry(self, entry: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse a server entry from the official registry format."""
        server = entry.get('server', {})
        meta = entry.get('_meta', {}).get('io.modelcontextprotocol.registry/official', {})
        
        # Extract repository info
        repo_data = server.get('repository', {})
        repo_url = repo_data.get('url')
        
        # Parse timestamps
        published_at = meta.get('publishedAt')
        updated_at = meta.get('updatedAt')
        
        created_datetime = datetime.fromisoformat(published_at.replace('Z', '+00:00')) if published_at else datetime.now()
        updated_datetime = datetime.fromisoformat(updated_at.replace('Z', '+00:00')) if updated_at else created_datetime
        
        # Build installation instructions from packages or remotes
        installation_instructions = self._build_installation_instructions(server)
        installation_type = InstallationHelper.detect_installation_type(installation_instructions)
        
        # Extract tags from description and name
        tags = self._extract_tags(server)
        
        return RegistryServiceInfo(
            service_id=server['name'],
            service_name=server['name'],
            service_description=server.get('description', 'No description'),
            version=1,  # Official registry doesn't expose numeric version in list endpoint
            status=self._map_status(meta.get('status', 'active')),
            support_level=self._infer_support_level(server),
            created_at=created_datetime,
            last_updated_at=updated_datetime,
            installation_instructions=installation_instructions,
            installation_type=installation_type,
            tags=tags,
            repository_url=repo_url,
            homepage_url=repo_url,  # Use repository as homepage fallback
            provider_metadata={
                'provider_id': 'official-mcp',
                'schema': server.get('$schema'),
                'packages': server.get('packages', []),
                'remotes': server.get('remotes', []),
                'is_latest': meta.get('isLatest', False),
                'registry_version': server.get('version')
            }
        )
    
    def _build_installation_instructions(self, server: Dict[str, Any]) -> Dict[str, Any]:
        """Build installation instructions from server definition."""
        instructions = {
            'type': 'unknown',
            'steps': []
        }
        
        # Check for packages (npm, pypi, docker)
        packages = server.get('packages', [])
        if packages:
            package = packages[0]  # Use first package
            registry_type = package.get('registryType', '')
            
            if registry_type == 'npm':
                instructions['type'] = 'npm'
                instructions['package'] = package.get('identifier')
                instructions['version'] = package.get('version')
                instructions['runtime_hint'] = package.get('runtimeHint', 'npx')
                instructions['env_vars'] = package.get('environmentVariables', [])
            elif registry_type == 'pypi':
                instructions['type'] = 'pypi'
                instructions['package'] = package.get('identifier')
                instructions['version'] = package.get('version')
                instructions['env_vars'] = package.get('environmentVariables', [])
            elif registry_type == 'oci':
                instructions['type'] = 'docker'
                instructions['image'] = package.get('identifier')
                instructions['command'] = ['docker', 'run', package.get('identifier')]
        
        # Check for remotes (hosted servers)
        remotes = server.get('remotes', [])
        if remotes:
            remote = remotes[0]
            instructions['type'] = 'remote'
            instructions['url'] = remote.get('url')
            instructions['transport'] = remote.get('type')
            instructions['headers'] = remote.get('headers', [])
        
        return instructions
    
    def _extract_tags(self, server: Dict[str, Any]) -> List[str]:
        """Extract tags from server metadata."""
        tags = []
        
        name = server.get('name', '').lower()
        description = server.get('description', '').lower()
        
        # Common categories
        categories = {
            'database': ['database', 'sql', 'postgres', 'mysql', 'mongodb'],
            'files': ['file', 'filesystem', 'storage'],
            'web': ['web', 'http', 'api', 'fetch'],
            'cloud': ['aws', 'azure', 'gcp', 'cloud'],
            'dev-tools': ['git', 'github', 'gitlab', 'code'],
            'search': ['search', 'query', 'find']
        }
        
        for category, keywords in categories.items():
            if any(kw in name or kw in description for kw in keywords):
                tags.append(category)
        
        # Check packages for language tags
        packages = server.get('packages', [])
        if packages:
            registry_type = packages[0].get('registryType', '')
            if registry_type == 'npm':
                tags.append('javascript')
            elif registry_type == 'pypi':
                tags.append('python')
            elif registry_type == 'oci':
                tags.append('docker')
        
        return tags
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from official MCP registry."""
        try:
            url = f"{self.base_url}/{self.api_version}/servers"
            params = {}
            
            # Apply filters if provided
            if filter_params:
                if 'search' in filter_params:
                    params['search'] = filter_params['search']
                if 'updated_since' in filter_params:
                    params['updated_since'] = filter_params['updated_since']
                if 'version' in filter_params:
                    params['version'] = filter_params['version']
            
            # Use cursor-based pagination from the API
            if next_token:
                params['cursor'] = next_token
            params['limit'] = min(max_results, 100)  # API max is 100
            
            client = self._get_http_client()
            response = await client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            # Handle both old format and new cursor-based format
            if 'servers' in data:
                servers = data['servers']
                new_next_token = data.get('metadata', {}).get('nextCursor')
            else:
                # Fallback for old format
                servers = data if isinstance(data, list) else []
                new_next_token = None
            
            # Filter for latest versions only by default
            if filter_params is None or filter_params.get('version') != 'all':
                servers = [s for s in servers if s.get('_meta', {}).get('io.modelcontextprotocol.registry/official', {}).get('isLatest', False)]
            
            # Parse all servers
            services = []
            for server_entry in servers:
                try:
                    # Add early provider_id marking for error tracking
                    if 'server' in server_entry:
                        server_entry.setdefault('_provider_id', 'official-mcp')
                    service = self._parse_server_entry(server_entry)
                    services.append(service)
                except Exception as e:
                    server_name = server_entry.get('server', {}).get('name', 'unknown')
                    logger.warning(f"Failed to parse server entry '{server_name}': {e}")
                    continue
            
            # If we got fewer results than requested and there's a cursor, there might be more
            if len(services) < max_results and new_next_token:
                logger.info(f"Got {len(services)} services with cursor, there may be more available")
            elif len(services) >= max_results:
                logger.info(f"Fetched maximum {len(services)} services")
            else:
                logger.info(f"Fetched all {len(services)} available services")
            
            return {
                'services': services,
                'next_token': new_next_token
            }
            
        except Exception as e:
            logger.error(f"Error listing official MCP registry services: {e}")
            logger.exception(e)  # Full stack trace
            raise  # Let the aggregator handle the error properly
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        try:
            # Use search to find specific service efficiently
            services_result = await self.list_services(
                max_results=10, 
                filter_params={'search': service_id, 'version': 'latest'}
            )
            
            service = next(
                (s for s in services_result['services'] if s.service_id == service_id),
                None
            )
            
            if not service:
                raise ValueError(f"Service {service_id} not found in official registry")
            
            return service
            
        except Exception as e:
            logger.error(f"Error getting service detail: {e}")
            raise
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search for services by keyword."""
        try:
            # Use the search parameter
            services_result = await self.list_services(
                max_results=max_results * 2,  # Get more results for better filtering
                filter_params={'search': query}
            )
            
            results = []
            query_lower = query.lower()
            
            for service in services_result['services']:
                # Calculate relevance score
                relevance_score = 0
                
                # Exact name match
                if query_lower == service.service_name.lower():
                    relevance_score += 100
                
                # Name contains query
                if query_lower in service.service_name.lower():
                    relevance_score += 50
                
                # Description contains query
                if query_lower in service.service_description.lower():
                    relevance_score += 20
                
                # Tag matches
                for tag in service.tags:
                    if query_lower in tag.lower():
                        relevance_score += 30
                
                if relevance_score > 0:
                    # Create placeholder tools (official registry doesn't expose tool list in summary)
                    tools = [RegistryTool(
                        tool_name=f"{service.service_name}_tool",
                        service_id=service.service_id,
                        description=service.service_description[:100]
                    )]
                    
                    results.append(ToolSearchResult(
                        service=service,
                        matching_tools=tools,
                        relevance_score=relevance_score
                    ))
            
            # Sort by relevance
            results.sort(key=lambda x: x.relevance_score or 0, reverse=True)
            return results[:max_results]
            
        except Exception as e:
            logger.error(f"Error searching official registry: {e}")
            return []
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service from official registry."""
        try:
            service = await self.get_service_detail(service_id)
            instructions = service.installation_instructions
            install_type = InstallationHelper.detect_installation_type(instructions)
            
            # Check prerequisites
            has_prereq, error_msg = InstallationHelper.check_prerequisites(install_type)
            if not has_prereq:
                raise RuntimeError(error_msg)
            
            # Create installation directory
            install_dir = Path.home() / ".ziya" / "mcp_services" / service_id.replace('/', '_')
            install_dir.mkdir(parents=True, exist_ok=True)
            
            command_array = []
            env_vars = {}
            
            # Handle different installation types
            if install_type == InstallationType.NPM:
                package = instructions.get('package')
                runtime_hint = instructions.get('runtime_hint', 'npx')
                
                if runtime_hint == 'npx':
                    command_array = ['npx', '-y', package]
                else:
                    # Install locally and run
                    install_result = InstallationHelper.install_npm_package(package, install_dir)
                    if not install_result['success']:
                        raise RuntimeError(install_result['error'])
                    command_array = install_result['command']
                
                # Parse environment variables
                for env_var in instructions.get('env_vars', []):
                    if env_var.get('isRequired'):
                        logger.warning(f"Required environment variable: {env_var.get('name')} - {env_var.get('description')}")
            
            elif install_type == InstallationType.PYPI:
                package = instructions.get('package')
                install_result = InstallationHelper.install_pypi_package(package)
                if not install_result['success']:
                    raise RuntimeError(install_result['error'])
                command_array = install_result['command']
                
            elif install_type == InstallationType.DOCKER:
                image = instructions.get('image')
                setup_result = InstallationHelper.setup_docker_container(image)
                if not setup_result['success']:
                    raise RuntimeError(setup_result['error'])
                command_array = setup_result['command']
                
            elif install_type == InstallationType.REMOTE:
                # Remote server - no installation needed
                command_array = None  # Will be handled as remote in config
                
            else:
                # Fallback to command if provided
                command_array = instructions.get('command', [])
                if not command_array:
                    raise ValueError(f"Unknown installation type: {install_type}")
            
            # Build configuration entries
            server_name = service_id.split('/')[-1]  # Use last part of namespaced ID
            config_entries = {
                "enabled": True,
                "description": service.service_description,
                "registry_provider": self.identifier,
                "service_id": service_id,
                "version": service.version,
                "support_level": service.support_level.value,
                "installed_at": datetime.now().isoformat(),
                "installation_path": str(install_dir),
                "repository_url": service.repository_url
            }
            
            # Add command or remote URL
            if command_array:
                config_entries['command'] = command_array
                if env_vars:
                    config_entries['env'] = env_vars
            elif install_type == InstallationType.REMOTE:
                config_entries['remote_url'] = instructions.get('url')
                config_entries['transport'] = instructions.get('transport', 'streamable-http')
                headers = instructions.get('headers', [])
                if headers:
                    config_entries['required_headers'] = headers
            else:
                raise ValueError(f"No valid installation method found for {service_id}")
            
            logger.info(f"Successfully prepared installation for {service_id} (type: {install_type})")
            
            return InstallationResult(
                success=True,
                service_id=service_id,
                server_name=server_name,
                installation_path=str(install_dir) if command_array else None,
                config_entries=config_entries
            )
            
        except Exception as e:
            logger.error(f"Error installing service {service_id}: {e}")
            return InstallationResult(
                success=False,
                service_id=service_id,
                server_name="",
                error_message=str(e)
            )
    
    async def validate_service(self, service_id: str) -> bool:
        """Validate service is still available."""
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
    
    def _extract_tags(self, server: Dict[str, Any]) -> List[str]:
        """Extract tags from server metadata."""
        tags = []
        
        name = server.get('name', '').lower()
        description = server.get('description', '').lower()
        
        # Category mapping
        categories = {
            'database': ['database', 'sql', 'postgres', 'mysql', 'mongodb', 'sqlite'],
            'files': ['file', 'filesystem', 'storage', 'drive'],
            'web': ['web', 'http', 'api', 'fetch', 'browser'],
            'cloud': ['aws', 'azure', 'gcp', 'cloud'],
            'dev-tools': ['git', 'github', 'gitlab', 'code', 'repo'],
            'search': ['search', 'query', 'find', 'exa', 'brave'],
            'productivity': ['calendar', 'email', 'slack', 'notion', 'task'],
            'ai': ['ai', 'ml', 'openai', 'claude', 'llm'],
            'data': ['data', 'analytics', 'visualization'],
        }
        
        for category, keywords in categories.items():
            if any(kw in name or kw in description for kw in keywords):
                tags.append(category)
        
        # Check packages for language/platform tags
        packages = server.get('packages', [])
        if packages:
            registry_type = packages[0].get('registryType', '')
            if registry_type == 'npm':
                tags.append('javascript')
                tags.append('typescript')
            elif registry_type == 'pypi':
                tags.append('python')
            elif registry_type == 'oci':
                tags.append('docker')
        
        # Check for remotes
        if server.get('remotes'):
            tags.append('remote')
            tags.append('hosted')
        
        # Deduplicate
        return list(set(tags))
