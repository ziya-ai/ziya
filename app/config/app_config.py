"""
General application configuration for Ziya.

This module contains application-wide settings that are not specific to models or shell commands.
"""
import os

# Canonical truthy strings for boolean environment variables.
# Every boolean env var in the codebase should be parsed through env_bool()
# so that "true", "1", "yes", "TRUE", "True" all behave identically.
_TRUTHY = frozenset({"true", "1", "yes"})


def env_bool(key: str, default: bool = False) -> bool:
    """Parse a boolean environment variable with consistent truthy handling.

    Accepts ``"true"``, ``"1"``, ``"yes"`` (case-insensitive).
    Returns *default* when the variable is unset.
    """
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in _TRUTHY


USE_DIRECT_STREAMING = env_bool('ZIYA_USE_DIRECT_STREAMING')

# Server configuration
DEFAULT_PORT = 6969

# Diff validation settings
ENABLE_DIFF_VALIDATION = env_bool('ZIYA_ENABLE_DIFF_VALIDATION', True)
AUTO_REGENERATE_INVALID_DIFFS = env_bool('ZIYA_AUTO_REGENERATE_INVALID_DIFFS', True)
AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE = env_bool('ZIYA_AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE', True)
