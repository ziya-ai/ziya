#!/usr/bin/env python3
"""
Test script for Amazon Internal MCP Registry connection.

Requirements:
- Valid AWS credentials (Midway via mwinit)
- VPN connection
- IAM role with Secrets Manager access
- Registered Cognito client credentials in Secrets Manager
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.mcp.registry.providers.amazon_internal import AmazonInternalRegistryProvider
from app.utils.logging_utils import logger
import logging

# Set logging to INFO to see detailed connection info
logging.basicConfig(level=logging.INFO)


async def test_amazon_registry():
    """Test Amazon internal registry connection and capabilities."""
    
    print("=" * 80)
    print("AMAZON INTERNAL MCP REGISTRY TEST")
    print("=" * 80)
    print()
    
    # Create provider
    print("📦 Initializing Amazon Internal Registry Provider...")
    provider = AmazonInternalRegistryProvider(
        registry_name="MainRegistry",
        region="us-east-1"
    )
    print(f"   Endpoint: {provider.endpoint_url}")
    print(f"   Secret: {provider.secret_name}")
    print(f"   Region: {provider.region}")
    print()
    
    # Test connection
    print("-" * 80)
    print("TEST 1: Connection Test")
    print("-" * 80)
    
    connected = await provider.test_connection()
    
    if connected:
        print("✅ Connection successful!")
        print()
        
        # Test list services
        print("-" * 80)
        print("TEST 2: List Services")
        print("-" * 80)
        
        try:
            result = await provider.list_services(max_results=10)
            services = result['services']
            
            print(f"Found {len(services)} services:\n")
            
            for i, service in enumerate(services, 1):
                print(f"{i}. {service.service_name}")
                print(f"   {service.service_description[:70]}...")
                print(f"   Support: {service.support_level.value}")
                print(f"   CTI: {service.provider_metadata.get('cti', 'N/A')}")
            
        except Exception as e:
            print(f"❌ Error listing services: {e}")
    else:
        print("❌ Connection failed!")
        print()
        print("Possible issues:")
        print("  • AWS credentials not configured (run 'mwinit')")
        print("  • Not on VPN")
        print("  • IAM role lacks Secrets Manager permissions")
        print("  • Secret name is incorrect")
        print("  • Not registered for MCP Registry access")
    
    # Cleanup
    await provider.close()
    print()
    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(test_amazon_registry())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
