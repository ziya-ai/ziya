import os
import zipfile
import tempfile
import shutil
import sys
import csv
import hashlib
import base64

def build(setup_kwargs):
    """
    This function is called by Poetry during the build process.
    It modifies setup_kwargs to ensure templates are included.
    """
    # Ensure we're building a pure Python package
    setup_kwargs.update({
        'zip_safe': False,
        'include_package_data': True,
    })
    
    # Register a post-build hook to modify the wheel after Poetry creates it
    import atexit
    atexit.register(process_wheel_after_build)
    
    return setup_kwargs

def process_wheel_after_build():
    """
    This function runs after Poetry builds the wheel.
    It modifies the wheel to include templates and ensure platform independence.
    """
    print("Processing wheel to include templates and ensure platform independence...")
    
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
        
        # Check if the wheel filename contains platform-specific tags
        wheel_filename = os.path.basename(wheel_path)
        if 'py3-none-any' not in wheel_filename:
            # Create a new platform-independent wheel filename
            parts = wheel_filename.split('-')
            new_filename = f"{parts[0]}-{parts[1]}-py3-none-any.whl"
            new_wheel_path = os.path.join(os.path.dirname(wheel_path), new_filename)
            print(f"Creating platform-independent wheel: {new_filename}")
        else:
            new_wheel_path = wheel_path
        
        # Create the wheel
        with zipfile.ZipFile(new_wheel_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zip_ref.write(file_path, arcname)
        
        print(f"Templates successfully added to {new_wheel_path}")
        
        # If we created a new wheel file, remove the old one
        if new_wheel_path != wheel_path and os.path.exists(wheel_path):
            os.remove(wheel_path)
            print(f"Removed platform-specific wheel: {wheel_path}")

if __name__ == "__main__":
    process_wheel_after_build()
