"""
Dynamic prompt generation for FileIO tools.

Reads the current project's effective write policy and generates
instructions telling the model which paths it can read/write directly
using file_read / file_write / file_list, and which paths still require
the git diff mechanism.

Called per-request from precision_prompt_system.py so the instructions
reflect the actual project configuration (not a cached template).
"""

import os
from typing import Optional

from app.utils.logging_utils import logger


def get_fileio_prompt_section() -> str:
    """
    Build the fileio instructions block based on the current project's
    effective write policy.

    Returns an empty string if the fileio category is disabled, or a
    multi-line instruction block otherwise.
    """
    # Check if fileio builtin tools are enabled
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("fileio"):
        return ""

    # Read the effective write policy
    try:
        from app.config.write_policy import get_write_policy_manager
        pm = get_write_policy_manager()
        policy = pm.get_effective_policy()
    except Exception as e:
        logger.debug(f"Could not read write policy for fileio prompt: {e}")
        return ""

    safe_paths = policy.get("safe_write_paths", [])
    write_patterns = policy.get("allowed_write_patterns", [])

    # Build human-readable list of writable targets
    writable_targets = []
    for p in safe_paths:
        if p in ("/dev/null",):
            continue  # not useful to mention
        writable_targets.append(p)

    # Separate the "always safe" defaults from project-configured patterns
    # so the model understands the distinction.
    project_patterns = [p for p in write_patterns if p]  # filter empties

    # If there are no writable targets at all beyond /dev/null, still tell
    # the model about .ziya/ (it's always in the defaults).
    if not writable_targets and not project_patterns:
        return ""

    lines = [
        "",
        "IMPORTANT: When making changes:",
    ]

    if project_patterns:
        pattern_list = ", ".join(f"`{p}`" for p in project_patterns)
        lines.append(
            f"Files matching these project-configured patterns can be "
            f"written DIRECTLY using the `file_write` tool: {pattern_list}. "
            f"For these files, do NOT use the git diff format — read the "
            f"file with `file_read`, apply your changes, and write it back "
            f"with `file_write` (or use the `patch` parameter for targeted "
            f"edits). This is more efficient and less error-prone than diffs "
            f"for design documents and state-tracking files."
        )

    safe_list = ", ".join(f"`{p}`" for p in writable_targets)
    lines.append(
        f"Paths under {safe_list} are always writable. Use `.ziya/` for "
        f"agentic state tracking: progress notes, scratch data, session "
        f"logs, or any working files you need across tool iterations."
    )

    lines.append(
        "All other project source files require the standard git diff "
        "format for modifications."
    )

    # Remind the model about the tools available
    lines.append(
        "Available file tools: `file_read` (read any file), "
        "`file_write` (write/patch to approved paths), "
        "`file_list` (list directory contents)."
    )

    return "\n".join(lines)
