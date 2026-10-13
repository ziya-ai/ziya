"""
Lenient SKILL.md frontmatter parser (agentskills.io).

The integration guide ("Step 2: Parse SKILL.md files") asks clients to
accept frontmatter that other harnesses' YAML parsers accept, and to warn
rather than reject.  Real skills in the wild use three value shapes the
previous flat ``key: value`` parser could not read:

  * plain scalars continued on indented lines
        description: Query fleet telemetry -- live or historical --
          for any entity in the system ...
  * literal block scalars      description: |
  * folded block scalars       description: >-

With the old parser the first came back as only its first line, and any
continuation line containing a colon ("  Sources: the Metrics API ...") was
mistaken for a new top-level key.  The block forms came back as the bare
indicator ("|" or ">-").

Output shape is unchanged so callers need no edits: every value is a
string; a one-level map (``metadata:``) is flattened to ``"k=v,k=v"``.
No PyYAML: it rejects the unquoted-colon values skills commonly contain,
and its typed output (lists, dates, booleans) would change the shape.
"""
from __future__ import annotations

import re
from typing import Dict, List

# Top-level key.  Lenient about the space after the colon, as before.
_KEY_RE = re.compile(r"^(\w[\w-]*):\s*(.*?)\s*$")
# Block-scalar header: style, optional chomping and indentation indicators
# in either order, optional trailing comment.
_BLOCK_RE = re.compile(r"^([|>])([+-]?)(\d?)([+-]?)\s*(#.*)?$")


def _unquote(value: str) -> str:
    # Same leniency as the original parser (also trims unbalanced quotes).
    return value.strip('"').strip("'")


def _dedent(lines: List[str]) -> List[str]:
    widths = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    cut = min(widths) if widths else 0
    return [l[cut:] if l.strip() else "" for l in lines]


def _fold(lines: List[str]) -> str:
    """YAML folding: line breaks become spaces, a blank line becomes "\\n"."""
    out = ""
    pending_break = False
    for line in lines:
        text = line.strip()
        if not text:
            out += "\n"
            pending_break = True
            continue
        if out and not pending_break:
            out += " "
        out += text
        pending_break = False
    return out


def _block_scalar(header: re.Match, body: List[str]) -> str:
    style, chomp = header.group(1), header.group(2) or header.group(4)
    lines = _dedent(body)
    text = "\n".join(lines) if style == "|" else _fold(lines)
    return text if chomp == "+" else text.rstrip("\n")


def _is_map(body: List[str]) -> bool:
    content = [l.strip() for l in body if l.strip() and not l.strip().startswith("#")]
    return bool(content) and all(_KEY_RE.match(l) for l in content)


def parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse SKILL.md frontmatter into a flat ``{key: str}`` dict."""
    result: Dict[str, str] = {}
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        i += 1
        # Only unindented lines start a key; stray indented lines are skipped.
        if not line.strip() or line[:1].isspace() or line.startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)

        # Everything indented (or blank) below the key belongs to it.
        body: List[str] = []
        while i < n and (not lines[i].strip() or lines[i][:1].isspace()):
            body.append(lines[i])
            i += 1
        while body and not body[-1].strip():
            body.pop()

        block = _BLOCK_RE.match(value)
        if block:
            result[key] = _block_scalar(block, body)
        elif not value:
            if _is_map(body):
                pairs = []
                for b in body:
                    s = b.strip()
                    if not s or s.startswith("#"):
                        continue
                    km = _KEY_RE.match(s)
                    pairs.append(f"{km.group(1)}={_unquote(km.group(2))}")
                result[key] = ",".join(pairs)
            elif body:
                result[key] = _unquote(_fold(body))
            # A bare key with nothing under it is omitted, as before.
        else:
            result[key] = _unquote(_fold([value] + body))
    return result
