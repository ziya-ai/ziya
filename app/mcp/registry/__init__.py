"""
MCP Registry integration system.
"""

from app.mcp.registry.interface import RegistryProvider, RegistryServiceInfo
from app.mcp.registry.registry import get_provider_registry, initialize_registry_providers
from app.mcp.registry.aggregator import get_registry_aggregator

__all__ = [
    'RegistryProvider',
    'RegistryServiceInfo',
    'get_provider_registry',
    'initialize_registry_providers',
    'get_registry_aggregator'
]
