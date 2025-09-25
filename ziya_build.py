#!/usr/bin/env python3
"""Simple build script for Ziya."""

import subprocess
import sys
import os
import shutil
from pathlib import Path
# Import the robust process_wheel function
from scripts.post_build import process_wheel

def get_newest_mtime(directory):
    """Get the newest modification time in a directory tree."""
    newest = 0
    for root, dirs, files in os.walk(directory):
        # Skip node_modules and build directories
        dirs[:] = [d for d in dirs if d not in ['node_modules', 'build']]
        for file in files:
            file_path = os.path.join(root, file)
            try:
                mtime = os.path.getmtime(file_path)
                if mtime > newest:
                    newest = mtime
            except OSError:
                continue
    return newest

def should_rebuild_frontend():
    """Check if frontend needs rebuilding."""
    frontend_dir = Path("frontend")
    build_dir = frontend_dir / "build"
    
    if not build_dir.exists():
        return True
    
    # Get build time
    try:
        build_mtime = os.path.getmtime(build_dir)
    except OSError:
        return True
    
    # Check if any source files are newer than build
    src_mtime = get_newest_mtime(frontend_dir / "src")
    config_files = ["package.json", "tsconfig.json", "webpack.config.js", "eslint.config.mjs"]
    
    for config_file in config_files:
        config_path = frontend_dir / config_file
        if config_path.exists():
            try:
                if os.path.getmtime(config_path) > build_mtime:
                    return True
            except OSError:
                continue
    
    return src_mtime > build_mtime
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
        if should_rebuild_frontend():
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

            except subprocess.CalledProcessError as e:
                print(f"Frontend build failed: {e}")
                return 1
            except FileNotFoundError:
                print("npm command not found. Please ensure Node.js and npm are installed and in your PATH.")
                return 1
        else:
            print("Frontend source unchanged since last build, skipping TypeScript compilation")
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
