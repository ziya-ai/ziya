"""
Minimal plugin system for Ziya.

This allows internal or enterprise deployments to extend Ziya with
environment-specific authentication, configuration, and integrations
without modifying the core codebase.
"""

import os
from typing import List, Optional
from datetime import timedelta
from app.utils.logging_utils import logger
from app.plugins.interfaces import DataRetentionPolicy

# Global plugin registries
_auth_providers = []
_config_providers = []
_registry_providers = []
_formatter_providers = []
_tool_validator_providers = []
_data_retention_providers = []
_initialized = False

def register_auth_provider(provider):
    """
    Register an authentication provider plugin.
    
    Providers are checked in priority order (highest first) to detect
    which environment we're running in.
    """
    _auth_providers.append(provider)
    _auth_providers.sort(key=lambda p: getattr(p, 'priority', 0), reverse=True)
    logger.debug(f"Registered auth provider: {getattr(provider, 'provider_id', 'unknown')}")

def register_config_provider(provider):
    """Register a configuration provider plugin."""
    _config_providers.append(provider)
    logger.debug(f"Registered config provider: {getattr(provider, 'provider_id', 'unknown')}")

def register_registry_provider(provider):
    """Register an MCP registry provider plugin."""
    _registry_providers.append(provider)
    # Suppress in chat mode - only show in server mode
    logger.debug(f"Registered registry provider: {getattr(provider, 'identifier', 'unknown')}")

def register_formatter_provider(provider):
    """Register a formatter provider plugin."""
    _formatter_providers.append(provider)
    logger.debug(f"Registered formatter provider: {getattr(provider, 'formatter_id', 'unknown')}")

def register_tool_validator_provider(provider):
    """
    Register a tool validator provider plugin.
    
    Validators can provide tool-specific argument validation and
    self-correcting error messages for internal/enterprise tools.
    """
    _tool_validator_providers.append(provider)
    logger.debug(f"Registered tool validator provider: {getattr(provider, 'validator_id', 'unknown')}")

def register_data_retention_provider(provider):
    """
    Register a data retention policy provider.

    When multiple providers register, the most restrictive (shortest)
    TTL for each category wins during policy resolution.
    """
    _data_retention_providers.append(provider)
    _data_retention_providers.sort(key=lambda p: getattr(p, 'priority', 0), reverse=True)
    logger.debug(f"Registered data retention provider: {getattr(provider, 'provider_id', 'unknown')}")

def get_all_config_providers() -> List:
    """Get all registered config providers (regardless of should_apply)."""
    return _config_providers.copy()


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

def get_formatter_providers() -> List:
    """Get all registered formatter providers."""
    return _formatter_providers.copy()

def get_tool_validator_providers() -> List:
    """Get all registered tool validator providers."""
    return _tool_validator_providers.copy()

def get_data_retention_providers() -> List:
    """Get all registered data retention providers."""
    return _data_retention_providers.copy()

def get_effective_retention_policy() -> DataRetentionPolicy:
    """
    Resolve the effective data retention policy from all registered providers.

    For each TTL category, the most restrictive (shortest non-None) value
    across all active providers is used. This follows least-privilege:
    if any provider demands a shorter TTL, that wins.

    Returns:
        DataRetentionPolicy with the effective (most restrictive) TTLs.
    """
    active_policies = []
    reasons = []

    for provider in _data_retention_providers:
        try:
            if hasattr(provider, 'should_apply') and not provider.should_apply():
                continue
            policy = provider.get_retention_policy()
            active_policies.append(policy)
            if policy.policy_reason:
                reasons.append(f"{getattr(provider, 'provider_id', '?')}: {policy.policy_reason}")
        except Exception as e:
            logger.warning(f"Error getting retention policy from {getattr(provider, 'provider_id', '?')}: {e}")

    if not active_policies:
        return DataRetentionPolicy(policy_reason="system default (no providers)")

    # Merge: most restrictive (shortest non-None) TTL per category wins
    ttl_fields = [
        'conversation_data_ttl', 'context_cache_ttl', 'prompt_cache_ttl',
        'tool_result_ttl', 'file_state_ttl', 'session_max_ttl', 'default_ttl',
    ]
    merged_kwargs = {}

    for field_name in ttl_fields:
        candidates = [getattr(p, field_name) for p in active_policies if getattr(p, field_name) is not None]
        if candidates:
            merged_kwargs[field_name] = min(candidates)

    # Merge custom_ttls: shortest per key
    all_custom_keys = set()
    for p in active_policies:
        all_custom_keys.update(p.custom_ttls.keys())
    merged_custom = {}
    for key in all_custom_keys:
        candidates = [p.custom_ttls[key] for p in active_policies if key in p.custom_ttls]
        if candidates:
            merged_custom[key] = min(candidates)

    return DataRetentionPolicy(
        **merged_kwargs,
        custom_ttls=merged_custom,
        policy_reason="; ".join(reasons) if reasons else "merged from providers",
    )

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
                # Suppress in chat mode - only show in server mode
                logger.debug(f"✓ Enterprise plugins loaded from {module_name}")
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
