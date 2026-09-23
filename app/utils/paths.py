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


# ---------------------------------------------------------------------------
# Classified storage accessors (design/capabilities-hub.md, slice 1).
#
# Writers should compose paths through these rather than inline
# ``Path.home() / ".ziya" / ...``.  The classes exist so a later sweeper can
# age ``cache`` and ``scratch`` without touching durable state, and so the
# storage-layout consolidation (design/backlog-storage-layout-consolidation.md)
# has a target to migrate existing sites toward.  Nothing existing is moved.
# ---------------------------------------------------------------------------

_STATE_NAME_RE = __import__('re').compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


def _check_name(name: str) -> str:
    # Names become path segments; refuse separators and dotfiles so a caller
    # cannot escape the class directory or shadow a hidden key file.
    if not _STATE_NAME_RE.match(name) or name in ('.', '..'):
        raise ValueError(f"invalid storage name: {name!r}")
    return name


def user_state_file(name: str) -> Path:
    """Durable per-user state: ``~/.ziya/state/<name>.json``."""
    d = get_ziya_home() / 'state'
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_check_name(name)}.json"


def project_state_file(project_id: str, name: str) -> Path:
    """Durable per-project, per-machine state:
    ``~/.ziya/projects/<id>/state/<name>.json``."""
    d = get_project_dir(_check_name(project_id)) / 'state'
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_check_name(name)}.json"


def cache_dir(name: str) -> Path:
    """Regenerable data eligible for TTL sweeping: ``~/.ziya/cache/<name>/``."""
    d = get_ziya_home() / 'cache' / _check_name(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def scratch_dir(name: str) -> Path:
    """Transient working files, sweepable at any time: ``~/.ziya/scratch/<name>/``."""
    d = get_ziya_home() / 'scratch' / _check_name(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_relative_path(base_path: str, relative_path: str) -> bool:
    """Ensure relative_path doesn't escape base_path."""
    base = Path(base_path).resolve()
    full = (base / relative_path).resolve()
    
    try:
        full.relative_to(base)
        return True
    except ValueError:
        return False
