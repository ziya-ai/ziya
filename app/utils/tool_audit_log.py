"""
MCP Tool Execution Audit Log.

Writes a structured, append-only log of every tool invocation so that
post-hoc forensic analysis is possible.  The log lives under
~/.ziya/audit/ and rotates daily.

Enabled by default; disable with ZIYA_DISABLE_AUDIT_LOG=1.

Each entry records (aligned with SEL §5.1.4):
  - eventTime      — ISO-8601 UTC timestamp
  - eventName      — tool name (= the action requested)
  - userIdentity   — OS-level user running the process
  - principalType  — always "LocalUser" for localhost
  - sourceHostname — machine hostname
  - args           — argument summary (truncated to prevent log bloat)
  - status         — ok | error
  - conv           — conversation_id for correlation
  - verified       — HMAC verification status (true / false / null)
  - error          — error message if status=error
  - ms             — execution duration in milliseconds

References:
  - Amazon Security Event Logging Standard §5.1.4
  - Aristotle SDO-183 (hidden character smuggling audit trail)
"""

import getpass
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.utils.logging_utils import logger

_LOG_DIR: Optional[Path] = None
_DISABLED = os.environ.get("ZIYA_DISABLE_AUDIT_LOG", "").lower() in ("1", "true", "yes")

_HOSTNAME: Optional[str] = None
_USERNAME: Optional[str] = None


def _get_hostname() -> str:
    """Cache and return the machine hostname."""
    global _HOSTNAME
    if _HOSTNAME is None:
        try:
            _HOSTNAME = socket.gethostname()
        except Exception:
            _HOSTNAME = "unknown"
    return _HOSTNAME


def _get_username() -> str:
    """Cache and return the OS-level username."""
    global _USERNAME
    if _USERNAME is None:
        try:
            _USERNAME = getpass.getuser()
        except Exception:
            _USERNAME = "unknown"
    return _USERNAME


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
            # Restrict directory permissions to owner-only (SEL §5.2.1.1)
            try:
                os.chmod(_LOG_DIR, 0o700)
            except OSError:
                pass  # Best-effort on platforms that don't support chmod
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
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "eventName": tool_name,
            "userIdentity": _get_username(),
            "principalType": "LocalUser",
            "sourceHostname": _get_hostname(),
            "args": safe_args,
            "status": result_status,
            "conv": conversation_id[:12] if conversation_id else "",
            "verified": verified,
            "error": error_message[:200] if error_message else "",
            "ms": round(duration_ms, 1),
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        # Restrict file permissions to owner-only (SEL §5.2.1.1)
        try:
            os.chmod(log_file, 0o600)
        except OSError:
            pass  # Best-effort
    except Exception:
        pass  # Audit logging must never break the main flow
