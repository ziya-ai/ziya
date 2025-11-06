#!/usr/bin/env python3
"""
Diagnostic script for Amazon Internal MCP Registry access.
Checks credentials, permissions, and connectivity step by step.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.mcp.registry.providers.amazon_internal import AmazonInternalRegistryProvider
from app.utils.logging_utils import logger
import logging
import boto3
import subprocess
from botocore.exceptions import ClientError

# Set detailed logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def check_midway_mwinit():
    """Check if mwinit has been run."""
    print("🔧 Step 0: Checking Midway/mwinit...")
    
    # Check if mwinit exists
    try:
        result = subprocess.run(
            ['which', 'mwinit'],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print("   ⚠️  mwinit not found in PATH")
            print("      You may not be on an Amazon device")
            return False
        
        print(f"   ✅ mwinit found: {result.stdout.strip()}")
        
        # Check if credentials file has dummy values
        creds_file = Path.home() / '.aws' / 'credentials'
        if creds_file.exists():
            content = creds_file.read_text()
            if 'aws_access_key_id = test' in content:
                print("   ⚠️  Dummy AWS credentials detected in ~/.aws/credentials")
                print("      These will override Midway credentials!")
                print()
                print("      🔧 Solution: Use ADA credentials instead:")
                print("         ada credentials update --account=YOUR_ACCOUNT --provider=isengard --role=YOUR_ROLE --once")
                print()
                print("      Or remove dummy credentials:")
                print("         mv ~/.aws/credentials ~/.aws/credentials.backup")
                return False
        
        # Check for ADA
        ada_result = subprocess.run(['which', 'ada'], capture_output=True, text=True)
        if ada_result.returncode == 0:
            print(f"   ✅ ADA found: {ada_result.stdout.strip()}")
        else:
            print("   ⚠️  ADA not found (alternative to mwinit)")
        
        return True
        
    except Exception as e:
        print(f"   ⚠️  Error checking Midway: {e}")
        return False


def check_aws_credentials():
    """Check if AWS credentials are configured."""
    print("🔐 Step 1: Checking AWS Credentials...")
    
    try:
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        
        print(f"   ✅ AWS credentials valid")
        print(f"      User ID: {identity.get('UserId')}")
        print(f"      ARN: {identity.get('Arn')}")
        print(f"      Account: {identity.get('Account')}")
        
        # Check if it's an Amazon identity
        user_id = identity.get('UserId', '').lower()
        arn = identity.get('Arn', '').lower()
        
        is_amazon = any([
            'amazon.com' in user_id,
            'midway' in user_id,
            '/amazon' in arn
        ])
        
        if is_amazon:
            print(f"      ✅ Amazon internal identity detected")
        else:
            print(f"      ⚠️  External AWS identity (Amazon registry may not be accessible)")
        
        print()
        print("   💡 Credential source:")
        # Try to determine credential source
        session = boto3.Session()
        creds = session.get_credentials()
        if creds:
            print(f"      Method: {creds.method}")
            print(f"      Access Key: {creds.access_key[:8]}...")
        
        return True, identity
        
    except ClientError as e:
        print(f"   ❌ AWS credentials not configured")
        print(f"      Error: {e}")
        print()
        print("      🔧 Solutions:")
        print("         1. Use ADA: ada credentials update --account=ACCOUNT --provider=isengard --role=ROLE --once")
        print("         2. Remove dummy credentials: mv ~/.aws/credentials ~/.aws/credentials.backup")
        print("         3. Set environment variables with valid credentials")
        print("         4. Use a different AWS_PROFILE: export AWS_PROFILE=your-profile")
        return False, None
    except Exception as e:
        print(f"   ❌ Error checking credentials: {e}")
        return False, None


async def check_secrets_manager(provider):
    """Check if we can access Secrets Manager."""
    print("\n🔑 Step 2: Checking Secrets Manager Access...")
    print(f"   Secret name: {provider.secret_name}")
    print(f"   Region: {provider.region}")
    
    try:
        creds = await provider._get_cognito_credentials()
        
        print(f"   ✅ Secret retrieved successfully")
        print(f"      Client ID: {creds['client_id'][:8]}..." if 'client_id' in creds else "      Missing client_id")
        print(f"      Discovery URL: {creds.get('discovery_url', 'Missing')[:50]}...")
        
        return True, creds
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        print(f"   ❌ Cannot access secret: {error_code}")
        
        if error_code == 'AccessDeniedException':
            print(f"      Solution: Add secretsmanager:GetSecretValue permission to your IAM role")
        elif error_code == 'ResourceNotFoundException':
            print(f"      Secret '{provider.secret_name}' not found in regions: {provider.secret_regions}")
            print()
            print("      Let's check what secrets you DO have access to...")
            
            # Try to list secrets to help debug
            try:
                session = provider._get_boto_session()
                for region in provider.secret_regions:
                    try:
                        sm_client = session.client('secretsmanager', region_name=region)
                        response = sm_client.list_secrets(MaxResults=10)
                        
                        if response['SecretList']:
                            print(f"      ✓ Available secrets in {region}:")
                            for secret in response['SecretList'][:5]:
                                name = secret['Name']
                                if 'mcp' in name.lower() or 'cognito' in name.lower():
                                    print(f"        • {name} ⭐ (MCP-related)")
                                else:
                                    print(f"        • {name}")
                    except Exception as e2:
                        print(f"      ✗ Cannot list secrets in {region}: {e2}")
            except Exception as e2:
                print(f"      ✗ Cannot check available secrets: {e2}")
            
            print()
            print("      Solutions:")
            print("        1. If you see an MCP-related secret above, update MCP_REGISTRY_SECRET env var")
            print("        2. Request MCP Registry access via SIM ticket")
            print("        3. Check if you're using the right AWS account")
        
        return False, None
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False, None


async def check_cognito_auth(provider):
    """Check if we can authenticate with Cognito."""
    print("\n🎫 Step 3: Checking Cognito Authentication...")
    
    try:
        token = await provider._get_access_token()
        
        print(f"   ✅ Cognito authentication successful")
        print(f"      Token length: {len(token)} characters")
        print(f"      Token preview: {token[:20]}...")
        
        return True, token
        
    except Exception as e:
        print(f"   ❌ Authentication failed: {e}")
        print(f"      Solution: Verify Cognito client credentials in secret")
        return False, None


async def check_mcp_api(provider):
    """Check if we can call the MCP API."""
    print("\n🌐 Step 4: Checking MCP API Access...")
    print(f"   Endpoint: {provider.endpoint_url}")
    
    try:
        result = await provider.list_services(max_results=3)
        services = result['services']
        
        print(f"   ✅ MCP API accessible")
        print(f"      Found {len(services)} services")
        
        for service in services:
            print(f"        • {service.service_name}")
        
        return True
        
    except Exception as e:
        print(f"   ❌ API call failed: {e}")
        print(f"      Solution: Verify network access and endpoint URL")
        return False


async def main():
    """Run all diagnostic checks."""
    provider = AmazonInternalRegistryProvider()
    
    # Step 0: Check Midway/mwinit
    check_midway_mwinit()
    print()
    
    # Run checks sequentially
    has_creds, identity = check_aws_credentials()
    if not has_creds:
        print("\n⛔ Cannot proceed without AWS credentials")
        return
    
    has_secret, creds = await check_secrets_manager(provider)
    if not has_secret:
        print("\n⛔ Cannot proceed without Secrets Manager access")
        return
    
    has_auth, token = await check_cognito_auth(provider)
    if not has_auth:
        print("\n⛔ Cannot proceed without Cognito authentication")
        return
    
    has_api = await check_mcp_api(provider)
    
    # Summary
    print("\n" + "=" * 80)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 80)
    print(f"AWS Credentials:        {'✅' if has_creds else '❌'}")
    print(f"Secrets Manager Access: {'✅' if has_secret else '❌'}")
    print(f"Cognito Authentication: {'✅' if has_auth else '❌'}")
    print(f"MCP API Access:         {'✅' if has_api else '❌'}")
    print()
    
    if has_api:
        print("🎉 All checks passed! Amazon Internal Registry is accessible.")
    else:
        print("⚠️  Some checks failed. See errors above for troubleshooting.")
    
    await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
