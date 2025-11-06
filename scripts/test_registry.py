#!/usr/bin/env python3
"""
CLI tool for testing the MCP registry system.

Usage:
    python scripts/test_registry.py list
    python scripts/test_registry.py search "filesystem"
    python scripts/test_registry.py providers
    python scripts/test_registry.py health
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.mcp.registry_manager import get_registry_manager
from app.mcp.registry.aggregator import get_registry_aggregator
from app.mcp.registry.registry import initialize_registry_providers, get_provider_registry
from app.utils.logging_utils import logger


async def cmd_list(max_results: int = 20):
    """List available MCP servers."""
    print("🔍 Fetching MCP servers from all registries...\n")
    
    manager = get_registry_manager()
    services = await manager.get_available_services(max_results=max_results)
    
    print(f"Found {len(services)} servers:\n")
    
    for i, service in enumerate(services, 1):
        print(f"{i}. {service.service_name}")
        print(f"   {service.service_description[:100]}...")
        print(f"   Type: {service.installation_type.value}")
        print(f"   Support: {service.support_level.value}")
        
        # Show which registries have this server
        sources = service.provider_metadata.get('available_in', 
            [service.provider_metadata.get('provider_id')])
        # Filter out None values
        sources = [s for s in sources if s is not None]
        if not sources:
            sources = ['unknown']
        print(f"   Sources: {', '.join(sources)}")
        print()


async def cmd_search(query: str):
    """Search for MCP servers by functionality."""
    print(f"🔍 Searching for '{query}' across all registries...\n")
    
    manager = get_registry_manager()
    results = await manager.search_services_by_tools(query)
    
    print(f"Found {len(results)} matching servers:\n")
    
    for i, result in enumerate(results, 1):
        service = result.service
        print(f"{i}. {service.service_name} (relevance: {result.relevance_score:.0f})")
        print(f"   {service.service_description[:100]}...")
        print(f"   Matching tools: {len(result.matching_tools)}")
        print(f"   Install: {service.installation_type.value}")
        print()


async def cmd_providers():
    """List available registry providers."""
    print("📚 Available Registry Providers:\n")
    
    initialize_registry_providers()
    registry = get_provider_registry()
    providers = registry.get_available_providers(include_internal=True)
    
    for provider in providers:
        print(f"• {provider.name} ({provider.identifier})")
        print(f"  Internal: {'Yes' if provider.is_internal else 'No'}")
        print(f"  Search: {'Yes' if provider.supports_search else 'No'}")
        print()


async def cmd_health():
    """Check health of all providers."""
    print("🏥 Checking provider health...\n")
    
    initialize_registry_providers()
    registry = get_provider_registry()
    providers = registry.get_available_providers(include_internal=True)
    
    for provider in providers:
        print(f"Testing {provider.name}...", end=" ", flush=True)
        
        try:
            result = await provider.list_services(max_results=1)
            count = len(result['services'])
            print(f"✓ ({count} service{'s' if count != 1 else ''} accessible)")
        except Exception as e:
            print(f"✗ Error: {str(e)[:50]}")
    
    print("\n📊 Aggregation test...", end=" ", flush=True)
    try:
        aggregator = get_registry_aggregator()
        services = await aggregator.get_all_services(max_results=10, force_refresh=True)
        print(f"✓ ({len(services)} unique servers)")
    except Exception as e:
        print(f"✗ Error: {str(e)[:50]}")


async def cmd_stats():
    """Show registry statistics."""
    print("📈 Registry Statistics\n")
    initialize_registry_providers()
    
    aggregator = get_registry_aggregator()
    services = await aggregator.get_all_services(max_results=1000, force_refresh=True)
async def cmd_stats():
    """Show registry statistics."""
    print("📈 Registry Statistics\n")
    
    initialize_registry_providers()
    
    aggregator = get_registry_aggregator()
    services = await aggregator.get_all_services(max_results=2000, force_refresh=True)
    
    # Count by type
    by_type = {}
    by_support = {}
    by_provider = {}
    
    for service in services:
        # By installation type
        itype = service.installation_type.value
        by_type[itype] = by_type.get(itype, 0) + 1
        
        # By support level
        support = service.support_level.value
        by_support[support] = by_support.get(support, 0) + 1
        
        # By provider
        sources = service.provider_metadata.get('available_in', 
            [service.provider_metadata.get('provider_id')])
        for source in sources:
            by_provider[source] = by_provider.get(source, 0) + 1
    
    print(f"Total unique servers: {len(services)}\n")
    
    print("By Installation Type:")
    for itype, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        print(f"  {itype:10s}: {count:4d}")
    
    print("\nBy Support Level:")
    for support, count in sorted(by_support.items(), key=lambda x: x[1], reverse=True):
        print(f"  {support:20s}: {count:4d}")
    
    print("\nBy Provider:")
    for provider, count in sorted(by_provider.items(), key=lambda x: x[1], reverse=True):
        print(f"  {provider:20s}: {count:4d}")
    
    # Deduplication stats
    multi_source = [s for s in services if len(s.provider_metadata.get('available_in', [])) > 1]
    print(f"\nDeduplication:")
    print(f"  Multi-source servers: {len(multi_source)}")
    if len(services) > 0:
        print(f"  Deduplication rate: {len(multi_source)/len(services)*100:.1f}%")
    else:
        print(f"  Deduplication rate: N/A (no services)")


def main():
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/test_registry.py list [max_results]")
        print("  python scripts/test_registry.py search <query>")
        print("  python scripts/test_registry.py providers")
        print("  python scripts/test_registry.py health")
        print("  python scripts/test_registry.py stats")
        sys.exit(1)
    
    command = sys.argv[1]
    
    try:
        if command == "list":
            max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 20
            asyncio.run(cmd_list(max_results))
        
        elif command == "search":
            if len(sys.argv) < 3:
                print("Error: search requires a query")
                sys.exit(1)
            query = sys.argv[2]
            asyncio.run(cmd_search(query))
        
        elif command == "providers":
            asyncio.run(cmd_providers())
        
        elif command == "health":
            asyncio.run(cmd_health())
        
        elif command == "stats":
            asyncio.run(cmd_stats())
        
        else:
            print(f"Unknown command: {command}")
            sys.exit(1)
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
