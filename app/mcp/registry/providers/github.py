"""
GitHub-based MCP Registry Provider for public/community servers.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel
)
from app.utils.logging_utils import logger


class GitHubRegistryProvider(RegistryProvider):
    """Provider for GitHub-based MCP registry (community/public servers)."""
    
    def __init__(self, registry_repo: str = "modelcontextprotocol/registry"):
        # Note: This provider is deprecated - no static registry file exists
        self.registry_repo = registry_repo
        self.github_api_base = "https://api.github.com"
    
    @property
    def name(self) -> str:
        return "Community Registry (GitHub)"
    
    @property
    def identifier(self) -> str:
        return "github"
    
    @property
    def is_internal(self) -> bool:
        return False
    
    @property
    def supports_search(self) -> bool:
        return True  # We can implement basic search over GitHub data
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from GitHub registry repository."""
        try:
            # GitHub provider is deprecated - the official registry moved to registry.modelcontextprotocol.io
            logger.info("GitHub provider is deprecated - use Official MCP Registry instead")
            
            url = f"{self.github_api_base}/repos/{self.registry_repo}/contents/registry.json"
            
            # Return empty results since no static registry file exists
            if response.status_code == 404:
                logger.debug(f"GitHub static registry file doesn't exist (expected)")
                return {
                    'services': [],
                    'next_token': None
                }
            
            response.raise_for_status()
            
            # Decode content (it's base64 encoded)
            import base64
            content = base64.b64decode(response.json()['content']).decode('utf-8')
            registry_data = json.loads(content)
            
            services = []
            for server_data in registry_data.get('servers', []):
                service = RegistryServiceInfo(
                    service_id=server_data['id'],
                    service_name=server_data['name'],
                    service_description=server_data['description'],
                    version=server_data.get('version', 1),
                    status=ServiceStatus.ACTIVE,  # Assume active for GitHub
                    support_level=SupportLevel.COMMUNITY,
                    created_at=datetime.fromisoformat(server_data.get('created_at', datetime.now().isoformat())),
                    last_updated_at=datetime.fromisoformat(server_data.get('updated_at', datetime.now().isoformat())),
                    installation_instructions=server_data.get('installation', {}),
                    tags=server_data.get('tags', []),
                    author=server_data.get('author'),
                    repository_url=server_data.get('repository'),
                    homepage_url=server_data.get('homepage'),
                    license=server_data.get('license'),
                    provider_metadata={
                        'github_repo': server_data.get('repository'),
                        'npm_package': server_data.get('npm_package'),
                        'pip_package': server_data.get('pip_package')
                    }
                )
                services.append(service)
            
            # Apply pagination (simple offset-based for GitHub)
            start_idx = int(next_token or 0)
            end_idx = start_idx + max_results
            paginated_services = services[start_idx:end_idx]
            
            return {
                'services': paginated_services,
                'next_token': str(end_idx) if end_idx < len(services) else None
            }
            
        except Exception as e:
            logger.debug(f"GitHub registry access failed (expected - provider deprecated): {e}")
            return {
                'services': [],
                'next_token': None
            }
            raise
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        services_result = await self.list_services(max_results=1000)
        service = next(
            (s for s in services_result['services'] if s.service_id == service_id),
            None
        )
        
        if not service:
            raise ValueError(f"Service {service_id} not found in GitHub registry")
        
        return service
    
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search tools by keywords in GitHub registry."""
        try:
            services_result = await self.list_services(max_results=1000)
            
            results = []
            query_lower = query.lower()
            
            for service in services_result['services']:
                # Simple keyword matching in description and tags
                relevance_score = 0
                
                # Check description
                if query_lower in service.service_description.lower():
                    relevance_score += 10
                
                # Check service name
                if query_lower in service.service_name.lower():
                    relevance_score += 5
                
                # Check tags
                for tag in service.tags:
                    if query_lower in tag.lower():
                        relevance_score += 3
                
                if relevance_score > 0:
                    # For GitHub, we don't have specific tool information,
                    # so we create placeholder tools based on common patterns
                    tools = self._extract_likely_tools(service)
                    
                    results.append(ToolSearchResult(
                        service=service,
                        matching_tools=tools,
                        relevance_score=relevance_score
                    ))
            
            # Sort by relevance and limit results
            results.sort(key=lambda x: x.relevance_score or 0, reverse=True)
            return results[:max_results]
            
        except Exception as e:
            logger.error(f"Error searching GitHub registry tools: {e}")
            raise
    
    def _extract_likely_tools(self, service: RegistryServiceInfo) -> List[RegistryTool]:
        """Extract likely tool names from service information."""
        tools = []
        
        # Use service name and description to infer tool names
        name_lower = service.service_name.lower()
        
        # Common patterns for tool name inference
        if 'file' in name_lower or 'filesystem' in name_lower:
            tools.append(RegistryTool('read_file', service.service_id, 'File operations'))
            tools.append(RegistryTool('write_file', service.service_id, 'File operations'))
        
        if 'database' in name_lower or 'sql' in name_lower:
            tools.append(RegistryTool('query_database', service.service_id, 'Database operations'))
        
        if 'web' in name_lower or 'http' in name_lower:
            tools.append(RegistryTool('make_request', service.service_id, 'Web requests'))
        
        # Fallback: create a generic tool
        if not tools:
            tools.append(RegistryTool(
                f"{service.service_name.lower().replace(' ', '_')}_tool",
                service.service_id,
                service.service_description[:100]
            ))
        
        return tools
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service from GitHub."""
        try:
            service = await self.get_service_detail(service_id)
            
            # Handle different installation methods
            instructions = service.installation_instructions
            install_dir = Path.home() / ".ziya" / "mcp_services" / service_id
            install_dir.mkdir(parents=True, exist_ok=True)
            
            # npm package installation
            if 'npm_package' in service.provider_metadata:
                npm_package = service.provider_metadata['npm_package']
                result = subprocess.run(['npm', 'install', npm_package], 
                                      cwd=str(install_dir), capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"npm install failed: {result.stderr}")
            
            # pip package installation  
            elif 'pip_package' in service.provider_metadata:
                pip_package = service.provider_metadata['pip_package']
                result = subprocess.run(['pip', 'install', pip_package], 
                                      capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"pip install failed: {result.stderr}")
            
            # git clone installation
            elif service.repository_url:
                result = subprocess.run(['git', 'clone', service.repository_url, str(install_dir)], 
                                      capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"git clone failed: {result.stderr}")
            
            # Build configuration entries
            server_name = f"github_{service.service_id}"
            config_entries = {
                "command": self._build_command_array(instructions, str(install_dir)),
                "enabled": True,
                "description": service.service_description,
                "registry_provider": self.identifier,
                "service_id": service_id,
                "version": service.version,
                "support_level": service.support_level.value,
                "installed_at": datetime.now().isoformat(),
                "installation_path": str(install_dir),
                "repository_url": service.repository_url,
                "author": service.author
            }
            
            return InstallationResult(
                success=True,
                service_id=service_id,
                server_name=server_name,
                installation_path=str(install_dir),
                config_entries=config_entries
            )
            
        except Exception as e:
            logger.error(f"Error installing GitHub service {service_id}: {e}")
            return InstallationResult(
                success=False,
                service_id=service_id,
                server_name="",
                error_message=str(e)
            )
    
    async def validate_service(self, service_id: str) -> bool:
        """Validate service availability on GitHub."""
        try:
            await self.get_service_detail(service_id)
            return True
        except Exception:
            return False
    
    def _build_command_array(self, instructions: Dict[str, Any], install_path: str) -> List[str]:
        """Build command array for GitHub-installed services."""
        command = instructions.get('command', '')
        
        if not command:
            # Auto-detect based on installation
            install_dir = Path(install_path)
            
            # Look for package.json (Node.js)
            if (install_dir / 'package.json').exists():
                return ['node', str(install_dir / 'index.js')]
            
            # Look for setup.py or pyproject.toml (Python)
            if (install_dir / 'setup.py').exists() or (install_dir / 'pyproject.toml').exists():
                return ['python', '-u', str(install_dir / 'server.py')]
            
            # Fallback
            return ['echo', 'No executable found']
        
        return command if isinstance(command, list) else [command]
