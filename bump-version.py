#!/usr/bin/env python3
import re
import sys
from pathlib import Path

def bump_version(new_version):
    root = Path(__file__).parent
    
    # Convert 4-part version to 3-part for npm (PyPI supports 4-part, npm doesn't)
    npm_version = '.'.join(new_version.split('.')[:3])
    
    # Check if 3-part version changed by reading current package.json
    package_json = root / "frontend" / "package.json"
    old_content = package_json.read_text()
    old_version_match = re.search(r'"version": "([^"]+)"', old_content)
    old_npm_version = old_version_match.group(1) if old_version_match else None
    
    # Update pyproject.toml
    pyproject = root / "pyproject.toml"
    content = pyproject.read_text()
    content = re.sub(r'version = "[^"]+"', f'version = "{new_version}"', content, count=1)
    pyproject.write_text(content)
    print(f"✓ Updated {pyproject}")
    
    # Update setup.py
    setup_py = root / "setup.py"
    content = setup_py.read_text()
    content = re.sub(r'version="[^"]+"', f'version="{new_version}"', content, count=1)
    setup_py.write_text(content)
    print(f"✓ Updated {setup_py}")
    
    # Update frontend/package.json
    content = re.sub(r'"version": "[^"]+"', f'"version": "{npm_version}"', old_content, count=1)
    package_json.write_text(content)
    print(f"✓ Updated {package_json}")
    
    # If 3-part version changed, delete package-lock.json to avoid conflicts
    package_lock = root / "frontend" / "package-lock.json"
    if old_npm_version and old_npm_version != npm_version and package_lock.exists():
        package_lock.unlink()
        print(f"✓ Deleted {package_lock} (3-part version changed)")
    elif package_lock.exists():
        content = package_lock.read_text()
        content = re.sub(r'"version": "[^"]+"', f'"version": "{npm_version}"', content, count=2)
        package_lock.write_text(content)
        print(f"✓ Updated {package_lock}")
    
    print(f"\nVersion bumped to {new_version}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python bump-version.py <version>")
        print("Example: python bump-version.py 0.3.3")
        sys.exit(1)
    
    bump_version(sys.argv[1])
