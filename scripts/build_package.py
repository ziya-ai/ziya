#!/usr/bin/env python3
"""Build script to ensure templates are generated before packaging."""

import os
import subprocess
import sys

def main():
    # Get the project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Build frontend
    frontend_dir = os.path.join(project_root, "frontend")
    if os.path.exists(frontend_dir):
        print("Building frontend...")
        subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "copy-to-templates"], cwd=frontend_dir, check=True)
    
    # Now build the Python package
    print("Building Python package...")
    subprocess.run(["poetry", "build"], cwd=project_root, check=True)

if __name__ == "__main__":
    main()
