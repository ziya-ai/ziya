"""
MCP Tool Execution Audit Log.

Writes a structured, append-only log of every tool invocation so that
post-hoc forensic analysis is possible.  The log lives under
~/.ziya/audit/ and rotates daily.

Enabled by default; disable with ZIYA_DISABLE_AUDIT_LOG=1.

Each entry records:
  - timestamp (ISO-8601)
  - tool name
  - argument summary (truncated to prevent log bloat)
  - result status (ok / error)
  - conversation_id (for correlation)
  - verification status (signed / unsigned / failed)
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.utils.logging_utils import logger

_LOG_DIR: Optional[Path] = None
_DISABLED = os.environ.get("ZIYA_DISABLE_AUDIT_LOG", "").lower() in ("1", "true", "yes")


def _ensure_log_dir() -> Optional[Path]:
    """Lazily create and return the audit log directory."""
    global _LOG_DIR
    if _DISABLED:
        return None
    if _LOG_DIR is None:
        try:
            from app.utils.paths import get_ziya_home
            _LOG_DIR = Path(get_ziya_home()) / "audit"
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Audit log directory creation failed: {e}")
            return None
    return _LOG_DIR


def log_tool_execution(
    tool_name: str,
    args: Dict[str, Any],
    result_status: str = "ok",
    conversation_id: str = "",
    verified: Optional[bool] = None,
    error_message: str = "",
    duration_ms: float = 0,
) -> None:
    """Append a single audit entry.

    This function is designed to be safe to call in any context:
    - Never raises exceptions (catches internally)
    - Truncates large values to prevent log bloat
    - Strips internal args (prefixed with _) from the log
    """
    log_dir = _ensure_log_dir()
    if log_dir is None:
        return
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"tool_audit_{today}.jsonl"

        # Truncate large argument values to keep log entries bounded
        safe_args = {}
        for k, v in (args or {}).items():
            if k.startswith("_"):
                continue  # Skip internal params like _workspace_path
            s = str(v)
            safe_args[k] = s[:500] if len(s) > 500 else s

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": safe_args,
            "status": result_status,
            "conv": conversation_id[:12] if conversation_id else "",
            "verified": verified,
            "error": error_message[:200] if error_message else "",
            "ms": round(duration_ms, 1),
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass  # Audit logging must never break the main flow
