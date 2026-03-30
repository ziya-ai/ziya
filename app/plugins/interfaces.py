"""
Plugin interfaces for Ziya.

These define the contracts that plugin providers must implement.
"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional, Any, Dict, List, Set
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

    def get_allowed_endpoints(self) -> Optional[List[str]]:
        """
        Return the list of permitted endpoints for this deployment.

        Return None (default) to allow all endpoints.
        Return ['bedrock'] to restrict to Bedrock only.
        When multiple providers declare restrictions, the intersection is used.
        """
        return None


class ShellConfigProvider(ABC):
    """
    Shell configuration provider interface.

    Plugins implement this to register additional shell commands, git
    operations, and interpreters that should be allowed by default in
    their environment.  Additions are merged on top of the base
    ``DEFAULT_SHELL_CONFIG`` so enterprise plugins can extend the
    command allowlist without modifying core code.
    """

    provider_id: str = "default"
    priority: int = 0

    @abstractmethod
    def get_additional_commands(self) -> List[str]:
        """
        Return shell commands to add to the default allowlist.

        These are merged (union) with ``DEFAULT_SHELL_CONFIG["allowedCommands"]``.
        """
        pass

    def get_additional_git_operations(self) -> List[str]:
        """
        Return git sub-commands to add to the safe git operations list.

        Default returns empty (no extra git ops).
        """
        return []

    def get_additional_interpreters(self) -> List[str]:
        """Return interpreter commands to add to the allowed interpreters."""
        return []

    def get_additional_write_patterns(self) -> List[str]:
        """Return file-path glob patterns to add to allowed write patterns."""
        return []

    def should_apply(self) -> bool:
        """Return True if this provider's additions should be applied."""
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


class ToolResultFilterProvider(ABC):
    """
    Tool result filter provider interface.

    Plugins implement this to sanitize tool results before they enter
    conversation context.  Filters strip metadata bloat, extract text
    from binary blobs, and enforce size limits — reducing token waste
    without losing semantic content.

    Multiple providers can register.  They are applied in priority order
    (highest first).  Each filter receives the output of the previous one.
    """

    provider_id: str = "default"
    priority: int = 0

    def should_filter(self, tool_name: str) -> bool:
        """Return True if this provider wants to filter the given tool's results.

        Default returns True (filter all tools).  Override to target
        specific tools, e.g. only 'QuipEditor'.
        """
        return True

    @abstractmethod
    def filter_result(self, tool_name: str, result_text: str, args: dict) -> str:
        """Filter a tool result string before it enters conversation context.

        Args:
            tool_name: Normalized tool name (e.g. 'QuipEditor', 'run_shell_command').
            result_text: The raw result text from tool execution.
            args: The arguments that were passed to the tool.

        Returns:
            The filtered result text.
        """
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


class ServiceModelProvider(ABC):
    """
    Service model provider interface.

    Plugins implement this to configure small specialized models that
    augment the primary model with specific capabilities (web search,
    code execution, knowledge base retrieval, etc.).

    The canonical example is Nova Web Grounding: a lightweight Nova
    model that performs web searches and returns cited results, exposed
    as a tool that the primary model can call.

    Providers can enable builtin tool categories by default and supply
    custom configuration for the underlying service models.
    """

    provider_id: str = "default"
    priority: int = 0

    @abstractmethod
    def get_enabled_service_tools(self) -> Set[str]:
        """
        Return builtin tool category names that should be enabled.

        Example: {'nova_grounding'} to auto-enable web search.
        """
        pass

    def get_service_model_config(self) -> Dict[str, Any]:
        """
        Return configuration overrides for service models.

        Keys are category names, values are config dicts.  For example:
            {'nova_grounding': {'model': 'nova-premier', 'region': 'us-west-2'}}
        """
        return {}

    def should_apply(self) -> bool:
        """Return True if this provider should be applied."""
        return True


@dataclass
class EncryptionPolicy:
    """
    Encryption-at-rest policy for stored data.

    Controls whether Application Level Encryption (ALE) is required,
    which key source to use, and rotation schedules.

    When multiple providers register policies, the most restrictive
    settings win (encryption enabled beats disabled, shortest rotation
    interval wins).
    """

    # Master switch.  When False, no ALE is applied.
    enabled: bool = False

    # Where the Key Encryption Key comes from.
    # "none"       — encryption disabled (default for community)
    # "midway"     — derive from Midway certificate
    # "kms"        — AWS KMS GenerateDataKey
    # "passphrase" — PBKDF2 from ZIYA_ENCRYPTION_KEY env var
    # "env"        — raw 32-byte key from ZIYA_ENCRYPTION_KEY_RAW (hex)
    kek_source: str = "none"

    # Source-specific config (e.g. KMS key ARN, HKDF salt, etc.)
    kek_config: Dict[str, Any] = field(default_factory=dict)

    # Symmetric cipher for Data Encryption Keys
    dek_algorithm: str = "AES-256-GCM"

    # How often DEKs should be rotated.  None = no automatic rotation.
    dek_rotation_interval: Optional[timedelta] = None

    # Expected KEK rotation interval, used for audit logging.
    kek_rotation_interval: Optional[timedelta] = None

    # Which data categories MUST be encrypted.  Storage layers check
    # membership before deciding whether to call the encryptor.
    categories_requiring_encryption: Set[str] = field(default_factory=lambda: set())

    # Human-readable explanation (appears in audit logs / ASR docs).
    policy_reason: str = ""

    def requires_encryption(self, category: str) -> bool:
        """Check if a specific data category requires encryption."""
        if not self.enabled:
            return False
        # Empty set means "encrypt everything"
        if not self.categories_requiring_encryption:
            return True
        return category in self.categories_requiring_encryption


class EncryptionProvider(ABC):
    """
    Encryption provider interface.

    Enterprise plugins implement this to declare encryption requirements
    and supply the Key Encryption Key (KEK).  The KEK is used to wrap
    per-project Data Encryption Keys (DEKs) — an envelope encryption
    model that allows KEK rotation without re-encrypting all data.

    Community users who want encryption can either:
    1. Set ``ZIYA_ENCRYPTION_KEY`` env var (passphrase-based, no plugin needed)
    2. Implement a minimal EncryptionProvider

    When no provider is registered and no env var is set, encryption is
    OFF — community users are never forced into it.
    """

    provider_id: str = "default"
    priority: int = 0

    @abstractmethod
    def get_encryption_policy(self) -> EncryptionPolicy:
        """Return the encryption policy for this provider."""
        pass

    @abstractmethod
    def derive_kek(self) -> Optional[bytes]:
        """
        Derive or retrieve the 32-byte Key Encryption Key.

        Returns None if the KEK source is temporarily unavailable
        (e.g. expired Midway cookie).  In that case the framework
        logs a warning and falls back to plaintext for that session.
        """
        pass

    def get_kek_identifier(self) -> str:
        """
        Return a stable identifier for the *current* KEK material.

        Used to detect when the KEK has rotated (e.g. Midway cert
        fingerprint, KMS key version ARN).  When this value changes
        between sessions, the framework re-wraps all DEKs.
        """
        return "default"

    def should_apply(self) -> bool:
        """Return True if this encryption policy should be applied."""
        return True
