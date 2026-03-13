"""
Disk-based AST cache.

Persists per-file AST data to .ziya/ast_cache/ so that unchanged files
don't need to be re-parsed on subsequent startups.  Each project gets its
own cache file, keyed by a hash of the absolute project root.

Cache format (gzipped JSON):
    {
        "version": 1,
        "files": {
            "<abs_path>": {
                "mtime": <float>,
                "size":  <int>,
                "ast":   <dict>          # UnifiedAST.to_dict() output
            }
        }
    }
"""

import gzip
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.utils.logging_utils import logger

CACHE_VERSION = 1


def _cache_dir() -> Path:
    """Return (and create) the ast_cache directory under .ziya/."""
    home = Path(os.environ.get("ZIYA_HOME", Path.home() / ".ziya"))
    d = home / "ast_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(project_root: str) -> Path:
    """Deterministic cache file for a project root."""
    h = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    return _cache_dir() / f"{h}.json.gz"


def load_cache(project_root: str) -> Dict[str, Any]:
    """
    Load the AST cache for *project_root*.

    Returns a dict mapping absolute file paths to
    ``{"mtime": float, "size": int, "ast": dict}``.
    Returns an empty dict on miss, corruption, or version mismatch.
    """
    path = _cache_path(project_root)
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION:
            logger.debug("AST cache version mismatch, discarding")
            return {}
        return data.get("files", {})
    except Exception as e:
        logger.debug(f"AST cache load failed ({e}), will re-index")
        return {}


def save_cache(project_root: str, file_entries: Dict[str, Any]) -> None:
    """
    Persist the AST cache for *project_root*.

    *file_entries* maps absolute paths to
    ``{"mtime": float, "size": int, "ast": dict}``.
    """
    path = _cache_path(project_root)
    payload = {"version": CACHE_VERSION, "files": file_entries}
    try:
        start = time.monotonic()
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=3) as f:
            json.dump(payload, f, separators=(",", ":"))
        elapsed = time.monotonic() - start
        size_kb = path.stat().st_size / 1024
        logger.info(
            f"AST cache saved: {len(file_entries)} files, "
            f"{size_kb:.0f} KB, {elapsed:.1f}s"
        )
    except Exception as e:
        logger.warning(f"AST cache save failed: {e}")


def is_fresh(entry: Dict[str, Any], file_path: str) -> bool:
    """
    Check whether a cache entry is still valid for the file on disk.

    Compares mtime and size — cheap stat() vs expensive parse.
    """
    try:
        st = os.stat(file_path)
        return (
            abs(st.st_mtime - entry.get("mtime", 0)) < 0.01
            and st.st_size == entry.get("size", -1)
        )
    except OSError:
        return False
