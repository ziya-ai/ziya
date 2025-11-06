#!/usr/bin/env python3
"""
Demo script showcasing the unified MCP registry system.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.mcp.registry_manager import get_registry_manager
from app.mcp.registry.aggregator import get_registry_aggregator
from app.mcp.registry.registry import initialize_registry_providers, get_provider_registry


async def demo():
    """Run comprehensive demo of registry capabilities."""
    
    print("=" * 80)
    print("MCP UNIFIED REGISTRY SYSTEM DEMO")
    print("=" * 80)
    print()
    
    # Initialize
    print("📦 Initializing registry providers...")
    initialize_registry_providers()
    registry = get_provider_registry()
    manager = get_registry_manager()
    
    # Show loaded providers
    providers = registry.get_available_providers(include_internal=False)
    print(f"✓ Loaded {len(providers)} registry providers:")
    for p in providers:
        search_support = "✓" if p.supports_search else "✗"
        print(f"  • {p.name:30s} [{search_support} search]")
    print()
    
    # Demo 1: List top servers
    print("-" * 80)
    print("DEMO 1: Listing Top 10 MCP Servers (Deduplicated)")
    print("-" * 80)
    
    services = await manager.get_available_services(max_results=10)
    print(f"Found {len(services)} unique servers:\n")
    
    for i, service in enumerate(services, 1):
        sources = service.provider_metadata.get('available_in', 
            [service.provider_metadata.get('provider_id')])
        sources = [s for s in sources if s]
        sources_str = ', '.join(sources) if sources else 'unknown'
        
        print(f"{i:2d}. {service.service_name}")
        print(f"    {service.service_description[:70]}...")
        print(f"    📦 Type: {service.installation_type.value:10s}  "
              f"⭐ Support: {service.support_level.value:15s}  "
              f"🌐 Sources: {sources_str}")
    print()
    
    # Demo 2: Search functionality
    print("-" * 80)
    print("DEMO 2: Searching for 'database' Servers")
    print("-" * 80)
    
    results = await manager.search_services_by_tools("database")
    print(f"Found {len(results)} database-related servers:\n")
    
    for i, result in enumerate(results[:5], 1):
        service = result.service
        print(f"{i}. {service.service_name} (relevance: {result.relevance_score:.0f})")
        print(f"   {service.service_description[:70]}...")
        print(f"   🔧 Tools: {len(result.matching_tools)}  📦 Type: {service.installation_type.value}")
    print()
    
    # Demo 3: Statistics
    print("-" * 80)
    print("DEMO 3: Registry Statistics")
    print("-" * 80)
    
    aggregator = get_registry_aggregator()
    all_services = await aggregator.get_all_services(max_results=100, force_refresh=True)
    
    # Count installation types
    by_type = {}
    for service in all_services:
        itype = service.installation_type.value
        by_type[itype] = by_type.get(itype, 0) + 1
    
    print(f"Total unique servers: {len(all_services)}")
    print(f"\nBy Installation Type:")
    for itype, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        print(f"  {itype:10s}: {count:3d} {'█' * (count * 50 // max(by_type.values()))}")
    
    # Deduplication stats
    multi_source = [s for s in all_services if len(s.provider_metadata.get('available_in', [])) > 1]
    print(f"\n📊 Deduplication: {len(multi_source)}/{len(all_services)} servers found in multiple registries")
    print()
    
    print("=" * 80)
    print("✨ Demo Complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(demo())
