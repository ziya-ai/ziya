"""
MCP Tool Guard — mitigations for tool poisoning, shadowing, and rug-pull attacks.

Addresses threats identified by the Agent Tool Checker (ATC) framework:
- Tool poisoning: scans descriptions for hidden prompt-injection instructions
- Cross-origin escalation: detects tool name collisions (shadowing)
- Rug-pull detection: fingerprints tool definitions to detect post-install changes

Reference: "Mitigating Tool Squatting and Rug Pull Attacks in MCP"
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.utils.logging_utils import logger

# Patterns that indicate prompt injection in tool descriptions.
# These are instructions aimed at the LLM rather than the human user.
_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?(prior|previous|above)",
        r"you\s+must\s+(always|never)\s+",
        r"system\s*:\s*",
        r"<\s*/?\s*system\s*>",
        r"override\s+(security|safety|permissions?|policy)",
        r"do\s+not\s+(mention|reveal|disclose|tell)",
        r"pretend\s+(you\s+are|to\s+be)",
        r"act\s+as\s+(if|though)\s+you",
        r"bypass\s+(any|all|the)\s+(restrictions?|limits?|safeguards?|filters?)",
        r"execute\s+(this|the\s+following)\s+(silently|quietly|without)",
        r"hidden\s+instruction",
        r"<!-{2,}.*?-{2,}>",  # HTML comments hiding content
    ]
]


def scan_tool_description(tool_name: str, description: str) -> List[str]:
    """Scan a tool description for prompt-injection indicators.

    Returns a list of warning strings (empty if clean).
    """
    warnings: List[str] = []
    if not description:
        return warnings
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(description)
        if match:
            warnings.append(
                f"Tool '{tool_name}': description matches injection pattern "
                f"'{pattern.pattern}' near: ...{match.group()[:60]}..."
            )
    # Flag excessively long descriptions (may hide instructions in noise)
    if len(description) > 4000:
        warnings.append(
            f"Tool '{tool_name}': description is unusually long "
            f"({len(description)} chars) — review for hidden instructions"
        )
    return warnings


def detect_shadowing(
    builtin_tool_names: Set[str],
    external_tool_name: str,
    external_server: str,
) -> Optional[str]:
    """Detect if an external tool shadows a built-in tool name.

    Returns a warning string if shadowing is detected, else None.
    """
    if external_tool_name in builtin_tool_names:
        return (
            f"Tool '{external_tool_name}' from server '{external_server}' "
            f"shadows a built-in tool — the built-in version will be used"
        )
    return None


def fingerprint_tools(tools: List[Dict[str, Any]]) -> str:
    """Generate a fingerprint of a server's tool definitions.

    Use at connect time to establish a baseline, then re-check periodically
    to detect rug-pull changes (tool definitions mutating after install).
    """
    canonical = json.dumps(
        sorted(
            [{"name": t.get("name"), "description": t.get("description"),
              "inputSchema": t.get("inputSchema")} for t in tools],
            key=lambda x: x.get("name", ""),
        ),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_fingerprint_change(
    server_name: str, old_fp: str, new_fp: str
) -> Optional[str]:
    """Compare tool fingerprints and return a warning if they differ."""
    if old_fp != new_fp:
        return (
            f"Server '{server_name}' tool definitions changed since last connect "
            f"(fingerprint {old_fp[:12]}… → {new_fp[:12]}…) — possible rug-pull"
        )
    return None
