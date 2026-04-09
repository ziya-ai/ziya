"""
Minimal plugin system for Ziya.

This allows internal or enterprise deployments to extend Ziya with
environment-specific authentication, configuration, and integrations
without modifying the core codebase.
"""

import os
from typing import Any, Dict, List, Optional
from datetime import timedelta
from app.utils.logging_utils import logger
from app.plugins.interfaces import DataRetentionPolicy, EncryptionPolicy

# Global plugin registries
_auth_providers = []
_config_providers = []
_registry_providers = []
_service_model_providers = []
_formatter_providers = []
_shell_config_providers = []
_tool_result_filter_providers = []
_tool_validator_providers = []
_data_retention_providers = []
_tool_enhancement_providers = []
_encryption_providers = []
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

def register_shell_config_provider(provider):
    """
    Register a shell configuration provider plugin.

    Providers contribute additional allowed commands, git operations,
    and interpreters that are merged into the base shell config.
    """
    _shell_config_providers.append(provider)
    _shell_config_providers.sort(key=lambda p: getattr(p, 'priority', 0), reverse=True)
    logger.debug(f"Registered shell config provider: {getattr(provider, 'provider_id', 'unknown')}")

def register_formatter_provider(provider):
    """Register a formatter provider plugin."""
    _formatter_providers.append(provider)
    logger.debug(f"Registered formatter provider: {getattr(provider, 'formatter_id', 'unknown')}")

def register_tool_result_filter_provider(provider):
    """
    Register a tool result filter provider plugin.

    Filters sanitize tool results before they enter conversation context.
    Applied in priority order (highest first); each filter receives the
    output of the previous one.
    """
    _tool_result_filter_providers.append(provider)
    _tool_result_filter_providers.sort(key=lambda p: getattr(p, 'priority', 0), reverse=True)
    logger.debug(f"Registered tool result filter provider: {getattr(provider, 'provider_id', 'unknown')}")

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

def register_service_model_provider(provider):
    """Register a service model provider plugin."""
    _service_model_providers.append(provider)
    logger.debug(f"Registered service model provider: {getattr(provider, 'provider_id', 'unknown')}")

def get_all_config_providers() -> List:
    """Get all registered config providers (regardless of should_apply)."""
    return _config_providers.copy()

def register_encryption_provider(provider):
    """
    Register an encryption provider plugin.

    When multiple providers register, the most restrictive policy wins
    (enabled beats disabled, shortest rotation interval wins).
    """
    _encryption_providers.append(provider)
    _encryption_providers.sort(key=lambda p: getattr(p, 'priority', 0), reverse=True)
    logger.info(f"Registered encryption provider: {getattr(provider, 'provider_id', 'unknown')}")

def register_tool_enhancement_provider(provider):
    """
    Register a tool enhancement provider plugin.
    
    Providers supply supplemental description text for MCP tools
    to reduce parameter errors from models.
    """
    _tool_enhancement_providers.append(provider)
    logger.debug(f"Registered tool enhancement provider: {getattr(provider, 'provider_id', 'unknown')}")



def get_tool_enhancements() -> Dict[str, Any]:
    """Get merged tool enhancements from all registered providers.
    
    Returns a dict mapping tool names to enhancement config dicts.
    Later providers override earlier ones for the same tool name.
    """
    merged = {}
    for provider in _tool_enhancement_providers:
        try:
            enhancements = provider.get_tool_enhancements()
            if enhancements:
                merged.update(enhancements)
        except Exception as e:
            logger.warning(f"Failed to get tool enhancements from provider: {e}")
    return merged
def get_shell_config_providers() -> List:
    """Get all registered shell config providers."""
    return _shell_config_providers.copy()


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


def get_allowed_endpoints() -> Optional[List[str]]:
    """
    Resolve the effective allowed endpoints across all active config providers.

    Returns the intersection of all provider restrictions.
    Returns None if no provider declares a restriction (all endpoints allowed).
    """
    restrictions = []
    for provider in get_active_config_providers():
        try:
            if not hasattr(provider, 'get_allowed_endpoints'):
                continue
            allowed = provider.get_allowed_endpoints()
            if allowed is not None:
                restrictions.append(list(allowed))
        except Exception as e:
            logger.warning(f"Error getting allowed endpoints from {getattr(provider, 'provider_id', '?')}: {e}")

    if not restrictions:
        return None
    result = set(restrictions[0])
    for r in restrictions[1:]:
        result &= set(r)
    return sorted(result)


def get_shell_config_additions() -> dict:
    """
    Collect shell config additions from all active shell config providers.

    Returns a dict with keys:
      - additional_commands: list[str]
      - additional_git_operations: list[str]
      - additional_interpreters: list[str]
      - additional_write_patterns: list[str]
      - providers: list[str]  (provider_ids that contributed)
    """
    commands = []
    git_ops = []
    interpreters = []
    write_patterns = []
    providers = []

    for provider in _shell_config_providers:
        try:
            if hasattr(provider, 'should_apply') and not provider.should_apply():
                continue
            pid = getattr(provider, 'provider_id', 'unknown')
            providers.append(pid)

            for cmd in provider.get_additional_commands():
                if cmd not in commands:
                    commands.append(cmd)
            for op in provider.get_additional_git_operations():
                if op not in git_ops:
                    git_ops.append(op)
            for interp in provider.get_additional_interpreters():
                if interp not in interpreters:
                    interpreters.append(interp)
            for pat in provider.get_additional_write_patterns():
                if pat not in write_patterns:
                    write_patterns.append(pat)
        except Exception as e:
            logger.warning(f"Error collecting shell config from {getattr(provider, 'provider_id', '?')}: {e}")

    return {
        "additional_commands": commands,
        "additional_git_operations": git_ops,
        "additional_interpreters": interpreters,
        "additional_write_patterns": write_patterns,
        "providers": providers,
    }


def get_registry_providers() -> List:
    """Get all registered MCP registry providers."""
    return _registry_providers.copy()

def get_formatter_providers() -> List:
    """Get all registered formatter providers."""
    return _formatter_providers.copy()

def get_tool_result_filter_providers() -> List:
    """Get all registered tool result filter providers."""
    return _tool_result_filter_providers.copy()

def get_tool_validator_providers() -> List:
    """Get all registered tool validator providers."""
    return _tool_validator_providers.copy()

def get_data_retention_providers() -> List:
    """Get all registered data retention providers."""
    return _data_retention_providers.copy()

def get_service_model_providers() -> List:
    """Get all registered service model providers sorted by priority."""
    return sorted(_service_model_providers, key=lambda p: getattr(p, 'priority', 0), reverse=True)


def get_enabled_service_tool_categories() -> set:
    """
    Collect all builtin tool categories that service model providers
    want enabled.  Returns a set of category name strings.
    """
    enabled = set()
    for provider in get_service_model_providers():
        try:
            if provider.should_apply():
                enabled.update(provider.get_enabled_service_tools())
        except Exception as e:
            logger.warning(f"Service model provider error: {e}")
    return enabled

def get_effective_retention_policy() -> DataRetentionPolicy:
    """
    Resolve the effective data retention policy from all registered providers.

    For each TTL category, the most restrictive (shortest non-None) value
    across all active providers is used. This follows least-privilege:
    if any provider demands a shorter TTL, that wins.

    After merging, the ``ZIYA_RETENTION_OVERRIDE_DAYS`` environment variable
    is checked.  When set to a positive number, every TTL category in the
    merged policy is raised to *at least* that many days.  This allows a
    local administrator to relax an overly aggressive corporate policy
    without modifying the plugin itself.  Set to ``0`` to disable the
    override (same as unsetting the variable).

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

    # ── Local override: ZIYA_RETENTION_OVERRIDE_DAYS ─────────────────
    # Raises every TTL to at least N days.  Useful when a corporate
    # plugin enforces a short TTL that doesn't suit local workflows.
    override_env = os.environ.get("ZIYA_RETENTION_OVERRIDE_DAYS")
    if override_env:
        try:
            override_days = float(override_env)
            if override_days == 0:
                # 0 means "no retention limit" — disables all plugin-provided TTLs
                return DataRetentionPolicy(
                    policy_reason="retention-disabled: ZIYA_RETENTION_OVERRIDE_DAYS=0",
                )
            elif override_days > 0:
                override_td = timedelta(days=override_days)
                for field_name in ttl_fields:
                    current = merged_kwargs.get(field_name)
                    if current is not None and current < override_td:
                        logger.info(
                            f"Retention override: {field_name} raised from "
                            f"{current.days}d to {override_days}d "
                            f"(ZIYA_RETENTION_OVERRIDE_DAYS)"
                        )
                        merged_kwargs[field_name] = override_td
                for key, current in list(merged_custom.items()):
                    if current < override_td:
                        logger.info(
                            f"Retention override: custom/{key} raised from "
                            f"{current.days}d to {override_days}d"
                        )
                        merged_custom[key] = override_td
                reasons.append(f"local override: {override_days}d minimum (ZIYA_RETENTION_OVERRIDE_DAYS)")
        except ValueError:
            logger.warning(f"Invalid ZIYA_RETENTION_OVERRIDE_DAYS value: {override_env!r} (expected number)")

    return DataRetentionPolicy(
        **merged_kwargs,
        custom_ttls=merged_custom,
        policy_reason="; ".join(reasons) if reasons else "merged from providers",
    )


def get_encryption_providers() -> List:
    """Return all registered encryption providers."""
    return list(_encryption_providers)


def get_effective_encryption_policy() -> EncryptionPolicy:
    """
    Resolve the effective encryption policy from all registered providers.

    Merge rules (most restrictive wins):
    - enabled: True if ANY provider requires it
    - kek_source: highest-priority provider's source
    - dek_rotation_interval: shortest non-None interval
    - kek_rotation_interval: shortest non-None interval
    - categories_requiring_encryption: union of all providers' sets

    If no provider is registered, checks ZIYA_ENCRYPTION_KEY env var
    for community passphrase-based encryption.

    Returns:
        EncryptionPolicy with the effective merged settings.
    """
    import os

    active_policies = []
    reasons = []

    for provider in _encryption_providers:
        try:
            if hasattr(provider, 'should_apply') and not provider.should_apply():
                continue
            policy = provider.get_encryption_policy()
            active_policies.append((provider, policy))
            if policy.policy_reason:
                reasons.append(f"{getattr(provider, 'provider_id', '?')}: {policy.policy_reason}")
        except Exception as e:
            logger.warning(f"Error getting encryption policy from {getattr(provider, 'provider_id', '?')}: {e}")

    # Fallback: check env var for community passphrase-based encryption
    if not active_policies and os.environ.get('ZIYA_ENCRYPTION_KEY'):
        return EncryptionPolicy(
            enabled=True,
            kek_source="passphrase",
            dek_rotation_interval=timedelta(days=90),
            policy_reason="user-configured via ZIYA_ENCRYPTION_KEY",
        )

    if not active_policies:
        return EncryptionPolicy(policy_reason="system default (encryption disabled)")

    # Merge: most restrictive wins
    enabled = any(p.enabled for _, p in active_policies)
    # KEK source from highest-priority provider that has encryption enabled
    kek_source = "none"
    kek_config: Dict[str, Any] = {}
    for provider, policy in active_policies:
        if policy.enabled and policy.kek_source != "none":
            kek_source = policy.kek_source
            kek_config = policy.kek_config
            break

    dek_intervals = [p.dek_rotation_interval for _, p in active_policies if p.dek_rotation_interval is not None]
    kek_intervals = [p.kek_rotation_interval for _, p in active_policies if p.kek_rotation_interval is not None]
    all_categories: set = set()
    for _, p in active_policies:
        all_categories.update(p.categories_requiring_encryption)

    return EncryptionPolicy(
        enabled=enabled,
        kek_source=kek_source,
        kek_config=kek_config,
        dek_rotation_interval=min(dek_intervals) if dek_intervals else None,
        kek_rotation_interval=min(kek_intervals) if kek_intervals else None,
        categories_requiring_encryption=all_categories,
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
