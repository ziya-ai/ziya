"""
Single source of truth for shell command configuration.

IMPORTANT: All commands must be complete, non-interactive operations.
Do not use tools in interactive mode (e.g., 'bc' without expression, 'python' REPL).
Always provide complete command with all arguments needed for one-shot execution.
"""

import copy
import json
import os
from pathlib import Path

# SINGLE SOURCE OF TRUTH for shell command configuration
DEFAULT_SHELL_CONFIG = {
    "enabled": True,
    "allowedCommands": [
        "ls", "cat", "pwd", "grep", "wc", "touch", "find", "date", "od", "df", 
        "netstat", "lsof", "ps", "sed", "awk", "cut", "sort", "which", "hexdump", 
        "xxd", "tail", "head", "echo", "printf", "tr", "uniq", "column", "nl", 
        "tee", "base64", "md5sum", "sha1sum", "sha256sum", "bc", "expr", "seq", 
        "paste", "join", "fold", "expand", "cd", "tree", "less", "xargs", "curl", 
        "ping", "du", "file",
        # Additional text/binary inspection
        "strings", "diff", "stat", "readlink", "realpath", "basename", "dirname",
        # System information
        "uname", "hostname", "whoami", "id", "uptime", "free",
        # Compressed file viewing
        "zcat", "zgrep", "zless",
        # Network diagnostics
        "dig", "host", "nslookup"
    ],
    "gitOperationsEnabled": True,
    "safeGitOperations": [
        "status", "log", "show", "diff", "branch", "remote", "config --get",
        "ls-files", "ls-tree", "blame", "tag", "stash list", "reflog", 
        "rev-parse", "describe", "shortlog", "whatchanged"
    ],
    "timeout": 30
}


def get_default_shell_config():
    """Get the default shell configuration, merged with plugin provider additions."""
    return _get_merged_shell_config()



def _get_merged_shell_config() -> dict:
    """Merge base defaults with any registered ShellConfigProvider additions."""
    import copy
    merged = copy.deepcopy(DEFAULT_SHELL_CONFIG)

    try:
        from app.plugins import get_shell_config_additions
        additions = get_shell_config_additions()
    except Exception:
        # Plugin system not initialized or unavailable
        return merged

    for cmd in additions.get("additional_commands", []):
        if cmd not in merged["allowedCommands"]:
            merged["allowedCommands"].append(cmd)

    for op in additions.get("additional_git_operations", []):
        if op not in merged["safeGitOperations"]:
            merged["safeGitOperations"].append(op)

    return merged


def get_base_shell_config():
    """Get the unmodified base shell config (without plugin additions)."""
    return DEFAULT_SHELL_CONFIG.copy()


# ---------------------------------------------------------------------------
# Persisted config helpers  (reads/writes ~/.ziya/mcp_config.json)
# ---------------------------------------------------------------------------

def _mcp_config_path() -> Path:
    return Path.home() / ".ziya" / "mcp_config.json"


def _read_mcp_config() -> dict:
    path = _mcp_config_path()
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _write_mcp_config(data: dict) -> None:
    path = _mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _ensure_shell_env(cfg: dict) -> dict:
    """Ensure mcpServers.shell.env exists and return the env dict."""
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"].setdefault("shell", {
        "command": "python3",
        "args": ["-u", "app/mcp_servers/shell_server.py"],
        "enabled": True,
        "description": "Shell command execution server",
        "env": {},
    })
    cfg["mcpServers"]["shell"].setdefault("env", {})
    return cfg["mcpServers"]["shell"]["env"]


def get_persisted_allowed_commands() -> list:
    """Return the allowed commands currently persisted in mcp_config.json."""
    cfg = _read_mcp_config()
    raw = cfg.get("mcpServers", {}).get("shell", {}).get("env", {}).get("ALLOW_COMMANDS", "")
    if raw.strip():
        return [c.strip() for c in raw.split(",") if c.strip()]
    # No explicit user override — return plugin-aware defaults
    return _get_merged_shell_config()["allowedCommands"]


def set_persisted_allowed_commands(commands: list) -> None:
    """Write the allowed commands list to mcp_config.json."""
    cfg = _read_mcp_config()
    env = _ensure_shell_env(cfg)
    env["ALLOW_COMMANDS"] = ",".join(commands)
    _write_mcp_config(cfg)


def is_yolo_mode() -> bool:
    cfg = _read_mcp_config()
    val = cfg.get("mcpServers", {}).get("shell", {}).get("env", {}).get("YOLO_MODE", "false")
    return val.lower() in ("true", "1", "yes")


def set_yolo_mode(enabled: bool) -> None:
    cfg = _read_mcp_config()
    env = _ensure_shell_env(cfg)
    env["YOLO_MODE"] = "true" if enabled else "false"
    _write_mcp_config(cfg)


def reset_shell_config() -> None:
    """Reset shell config in mcp_config.json to defaults."""
    cfg = _read_mcp_config()
    env = _ensure_shell_env(cfg)
    env["ALLOW_COMMANDS"] = ",".join(DEFAULT_SHELL_CONFIG["allowedCommands"])
    env["YOLO_MODE"] = "false"
    env["GIT_OPERATIONS_ENABLED"] = "true"
    env["SAFE_GIT_OPERATIONS"] = ",".join(DEFAULT_SHELL_CONFIG["safeGitOperations"])
    env["COMMAND_TIMEOUT"] = str(DEFAULT_SHELL_CONFIG["timeout"])
    _write_mcp_config(cfg)
