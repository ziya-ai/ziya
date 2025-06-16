#!/usr/bin/env python3
"""
Build script for Ziya.
This script is run by Poetry when using 'poetry run fbuild'.
"""

import os
import sys
import subprocess
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fbuild")

def install_typescript():
    """Install TypeScript for AST parser."""
    from scripts.install_typescript import install_typescript as install_ts
    return install_ts()

def main():
    """Main entry point for the build script."""
    # Generate templates first
    from scripts.generate_templates import generate_templates
    generate_templates()
    
    # Get the project root directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Install TypeScript for AST parser
    logger.info("Installing TypeScript for AST parser")
    install_typescript()
    
    # Ensure templates directory exists
    logger.info("Ensuring templates directory exists")
    
    # Run any other build steps here
    
    logger.info("Build completed successfully")
    return 0

if __name__ == "__main__":
    sys.exit(main())
