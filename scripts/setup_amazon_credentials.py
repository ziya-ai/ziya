#!/usr/bin/env python3
"""
Helper script to setup AWS credentials for Amazon Internal MCP Registry.
Guides users through the ADA credential setup process.
"""

import subprocess
import sys
from pathlib import Path


def check_ada_installed():
    """Check if ADA is installed."""
    try:
        result = subprocess.run(['which', 'ada'], capture_output=True, text=True)
        if result.returncode == 0:
            ada_path = result.stdout.strip()
            print(f"✅ ADA found: {ada_path}")
            return True
        else:
            print("❌ ADA not found in PATH")
            return False
    except Exception as e:
        print(f"❌ Error checking for ADA: {e}")
        return False


def check_dummy_credentials():
    """Check if dummy credentials are blocking Midway."""
    creds_file = Path.home() / '.aws' / 'credentials'
    
    if not creds_file.exists():
        print("✅ No ~/.aws/credentials file (good)")
        return False
    
    content = creds_file.read_text()
    
    if 'aws_access_key_id = test' in content or 'aws_secret_access_key = test' in content:
        print("⚠️  Dummy credentials detected in ~/.aws/credentials")
        print("   These will prevent Midway/ADA from working!")
        return True
    
    print("✅ Real credentials in ~/.aws/credentials")
    return False


def show_ada_commands():
    """Show ADA commands for common accounts."""
    print("\n" + "=" * 80)
    print("ADA CREDENTIAL SETUP")
    print("=" * 80)
    print()
    print("To use Amazon Internal MCP Registry, you need temporary AWS credentials.")
    print("Use ADA to generate them:")
    print()
    print("📋 Common ADA Commands:")
    print()
    
    # Common accounts and roles
    accounts = [
        {
            'name': 'Beta Account',
            'account': '339712844704',
            'role': 'IibsAdminAccess-DO-NOT-DELETE',
            'provider': 'isengard'
        },
        {
            'name': 'Prod Account',
            'account': 'YOUR_PROD_ACCOUNT',
            'role': 'YOUR_ROLE',
            'provider': 'isengard'
        }
    ]
    
    for acc in accounts:
        print(f"   {acc['name']}:")
        print(f"   ada credentials update \\")
        print(f"       --account={acc['account']} \\")
        print(f"       --provider={acc['provider']} \\")
        print(f"       --role={acc['role']} \\")
        print(f"       --once")
        print()
    
    print("💡 The '--once' flag means credentials are temporary (not auto-refreshed)")
    print("💡 For longer sessions, omit '--once' and ADA will auto-refresh")
    print()


def offer_to_fix_credentials():
    """Offer to backup dummy credentials."""
    creds_file = Path.home() / '.aws' / 'credentials'
    backup_file = Path.home() / '.aws' / 'credentials.backup'
    
    if not creds_file.exists():
        return
    
    content = creds_file.read_text()
    
    if 'aws_access_key_id = test' not in content:
        return
    
    print()
    print("=" * 80)
    print("🔧 FIX DUMMY CREDENTIALS")
    print("=" * 80)
    print()
    print("Your ~/.aws/credentials file has dummy values that block Midway/ADA.")
    print()
    
    response = input("Would you like to backup and remove them? (y/n): ").strip().lower()
    
    if response == 'y':
        try:
            # Backup existing file
            if backup_file.exists():
                print(f"⚠️  Backup already exists: {backup_file}")
                response = input("Overwrite backup? (y/n): ").strip().lower()
                if response != 'y':
                    print("Aborted.")
                    return
            
            creds_file.rename(backup_file)
            print(f"✅ Backed up to: {backup_file}")
            print(f"✅ Removed: {creds_file}")
            print()
            print("Now run ADA to generate real credentials:")
            show_ada_commands()
            
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        print("Keeping existing credentials.")
        print()
        print("Manual fix:")
        print(f"   mv {creds_file} {backup_file}")


def main():
    print("=" * 80)
    print("AMAZON INTERNAL MCP REGISTRY - CREDENTIAL SETUP")
    print("=" * 80)
    print()
    
    has_ada = check_ada_installed()
    has_dummy = check_dummy_credentials()
    
    if not has_ada:
        print()
        print("❌ ADA is required but not found.")
        print("   You may not be on an Amazon device or ADA is not installed.")
        sys.exit(1)
    
    if has_dummy:
        offer_to_fix_credentials()
    else:
        print()
        show_ada_commands()
    
    print()
    print("After setting up credentials, test with:")
    print("   python scripts/diagnose_amazon_registry.py")
    print()


if __name__ == "__main__":
    main()
