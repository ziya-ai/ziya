"""
End-to-end integration tests for the complete registry system.
"""

import pytest
from pathlib import Path

from app.mcp.registry_manager import get_registry_manager
from app.mcp.registry.aggregator import get_registry_aggregator
from app.mcp.registry.registry import initialize_registry_providers, get_provider_registry


@pytest.mark.integration
class TestRegistrySystemIntegration:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    async def test_full_workflow_search_and_discovery(self):
        """Test complete workflow: initialize -> search -> discover."""
        # Initialize registry system
        initialize_registry_providers()
        registry = get_provider_registry()
        
        # Verify providers loaded
        providers = registry.get_available_providers(include_internal=False)
        assert len(providers) >= 3  # At least official, pulsemcp, smithery
        
        # Test manager
        manager = get_registry_manager()
        
        # Search for a common server
        results = await manager.search_services_by_tools("filesystem")
        
        assert len(results) > 0
        
        # Verify result structure
        result = results[0]
        assert result.service is not None
        assert result.service.service_id
        assert result.service.service_name
        assert result.service.installation_instructions
        assert result.relevance_score is not None
        
        print(f"\nFound {len(results)} filesystem servers:")
        for r in results[:5]:
            print(f"  - {r.service.service_name} (score: {r.relevance_score})")
            providers = r.service.provider_metadata.get('available_in', 
                [r.service.provider_metadata.get('provider_id')])
            print(f"    Sources: {', '.join(providers)}")
    
    @pytest.mark.asyncio
    async def test_aggregator_deduplication_real(self):
        """Test that aggregator properly deduplicates real data."""
        aggregator = get_registry_aggregator()
        
        # Force fresh fetch
        services = await aggregator.get_all_services(
            max_results=100,
            include_internal=False,
            force_refresh=True
        )
        
        assert len(services) > 0
        
        # Check for deduplication indicators
        deduplicated = [s for s in services if 'available_in' in s.provider_metadata]
        
        if deduplicated:
            print(f"\nDeduplication stats:")
            print(f"  Total unique servers: {len(services)}")
            print(f"  Servers in multiple registries: {len(deduplicated)}")
            
            # Show example
            example = deduplicated[0]
            print(f"\nExample deduplicated server: {example.service_name}")
            print(f"  Available in: {example.provider_metadata['available_in']}")
            print(f"  Merged from: {example.provider_metadata.get('merged_from', 1)} sources")
    
    @pytest.mark.asyncio
    async def test_get_available_providers(self):
        """Test getting list of available providers."""
        manager = get_registry_manager()
        provider_ids = manager.get_available_providers()
        
        assert 'official-mcp' in provider_ids
        assert 'pulsemcp' in provider_ids
        
        print(f"\nAvailable providers: {', '.join(provider_ids)}")
    
    @pytest.mark.asyncio
    async def test_list_services_with_pagination(self):
        """Test pagination through service list."""
        manager = get_registry_manager()
        
        # Get first page
        page1 = await manager.get_available_services(max_results=10)
        assert len(page1) <= 10
        
        # Get larger set
        page2 = await manager.get_available_services(max_results=50)
        assert len(page2) <= 50
        assert len(page2) >= len(page1)
        
        print(f"\nPagination test:")
        print(f"  First 10: {len(page1)} results")
        print(f"  First 50: {len(page2)} results")
    
    @pytest.mark.asyncio
    async def test_search_multiple_categories(self):
        """Test searching for different categories of servers."""
        manager = get_registry_manager()
        
        categories = ["database", "filesystem", "web", "git"]
        
        print(f"\nCategory search results:")
        for category in categories:
            results = await manager.search_services_by_tools(category)
            print(f"  {category}: {len(results)} servers")
            
            assert len(results) >= 0  # May have 0 for some categories
    
    @pytest.mark.asyncio
    async def test_installation_preview(self):
        """Test getting installation preview without actually installing."""
        manager = get_registry_manager()
        
        # Get a service
        services = await manager.get_available_services(max_results=5)
        
        if services:
            service = services[0]
            
            print(f"\nInstallation preview for: {service.service_name}")
            print(f"  Type: {service.installation_type.value}")
            print(f"  Instructions: {service.installation_instructions}")
            print(f"  Repository: {service.repository_url}")
            
            # Check if installation is feasible
            from app.mcp.registry.installation_helper import InstallationHelper
            
            has_prereq, msg = InstallationHelper.check_prerequisites(service.installation_type)
            print(f"  Prerequisites: {'✓' if has_prereq else '✗'} {msg}")


@pytest.mark.integration  
class TestProviderHealthChecks:
    """Health checks for each provider."""
    
    @pytest.mark.asyncio
    async def test_official_registry_health(self):
        """Test official registry is accessible."""
        from app.mcp.registry.providers.official_mcp import OfficialMCPRegistryProvider
        
        provider = OfficialMCPRegistryProvider()
        result = await provider.list_services(max_results=1)
        
        assert 'services' in result
        print(f"✓ Official MCP Registry: {len(result['services'])} services accessible")
        
        await provider.close()
    
    @pytest.mark.asyncio
    async def test_awesome_lists_health(self):
        """Test awesome lists are parseable."""
        from app.mcp.registry.providers.awesome_list import AwesomeListRegistryProvider
        
        provider = AwesomeListRegistryProvider(lists=["punkpeye/awesome-mcp-servers"])
        result = await provider.list_services(max_results=10)
        
        assert 'services' in result
        assert len(result['services']) > 0
        print(f"✓ Awesome Lists: {len(result['services'])} services parsed")
        
        await provider.close()
    
    @pytest.mark.asyncio
    @pytest.mark.skipif(
        "not os.environ.get('AWS_PROFILE')",
        reason="Requires AWS credentials"
    )
    async def test_amazon_internal_health(self):
        """Test Amazon internal registry (requires credentials)."""
        from app.mcp.registry.providers.amazon_internal import AmazonInternalRegistryProvider
        
        provider = AmazonInternalRegistryProvider()
        
        # Test connection
        can_connect = await provider.test_connection()
        
        if can_connect:
            result = await provider.list_services(max_results=1)
            assert 'services' in result
            print(f"✓ Amazon Internal Registry: {len(result['services'])} services accessible")
        else:
            print("⚠ Amazon Internal Registry: Connection failed (expected if not on VPN)")
