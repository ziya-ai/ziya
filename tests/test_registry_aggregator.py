"""
Unit tests for registry aggregator functionality.
"""

import pytest
from datetime import datetime
from typing import Dict, List

from app.mcp.registry.aggregator import RegistryAggregator
from app.mcp.registry.interface import (
    RegistryServiceInfo, ServiceStatus, SupportLevel, 
    InstallationType, ToolSearchResult, RegistryTool
)


@pytest.fixture
def sample_service_official():
    """Sample service from official registry."""
    return RegistryServiceInfo(
        service_id="io.github.example/test-server",
        service_name="Test Server",
        service_description="A test MCP server",
        version=1,
        status=ServiceStatus.ACTIVE,
        support_level=SupportLevel.RECOMMENDED,
        created_at=datetime.now(),
        last_updated_at=datetime.now(),
        installation_instructions={'type': 'npm', 'package': 'test-server'},
        installation_type=InstallationType.NPM,
        tags=['test', 'example'],
        repository_url='https://github.com/example/test-server',
        provider_metadata={'provider_id': 'official-mcp'}
    )


@pytest.fixture
def sample_service_pulsemcp():
    """Sample service from PulseMCP (duplicate of official)."""
    return RegistryServiceInfo(
        service_id="io.github.example/test-server",
        service_name="Test Server",
        service_description="A test MCP server from PulseMCP",
        version=1,
        status=ServiceStatus.ACTIVE,
        support_level=SupportLevel.COMMUNITY,
        created_at=datetime.now(),
        last_updated_at=datetime.now(),
        installation_instructions={'type': 'npm', 'package': 'test-server'},
        installation_type=InstallationType.NPM,
        tags=['test'],
        repository_url='https://github.com/example/test-server',
        provider_metadata={'provider_id': 'pulsemcp'}
    )


@pytest.fixture
def sample_service_awesome():
    """Sample service from awesome list."""
    return RegistryServiceInfo(
        service_id="github.com.example.test-server",
        service_name="Test Server",
        service_description="A test MCP server from awesome list",
        version=1,
        status=ServiceStatus.ACTIVE,
        support_level=SupportLevel.COMMUNITY,
        created_at=datetime.now(),
        last_updated_at=datetime.now(),
        installation_instructions={'type': 'git', 'repository': 'https://github.com/example/test-server'},
        installation_type=InstallationType.GIT,
        tags=['test', 'awesome'],
        repository_url='https://github.com/example/test-server',
        provider_metadata={'provider_id': 'awesome-lists'}
    )


class TestRegistryAggregator:
    """Tests for RegistryAggregator."""
    
    def test_compute_service_fingerprint_by_repo(self, sample_service_official):
        """Test fingerprint computation using repository URL."""
        aggregator = RegistryAggregator()
        fingerprint = aggregator._compute_service_fingerprint(sample_service_official)
        
        assert fingerprint == "repo:example/test-server"
    
    def test_compute_service_fingerprint_by_npm_package(self):
        """Test fingerprint computation for NPM package."""
        service = RegistryServiceInfo(
            service_id="test",
            service_name="Test",
            service_description="Test",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions={'type': 'npm', 'package': '@scope/my-package'},
            installation_type=InstallationType.NPM,
            repository_url=None
        )
        
        aggregator = RegistryAggregator()
        fingerprint = aggregator._compute_service_fingerprint(service)
        
        assert fingerprint == "npm:@scope/my-package"
    
    def test_compute_service_fingerprint_by_name_fallback(self):
        """Test fingerprint fallback to name when no other identifier."""
        service = RegistryServiceInfo(
            service_id="test",
            service_name="Unique Server Name",
            service_description="Test",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions={'type': 'unknown'},
            installation_type=InstallationType.UNKNOWN,
            repository_url=None
        )
        
        aggregator = RegistryAggregator()
        fingerprint = aggregator._compute_service_fingerprint(service)
        
        assert fingerprint == "name:unique server name"
    
    def test_merge_services_single(self, sample_service_official):
        """Test merging with single service returns it unchanged."""
        aggregator = RegistryAggregator()
        merged = aggregator._merge_services([sample_service_official])
        
        assert merged == sample_service_official
    
    def test_merge_services_prioritizes_official(
        self, 
        sample_service_official, 
        sample_service_pulsemcp
    ):
        """Test that official registry takes priority in merge."""
        aggregator = RegistryAggregator()
        
        # Pass in wrong order - should still prioritize official
        merged = aggregator._merge_services([sample_service_pulsemcp, sample_service_official])
        
        # Should use official as base
        assert merged.service_description == sample_service_official.service_description
        assert merged.support_level == SupportLevel.RECOMMENDED
        
        # Should track both sources
        assert 'available_in' in merged.provider_metadata
        assert 'official-mcp' in merged.provider_metadata['available_in']
        assert 'pulsemcp' in merged.provider_metadata['available_in']
    
    def test_merge_services_combines_tags(
        self,
        sample_service_official,
        sample_service_awesome
    ):
        """Test that tags are combined from all sources."""
        aggregator = RegistryAggregator()
        
        merged = aggregator._merge_services([sample_service_official, sample_service_awesome])
        
        # Should have tags from both sources
        assert 'test' in merged.tags
        assert 'example' in merged.tags
        assert 'awesome' in merged.tags
        assert len(merged.tags) == 3
    
    def test_merge_services_uses_better_description(self):
        """Test that longer/better description is preferred."""
        service1 = RegistryServiceInfo(
            service_id="test",
            service_name="Test",
            service_description="Short",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions={},
            installation_type=InstallationType.NPM,
            repository_url='https://github.com/test/test',
            provider_metadata={'provider_id': 'pulsemcp'}
        )
        
        service2 = RegistryServiceInfo(
            service_id="test",
            service_name="Test",
            service_description="This is a much longer and more detailed description of what the server does",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions={},
            installation_type=InstallationType.NPM,
            repository_url='https://github.com/test/test',
            provider_metadata={'provider_id': 'awesome-lists'}
        )
        
        aggregator = RegistryAggregator()
        merged = aggregator._merge_services([service1, service2])
        
        assert len(merged.service_description) > 50
        assert 'detailed description' in merged.service_description
    
    def test_fingerprint_normalization(self):
        """Test that GitHub URLs are properly normalized."""
        aggregator = RegistryAggregator()
        
        service1 = RegistryServiceInfo(
            service_id="test1",
            service_name="Test",
            service_description="Test",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions={},
            installation_type=InstallationType.GIT,
            repository_url='https://github.com/user/repo',
            provider_metadata={}
        )
        
        service2 = RegistryServiceInfo(
            service_id="test2",
            service_name="Test",
            service_description="Test",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.COMMUNITY,
            created_at=datetime.now(),
            last_updated_at=datetime.now(),
            installation_instructions={},
            installation_type=InstallationType.GIT,
            repository_url='https://github.com/user/repo.git',  # Different format
            provider_metadata={}
        )
        
        fp1 = aggregator._compute_service_fingerprint(service1)
        fp2 = aggregator._compute_service_fingerprint(service2)
        
        # Should normalize to same fingerprint
        assert fp1 == fp2 == "repo:user/repo"


@pytest.fixture
def mock_providers(monkeypatch, sample_service_official, sample_service_pulsemcp):
    """Mock provider registry with test data."""
    from app.mcp.registry.registry import get_provider_registry
    from app.mcp.registry.interface import RegistryProvider
    
    class MockProvider(RegistryProvider):
        def __init__(self, identifier: str, services: List[RegistryServiceInfo]):
            self._identifier = identifier
            self._services = services
        
        @property
        def name(self) -> str:
            return f"Mock {self._identifier}"
        
        @property
        def identifier(self) -> str:
            return self._identifier
        
        @property
        def is_internal(self) -> bool:
            return False
        
        @property
        def supports_search(self) -> bool:
            return True
        
        async def list_services(self, max_results=50, next_token=None, filter_params=None):
            return {'services': self._services, 'next_token': None}
        
        async def get_service_detail(self, service_id):
            service = next((s for s in self._services if s.service_id == service_id), None)
            if not service:
                raise ValueError(f"Service {service_id} not found")
            return service
        
        async def search_tools(self, query, max_results=10):
            return []
        
        async def install_service(self, service_id, config_path):
            return InstallationResult(success=False, service_id=service_id, server_name="")
        
        async def validate_service(self, service_id):
            return True
    
    # Create mock providers
    official = MockProvider('official-mcp', [sample_service_official])
    pulsemcp = MockProvider('pulsemcp', [sample_service_pulsemcp])
    
    # Patch get_provider_registry
    registry = get_provider_registry()
    registry._providers = {
        'official-mcp': official,
        'pulsemcp': pulsemcp
    }
    
    return registry


@pytest.mark.asyncio
async def test_aggregator_deduplication(
    mock_providers,
    sample_service_official,
    sample_service_pulsemcp
):
    """Test that aggregator deduplicates services from multiple sources."""
    aggregator = RegistryAggregator()
    
    services = await aggregator.get_all_services(max_results=100, include_internal=False)
    
    # Should have only 1 service (deduplicated)
    assert len(services) == 1
    
    # Should use official as primary source
    service = services[0]
    assert service.support_level == SupportLevel.RECOMMENDED
    
    # Should track both sources
    assert 'available_in' in service.provider_metadata
    assert 'official-mcp' in service.provider_metadata['available_in']
    assert 'pulsemcp' in service.provider_metadata['available_in']


@pytest.mark.asyncio
async def test_aggregator_caching():
    """Test that aggregator caches results."""
    aggregator = RegistryAggregator()
    
    # First call should fetch
    services1 = await aggregator.get_all_services(max_results=10)
    time1 = aggregator._last_refresh
    
    # Second call should use cache
    services2 = await aggregator.get_all_services(max_results=10)
    time2 = aggregator._last_refresh
    
    assert time1 == time2  # Cache timestamp unchanged
    assert len(services1) == len(services2)


@pytest.mark.asyncio
async def test_aggregator_force_refresh():
    """Test force refresh bypasses cache."""
    aggregator = RegistryAggregator()
    
    # First call with cache
    await aggregator.get_all_services(max_results=10)
    time1 = aggregator._last_refresh
    
    # Wait a bit
    import asyncio
    await asyncio.sleep(0.1)
    
    # Force refresh should update cache time
    await aggregator.get_all_services(max_results=10, force_refresh=True)
    time2 = aggregator._last_refresh
    
    assert time2 > time1
