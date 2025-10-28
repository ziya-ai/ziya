"""
Registry provider management and configuration.
"""

import os
from typing import Dict, List, Optional, Type
import boto3
from botocore.exceptions import ClientError

from app.mcp.registry.interface import RegistryProvider
from app.mcp.registry.providers.amazon_internal import AmazonInternalRegistryProvider
from app.mcp.registry.providers.github import GitHubRegistryProvider
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
    
    # Always register GitHub/community provider for external users
    registry.register_provider_class(
        "github",
        GitHubRegistryProvider,
        is_default=True
    )
    
    # Register Amazon internal provider only if we're in an Amazon environment
    if _is_amazon_environment():
        logger.info("Amazon environment detected, registering internal registry")
        registry.register_provider_class(
            "amazon-internal", 
            AmazonInternalRegistryProvider,
            is_default=True
        )
    else:
        logger.info("External environment, skipping Amazon internal registry")
    
    # Future: Add other providers here
    # registry.register_provider_class("npm", NPMRegistryProvider)
    # registry.register_provider_class("pypi", PyPIRegistryProvider)


def _is_amazon_environment(profile_name: str = None) -> bool:
    """Detect if we're running in an Amazon environment."""
    try:
        # Get the profile from multiple sources
        if not profile_name:
            # Try to get from ModelManager state first
            try:
                from app.agents.models import ModelManager
                profile_name = ModelManager.get_state().get('aws_profile')
            except Exception:
                pass
            
            # Fall back to environment variable
            if not profile_name:
                profile_name = os.environ.get('AWS_PROFILE')
        
        # Create session with the profile if specified
        if profile_name:
            logger.info(f"Using AWS profile: {profile_name}")
            session = boto3.Session(profile_name=profile_name)
            sts = session.client('sts')
        else:
            logger.info("Using default AWS credentials")
            sts = boto3.client('sts')
        
        identity = sts.get_caller_identity()
        user_id = identity.get('UserId', '')
        arn = identity.get('Arn', '')
        account = identity.get('Account', '')
        
        logger.info(f"AWS Identity: UserId={user_id}, Arn={arn}, Account={account}")

        # Check for Amazon internal indicators
        is_amazon = any([
            'amazon.com' in user_id.lower(),
            'midway.amazon.com' in user_id.lower(),
            '/amazon' in arn.lower(),
            account in ['339712844704']  # Add known internal account IDs
        ])
        
        logger.info(f"Amazon environment detected: {is_amazon}")
        return is_amazon
    except ClientError as e:
        logger.debug(f"Could not determine AWS identity for Amazon environment detection: {e}")
        return False
    except Exception as e:
        logger.debug(f"Error checking Amazon environment: {e}")
        return False
