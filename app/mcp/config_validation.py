"""
Per-server validation of mcp_config.json entries.

Whole-file JSON syntax errors were already reported (``MCPManager.config_error``),
but a file that parses cleanly could still contain entries that are silently
unusable: a typo'd key (``"commands"`` for ``"command"``), a non-string env
value, a relative script path. Those servers were dropped during load with a
``logger.info`` and never appeared in the GUI at all — the user's only signal
was a server they configured being absent, with nothing explaining why.

This module reports such problems as structured findings so the GUI can name
the offending key, quote the line, and suggest the correction.

Findings are advisory: validation never rejects a server that the loader would
otherwise accept. ``severity`` distinguishes "this entry cannot work" (error)
from "this is suspicious but was handled" (warning), so a config full of
harmless legacy keys does not read as broken.
"""

import difflib
import json
import re
from typing import Any, Dict, List, Optional

# Keys the loader understands. Anything else in a server entry is either a typo
# or dead config, and in both cases the user's intent was not honoured.
_KNOWN_SERVER_KEYS = frozenset({
    "command", "args", "env", "url", "auth", "enabled", "disabled",
    "builtin", "trusted", "installation_path", "timeout", "max_retries",
    "external_server", "enable_response_cleaning", "description",
    "workspace_scoped", "name",

    # Provenance written by mcp/registry_manager.py when it installs a service
    # from a registry, and read back by it to render the installed-services
    # list.  Not consumed by the launch path, but Ziya wrote them, so flagging
    # them accuses the tool of misconfiguring itself — every registry install
    # produced eight findings on a config that works correctly.
    "registry_provider", "service_id", "version", "support_level",
    "installed_at", "cti", "bindle_id", "security_review_url",
})

# Keys whose absence makes an entry unusable: the loader needs at least one way
# to reach the server.
_LAUNCH_KEYS = ("command", "url", "installation_path")

# Explicit aliases for mistakes difflib does not catch: abbreviations share too
# few characters to score, and case differences score as unrelated strings.
# Lowering the difflib cutoff instead would produce confidently wrong guesses,
# which is worse than none.
_KEY_ALIASES = {
    "cmd": "command",
    "exec": "command",
    "executable": "command",
    "argv": "args",
    "arguments": "args",
    "environment": "env",
    "envvars": "env",
    "endpoint": "url",
}


def _find_key_line(raw: str, server_name: str, key: str) -> Optional[int]:
    """Best-effort 1-based line number of ``key`` within ``server_name``'s block.

    json.load() discards positions, so the raw text is re-scanned. This is
    presentational only — a wrong or missing line number degrades the message
    but never changes validation behaviour, so the scan stays deliberately
    simple rather than becoming a second JSON parser.
    """
    lines = raw.splitlines()
    server_pat = re.compile(r'"%s"\s*:' % re.escape(server_name))
    key_pat = re.compile(r'"%s"\s*:' % re.escape(key))

    start = None
    for i, line in enumerate(lines):
        if server_pat.search(line):
            start = i
            break
    if start is None:
        return None

    # Walk forward tracking brace depth; stop when the server's object closes.
    depth = 0
    seen_open = False
    for i in range(start, len(lines)):
        line = lines[i]
        if key_pat.search(line) and (seen_open or i == start):
            return i + 1
        depth += line.count("{") + line.count("[")
        depth -= line.count("}") + line.count("]")
        if line.count("{"):
            seen_open = True
        if seen_open and depth <= 0 and i > start:
            break
    return None


def _finding(
    server: str,
    code: str,
    summary: str,
    detail: str,
    severity: str = "error",
    line: Optional[int] = None,
    suggestion: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "server": server,
        "code": code,
        "severity": severity,
        "summary": summary,
        "detail": detail,
        "line": line,
        "suggestion": suggestion,
    }


def validate_server_entry(
    server_name: str,
    entry: Any,
    raw_text: str = "",
) -> List[Dict[str, Any]]:
    """Return findings for a single server entry. Never raises."""
    findings: List[Dict[str, Any]] = []

    if not isinstance(entry, dict):
        return [_finding(
            server_name,
            "entry_not_object",
            f"Entry must be a JSON object, got {type(entry).__name__}",
            "Each server maps to an object like "
            '{"command": "npx", "args": ["-y", "pkg"]}.',
            line=_find_key_line(raw_text, server_name, server_name),
        )]

    # Unknown / misspelled keys. Reported before the missing-launch check so a
    # typo'd "commands" explains itself rather than surfacing as "no command".
    for key in entry:
        if key in _KNOWN_SERVER_KEYS:
            continue
        # Underscore-prefixed keys are the established convention for inline
        # documentation and provenance metadata: registry_manager writes
        # "_comment" into every entry it installs, and the first-run seed's
        # examples carry one.  Reporting them means an untouched, working
        # config accuses itself, which trains users to ignore the panel.
        if key.startswith("_"):
            continue
        lowered = key.lower()
        # Case-only mismatch ("COMMAND") and known abbreviations ("cmd") are
        # resolved directly; difflib scores both as unrelated.
        if lowered in _KNOWN_SERVER_KEYS:
            suggestion = lowered
        elif lowered in _KEY_ALIASES:
            suggestion = _KEY_ALIASES[lowered]
        else:
            close = difflib.get_close_matches(
                lowered, _KNOWN_SERVER_KEYS, n=1, cutoff=0.7
            )
            suggestion = close[0] if close else None

        if suggestion:
            findings.append(_finding(
                server_name,
                "unknown_key_typo",
                f'Unknown key "{key}"',
                f'Did you mean "{suggestion}"? "{key}" is ignored, so anything '
                f"it was meant to configure has no effect.",
                line=_find_key_line(raw_text, server_name, key),
                suggestion=suggestion,
            ))
        else:
            findings.append(_finding(
                server_name,
                "unknown_key",
                f'Unknown key "{key}"',
                f'"{key}" is not a recognized MCP server option and is ignored.',
                severity="warning",
                line=_find_key_line(raw_text, server_name, key),
            ))

    # A launch mechanism is mandatory.
    if not any(k in entry for k in _LAUNCH_KEYS):
        typo_hint = ""
        for f in findings:
            if f.get("suggestion") in _LAUNCH_KEYS:
                typo_hint = (
                    f' The unknown key "{f["summary"].split(chr(34))[1]}" above '
                    f'is probably the cause.'
                )
                break
        findings.append(_finding(
            server_name,
            "missing_launch_key",
            "No 'command' or 'url'",
            f"This server cannot be started and is skipped entirely."
            f"{typo_hint}",
            line=_find_key_line(raw_text, server_name, server_name),
        ))

    # command: string expected. An array is normalized by the loader, so that
    # is a warning; anything else is fatal.
    if "command" in entry:
        cmd = entry["command"]
        if isinstance(cmd, list):
            if not cmd:
                findings.append(_finding(
                    server_name, "empty_command_array",
                    "'command' is an empty array",
                    "There is no executable to launch, so this server is skipped.",
                    line=_find_key_line(raw_text, server_name, "command"),
                ))
            else:
                findings.append(_finding(
                    server_name, "command_as_array",
                    "'command' is an array",
                    f'MCP expects a string command with separate "args". Ziya '
                    f'normalized this to command="{cmd[0]}" plus args, but the '
                    f'config is clearer written that way.',
                    severity="warning",
                    line=_find_key_line(raw_text, server_name, "command"),
                ))
        elif not isinstance(cmd, str):
            findings.append(_finding(
                server_name, "command_wrong_type",
                f"'command' must be a string, got {type(cmd).__name__}",
                "This server is skipped.",
                line=_find_key_line(raw_text, server_name, "command"),
            ))

    if "args" in entry and not isinstance(entry["args"], list):
        findings.append(_finding(
            server_name, "args_wrong_type",
            f"'args' must be an array, got {type(entry['args']).__name__}",
            "Ziya coerced it to a single-element list, which is probably not "
            "what was intended.",
            severity="warning",
            line=_find_key_line(raw_text, server_name, "args"),
        ))

    # env values must be strings: a null/number reaches the subprocess env and
    # raises at spawn time, far from the config that caused it.
    env = entry.get("env")
    if env is not None and not isinstance(env, dict):
        findings.append(_finding(
            server_name, "env_wrong_type",
            f"'env' must be an object, got {type(env).__name__}",
            'Use {"KEY": "value"} pairs.',
            line=_find_key_line(raw_text, server_name, "env"),
        ))
    elif isinstance(env, dict):
        for k, v in env.items():
            if isinstance(v, str):
                continue
            findings.append(_finding(
                server_name, "env_value_not_string",
                f"env.{k} is not a string",
                f"Got {json.dumps(v)}. Environment values must be strings — "
                f"quote it, or use an auth token_env reference to read it from "
                f"your shell.",
                line=_find_key_line(raw_text, server_name, k),
            ))

    # Relative script paths are resolved against trusted roots only (F-024), so
    # a relative path in user config almost never resolves.
    args = entry.get("args")
    if isinstance(args, list):
        for a in args:
            if not isinstance(a, str):
                continue
            if a.endswith((".py", ".js")) and not a.startswith(("/", "~", ".")):
                findings.append(_finding(
                    server_name, "relative_script_path",
                    f"Relative script path: {a}",
                    "Script paths are resolved against Ziya's trusted "
                    "installation roots, not your working directory, so this "
                    "will usually not be found. Use an absolute path.",
                    severity="warning",
                    line=_find_key_line(raw_text, server_name, "args"),
                ))

    if "enabled" in entry and "disabled" in entry:
        findings.append(_finding(
            server_name, "enabled_and_disabled",
            "Both 'enabled' and 'disabled' are set",
            f"'enabled' wins ({entry['enabled']!r}); 'disabled' is ignored. "
            f"Remove one to avoid ambiguity.",
            severity="warning",
            line=_find_key_line(raw_text, server_name, "disabled"),
        ))

    return findings


def validate_config(
    config_data: Any,
    raw_text: str = "",
) -> List[Dict[str, Any]]:
    """Validate a parsed mcp_config.json. Never raises."""
    findings: List[Dict[str, Any]] = []
    try:
        if not isinstance(config_data, dict):
            return [_finding(
                "", "config_not_object",
                f"Config root must be a JSON object, got "
                f"{type(config_data).__name__}",
                'Expected {"mcpServers": {...}}.',
            )]

        servers = config_data.get("mcpServers")
        if servers is None:
            # Tolerate a bare mapping of servers (some tools write it that way)
            # rather than calling a usable file broken.
            if any(isinstance(v, dict) for v in config_data.values()):
                return [_finding(
                    "", "missing_mcpservers_key",
                    "No 'mcpServers' key",
                    "Server definitions must be nested under \"mcpServers\". "
                    "Nothing in this file was loaded.",
                )]
            return findings

        if not isinstance(servers, dict):
            return [_finding(
                "", "mcpservers_not_object",
                f"'mcpServers' must be an object, got {type(servers).__name__}",
                'Expected {"mcpServers": {"name": {...}}}.',
            )]

        for name, entry in servers.items():
            findings.extend(validate_server_entry(name, entry, raw_text))
    except Exception:  # noqa: BLE001 - diagnostics must never break startup
        return findings
    return findings
