"""
Plugin interfaces for Ziya.

These define the contracts that plugin providers must implement.
"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional, Any, Dict

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
