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
    # PenPal #134 [CWE-200]: ~/.ziya holds key material (keyring.json, the
    # PBKDF2 salt) and cached source contents. A plain mkdir leaves it at the
    # umask default (typically 0o755 = world-traversable), so during the brief
    # window before any per-file chmod a cross-user local process could reach
    # a sensitive file inside it. Harden the directory itself to owner-only so
    # the containing dir denies traversal regardless of any inner file's mode.
    # Best-effort: a chmod failure (e.g. exotic FS) must not break startup.
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
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
