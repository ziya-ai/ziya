"""
Registry provider management and configuration.
"""

import os
from typing import Dict, List, Optional, Type
import boto3
from botocore.exceptions import ClientError

from app.mcp.registry.interface import RegistryProvider
from app.mcp.registry.providers.official_mcp import OfficialMCPRegistryProvider
from app.mcp.registry.providers.smithery import SmitheryRegistryProvider
from app.mcp.registry.providers.pulsemcp import PulseMCPRegistryProvider
from app.mcp.registry.providers.awesome_list import AwesomeListRegistryProvider
from app.mcp.registry.providers.github import GitHubRegistryProvider
from app.mcp.registry.providers.open_mcp import OpenMCPProvider
from app.utils.logging_utils import logger


class RegistryProviderRegistry:
    """Registry for managing different registry providers."""
    
    def __init__(self):
        self._providers: Dict[str, RegistryProvider] = {}
        self._provider_classes: Dict[str, Type[RegistryProvider]] = {}
        self._default_providers: List[str] = []
        
    def register_provider_class(
        self, 
        identifier: str, 
        provider_class: Type[RegistryProvider],
        is_default: bool = False
    ):
        """Register a provider class for lazy initialization."""
        self._provider_classes[identifier] = provider_class
        if is_default:
            self._default_providers.append(identifier)
        
        logger.info(f"Registered registry provider class: {identifier}")
    
    def register_provider(self, provider: RegistryProvider, is_default: bool = False):
        """Register an initialized provider instance."""
        self._providers[provider.identifier] = provider
        if is_default:
            self._default_providers.append(provider.identifier)
        
        logger.info(f"Registered registry provider: {provider.identifier}")
    
    def get_provider(self, identifier: str) -> Optional[RegistryProvider]:
        """Get a provider by identifier, initializing if necessary."""
        # Return cached instance if available
        if identifier in self._providers:
            return self._providers[identifier]
        
        # Initialize from class if available
        if identifier in self._provider_classes:
            try:
                provider_class = self._provider_classes[identifier]
                provider = provider_class()
                self._providers[identifier] = provider
                return provider
            except Exception as e:
                logger.error(f"Failed to initialize provider {identifier}: {e}")
                return None
        
        return None
    
    def get_available_providers(self, include_internal: bool = True) -> List[RegistryProvider]:
        """Get all available providers, optionally filtering internal ones."""
        providers = []
        
        # Get all registered identifiers
        all_identifiers = set(self._providers.keys()) | set(self._provider_classes.keys())
        
        for identifier in all_identifiers:
            provider = self.get_provider(identifier)
            if provider and (include_internal or not provider.is_internal):
                providers.append(provider)
        
        return providers
    
    def get_default_providers(self, include_internal: bool = True) -> List[RegistryProvider]:
        """Get default providers for the current environment."""
        providers = []
        
        for identifier in self._default_providers:
            provider = self.get_provider(identifier)
            if provider and (include_internal or not provider.is_internal):
                providers.append(provider)
        
        return providers


# Global provider registry
_provider_registry = RegistryProviderRegistry()


def get_provider_registry() -> RegistryProviderRegistry:
    """Get the global provider registry."""
    return _provider_registry


def initialize_registry_providers():
    """Initialize all available registry providers based on environment."""
    registry = get_provider_registry()
    
    # Register official MCP registry (highest priority)
    registry.register_provider_class(
        "official-mcp",
        OfficialMCPRegistryProvider,
        is_default=True
    )
    
    # Register PulseMCP (large community collection)
    registry.register_provider_class(
        "pulsemcp",
        PulseMCPRegistryProvider,
        is_default=True
    )
    
    # Register Smithery (quality-focused)
    registry.register_provider_class(
        "smithery",
        SmitheryRegistryProvider,
        is_default=True
    )
    
    # Register Awesome Lists (community-maintained)
    registry.register_provider_class(
        "awesome-lists",
        AwesomeListRegistryProvider,
        is_default=True
    )
    
    # Register Open MCP (community registry)
    registry.register_provider_class(
        "open-mcp",
        OpenMCPProvider,
        is_default=True
    )
    
    # Keep GitHub provider for backwards compatibility (deprecated)
    registry.register_provider_class(
        "github",
        GitHubRegistryProvider,
        is_default=False
    )
    
    # Register any providers from plugin system
    from app.plugins import get_registry_providers as get_plugin_providers
    
    try:
        plugin_providers = get_plugin_providers()
        for provider in plugin_providers:
            registry.register_provider(provider, is_default=True)
        
        if plugin_providers:
            logger.info(f"Registered {len(plugin_providers)} registry provider(s) from plugins")
    except Exception as e:
        logger.debug(f"No plugin providers available: {e}")
    
    # Future: Add other providers here
    # registry.register_provider_class("npm", NPMRegistryProvider)
    # registry.register_provider_class("pypi", PyPIRegistryProvider)


def is_internal_environment(profile_name: str = None) -> bool:
    """
    Detect if running in an internal/enterprise environment.
    
    Uses plugin system to detect environment rather than hardcoding specific
    environment checks. This allows any enterprise to provide their own
    detection logic via plugins.
    """
    from app.plugins import get_active_auth_provider
    
    try:
        auth_provider = get_active_auth_provider()
        
        # Internal/enterprise if provider is not the default (community) provider
        if auth_provider and auth_provider.provider_id != "default":
            logger.info(f"Internal environment detected via '{auth_provider.provider_id}' provider")
            return True
        else:
            return False
            
    except Exception as e:
        logger.debug(f"Could not detect environment: {e}")
        return False
