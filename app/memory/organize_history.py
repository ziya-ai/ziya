"""
Bounded JSON log of organize-pass results.

Drives the Memory Browser's "Recent Activity" tab.  Stored at
~/.ziya/memory/organize_history.json, capped at 50 entries.  Each entry
records the timestamp + summary of one organize run, including the REM
phase results so the UI can show synthesis/contested activity over time.

Append-only on success; never raises.  Callers that fail to log don't
break organization.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from app.utils.logging_utils import logger


_HISTORY_CAP = 50


def _history_file() -> Path:
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "memory" / "organize_history.json"


def append_organize_result(result: Dict[str, Any]) -> None:
    """Append a summary record to the bounded history log.

    Strips heavy / repetitive fields to keep the log compact and useful
    for the UI.  Never raises.
    """
    try:
        path = _history_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        history: List[Dict[str, Any]] = []
        if path.exists():
            try:
                history = json.loads(path.read_text())
                if not isinstance(history, list):
                    history = []
            except (json.JSONDecodeError, OSError):
                history = []

        record = {
            "timestamp": int(time.time() * 1000),
            "cleanup": {
                "removed": (result.get("cleanup") or {}).get("removed", 0),
                "merged": (result.get("cleanup") or {}).get("merged", 0),
                "reviewed": (result.get("cleanup") or {}).get("reviewed", 0),
            },
            "bootstrap": {
                "domains_created": (result.get("bootstrap") or {}).get("domains_created", 0),
                "domains_updated": (result.get("bootstrap") or {}).get("domains_updated", 0),
                "memories_placed": (result.get("bootstrap") or {}).get("memories_placed", 0),
            },
            "relations_found": (result.get("relations") or {}).get("relations_found", 0),
            "cross_links_added": len(result.get("cross_links") or []),
            "divisions": len(result.get("divisions") or []),
            "rem": {
                "nodes_mature": (result.get("rem") or {}).get("nodes_mature", 0),
                "syntheses_created": (result.get("rem") or {}).get("syntheses_created", 0),
                "memories_contested": (result.get("rem") or {}).get("memories_contested", 0),
                "syntheses": (result.get("rem") or {}).get("syntheses", []),
                "contested": (result.get("rem") or {}).get("contested", []),
            },
        }
        history.append(record)
        if len(history) > _HISTORY_CAP:
            history = history[-_HISTORY_CAP:]
        path.write_text(json.dumps(history, indent=2))
    except Exception as e:
        logger.warning(f"organize_history append failed (non-fatal): {e}")


def load_organize_history() -> List[Dict[str, Any]]:
    """Return the history log, newest-first.  Empty list on any failure."""
    try:
        path = _history_file()
        if not path.exists():
            return []
        history = json.loads(path.read_text())
        if not isinstance(history, list):
            return []
        return list(reversed(history))
    except Exception:
        return []
