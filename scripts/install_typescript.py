#!/usr/bin/env python3
"""
Script to install TypeScript for Ziya AST parser.
This script is run during the build process to ensure TypeScript is available.
"""

import os
import subprocess
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("typescript-installer")

def install_typescript():
    """Install TypeScript in the AST parser directory."""
    # Get the project root directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Path to the TypeScript parser directory
    parser_dir = os.path.join(project_root, "app", "utils", "ast_parser", "ts_parser")
    
    # Create the directory if it doesn't exist
    os.makedirs(parser_dir, exist_ok=True)
    
    # Create package.json if it doesn't exist
    package_json_path = os.path.join(parser_dir, "package.json")
    if not os.path.exists(package_json_path):
        logger.info(f"Creating package.json in {parser_dir}")
        with open(package_json_path, "w") as f:
            f.write("""
{
  "name": "ziya-typescript-parser",
  "version": "1.0.0",
  "description": "TypeScript parser for Ziya",
  "main": "parse_typescript.js",
  "dependencies": {
    "typescript": "^4.9.5"
  }
}
            """)
    
    # Install TypeScript
    logger.info(f"Installing TypeScript in {parser_dir}")
    try:
        result = subprocess.run(
            ["npm", "install"],
            cwd=parser_dir,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            logger.info("TypeScript installed successfully")
            return True
        else:
            logger.warning(f"Failed to install TypeScript: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error installing TypeScript: {e}")
        return False

def main():
    """Main entry point."""
    success = install_typescript()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
