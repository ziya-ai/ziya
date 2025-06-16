#!/usr/bin/env python3
"""
Post-build script to convert platform-specific wheel to platform-independent wheel,
and include templates.
"""

import os
import zipfile
import tempfile
import shutil
import sys
import csv
import hashlib
import base64
import re

def process_wheel():
    """
    Process the wheel to include templates and make it platform-independent.
    """
    print("Post-build: Creating platform-independent wheel with templates...")
    
    # Find the wheel file
    wheel_path = None
    if os.path.exists('dist'):
        for file in os.listdir('dist'):
            if file.endswith('.whl'):
                wheel_path = os.path.join('dist', file)
                break
    
    if not wheel_path:
        print("No wheel file found in dist directory")
        return
    
    print(f"Processing wheel: {wheel_path}")
    
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract the wheel
        with zipfile.ZipFile(wheel_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Check if templates directory exists
        templates_src = os.path.join(os.getcwd(), 'templates')
        if not os.path.exists(templates_src):
            templates_src = os.path.join(os.getcwd(), 'frontend', 'build')
            if not os.path.exists(templates_src):
                print("ERROR: Could not find templates directory at ./templates or ./frontend/build")
                return
            print(f"Using templates from {templates_src}")
        else:
            print(f"Using templates from {templates_src}")
        
        # Copy templates to app/templates in the wheel
        app_dir = os.path.join(temp_dir, 'app')
        if not os.path.exists(app_dir):
            os.makedirs(app_dir)
            print(f"Created app directory at {app_dir}")
        
        app_templates_dir = os.path.join(app_dir, 'templates')
        if os.path.exists(app_templates_dir):
            shutil.rmtree(app_templates_dir)
        
        print(f"Copying templates from {templates_src} to {app_templates_dir}")
        shutil.copytree(templates_src, app_templates_dir)
        
        # --- Handle mcp_servers ---
        mcp_servers_src = os.path.join(os.getcwd(), 'app', 'mcp_servers')
        app_mcp_servers_dir = os.path.join(app_dir, 'mcp_servers')
        
        if os.path.exists(mcp_servers_src) and os.path.isdir(mcp_servers_src):
            if os.path.exists(app_mcp_servers_dir):
                shutil.rmtree(app_mcp_servers_dir)
            shutil.copytree(mcp_servers_src, app_mcp_servers_dir)
            print(f"Copied MCP servers from {mcp_servers_src} to {app_mcp_servers_dir}")
        else:
            print(f"WARNING: mcp_servers directory not found at {mcp_servers_src}, built-in MCP servers might not work.")
        
        # Find the RECORD file
        dist_info_dir = None
        for item in os.listdir(temp_dir):
            if item.endswith('.dist-info'):
                dist_info_dir = os.path.join(temp_dir, item)
                break
        
        if not dist_info_dir:
            print("ERROR: Could not find .dist-info directory in wheel")
            return
        
        record_file = os.path.join(dist_info_dir, 'RECORD')
        if not os.path.exists(record_file):
            print(f"ERROR: Could not find RECORD file at {record_file}")
            return
        
        # Read the existing RECORD file
        records = []
        with open(record_file, 'r', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                records.append(row)
        
        # Add entries for template files - (Existing logic, ensure it's compatible with mcp_servers addition)
        asset_records = [] # Combined list for templates and mcp_servers
        
        # Helper to add asset records
        def add_asset_records(asset_dir):
            for root, dirs, files_in_dir in os.walk(asset_dir): # Renamed 'files' to 'files_in_dir'
                for file_item in files_in_dir: # Renamed 'file' to 'file_item'
                    file_path = os.path.join(root, file_item)
                    rel_path = os.path.relpath(file_path, temp_dir).replace(os.sep, '/') # Normalize path separators
                    
                    # Calculate hash
                    with open(file_path, 'rb') as f_content: # Renamed 'f' to 'f_content'
                        file_hash = hashlib.sha256(f_content.read()).digest()
                        hash_digest = base64.urlsafe_b64encode(file_hash).rstrip(b'=').decode('ascii')
                    
                    # Get file size
                    file_size = os.path.getsize(file_path)
                    
                    # Add to records
                    asset_records.append([rel_path, f"sha256={hash_digest}", str(file_size)])

        # Add records for templates
        add_asset_records(app_templates_dir)
        # Add records for mcp_servers
        if os.path.exists(app_mcp_servers_dir): # Check if mcp_servers were copied
            add_asset_records(app_mcp_servers_dir)
        
        # Write the updated RECORD file
        with open(record_file, 'w', newline='') as f:
            writer = csv.writer(f)
            existing_non_asset_records = [r for r in records if not (r[0].startswith('app/templates/') or r[0].startswith('app/mcp_servers/'))]
            # Add the RECORD file itself to the list of records to write, without hash and size
            record_rel_path = os.path.relpath(record_file, temp_dir).replace(os.sep, '/')
            all_records_to_write = sorted(existing_non_asset_records + asset_records + [[record_rel_path, "", ""]], key=lambda x: x[0])
            for row in all_records_to_write:
                writer.writerow(row)
        
        print(f"Updated RECORD file with {len(asset_records)} asset file entries (templates and mcp_servers).")
        
        # Always create a platform-independent wheel
        wheel_filename = os.path.basename(wheel_path)
        
        # Extract the package name and version
        # Replace the platform-specific part with py3-none-any
        package_name, version = wheel_filename.split('-')[:2]
        new_filename = f"{package_name}-{version}-py3-none-any.whl"
        new_wheel_path = os.path.join(os.path.dirname(wheel_path), new_filename)
        
        # Create the platform-independent wheel
        with zipfile.ZipFile(new_wheel_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zip_ref.write(file_path, arcname)
        
        print(f"Created platform-independent wheel: {new_filename}")
        
        # Remove the original wheel if it's different
        if new_wheel_path != wheel_path and os.path.exists(wheel_path):
            os.remove(wheel_path)
            print(f"Removed platform-specific wheel: {wheel_path}")

if __name__ == "__main__":
    process_wheel()
