"""
Builtin tool: mcp_server_status

Self-inspection for MCP server startup.  The per-server startup logs,
``startup_stage``, and preflight/startup failure diagnostics live only in
the running manager's in-memory ``MCPClient`` objects (app/mcp/client.py)
and are surfaced to the browser's Logs tab through the MCP status route in
app/routes/mcp_routes.py.  Nothing exposed those same fields to the model,
so when a user asked Ziya about their own failing server the model was
blind to the exact data that would answer it and could only ask the user
to paste their Logs tab.

This tool reads the same manager the route reads (``get_mcp_manager``) and
returns, per server: connection state, the furthest startup stage reached,
any preflight/startup failure diagnostic, tool/resource/prompt counts, and
a tail of the captured log buffer.  Read-only: it never starts, stops, or
otherwise mutates a server.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool

logger = logging.getLogger(__name__)

# Cap the per-server log tail returned by default.  A server's buffer can
# run to hundreds of lines (registry install output, repeated reconnect
# attempts); dumping all of it for every server would swamp the model's
# context.  log_lines=0 opts into the whole buffer for deep debugging.
_DEFAULT_LOG_TAIL = 60


def _text(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}]}


class McpServerStatusInput(BaseModel):
    """Input schema for mcp_server_status."""

    server_name: Optional[str] = Field(
        default=None,
        description=(
            "Report only this server.  Omit (the default) to report every "
            "configured MCP server, including ones that failed to start."
        ),
    )
    log_lines: int = Field(
        default=_DEFAULT_LOG_TAIL,
        description=(
            f"How many trailing log lines to include per server (default "
            f"{_DEFAULT_LOG_TAIL}).  Use 0 for the entire captured buffer."
        ),
    )


class McpServerStatusTool(BaseMCPTool):
    """Report MCP server startup state and logs for self-inspection."""

    name: str = "mcp_server_status"
    description: str = (
        "[DIRECT] Inspect the startup state and logs of this Ziya "
        "instance's own MCP servers — the same data shown in the Logs tab "
        "of the MCP panel. Returns, per server: connected/quarantined "
        "flags, the furthest startup stage reached "
        "(config -> preflight -> spawn -> handshake -> ready), any "
        "preflight/startup failure diagnostic, tool/resource/prompt counts, "
        "and a tail of the log buffer. Call with no arguments to get every "
        "server; pass 'server_name' to focus on one. Use this when a user "
        "asks why an MCP server failed to start. Read-only — it never "
        "starts, stops, or mutates a server."
    )

    InputSchema = McpServerStatusInput

    def _server_report(
        self,
        name: str,
        config: Dict[str, Any],
        client: Any,
        quarantined_set: Any,
        log_lines: int,
    ) -> Dict[str, Any]:
        is_builtin = bool(config.get("builtin", False)) if isinstance(config, dict) else False
        report: Dict[str, Any] = {
            "name": name,
            "builtin": is_builtin,
            "quarantined": name in quarantined_set,
        }
        if client is None:
            # Configured but no client: rejected at config load or never
            # started, so it has no diagnostic buffer of its own.  The
            # config-findings panel names the offending key.
            report.update({
                "connected": False,
                "startup_stage": None,
                "status": "no_client",
                "note": (
                    "Configured but has no client — rejected during config "
                    "load or never started. Check the MCP config-findings "
                    "panel for the offending key."
                ),
            })
            return report

        logs = list(getattr(client, "logs", []) or [])
        total_logs = len(logs)
        logs_out = logs[-log_lines:] if log_lines and log_lines > 0 else logs
        report.update({
            "connected": bool(getattr(client, "is_connected", False)),
            "startup_stage": getattr(client, "startup_stage", None),
            "preflight_failure": getattr(client, "preflight_failure", None),
            "startup_failure": getattr(client, "startup_failure", None),
            "tools": len(getattr(client, "tools", []) or []),
            "resources": len(getattr(client, "resources", []) or []),
            "prompts": len(getattr(client, "prompts", []) or []),
            "log_lines_total": total_logs,
            "log_lines_returned": len(logs_out),
            "logs": logs_out,
        })
        return report

    async def execute(self, **kwargs) -> Any:
        kwargs.pop("_workspace_path", None)

        try:
            log_lines = int(kwargs.get("log_lines", _DEFAULT_LOG_TAIL))
        except (TypeError, ValueError):
            return _text("Error: 'log_lines' must be an integer.")
        if log_lines < 0:
            return _text("Error: 'log_lines' must be 0 or greater.")

        server_name = kwargs.get("server_name") or None

        try:
            from app.mcp.manager import get_mcp_manager
            manager = get_mcp_manager()
        except Exception as e:  # noqa: BLE001
            return _text(f"Error: could not access the MCP manager — {e}")

        if not getattr(manager, "is_initialized", False):
            return _text(
                "MCP is not initialized in this Ziya instance (it may be "
                "disabled via ZIYA_ENABLE_MCP, or still starting). No server "
                "diagnostics are available."
            )

        server_configs = getattr(manager, "server_configs", {}) or {}
        clients = getattr(manager, "clients", {}) or {}
        quarantined = getattr(manager, "_quarantined_servers", set()) or set()

        if server_name is not None:
            if server_name not in server_configs and server_name not in clients:
                known = sorted(set(server_configs) | set(clients))
                return _text(
                    f"No MCP server named '{server_name}'. Known servers: "
                    f"{', '.join(known) if known else '(none)'}"
                )
            names = [server_name]
        else:
            names = sorted(set(server_configs) | set(clients))

        if not names:
            return _text(
                "No MCP servers are configured in this Ziya instance."
            )

        reports = [
            self._server_report(
                n, server_configs.get(n, {}), clients.get(n),
                quarantined, log_lines,
            )
            for n in names
        ]
        return _text(json.dumps(
            {"count": len(reports), "servers": reports}, indent=1,
        ))
