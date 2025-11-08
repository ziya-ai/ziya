"""
Abstract interface for MCP Registry providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any, AsyncGenerator
from enum import Enum


class ServiceStatus(Enum):
    """Registry service status."""
    ACTIVE = "active"
    PENDING = "pending" 
    DELETED = "deleted"
    DEPRECATED = "deprecated"


class SupportLevel(Enum):
    """Registry service support level."""
    RECOMMENDED = "Recommended"
    SUPPORTED = "Supported" 
    UNDER_ASSESSMENT = "Under assessment"
    IN_DEVELOPMENT = "In development"
    COMMUNITY = "Community"
    EXPERIMENTAL = "Experimental"


class InstallationType(Enum):
    """Type of installation method."""
    NPM = "npm"
    PYPI = "pypi"
    DOCKER = "docker"
    GIT = "git"
    REMOTE = "remote"
    BINARY = "binary"
    MCP_REGISTRY = "mcp-registry"
    UNKNOWN = "unknown"


@dataclass
class RegistryServiceInfo:
    """Standardized service information across all registry providers."""
    service_id: str
    service_name: str
    service_description: str
    version: int
    status: ServiceStatus
    support_level: SupportLevel
    created_at: datetime
    last_updated_at: datetime
    
    # Installation information
    installation_instructions: Dict[str, Any]
    installation_type: InstallationType = InstallationType.UNKNOWN
    
    # Optional metadata
    tags: List[str] = None
    author: Optional[str] = None
    homepage_url: Optional[str] = None
    repository_url: Optional[str] = None
    security_review_url: Optional[str] = None
    documentation_url: Optional[str] = None
    license: Optional[str] = None
    
    # Additional discovery metadata
    download_count: Optional[int] = None
    star_count: Optional[int] = None
    last_release_date: Optional[datetime] = None
    
    # Provider-specific data
    provider_metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.provider_metadata is None:
            self.provider_metadata = {}


@dataclass
class RegistryTool:
    """Information about a tool provided by a registry service."""
    tool_name: str
    service_id: str
    description: Optional[str] = None
    category: Optional[str] = None
    

@dataclass
class ToolSearchResult:
    """Result from a tool search query."""
    service: RegistryServiceInfo
    matching_tools: List[RegistryTool]
    relevance_score: Optional[float] = None


@dataclass
class InstallationResult:
    """Result of a service installation."""
    success: bool
    service_id: str
    server_name: str
    installation_path: Optional[str] = None
    config_entries: Dict[str, Any] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        if self.config_entries is None:
            self.config_entries = {}


class RegistryProvider(ABC):
    """Abstract base class for registry providers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this registry provider."""
        pass
    
    @property
    @abstractmethod
    def identifier(self) -> str:
        """Unique identifier for this provider (e.g., 'amazon-internal', 'github', 'npm')."""
        pass
    
    @property
    @abstractmethod
    def is_internal(self) -> bool:
        """Whether this is an internal registry (affects UI visibility)."""
        pass
    
    @property
    @abstractmethod
    def supports_search(self) -> bool:
        """Whether this provider supports tool search functionality."""
        pass
    
    @abstractmethod
    async def list_services(
        self, 
        max_results: int = 50, 
        next_token: Optional[str] = None,
        filter_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        List available services from this registry.
        
        Returns:
            Dict with 'services' (List[RegistryServiceInfo]) and optional 'next_token'
        """
        pass
    
    @abstractmethod
    async def get_service_detail(self, service_id: str) -> RegistryServiceInfo:
        """Get detailed information about a specific service."""
        pass
    
    @abstractmethod
    async def search_tools(self, query: str, max_results: int = 10) -> List[ToolSearchResult]:
        """Search for tools matching a natural language query."""
        pass
    
    @abstractmethod
    async def install_service(self, service_id: str, config_path: str) -> InstallationResult:
        """
        Install a service and return configuration to be added to mcp_config.json.
        
        Args:
            service_id: The service to install
            config_path: Path to the mcp_config.json file
            
        Returns:
            InstallationResult with success status and config entries
        """
        pass
    
    @abstractmethod
    async def validate_service(self, service_id: str) -> bool:
        """Validate that a service is still available and properly configured."""
        pass
    
    async def get_installation_preview(self, service_id: str) -> Dict[str, Any]:
        """
        Get a preview of what would be installed without actually installing.
        Default implementation returns basic service info.
        """
        service = await self.get_service_detail(service_id)
        return {
            'service_name': service.service_name,
            'description': service.service_description,
            'installation_instructions': service.installation_instructions
        }
