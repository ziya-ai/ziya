"""Rewrites the diagram preprocessing applied, recovered from the console log.

The frontend fix-up passes (Vega-Lite preprocessing, mermaid repair, drawio
sanitizers, ...) describe each change they make to a spec in a
``console.log`` line prefixed ``🔧 TAG:`` (e.g. ``🔧 LOG-SCALE-FIX: Converted
x log scale to symlog (has zero values)``).  The headless renderer behind
``render_diagram`` already captures every console line, but only warnings and
errors were reported, so after a render the model could only guess why the
image differs from its spec.  ``extract_rewrites`` turns the captured log
into that list.  Design: design/harness-small-items.md, item 3.

This changes no rendering behaviour; it only makes existing rewrites visible.
"""

from __future__ import annotations

import re
from typing import Iterable, List

REWRITE_MARK = "🔧"
MAX_REWRITES = 25
MAX_CHARS = 240

# Console types that carry rewrite notes.  Warnings and errors are reported
# separately by the renderer, so they are not repeated here.
_LOG_TYPES = ("[log] ", "[info] ", "[debug] ")

# Tags that trace execution rather than describe a change to the spec.
_NOISE_TAGS = re.compile(r"(^|-)DEBUG$|^ENHANCER-INPUT$|^VEGA-EMBED$")
# Progress lines inside otherwise useful tags.
_NOISE_BODY = re.compile(
    r"^(Starting\b|Received\b|About to\b|Building\b)|analyzing\.*$", re.IGNORECASE)
# Playwright renders non-string console args as handles; they carry nothing.
_HANDLE = re.compile(r"\s*JSHandle@\w+")
_TAGGED = re.compile(r"^([A-Z0-9][A-Z0-9_-]*):\s*(.*)$", re.DOTALL)


def extract_rewrites(console_log: Iterable[str], limit: int = MAX_REWRITES) -> List[str]:
    """Rewrite notes from raw ``[type] text`` console entries, in order.

    Duplicates (preprocessing can run more than once per render) are
    dropped; each note is trimmed to ``MAX_CHARS``.
    """
    out: List[str] = []
    seen = set()
    for entry in console_log or ():
        if not isinstance(entry, str) or not entry.startswith(_LOG_TYPES):
            continue
        text = entry.split("] ", 1)[1].strip()
        if not text.startswith(REWRITE_MARK):
            continue
        text = _HANDLE.sub("", text[len(REWRITE_MARK):]).strip()
        m = _TAGGED.match(text)
        if m:
            tag, body = m.group(1), m.group(2).strip()
            if _NOISE_TAGS.search(tag) or not body or _NOISE_BODY.search(body):
                continue
            text = f"{tag}: {body}"
        text = " ".join(text.split())
        if len(text) > MAX_CHARS:
            text = text[: MAX_CHARS - 1] + "…"
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    if len(out) > limit:
        more = len(out) - limit
        out = out[:limit] + [f"(+{more} more)"]
    return out


def format_rewrites(rewrites: List[str]) -> str:
    """Text block for the render_diagram result, or "" when there are none."""
    if not rewrites:
        return ""
    lines = ["", "Preprocessing changed the spec before rendering "
                 "(the image reflects these rewrites, not the spec as written):"]
    lines += [f"  - {r}" for r in rewrites]
    return "\n".join(lines)
