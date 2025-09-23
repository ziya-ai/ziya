#!/usr/bin/env python3
"""
Setup script for Mermaid tests - ensures all dependencies are installed
"""

import os
import subprocess
import sys
from pathlib import Path

def check_command(cmd):
    """Check if a command is available"""
    try:
        subprocess.run([cmd, '--version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def install_node_dependencies():
    """Install Node.js dependencies for Mermaid validation"""
    validator_dir = Path(__file__).parent / 'mermaid_validator'
    
    print(f"Installing Mermaid validator dependencies in {validator_dir}")
    
    try:
        result = subprocess.run(['npm', 'install'], 
                              cwd=validator_dir, 
                              capture_output=True, 
                              text=True, 
                              timeout=120)
        
        if result.returncode == 0:
            print("✓ Mermaid validator dependencies installed successfully")
            return True
        else:
            print(f"✗ npm install failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("✗ npm install timed out")
        return False
    except Exception as e:
        print(f"✗ Failed to install dependencies: {e}")
        return False

def main():
    print("Setting up Mermaid validation tests...")
    
    # Check Node.js
    if not check_command('node'):
        print("✗ Node.js not found")
        print("Please install Node.js from https://nodejs.org/")
        print("Required for Mermaid JavaScript validation")
        sys.exit(1)
    else:
        print("✓ Node.js found")
    
    # Check npm
    if not check_command('npm'):
        print("✗ npm not found")
        print("npm should be installed with Node.js")
        sys.exit(1)
    else:
        print("✓ npm found")
    
    # Install Node.js dependencies
    if not install_node_dependencies():
        print("\nFailed to install dependencies. Tests will fall back to basic validation.")
        sys.exit(1)
    
    print("\n✓ Mermaid test setup complete!")
    print("\nYou can now run the tests with:")
    print("  python tests/run_mermaid_tests.py")
    print("  python tests/run_mermaid_tests.py --show-cases")

if __name__ == '__main__':
    main()
