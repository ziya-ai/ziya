"""
First-run seed for ``~/.ziya/mcp_config.json`` and its example sibling.

Why this exists
---------------
``MCPManager._find_config_file`` searches three locations and returns ``None``
when none of them holds a file.  Built-in servers still load, so MCP *works* —
but there is no artifact anywhere on disk telling a new user where server
configuration goes or what an entry looks like.  The MCP status modal degrades
to "No MCP configuration file found in search path", which names the paths but
not the schema.

Two files, not one
------------------
The live config is seeded **minimal**: a one-line ``_help`` pointer and an empty
``mcpServers``.  All shape documentation lives in a sibling
``mcp_config.example.json``.

The earlier single-file design put a 24-line ``_README`` string array and an
``_example_mcpServers`` block inside the live config, which is what a new user
opens first — and a wall of quoted strings is exactly the wrong first
impression.  It could not be fixed by using comments: the loader is plain
``json.load``, and ``//``, ``/* */`` and ``#`` are all rejected identically
(verified, not assumed), so any comment syntax makes the file unparseable and
the manager reports ``config_error`` — a broken config out of the box.

Splitting the files removes the constraint.  The live config stays short enough
to read at a glance, and the example file is free to be as long as it needs to
be because nobody has to read past it to find ``mcpServers``.

The example file is never parsed by Ziya: ``_find_config_file`` matches the
literal name ``mcp_config.json`` and nothing in the codebase globs
``~/.ziya/*.json``, so a differently-named sibling cannot be picked up as
config.  It is still written as strict, comment-free JSON so an editor validates
it and — the load-bearing reason — so that copying an entry out of it into the
live config cannot carry a comment along and break the file being copied into.

Round-trip safety
-----------------
Every writer of the live config (``config/shell_config.py``,
``mcp/registry_manager.py``, the shell-config persist branch in
``routes/mcp_routes.py``) does a read-modify-write of the whole document, so
``_help`` survives a UI-driven save rather than being stripped.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from app.utils.logging_utils import logger
from app.utils.paths import get_ziya_home

MCP_CONFIG_FILENAME = "mcp_config.json"
MCP_EXAMPLE_FILENAME = "mcp_config.example.json"


def mcp_config_file() -> Path:
    """Absolute path to the user-level MCP config (may not exist)."""
    return get_ziya_home() / MCP_CONFIG_FILENAME


def mcp_example_file() -> Path:
    """Absolute path to the example/reference file (may not exist)."""
    return get_ziya_home() / MCP_EXAMPLE_FILENAME


# The live config.  Deliberately two keys: the one the loader reads, and one
# line saying where to look for more.  A single string rather than an array of
# lines because an array is what made the previous seed unreadable.
CONFIG_SEED_DOCUMENT = {
    "_help": (
        f"Declare MCP servers under 'mcpServers' below. See "
        f"{MCP_EXAMPLE_FILENAME} in this directory for entry shapes to copy. "
        f"JSON has no comments: a // or # line here makes this file "
        f"unparseable and Ziya will report a config syntax error."
    ),
    "mcpServers": {},
}


# The example file.  Entries live under "mcpServers" so that copying a block out
# of here lands in the live config at the right nesting depth — the previous
# design's "_example_mcpServers" wrapper meant the copied text was one level
# deeper than where it had to go.  Safe because this filename is never read as
# config.
EXAMPLE_DOCUMENT = {
    "_help": (
        "Reference entries for mcp_config.json. Ziya never loads this file. "
        "Copy an entry from 'mcpServers' below into mcp_config.json, edit it, "
        "then set \"enabled\": true (the examples ship disabled so that "
        "copying one wholesale cannot produce a server that fails to "
        "connect). Restart Ziya, or use Reload Config in the MCP status "
        "panel, to pick up changes. Full documentation: "
        "Docs/UserConfigurationFiles.md."
    ),
    "_notes": {
        "command": (
            "A string, with arguments in the separate 'args' array. If the "
            "launcher is installed through nvm/asdf/pyenv, give an absolute "
            "path — Ziya's PATH may not include the shims."
        ),
        "url": "Use instead of 'command' for a remote server.",
        "auth": (
            "Prefer 'token_env', which names an environment variable. An "
            "inline 'token' sits in plaintext on disk and is not covered by "
            "at-rest encryption."
        ),
        "script_paths": (
            "Use absolute paths. A relative script path is resolved against "
            "Ziya's trusted installation roots, not your working directory."
        ),
        "search_order": (
            "First file found wins: ./mcp_config.json, then "
            "<project root>/mcp_config.json, then ~/.ziya/mcp_config.json."
        ),
    },
    "mcpServers": {
        "example-stdio-server": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
            "env": {"EXAMPLE_SETTING": "value"},
            "enabled": False,
        },
        "example-remote-server": {
            "url": "https://mcp.example.com/sse",
            "auth": {"type": "bearer", "token_env": "EXAMPLE_MCP_TOKEN"},
            "enabled": False,
        },
    },
}


# A config carrying exactly these keys and no servers is provably the earlier
# verbose seed, untouched: Ziya wrote every byte of it and the user has added
# nothing.  Replacing such a file with the minimal form therefore discards only
# documentation Ziya authored itself.  Identified by key shape rather than by a
# byte-exact copy of the old document, so the check cannot silently stop
# matching because of a whitespace difference in a transcribed literal.
_LEGACY_SEED_KEYS = frozenset({"_README", "_example_mcpServers", "mcpServers"})


def _is_pristine_legacy_seed(path: Path) -> bool:
    """True when ``path`` is the earlier verbose seed with no user content.

    Requires ``mcpServers`` to be empty, so a file the user has actually
    configured is never a migration candidate no matter what else it contains.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return set(data.keys()) == _LEGACY_SEED_KEYS and data.get("mcpServers") == {}


def _write_json(path: Path, document: dict) -> None:
    """Write ``document`` atomically, owner-readable only.

    Temp-file-and-rename so an interrupted write cannot leave a truncated
    document that the manager would then report as a syntax error.  Mode 0o600
    because users do put inline bearer tokens in the live config despite the
    advice not to; ``~/.ziya`` is already 0o700 but the file should not rely on
    the directory for that.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_mcp_config_seed() -> Optional[Path]:
    """Create the starter MCP config and its example sibling if absent.

    Returns the config path when this call created or migrated it, otherwise
    ``None`` — including when it already existed and when creation failed.
    Never raises: an unwritable home directory must not prevent startup, and the
    absence of these files is the status quo that MCP already tolerates.

    A user-modified config is never touched, so a hand-edited file cannot be
    clobbered by an upgrade.  The one exception is a *pristine* copy of the
    earlier verbose seed (see ``_is_pristine_legacy_seed``), which is replaced
    with the minimal form — otherwise every existing installation would keep the
    unreadable file forever, since no-clobber would protect content the user
    never wrote.

    The example file is (re)created whenever it is missing, independently of the
    config, so an installation that predates it gains one.  Only the user-level
    directory is seeded: a project-local ``mcp_config.json`` shadows this one and
    writing into a project directory would be an unexpected side effect of
    starting Ziya.
    """
    try:
        config_path = mcp_config_file()
        example_path = mcp_example_file()
    except OSError as e:
        logger.debug(f"Could not resolve Ziya home for MCP config seed: {e}")
        return None

    created: Optional[Path] = None

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # The example file first: it is what the config's _help points at, so a
        # config written without it would reference something absent.
        if not example_path.exists():
            _write_json(example_path, EXAMPLE_DOCUMENT)
            logger.debug(f"Wrote MCP example configuration to {example_path}")

        if not config_path.exists():
            _write_json(config_path, CONFIG_SEED_DOCUMENT)
            created = config_path
            logger.info(
                f"Created starter MCP configuration at {config_path} — add "
                f"servers under 'mcpServers' (see {MCP_EXAMPLE_FILENAME} "
                f"alongside it for entry shapes)."
            )
        elif _is_pristine_legacy_seed(config_path):
            _write_json(config_path, CONFIG_SEED_DOCUMENT)
            created = config_path
            logger.info(
                f"Replaced the unmodified starter MCP configuration at "
                f"{config_path} with the current minimal form; examples now "
                f"live in {MCP_EXAMPLE_FILENAME}. No configured servers were "
                f"present, so nothing was lost."
            )
    except OSError as e:
        logger.debug(f"Skipped MCP config seed at {config_path}: {e}")
        return None

    return created
