"""
Minimal plugin system for Ziya.

This allows internal or enterprise deployments to extend Ziya with
environment-specific authentication, configuration, and integrations
without modifying the core codebase.
"""

import os
from typing import List, Optional
from app.utils.logging_utils import logger

# Global plugin registries
_auth_providers = []
_config_providers = []
_registry_providers = []
_initialized = False

def register_auth_provider(provider):
    """
    Register an authentication provider plugin.
    
    Providers are checked in priority order (highest first) to detect
    which environment we're running in.
    """
    _auth_providers.append(provider)
    _auth_providers.sort(key=lambda p: getattr(p, 'priority', 0), reverse=True)
    logger.info(f"Registered auth provider: {getattr(provider, 'provider_id', 'unknown')}")

def register_config_provider(provider):
    """Register a configuration provider plugin."""
    _config_providers.append(provider)
    logger.info(f"Registered config provider: {getattr(provider, 'provider_id', 'unknown')}")

def register_registry_provider(provider):
    """Register an MCP registry provider plugin."""
    _registry_providers.append(provider)
    logger.info(f"Registered registry provider: {getattr(provider, 'identifier', 'unknown')}")


def get_active_auth_provider():
    """
    Get the active authentication provider based on environment detection.
    
    Returns the first provider whose detect_environment() returns True,
    or the default provider if none match.
    """
    for provider in _auth_providers:
        if hasattr(provider, 'detect_environment'):
            try:
                if provider.detect_environment():
                    logger.debug(f"Active auth provider: {provider.provider_id}")
                    return provider
            except Exception as e:
                logger.warning(f"Error in {provider.provider_id}.detect_environment(): {e}")
    
    # Return lowest priority (default) provider
    return _auth_providers[-1] if _auth_providers else None

def get_active_config_providers() -> List:
    """Get all config providers that should be applied."""
    active = []
    for provider in _config_providers:
        try:
            if hasattr(provider, 'should_apply') and provider.should_apply():
                active.append(provider)
        except Exception as e:
            logger.warning(f"Error checking {provider.provider_id}.should_apply(): {e}")
    return active

def get_registry_providers() -> List:
    """Get all registered MCP registry providers."""
    return _registry_providers.copy()

def initialize():
    """Initialize plugin system and load available plugins."""
    global _initialized
    if _initialized:
        return
    
    # Register default providers first
    from app.plugins.default_providers import DefaultAuthProvider, DefaultConfigProvider
    register_auth_provider(DefaultAuthProvider())
    register_config_provider(DefaultConfigProvider())
    
    # Only load internal plugins if ZIYA_LOAD_INTERNAL_PLUGINS is set
    if os.environ.get('ZIYA_LOAD_INTERNAL_PLUGINS') == '1':
        loaded = False
        for module_name in ['plugins', 'internal.plugins']:
            try:
                import importlib
                internal_plugins = importlib.import_module(module_name)
                internal_plugins.register()
                logger.info(f"✓ Enterprise plugins loaded from {module_name}")
                loaded = True
                break
            except ImportError:
                continue
            except Exception as e:
                logger.warning(f"Failed to load enterprise plugins from {module_name}: {e}")
        
        if not loaded:
            logger.debug("No enterprise plugins found")
    else:
        logger.debug("Enterprise plugins disabled")
    
    _initialized = True
