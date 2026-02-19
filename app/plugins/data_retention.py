"""
Data retention enforcement utility.

Provides a single point of access for all storage layers to query the
effective retention policy resolved from registered plugin providers.

Usage by storage layers:
    from app.plugins.data_retention import get_retention_enforcer

    enforcer = get_retention_enforcer()
    ttl = enforcer.get_ttl_seconds("context_cache")  # returns float or None
    if enforcer.is_expired(created_at, "prompt_cache"):
        # discard the entry
"""

import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from app.utils.logging_utils import logger
from app.plugins.interfaces import DataRetentionPolicy


class DataRetentionEnforcer:
    """
    Resolves and caches the effective retention policy, and provides
    convenience methods for storage layers to check TTLs and expiration.

    The resolved policy is cached and refreshed periodically so that
    hot-path lookups don't re-merge providers on every call.
    """

    # Re-resolve the policy at most once per this interval
    _REFRESH_INTERVAL_SECONDS = 60.0

    def __init__(self):
        self._policy: Optional[DataRetentionPolicy] = None
        self._last_resolved: float = 0.0

    def _resolve(self) -> DataRetentionPolicy:
        now = time.monotonic()
        if self._policy is not None and (now - self._last_resolved) < self._REFRESH_INTERVAL_SECONDS:
            return self._policy

        from app.plugins import get_effective_retention_policy
        self._policy = get_effective_retention_policy()
        self._last_resolved = now
        if self._policy.policy_reason:
            logger.debug(f"Retention policy resolved: {self._policy.policy_reason}")
        return self._policy

    @property
    def policy(self) -> DataRetentionPolicy:
        """The current effective retention policy."""
        return self._resolve()

    def get_ttl_seconds(self, category: str) -> Optional[float]:
        """
        Get the effective TTL in seconds for *category*.

        Returns None if no provider has set a TTL for this category,
        meaning the storage layer should use its own built-in default.
        """
        return self._resolve().get_ttl_seconds(category)

    def effective_ttl(self, category: str, layer_default: float) -> float:
        """
        Return the effective TTL for *category*, falling back to the
        storage layer's own *layer_default* when no provider overrides it.

        The result is always the *shorter* of the provider TTL and the
        layer default, following least-privilege.
        """
        provider_ttl = self.get_ttl_seconds(category)
        if provider_ttl is None:
            return layer_default
        return min(provider_ttl, layer_default)

    def is_expired(self, created_at: float, category: str,
                   layer_default_ttl: Optional[float] = None) -> bool:
        """
        Check whether an item created at *created_at* (epoch seconds) has
        exceeded the retention TTL for *category*.

        If no TTL is configured (neither provider nor layer_default_ttl),
        returns False (never expires).
        """
        provider_ttl = self.get_ttl_seconds(category)
        if provider_ttl is not None and layer_default_ttl is not None:
            ttl = min(provider_ttl, layer_default_ttl)
        elif provider_ttl is not None:
            ttl = provider_ttl
        elif layer_default_ttl is not None:
            ttl = layer_default_ttl
        else:
            return False
        return time.time() > (created_at + ttl)

    def invalidate(self):
        """Force re-resolution on the next access."""
        self._policy = None
        self._last_resolved = 0.0


_enforcer: Optional[DataRetentionEnforcer] = None

def get_retention_enforcer() -> DataRetentionEnforcer:
    """Get the singleton DataRetentionEnforcer."""
    global _enforcer
    if _enforcer is None:
        _enforcer = DataRetentionEnforcer()
    return _enforcer
