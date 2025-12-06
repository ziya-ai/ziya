"""
MCP Registry Integration Manager that works with multiple registry providers.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from app.mcp.registry.registry import get_provider_registry, initialize_registry_providers
from app.mcp.registry.interface import RegistryServiceInfo, ToolSearchResult
from app.mcp.registry.aggregator import get_registry_aggregator
from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger


class RegistryIntegrationManager:
    """Manages integration between MCP registries and local configuration."""
    
    def __init__(self):
        """Initialize the registry integration manager."""
        initialize_registry_providers()
        self.provider_registry = get_provider_registry()
        self.aggregator = get_registry_aggregator()
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
        # Check if any internal providers are registered via plugins
        try:
            from app.mcp.registry.registry import is_internal_environment
            if is_internal_environment():
                return True
        except Exception:
            pass
        
        # Fall back to environment variable
        return os.getenv('ZIYA_INCLUDE_INTERNAL_REGISTRIES', 'false').lower() == 'true'
    
    async def get_available_services(self, max_results: int = 100, provider_filter: Optional[List[str]] = None) -> List[RegistryServiceInfo]:
        """Get unified list of services with deduplication across all registries."""
        # Use aggregator for unified results
        services = await self.aggregator.get_all_services(
            max_results=max_results,
            include_internal=self._should_include_internal_providers()
        )
        
        # Apply provider filter if specified
        if provider_filter:
            services = [
                s for s in services 
                if s.provider_metadata.get('provider_id') in provider_filter or
                   any(p in provider_filter for p in s.provider_metadata.get('available_in', []))
            ]
        
        return services
    
    async def search_services_by_tools(self, query: str, provider_filter: Optional[List[str]] = None) -> List[ToolSearchResult]:
        """Search for services using unified aggregator."""
        results = await self.aggregator.search_unified(
            query=query,
            max_results=100,
            include_internal=self._should_include_internal_providers()
        )
        
        # Apply provider filter if specified
        if provider_filter:
            results = [
                r for r in results
                if r.service.provider_metadata.get('provider_id') in provider_filter or
                   any(p in provider_filter for p in r.service.provider_metadata.get('available_in', []))
            ]
        
        return results
    
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
        if "mcpServers" not in config:
            config["mcpServers"] = {}
        config["mcpServers"][server_name] = config_entries
        self._save_config(config)
    
    def _match_installed_with_registry(self, installed_services: List[Dict], registry_services: List[RegistryServiceInfo]) -> List[Dict]:
        """Match installed services with registry services for unified display."""
        matched_services = []
        
        for installed in installed_services:
            # Try exact match first (service_id or server_name)
            registry_match = None
            service_id = installed.get('service_id') or installed.get('server_name')
            if service_id:
                registry_match = next((s for s in registry_services if s.service_id == service_id), None)
                # Debug logging
                if service_id == 'builder-mcp' and not registry_match:
                    logger.info(f"No registry match for builder-mcp. Available service IDs: {[s.service_id for s in registry_services if 'builder' in s.service_id.lower()]}")
            
            # Try fuzzy matching by name if no exact match
            if not registry_match and installed.get('server_name'):
                server_name = installed['server_name'].lower()
                # Try different matching strategies
                for service in registry_services:
                    service_name_lower = service.service_name.lower()
                    service_id_lower = service.service_id.lower()
                    
                    # Direct name match
                    if server_name == service_name_lower or server_name == service_id_lower:
                        registry_match = service
                        break
                    
                    # Partial match (e.g., "builder-mcp" matches "BuilderHub MCP Server")
                    if (server_name.replace('-', '').replace('_', '') in service_name_lower.replace('-', '').replace('_', '').replace(' ', '') or
                        server_name.replace('-', '').replace('_', '') in service_id_lower.replace('-', '').replace('_', '')):
                        registry_match = service
                        break
                    
                    # Reverse partial match
                    if (service_name_lower.replace('-', '').replace('_', '').replace(' ', '') in server_name.replace('-', '').replace('_', '') or
                        service_id_lower.replace('-', '').replace('_', '') in server_name.replace('-', '').replace('_', '')):
                        registry_match = service
                        break
            
            # Create unified service entry
            service_entry = {
                'server_name': installed['server_name'],
                'service_id': registry_match.service_id if registry_match else (installed.get('service_id') or installed['server_name']),
                'service_name': installed.get('service_name', installed['server_name']),
                'version': installed.get('version'),
                'registry_provider': installed.get('registry_provider'),
                'support_level': installed.get('support_level'),
                'installed_at': installed.get('installed_at'),
                'enabled': installed.get('enabled', True),
                'is_installed': True,
                'installation_path': installed.get('installation_path'),
                'security_review_url': installed.get('security_review_url'),
                '_manually_configured': not installed.get('registry_provider')  # Only manual if no registry provider
            }
            
            # Add registry information if matched
            if registry_match:
                service_entry.update({
                    'service_name': registry_match.service_name,
                    'serviceDescription': registry_match.service_description,
                    'supportLevel': registry_match.support_level.value,
                    'status': registry_match.status.value,
                    'version': registry_match.version,
                    'provider': {
                        'id': registry_match.provider_metadata.get('provider_id'),
                        'isInternal': registry_match.provider_metadata.get('is_internal', False)
                    },
                    'tags': registry_match.tags,
                    'securityReviewLink': registry_match.security_review_url,
                    'installationType': registry_match.installation_type.value,
                    'cti': registry_match.provider_metadata.get('cti'),
                    'registry_matched': True
                })
            
            matched_services.append(service_entry)
        
        return matched_services
    
    async def get_installed_services(self) -> List[Dict[str, Any]]:
        """Get list of currently installed services with registry matching."""
        config = self._load_current_config()
        installed = []
        
        logger.info(f"Found {len(config['mcpServers'])} servers in config")
        
        # Get basic installed service info
        for server_name, server_config in config["mcpServers"].items():
            logger.info(f"Processing server: {server_name}, config: {server_config}")
            installed.append({
                'server_name': server_name,
                'service_id': server_config.get('service_id'),
                'service_name': server_config.get('description', server_name),
                'version': server_config.get('version'),
                'support_level': server_config.get('support_level'),
                'installed_at': server_config.get('installed_at'),
                'enabled': server_config.get('enabled', True),
                'registry_provider': server_config.get('registry_provider'),
                'installation_path': server_config.get('installation_path'),
                'security_review_url': server_config.get('security_review_url')
            })
        
        logger.info(f"Built installed list with {len(installed)} services")
        
        # Get registry services for matching
        try:
            registry_services = await self.get_available_services(max_results=1000)
            matched_services = self._match_installed_with_registry(installed, registry_services)
            return matched_services
        except Exception as e:
            logger.warning(f"Could not match with registry services: {e}")
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
        return self._get_installed_from_config(config)

    def _get_installed_from_config(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract installed services from configuration."""
        installed = []
        
        for server_name, server_config in config.get("mcpServers", {}).items():
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


# Global registry integration manager
_registry_manager: Optional[RegistryIntegrationManager] = None

def get_registry_manager() -> RegistryIntegrationManager:
    """Get the global registry integration manager."""
    global _registry_manager
    if _registry_manager is None:
        _registry_manager = RegistryIntegrationManager()
    return _registry_manager
