#!/usr/bin/env python3
"""Simple build script for Ziya."""

import subprocess
import sys
import os
import shutil
from pathlib import Path
# Import the robust process_wheel function
from scripts.post_build import process_wheel
def main():
    """Build the project."""
    print("Building Ziya...")
    
    # Clean previous builds
    if os.path.exists("dist"):
        shutil.rmtree("dist")
        print("Cleaned previous build artifacts")

    # Ensure app/templates directory exists (though process_wheel will handle copying from frontend/build)
    templates_dir = Path("app/templates")
    templates_dir.mkdir(parents=True, exist_ok=True)

    # Build frontend if it exists
    frontend_project_dir = Path("frontend")
    if frontend_project_dir.exists():
        print("Building frontend...")
        try:
            # Check if node_modules exists, if not run npm install
            if not (frontend_project_dir / "node_modules").exists():
                print("Running npm install for frontend...")
                subprocess.run(["npm", "install"], cwd=str(frontend_project_dir), check=True, shell=sys.platform == "win32")
            else:
                print("Frontend dependencies (node_modules) already exist, skipping npm install.")

            subprocess.run(["npm", "run", "build"], cwd=str(frontend_project_dir), check=True, shell=sys.platform == "win32")
            print("Frontend build completed")

            # Copy frontend build to app/templates
            # First, create __init__.py in templates to help Poetry recognize it
            # The copying of frontend build to app/templates is now handled by process_wheel
            # but we still need the __init__.py for Poetry to recognize app.templates as containing package_data
            # if we were to use Poetry's native mechanisms. However, process_wheel handles this directly.
            # init_file = templates_dir / "__init__.py"
            # if not init_file.exists():
            #    init_file.write_text("# Placeholder for package data recognition\n")

        except subprocess.CalledProcessError as e:
            print(f"Frontend build failed: {e}")
            return 1
        except FileNotFoundError:
            print("npm command not found. Please ensure Node.js and npm are installed and in your PATH.")
            return 1
    else:
        print("No frontend directory found, skipping frontend build")

    # Build Python package
    print("Building Python package...")
    try:
        result = subprocess.run(["poetry", "build", "--format", "wheel"], check=True, capture_output=True, text=True)
        print("Poetry build output:", result.stdout)
        if result.stderr:
            print("Poetry build errors:", result.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Poetry build failed: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        return 1
    
    except FileNotFoundError:
        print("poetry command not found. Please ensure Poetry is installed and in your PATH.")
        return 1
 
    # Post-process the wheel to add templates and mcp_servers
    print("Post-processing wheel...")
    process_wheel()

    # Verify the wheel contains templates
    wheel_file = None
    for file in Path("dist").glob("*.whl"):
        wheel_file = file
        break
    
    if wheel_file:
        print(f"Final wheel created: {wheel_file}")
    else:
        print("ERROR: No wheel file found after post-processing.")
        return 1
    
    return 0
    
if __name__ == "__main__":
    sys.exit(main())
