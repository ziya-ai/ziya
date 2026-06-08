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
import unicodedata
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


# Common Cyrillic / Greek homoglyphs → Latin lookalikes. NFKC does not fold
# cross-script confusables, so the high-frequency ones are mapped explicitly.
_CONFUSABLE_MAP = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "У": "Y",
    "к": "k", "К": "K", "М": "M", "т": "T", "Т": "T", "Н": "H", "В": "B",
    "і": "i", "ѕ": "s", "ј": "j",
    "ο": "o", "Ο": "O", "α": "a", "ε": "e", "ρ": "p", "υ": "y", "ι": "i",
    "κ": "k", "ν": "v", "τ": "t",
})


def _fold_confusables(text: str) -> str:
    """NFKC-normalize and fold common non-Latin homoglyphs to Latin.

    NFKC does not merge cross-script lookalikes (e.g. Cyrillic 'о' vs Latin
    'o'), so the high-frequency confusables are mapped explicitly. Lets the
    injection patterns below see through homoglyph-substitution evasion.
    """
    return unicodedata.normalize("NFKC", text).translate(_CONFUSABLE_MAP)


def _has_mixed_script_token(text: str) -> bool:
    """True if any whitespace-delimited token mixes ASCII letters with
    non-ASCII letters — the signature of homoglyph substitution (e.g.
    'ignоre' with a Cyrillic 'о'). Pure non-Latin tokens (legitimately
    localized text) do not trip this, keeping false positives low.
    """
    for token in text.split():
        has_ascii = any("a" <= c.lower() <= "z" for c in token)
        has_non_ascii_alpha = any(ord(c) > 0x7F and c.isalpha() for c in token)
        if has_ascii and has_non_ascii_alpha:
            return True
    return False


def scan_tool_description(tool_name: str, description: str) -> List[str]:
    """Scan a tool description for prompt-injection indicators.

    Returns a list of warning strings (empty if clean).
    """
    warnings: List[str] = []
    if not description:
        return warnings

    # Scan the raw description AND a homoglyph-folded copy. Folding can only
    # ADD detections (it never hides a match visible in the original), so
    # Unicode-lookalike evasion is caught without weakening raw matching.
    scan_targets = [description]
    folded = _fold_confusables(description)
    if folded != description:
        scan_targets.append(folded)

    seen: Set[str] = set()
    for target in scan_targets:
        for pattern in _INJECTION_PATTERNS:
            if pattern.pattern in seen:
                continue
            match = pattern.search(target)
            if match:
                seen.add(pattern.pattern)
                warnings.append(
                    f"Tool '{tool_name}': description matches injection pattern "
                    f"'{pattern.pattern}' near: ...{match.group()[:60]}..."
                )
    # Mixed-script tokens are a strong homoglyph-obfuscation signal even when
    # no pattern matched (e.g. partial substitution that didn't fold cleanly).
    if _has_mixed_script_token(description):
        warnings.append(
            f"Tool '{tool_name}': description contains mixed-script tokens "
            f"(possible homoglyph obfuscation) — review carefully"
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
