"""
MCP Registry Integration Manager that works with multiple registry providers.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from app.mcp.registry.registry import get_provider_registry, initialize_registry_providers
from app.mcp.registry.interface import RegistryServiceInfo, ToolSearchResult
from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger


class RegistryIntegrationManager:
    """Manages integration between MCP registries and local configuration."""
    
    def __init__(self):
        """Initialize the registry integration manager."""
        initialize_registry_providers()
        self.provider_registry = get_provider_registry()
        self.mcp_manager = get_mcp_manager()
        self.config_path = self._get_config_path()
    
    def _get_config_path(self) -> str:
        """Get the path to the MCP configuration file."""
        # Use the same logic as MCPManager to find config
        cwd_config = Path.cwd() / "mcp_config.json"
        if cwd_config.exists():
            return str(cwd_config)
        
        # Check project root
        project_root_config = Path(__file__).resolve().parents[2] / "mcp_config.json"
        if project_root_config.exists():
            return str(project_root_config)
        
        # Default to user's Ziya directory
        user_config = Path.home() / ".ziya" / "mcp_config.json"
        user_config.parent.mkdir(exist_ok=True)
        return str(user_config)
    
    def _load_current_config(self) -> Dict[str, Any]:
        """Load the current MCP configuration."""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return json.load(f)
        return {"mcpServers": {}}
    
    def _save_config(self, config: Dict[str, Any]) -> None:
        """Save the MCP configuration."""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    def get_available_providers(self) -> List[str]:
        """Get list of available registry provider identifiers."""
        # Check environment to determine what to show
        include_internal = self._should_include_internal_providers()
        providers = self.provider_registry.get_available_providers(include_internal)
        return [p.identifier for p in providers]
    
    def _should_include_internal_providers(self) -> bool:
        """Determine if internal providers should be included."""
        # This could be based on user permissions, environment, etc.
        return os.getenv('ZIYA_INCLUDE_INTERNAL_REGISTRIES', 'false').lower() == 'true'
    
    async def get_available_services(self, max_results: int = 100, provider_filter: Optional[List[str]] = None) -> List[RegistryServiceInfo]:
        """Get all available services from all configured registries."""
        all_services = []
        
        providers = self.provider_registry.get_available_providers(self._should_include_internal_providers())
        if provider_filter:
            providers = [p for p in providers if p.identifier in provider_filter]
        
        for provider in providers:
            try:
                result = await provider.list_services(max_results=max_results)
                services = result['services']
                
                # Add provider information to each service
                for service in services:
                    service.provider_metadata = service.provider_metadata or {}
                    service.provider_metadata.update({
                        'provider_name': provider.name,
                        'provider_id': provider.identifier,
                        'is_internal': provider.is_internal
                    })
                
                all_services.extend(services)
                    
            except Exception as e:
                logger.error(f"Error fetching services from provider {provider.identifier}: {e}")
                continue
        
        return all_services[:max_results]
    
    async def search_services_by_tools(self, query: str, provider_filter: Optional[List[str]] = None) -> List[ToolSearchResult]:
        """Search for services that provide tools matching a query across all providers."""
        all_results = []
        
        providers = self.provider_registry.get_available_providers(self._should_include_internal_providers())
        if provider_filter:
            providers = [p for p in providers if p.identifier in provider_filter]
        
        for provider in providers:
            if not provider.supports_search:
                continue
                
            try:
                results = await provider.search_tools(query, max_results=10)
                
                # Add provider metadata to results
                for result in results:
                    result.service.provider_metadata = result.service.provider_metadata or {}
                    result.service.provider_metadata.update({
                        'provider_name': provider.name,
                        'provider_id': provider.identifier,
                        'is_internal': provider.is_internal
                    })
                
                all_results.extend(results)
                
            except Exception as e:
                logger.error(f"Error searching tools in provider {provider.identifier}: {e}")
                continue
        
        # Sort by relevance score if available
        all_results.sort(key=lambda x: x.relevance_score or 0, reverse=True)
        return all_results[:20]  # Limit total results
    
    async def install_service(self, service_id: str, provider_id: Optional[str] = None) -> Dict[str, Any]:
        """Install an MCP service from any available registry."""
        try:
            # Find the provider that has this service
            provider = None
            if provider_id:
                provider = self.provider_registry.get_provider(provider_id)
            else:
                # Search all providers
                for p in self.provider_registry.get_available_providers(self._should_include_internal_providers()):
                    try:
                        await p.get_service_detail(service_id)
                        provider = p
                        break
                    except Exception:
                        continue
            
            if not provider:
                return {
                    'status': 'error',
                    'error': f'Service {service_id} not found in any available registry'
                }
            
            # Install using the provider
            result = await provider.install_service(service_id, self.config_path)
            
            if not result.success:
                return {
                    'status': 'error',
                    'service_id': service_id,
                    'error': result.error_message
                }
            
            # Add to local configuration
            self._add_to_config(result.server_name, result.config_entries)
            
            # Restart MCP manager to pick up new service
            if self.mcp_manager.is_initialized:
                await self.mcp_manager.restart_server(result.server_name, result.config_entries)
            
            return {
                'status': 'success',
                'service_id': result.service_id,
                'server_name': result.server_name,
                'provider': provider.identifier,
                'installation_path': result.installation_path,
                'config_updated': True
            }
            
        except Exception as e:
            logger.error(f"Error installing service {service_id}: {e}")
            return {
                'status': 'error',
                'service_id': service_id,
                'error': str(e)
            }
    
    def _add_to_config(self, server_name: str, config_entries: Dict[str, Any]) -> None:
        """Add configuration entries to mcp_config.json."""
        config = self._load_current_config()
        config["mcpServers"][server_name] = config_entries
        self._save_config(config)
    
    def get_installed_services(self) -> List[Dict[str, Any]]:
        """Get list of currently installed registry services."""
        config = self._load_current_config()
        installed = []
        
        for server_name, server_config in config["mcpServers"].items():
            if server_config.get('registry_provider'):
                installed.append({
                    'server_name': server_name,
                    'service_id': server_config.get('service_id'),
                    'service_name': server_config.get('description', server_name),
                    'version': server_config.get('version'),
                    'support_level': server_config.get('support_level'),
                    'installed_at': server_config.get('installed_at'),
                    'enabled': server_config.get('enabled', True),
                    'provider': server_config.get('registry_provider'),
                    'installation_path': server_config.get('installation_path')
                })
        
        return installed
    
    async def uninstall_service(self, server_name: str) -> Dict[str, Any]:
        """Uninstall a registry service."""
        try:
            config = self._load_current_config()
            
            if server_name not in config["mcpServers"]:
                return {'status': 'error', 'error': f'Server {server_name} not found'}
            
            server_config = config["mcpServers"][server_name]
            
            # Only allow uninstalling registry services
            if not server_config.get('registry_provider'):
                return {'status': 'error', 'error': 'Cannot uninstall non-registry services'}
            
            # Remove installation directory if it exists
            install_path = server_config.get('installation_path')
            if install_path and os.path.exists(install_path):
                import shutil
                shutil.rmtree(install_path)
                logger.info(f"Removed installation directory: {install_path}")
            
            # Remove from configuration
            del config["mcpServers"][server_name]
            self._save_config(config)
            
            # Restart MCP manager
            if self.mcp_manager.is_initialized:
                await self.mcp_manager.shutdown()
                await self.mcp_manager.initialize()
            
            return {
                'status': 'success',
                'server_name': server_name,
                'uninstalled': True
            }
            
        except Exception as e:
            logger.error(f"Error uninstalling service {server_name}: {e}")
            return {
                'status': 'error',
                'server_name': server_name,
                'error': str(e)
            }
    
    def get_installed_services(self) -> List[Dict[str, Any]]:
        """Get list of currently installed registry services."""
        config = self._load_current_config()
        installed = []
        
        for server_name, server_config in config["mcpServers"].items():
            if server_config.get('registry_service'):
                installed.append({
                    'server_name': server_name,
                    'service_id': server_config.get('service_id'),
                    'service_name': server_config.get('description', server_name),
                    'version': server_config.get('version'),
                    'support_level': server_config.get('support_level'),
                    'installed_at': server_config.get('installed_at'),
                    'enabled': server_config.get('enabled', True)
                })
        
        return installed
    
    async def uninstall_service(self, server_name: str) -> Dict[str, Any]:
        """Uninstall a registry service."""
        try:
            config = self._load_current_config()
            
            if server_name not in config["mcpServers"]:
                return {'status': 'error', 'error': f'Server {server_name} not found'}
            
            server_config = config["mcpServers"][server_name]
            
            # Only allow uninstalling registry services
            if not server_config.get('registry_service'):
                return {'status': 'error', 'error': 'Cannot uninstall non-registry services'}
            
            # Remove installation directory if it exists
            install_path = server_config.get('installation_path')
            if install_path and os.path.exists(install_path):
                import shutil
                shutil.rmtree(install_path)
                logger.info(f"Removed installation directory: {install_path}")
            
            # Remove from configuration
            del config["mcpServers"][server_name]
            self._save_config(config)
            
            # Restart MCP manager
            if self.mcp_manager.is_initialized:
                await self.mcp_manager.shutdown()
                await self.mcp_manager.initialize()
            
            return {
                'status': 'success',
                'server_name': server_name,
                'uninstalled': True
            }
            
        except Exception as e:
            logger.error(f"Error uninstalling service {server_name}: {e}")
            return {
                'status': 'error',
                'server_name': server_name,
                'error': str(e)
            }


# Global registry integration manager
_registry_manager: Optional[RegistryIntegrationManager] = None

def get_registry_manager() -> RegistryIntegrationManager:
    """Get the global registry integration manager."""
    global _registry_manager
    if _registry_manager is None:
        _registry_manager = RegistryIntegrationManager()
    return _registry_manager
