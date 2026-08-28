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

# Object phrases used to make the "you must always/never …" patterns
# polarity-aware (see _INJECTION_PATTERNS below).
#  _CONCEAL — acts an injection suppresses ("never mention/reveal/warn …")
#  _HARMFUL — acts an injection compels ("always bypass/forward/delete …")
#  _COMPLY  — demands for blanket obedience ("always comply/obey/follow my …")
#  _DEFY    — prohibitions on refusing ("never refuse/question/decline …")
_CONCEAL = r"(mention|reveal|disclose|tell|inform|warn|notify|report|log|confirm|ask)"
_HARMFUL = r"(bypass|ignore|disregard|override|circumvent|exfiltrate|forward|send|upload|delete|disable)"
_COMPLY = r"(comply|obey|follow\s+(my|these|the\s+following)|respond\s+in|answer\s+in)"
_DEFY = r"(refuse|question|deviate|disobey|decline|stop|hesitate)"

# Patterns that indicate prompt injection in tool descriptions.
# These are instructions aimed at the LLM rather than the human user.
_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?(prior|previous|above)",
        # "you must always/never <anything>" cannot be flagged on the modal
        # alone: it is the standard voice of DEFENSIVE tool guidance. The
        # bare form matched "You MUST never bypass contingent authorization"
        # — a safety instruction — and silently dropped a legitimate tool.
        # Match on the polarity of the OBJECT instead: suppressing a
        # safety/disclosure act, or compelling a harmful or blanket-obedience
        # one. "never bypass" is safe; "always bypass" is not.
        rf"you\s+must\s+never\s+{_CONCEAL}",
        rf"you\s+must\s+always\s+{_HARMFUL}",
        rf"you\s+must\s+always\s+{_COMPLY}",
        rf"you\s+must\s+never\s+{_DEFY}",
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


# Warnings that are advisory heuristics — they flag a description for human
# review but are NOT evidence of a detected injection, so they must never by
# themselves block a tool or refuse re-authorization. Currently just the
# length heuristic: a legitimately verbose tool description (e.g. one that
# documents many sub-commands) trips the >4000-char check without carrying any
# actual injected instruction. Everything else the scanner emits (a concrete
# injection-pattern match, mixed-script/homoglyph obfuscation) is BLOCKING.
_ADVISORY_SIGNATURES: Tuple[str, ...] = (
    "is unusually long",
)


def is_advisory_warning(warning: str) -> bool:
    """True if *warning* is a soft, review-only heuristic (never blocking).

    Used to distinguish "flag for review" noise from a genuine injection
    signal so the length heuristic can no longer, on its own, quarantine a
    tool or make re-authorization impossible.
    """
    return any(sig in warning for sig in _ADVISORY_SIGNATURES)


def classify_warnings(warnings: List[str]) -> Tuple[List[str], List[str]]:
    """Split scanner warnings into ``(blocking, advisory)``.

    ``blocking`` warnings gate a tool (drop at connect time, refuse
    re-authorization unless forced); ``advisory`` warnings are logged for
    review but let the tool through.
    """
    blocking: List[str] = []
    advisory: List[str] = []
    for w in warnings:
        (advisory if is_advisory_warning(w) else blocking).append(w)
    return blocking, advisory


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

    A tool whose name is absent/None/non-str is SKIPPED rather than
    fingerprinted under a synthetic "" name: a server cannot dispatch such a
    tool, and folding it in would let two structurally different tool sets
    share a fingerprint (ASR VAL-04, stricter form of PenPal #118).
    """
    usable = []
    for t in tools:
        name = t.get("name")
        if not isinstance(name, str) or not name:
            logger.warning(
                f"fingerprint_tools: skipping tool with invalid name {name!r}"
            )
            continue
        usable.append(t)
    canonical = json.dumps(
        sorted(
            [{"name": t.get("name"), "description": t.get("description"),
              "inputSchema": t.get("inputSchema")} for t in usable],
            key=lambda x: x["name"],
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
