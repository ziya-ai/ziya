import os
import zipfile
import tempfile
import shutil
import sys
import csv
import hashlib
import base64

def create_platform_independent_wheel():
    print("Creating platform-independent wheel with templates...")
    
    # Find the existing py3-none-any.whl file
    py3_wheel_path = None
    platform_wheel_path = None
    
    if os.path.exists('dist'):
        for file in os.listdir('dist'):
            if file.endswith('.whl'):
                if 'py3-none-any' in file:
                    py3_wheel_path = os.path.join('dist', file)
                else:
                    platform_wheel_path = os.path.join('dist', file)
    
    if not py3_wheel_path and not platform_wheel_path:
        print("No wheel files found in dist directory")
        return
    
    # Use the py3-none-any wheel if available, otherwise use the platform-specific one
    base_wheel_path = py3_wheel_path if py3_wheel_path else platform_wheel_path
    print(f"Using base wheel: {base_wheel_path}")
    
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract the wheel
        with zipfile.ZipFile(base_wheel_path, 'r') as zip_ref:
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
        
        # Add entries for template files
        template_records = []
        for root, dirs, files in os.walk(app_templates_dir):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, temp_dir)
                
                # Calculate hash
                with open(file_path, 'rb') as f:
                    file_hash = hashlib.sha256(f.read()).digest()
                    hash_digest = base64.urlsafe_b64encode(file_hash).rstrip(b'=').decode('ascii')
                
                # Get file size
                file_size = os.path.getsize(file_path)
                
                # Add to records
                template_records.append([rel_path, f"sha256={hash_digest}", str(file_size)])
        
        # Write the updated RECORD file
        with open(record_file, 'w', newline='') as f:
            writer = csv.writer(f)
            for row in records:
                writer.writerow(row)
            for row in template_records:
                writer.writerow(row)
        
        print(f"Added {len(template_records)} template files to RECORD")
        
        # Create the platform-independent wheel filename
        output_wheel_path = os.path.join('dist', 'ziya-0.2.3-py3-none-any.whl')
        
        # Create the wheel
        with zipfile.ZipFile(output_wheel_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zip_ref.write(file_path, arcname)
        
        print(f"Created platform-independent wheel: {output_wheel_path}")
        return output_wheel_path

if __name__ == "__main__":
    create_platform_independent_wheel()
