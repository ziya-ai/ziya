#!/usr/bin/env python3
import re
import sys
from pathlib import Path

def bump_version(new_version):
    root = Path(__file__).parent
    
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
    package_json = root / "frontend" / "package.json"
    content = package_json.read_text()
    content = re.sub(r'"version": "[^"]+"', f'"version": "{new_version}"', content, count=1)
    package_json.write_text(content)
    print(f"✓ Updated {package_json}")
    
    # Update frontend/package-lock.json (first two occurrences only)
    package_lock = root / "frontend" / "package-lock.json"
    content = package_lock.read_text()
    content = re.sub(r'"version": "0\.\d+\.\d+"', f'"version": "{new_version}"', content, count=2)
    package_lock.write_text(content)
    print(f"✓ Updated {package_lock}")
    
    print(f"\nVersion bumped to {new_version}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python bump-version.py <version>")
        print("Example: python bump-version.py 0.3.3")
        sys.exit(1)
    
    bump_version(sys.argv[1])
