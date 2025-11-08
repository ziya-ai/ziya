"""
Open MCP Registry Provider

Provides access to the open-mcp.org registry of MCP servers.
"""

import asyncio
import aiohttp
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
import json
import subprocess
import os

from app.mcp.registry.interface import (
    RegistryProvider, RegistryServiceInfo, RegistryTool, ToolSearchResult,
    InstallationResult, ServiceStatus, SupportLevel, InstallationType
)
from app.utils.logging_utils import logger


class OpenMCPProvider(RegistryProvider):
    """Provider for open-mcp.org registry."""
    
    def __init__(self):
        self.identifier = "open-mcp"
        self.name = "Open MCP"
        self.description = "Community registry of MCP servers (API not yet available)"
        self.base_url = "https://api.open-mcp.org"
        self.enabled = False  # Disabled until API is available
        self.is_internal = False
    
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """List services from open-mcp registry."""
        # API not yet available, return empty results
        logger.info("Open MCP API not yet available, returning empty results")
        return {'services': [], 'next_token': None, 'total_count': 0}
    
    def _parse_server_data(self, server_data: Dict[str, Any]) -> RegistryServiceInfo:
        """Parse server data from open-mcp API."""
        
        # Determine installation type from package info
        install_type = InstallationType.UNKNOWN
        package_info = server_data.get('package', {})
        
        if package_info.get('type') == 'npm':
            install_type = InstallationType.NPM
        elif package_info.get('type') == 'pypi':
            install_type = InstallationType.PYPI
        elif server_data.get('repository'):
            install_type = InstallationType.GIT
        
        # Parse tags
        tags = server_data.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        
        return RegistryServiceInfo(
            service_id=server_data.get('name', 'unknown'),
            service_name=server_data.get('displayName', server_data.get('name', 'Unknown')),
            service_description=server_data.get('description', ''),
            version=server_data.get('version', 1),
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.UNDER_ASSESSMENT,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions=package_info,
            installation_type=install_type,
            tags=tags,
            security_review_url=None,
            provider_metadata={
                'provider_id': self.identifier,
                'repository': server_data.get('repository'),
                'package': package_info,
                'author': server_data.get('author'),
                'license': server_data.get('license')
            }
        )
    
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed service information."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/servers/{service_id}") as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._parse_server_data(data)
                    else:
                        raise Exception(f"Service {service_id} not found")
                        
        except Exception as e:
            logger.error(f"Error getting Open MCP service detail for {service_id}: {e}")
            raise
    
    async def search_tools(self, query: str, max_results: int = 20) -> List[ToolSearchResult]:
        """Search for tools in open-mcp registry."""
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    'q': query,
                    'limit': max_results
                }
                
                async with session.get(f"{self.base_url}/search", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = []
                        
                        for server in data.get('servers', []):
                            # Create tool search results from server data
                            tools = server.get('tools', [])
                            for tool in tools:
                                if query.lower() in tool.get('name', '').lower() or query.lower() in tool.get('description', '').lower():
                                    results.append(ToolSearchResult(
                                        tool_name=tool.get('name', 'unknown'),
                                        tool_description=tool.get('description', ''),
                                        service_id=server.get('name'),
                                        service_name=server.get('displayName', server.get('name')),
                                        provider_id=self.identifier
                                    ))
                        
                        return results[:max_results]
                    else:
                        return []
                        
        except Exception as e:
            logger.error(f"Error searching Open MCP tools: {e}")
            return []
    
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """Install service from open-mcp registry."""
        try:
            service = await self.get_service_detail(service_id)
            package_info = service.provider_metadata.get('package', {})
            
            # Create installation directory
            install_dir = Path.home() / ".ziya" / "mcp_services" / service_id.replace('/', '_')
            install_dir.mkdir(parents=True, exist_ok=True)
            
            # Install based on package type
            if package_info.get('type') == 'npm':
                # Install npm package
                result = subprocess.run(['npm', 'install', '-g', package_info.get('name', service_id)], 
                                      capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    raise RuntimeError(f"NPM install failed: {result.stderr}")
                
                command = [package_info.get('name', service_id)]
                
            elif package_info.get('type') == 'pypi':
                # Install pip package
                result = subprocess.run(['pip', 'install', package_info.get('name', service_id)], 
                                      capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    raise RuntimeError(f"Pip install failed: {result.stderr}")
                
                command = [package_info.get('name', service_id)]
                
            elif service.provider_metadata.get('repository'):
                # Clone git repository
                repo_url = service.provider_metadata['repository']
                result = subprocess.run(['git', 'clone', repo_url, str(install_dir)], 
                                      capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    raise RuntimeError(f"Git clone failed: {result.stderr}")
                
                # Look for common entry points
                entry_points = ['server.py', 'main.py', 'app.py', '__main__.py']
                command = None
                for entry in entry_points:
                    if (install_dir / entry).exists():
                        command = ['python', str(install_dir / entry)]
                        break
                
                if not command:
                    raise RuntimeError("No suitable entry point found in repository")
            else:
                raise RuntimeError("Unknown installation method")
            
            # Build configuration
            server_name = service_id.lower().replace('-', '_').replace('/', '_')
            config_entries = {
                "enabled": True,
                "command": command,
                "description": service.service_description,
                "registry_provider": self.identifier,
                "service_id": service_id,
                "version": service.version,
                "support_level": service.support_level.value,
                "installed_at": datetime.now().isoformat(),
                "installation_path": str(install_dir),
                "_comment": "Installed via Ziya MCP Registry Manager"
            }
            
            return InstallationResult(
                success=True,
                service_id=service_id,
                server_name=server_name,
                installation_path=str(install_dir),
                config_entries=config_entries
            )
            
        except Exception as e:
            logger.error(f"Error installing Open MCP service {service_id}: {e}")
            return InstallationResult(
                success=False,
                service_id=service_id,
                server_name="",
                installation_path="",
                config_entries={},
                error_message=str(e)
            )
