"""
Plugin interfaces for Ziya.

These define the contracts that plugin providers must implement.
"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional, Any, Dict, List
from dataclasses import dataclass, field
from datetime import timedelta

class AuthProvider(ABC):
    """
    Authentication provider interface.
    
    Plugins implement this to provide environment-specific authentication
    (e.g., Midway for Amazon, SSO for other enterprises).
    """
    
    provider_id: str = "default"
    priority: int = 0  # Higher priority providers are checked first
    
    def detect_environment(self) -> bool:
        """
        Return True if this provider should handle authentication.
        Called in priority order during startup.
        """
        return False
    
    @abstractmethod
    def check_credentials(
        self, 
        profile_name: Optional[str] = None,
        region: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Check if credentials are valid.
        
        Returns:
            (is_valid, message) - message explains status
        """
        pass
    
    @abstractmethod
    def get_credential_help_message(self, error_context: Optional[str] = None) -> str:
        """Return help text for credential issues."""
        pass
    
    def is_auth_error(self, error_str: str) -> bool:
        """Detect if error string indicates authentication failure."""
        indicators = ['credential', 'authentication', 'unauthorized', 'expired', 'token']
        return any(ind in error_str.lower() for ind in indicators)


class ConfigProvider(ABC):
    """
    Configuration provider interface.
    
    Plugins implement this to provide environment-specific defaults.
    """
    
    provider_id: str = "default"
    priority: int = 0
    
    @abstractmethod
    def get_defaults(self) -> Dict[str, Any]:
        """
        Return default configuration dictionary.
        
        User config and CLI args override these defaults.
        """
        pass
    
    def should_apply(self) -> bool:
        """Return True if this config should be applied."""
        return True


class FormatterProvider(ABC):
    """
    Formatter provider interface.
    
    Plugins implement this to provide custom tool output formatters
    that enhance the display of tool results in the UI.
    """
    
    formatter_id: str = "default"
    priority: int = 0
    
    @abstractmethod
    def get_formatter_code(self) -> str:
        """Return JavaScript module code that exports a ToolFormatter."""
        pass


@dataclass
class DataRetentionPolicy:
    """
    Expiration policy for stored information across the system.

    Each field specifies the maximum time-to-live for a category of data.
    A value of None means "use the system default" (no override).
    A value of timedelta(0) means "do not persist at all" (ephemeral only).

    If default_ttl is set, it applies to all categories that don't have
    an explicit per-category override. This is the simplest way for an
    enterprise to say "all data expires after N days".

    When multiple providers register policies, the most restrictive
    (shortest non-None) TTL for each category wins.
    """

    # Chat history and conversation state
    conversation_data_ttl: Optional[timedelta] = None

    # Blanket TTL applied to all categories unless overridden.
    # Set this to e.g. timedelta(days=90) to expire all data after 90 days.
    default_ttl: Optional[timedelta] = None

    # Bedrock context cache entries
    context_cache_ttl: Optional[timedelta] = None

    # On-disk prompt structure cache
    prompt_cache_ttl: Optional[timedelta] = None

    # MCP tool result validity window
    tool_result_ttl: Optional[timedelta] = None

    # File change tracking state
    file_state_ttl: Optional[timedelta] = None

    # Overall session maximum lifetime
    session_max_ttl: Optional[timedelta] = None

    # Human-readable reason for this policy (for audit logging)
    policy_reason: str = ""

    # Additional category TTLs for enterprise-specific data types.
    # Keys are category names, values are timedelta TTLs.
    custom_ttls: Dict[str, timedelta] = field(default_factory=dict)

    def get_ttl_seconds(self, category: str) -> Optional[float]:
        """
        Get TTL in seconds for a named category.

        Checks the typed fields first, then falls back to custom_ttls.
        Returns None if no TTL is set for this category.
        """
        attr_name = f"{category}_ttl" if not category.endswith("_ttl") else category
        # Check explicit per-category field first
        value = getattr(self, attr_name, None)
        if value is not None:
            return value.total_seconds()
        # Then custom_ttls
        custom = self.custom_ttls.get(category)
        if custom is not None:
            return custom.total_seconds()
        # Fall back to blanket default_ttl
        if self.default_ttl is not None:
            return self.default_ttl.total_seconds()
        return None


class DataRetentionProvider(ABC):
    """
    Data retention / expiration policy provider.

    Plugins implement this to enforce organization-specific data retention
    requirements. For example, a security team may require that all
    conversation data expires after 8 hours, or that tool results are
    never cached to disk.

    When multiple providers register, the most restrictive (shortest)
    TTL for each category is used.
    """

    provider_id: str = "default"
    priority: int = 0

    @abstractmethod
    def get_retention_policy(self) -> DataRetentionPolicy:
        """Return the data retention policy for this provider."""
        pass

    def should_apply(self) -> bool:
        """Return True if this retention policy should be applied."""
        return True
