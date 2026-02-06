"""
Path utilities for Ziya session management.
"""
import os
from pathlib import Path

def get_ziya_home() -> Path:
    """Get the Ziya home directory, creating if necessary."""
    # Allow override via environment variable
    if 'ZIYA_HOME' in os.environ:
        home = Path(os.environ['ZIYA_HOME'])
    else:
        home = Path.home() / '.ziya'
    
    home.mkdir(parents=True, exist_ok=True)
    return home

def get_project_dir(project_id: str) -> Path:
    """Get the directory for a specific project."""
    return get_ziya_home() / 'projects' / project_id

def validate_relative_path(base_path: str, relative_path: str) -> bool:
    """Ensure relative_path doesn't escape base_path."""
    base = Path(base_path).resolve()
    full = (base / relative_path).resolve()
    
    try:
        full.relative_to(base)
        return True
    except ValueError:
        return False
