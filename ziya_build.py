#!/usr/bin/env python3
"""Simple build script for Ziya."""

import subprocess
import sys
import os
import shutil
from pathlib import Path

def main():
    """Build the project."""
    print("Building Ziya...")
    
    # Clean previous builds
    if os.path.exists("dist"):
        shutil.rmtree("dist")
        print("Cleaned previous build artifacts")
    
    # Ensure app/templates directory exists
    templates_dir = Path("app/templates")
    templates_dir.mkdir(parents=True, exist_ok=True)
    
    # Build frontend if it exists
    if os.path.exists("frontend"):
        print("Building frontend...")
        try:
            subprocess.run(["npm", "install"], cwd="frontend", check=True)
            subprocess.run(["npm", "run", "build"], cwd="frontend", check=True)
            print("Frontend build completed")
            
            # Copy frontend build to app/templates
            # First, create __init__.py in templates to help Poetry recognize it
            init_file = templates_dir / "__init__.py"
            init_file.write_text("# This file ensures Poetry includes the templates directory as package data\n# Templates are served by FastAPI's Jinja2Templates\n")
            
            # Copy the frontend build
            frontend_build = Path("frontend/build")
            if frontend_build.exists():
                if templates_dir.exists():
                    shutil.rmtree(templates_dir)
                shutil.copytree(frontend_build, templates_dir)
                print(f"Copied frontend build to {templates_dir}")
                
        except subprocess.CalledProcessError as e:
            print(f"Frontend build failed: {e}")
            return 1
    else:
        print("No frontend directory found, skipping frontend build")
    
    # Verify templates exist and have content
    if templates_dir.exists():
        template_files = list(templates_dir.rglob("*"))
        print(f"Found {len(template_files)} template files:")
        for f in template_files[:10]:  # Show first 10 files
            print(f"  {f}")
        if len(template_files) > 10:
            print(f"  ... and {len(template_files) - 10} more files")
    else:
        print("No templates directory found - this will cause issues")
    
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
    
    # Post-process the wheel to add templates and static files
    add_assets_to_wheel()
    # Verify the wheel contains templates
    wheel_file = None
    for file in Path("dist").glob("*.whl"):
        wheel_file = file
        break
    
    if wheel_file:
        print(f"Wheel created: {wheel_file}")
        # You can add verification here if needed
    
    return 0

def add_assets_to_wheel():
    """Add templates and static files to the built wheel."""
    import zipfile
    import tempfile
    
    # Find the wheel file
    dist_dir = Path("dist")
    wheel_files = list(dist_dir.glob("*.whl"))
    
    if not wheel_files:
        print("No wheel file found")
        return
    
    wheel_path = wheel_files[0]
    print(f"Adding assets to wheel: {wheel_path}")
    
    # Check if templates exist
    templates_dir = Path("app/templates")
    if not templates_dir.exists():
        print("No templates directory found")
        return
    
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Extract the wheel
        with zipfile.ZipFile(wheel_path, 'r') as zip_ref:
            zip_ref.extractall(temp_path)
        
        # Copy templates to the extracted wheel
        wheel_templates_dir = temp_path / "app" / "templates"
        if wheel_templates_dir.exists():
            shutil.rmtree(wheel_templates_dir)
        
        shutil.copytree(templates_dir, wheel_templates_dir)
        template_count = len(list(templates_dir.rglob("*")))
        print(f"Copied {template_count} template files to wheel")
        
        # Update the RECORD file to include the new files
        update_wheel_record(temp_path, wheel_templates_dir)
        
        # Recreate the wheel
        wheel_path.unlink()  # Remove old wheel
        
        with zipfile.ZipFile(wheel_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for file_path in temp_path.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(temp_path)
                    zip_ref.write(file_path, arcname)
        
        print(f"Recreated wheel with assets: {wheel_path}")
 
def update_wheel_record(temp_path, templates_dir):
    """Update the RECORD file to include template files."""
    import csv
    import hashlib
    import base64
    
    # Find the .dist-info directory
    dist_info_dirs = list(temp_path.glob("*.dist-info"))
    if not dist_info_dirs:
        print("No .dist-info directory found")
        return
    
    dist_info_dir = dist_info_dirs[0]
    record_file = dist_info_dir / "RECORD"
    
    if not record_file.exists():
        print("No RECORD file found")
        return
    
    # Read existing records
    records = []
    with open(record_file, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            records.append(row)
    
    # Add records for template files
    for file_path in templates_dir.rglob("*"):
        if file_path.is_file():
            rel_path = file_path.relative_to(temp_path)
            
            # Calculate hash
            with open(file_path, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).digest()
                hash_digest = base64.urlsafe_b64encode(file_hash).rstrip(b'=').decode('ascii')
            
            # Get file size
            file_size = file_path.stat().st_size
            
            # Add to records
            records.append([str(rel_path), f"sha256={hash_digest}", str(file_size)])
    
    # Write updated RECORD file
    with open(record_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        for row in records:
            writer.writerow(row)
    
    print(f"Updated RECORD file with {len(list(templates_dir.rglob('*')))} template entries")

if __name__ == "__main__":
    sys.exit(main())
