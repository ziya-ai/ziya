"""
Task Card templating — variable substitution for iteration instructions.

Implements the design's §Propagation contract: what an iteration sees
depends on the Repeat block's propagate_mode.  Substitution happens at
iteration dispatch time, immediately before a Task block's instructions
become the seed of its sandboxed conversation.

Supported placeholders (Mustache-style, unescaped text only):

  {{index}}              0-based iteration index
  {{item}}               current for_each item (string or JSON-encoded)
  {{item.KEY}}           field access when item is a dict
  {{previous.summary}}   prior iteration's artifact.summary  (propagate: last|all)
  {{previous.decisions}} prior iteration's decisions (joined newline)
  {{all.summaries}}      all prior iterations' summaries (propagate: all)
  {{var.KEY}}            read-only run-scoped variable from a State block
  {{sibling("block-id")}}        a named block's artifact summary (run-scoped lookup)
  {{sibling("block-id").summary}}  / .decisions field access on that artifact

Unknown placeholders are left in place verbatim so typos are visible
to the author rather than silently producing empty strings.  Missing
but known placeholders (e.g. {{previous}} on iteration 0) render as
empty string.

This module is deliberately pure — no I/O, no async, no model state.
The executor owns the bindings; this file owns the substitution.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models.task_card import Artifact


# Two placeholder shapes:
#   - dotted name:        {{previous.summary}}, {{var.KEY}}, {{index}}
#   - function call:      {{sibling("block-id")}} with optional .field suffix
# The function form needs its own alternative because a block id can
# contain characters (quotes, parens, hyphens) outside the dotted-name
# class.  The whole inner expression is captured in group 1 and parsed
# by _resolve, which dispatches on whether it looks like sibling(...).
_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*("
    r"sibling\(\s*['\"][^'\"]+['\"]\s*\)(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?"
    r"|[a-zA-Z_][a-zA-Z0-9_.]*"
    r")\s*\}\}"
)

# Parses sibling("id") or sibling('id').field → (block_id, field_or_None)
_SIBLING_CALL_RE = re.compile(
    r"^sibling\(\s*['\"]([^'\"]+)['\"]\s*\)(?:\.([a-zA-Z_][a-zA-Z0-9_]*))?$"
)


@dataclass
class IterationBindings:
    """Per-iteration values made available to templated instructions.

    Built by the Repeat executor just before dispatching each body
    pass.  The executor is responsible for deciding which prior
    artifacts to include based on the block's repeat_propagate mode.
    """
    index: int = 0
    item: Any = None
    previous: Optional[Artifact] = None
    all_summaries: List[str] = field(default_factory=list)
    # Most-recent completed sibling in the enclosing sequence (the block
    # immediately before this one at the same depth).  Distinct from
    # ``previous`` (prior loop iteration).  Resolves {{previous_sibling}}
    # and {{previous_sibling.summary}} / .decisions.  None for the first
    # sibling or outside any sequence.
    previous_sibling: Optional[Artifact] = None
    # Read-only run-scoped variables declared by State blocks, attached
    # at render time by the block executor.  Resolved via {{var.NAME}}.
    variables: Dict[str, Any] = field(default_factory=dict)
    # Run-scoped registry of completed block artifacts, keyed by block id,
    # attached at render time by the block executor.  Resolves the
    # {{sibling("block-id")}} function form — an explicit by-id lookup of
    # ANY block that has completed in this run (unlike previous_sibling,
    # which is only the immediate prior block in the same sequence).
    sibling_artifacts: Dict[str, Artifact] = field(default_factory=dict)


def _render_item(item: Any, path: List[str]) -> str:
    """Resolve {{item}} or {{item.key.subkey}} given an arbitrary value."""
    if not path:
        if item is None:
            return ""
        if isinstance(item, str):
            return item
        # Non-string items render as compact JSON so the model sees them
        # in a parseable form rather than Python repr.
        try:
            return json.dumps(item, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(item)
    # Drill into nested dict/list with each path segment.
    cur: Any = item
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return ""
        else:
            return ""
        if cur is None:
            return ""
    return cur if isinstance(cur, str) else str(cur)


def _resolve(name: str, bindings: IterationBindings) -> Optional[str]:
    """Resolve a dotted placeholder name to its string value.

    Returns None for unknown placeholder heads (the caller leaves them
    in place); returns "" for known heads whose data is not available
    on this iteration (e.g. {{previous.summary}} on iteration 0).
    """
    # Function form {{sibling("block-id")}} / {{sibling('id').summary}}.
    # Checked before the dotted-name split because a quoted block id may
    # itself contain dots.  Resolves a run-scoped by-id lookup of any
    # block that has completed (distinct from previous_sibling, which is
    # only the immediate prior block).  A reference to a block that
    # hasn't completed (or doesn't exist) renders empty — it's an
    # explicit id, so empty is the honest "no result yet", matching
    # {{previous.summary}} on iteration 0.  An unknown field on a found
    # artifact also renders empty (parity with previous/previous_sibling).
    sib = _SIBLING_CALL_RE.match(name)
    if sib:
        block_id, field_name = sib.group(1), sib.group(2)
        art = bindings.sibling_artifacts.get(block_id)
        if art is None:
            return ""
        if not field_name or field_name == "summary":
            return art.summary or ""
        if field_name == "decisions":
            return "\n".join(art.decisions or [])
        return ""
    parts = name.split(".")
    head = parts[0]
    rest = parts[1:]
    if head == "index":
        return str(bindings.index) if not rest else None
    if head == "item":
        return _render_item(bindings.item, rest)
    if head == "previous":
        if bindings.previous is None:
            return ""
        if not rest:
            # {{previous}} alone → render the summary for convenience.
            return bindings.previous.summary or ""
        field_name = rest[0]
        if field_name == "summary":
            return bindings.previous.summary or ""
        if field_name == "decisions":
            return "\n".join(bindings.previous.decisions or [])
        return ""
    if head == "previous_sibling":
        # Prior sibling in the enclosing sequence (distinct from
        # {{previous}}, which is the prior loop iteration).
        if bindings.previous_sibling is None:
            return ""
        if not rest:
            return bindings.previous_sibling.summary or ""
        field_name = rest[0]
        if field_name == "summary":
            return bindings.previous_sibling.summary or ""
        if field_name == "decisions":
            return "\n".join(bindings.previous_sibling.decisions or [])
        return ""
    if head == "all":
        if not rest:
            return ""
        field_name = rest[0]
        if field_name == "summaries":
            return "\n\n".join(bindings.all_summaries or [])
        return ""
    if head == "var":
        # {{var.NAME}} / {{var.NAME.sub}} — read-only run-scoped state.
        # An unknown key is left literal (returns None) so typos surface
        # to the author rather than silently rendering empty — matching
        # the module's unknown-placeholder philosophy.
        if not rest or rest[0] not in bindings.variables:
            return None
        return _render_item(bindings.variables.get(rest[0]), rest[1:])
    return None  # unknown head — caller preserves the literal


def render(template: str, bindings: IterationBindings) -> str:
    """Apply bindings to a template string.

    Unknown placeholders are preserved verbatim; this is deliberate,
    so authoring mistakes surface to the user rather than producing
    silently-empty instructions.
    """
    if not template or "{{" not in template:
        return template or ""

    def _sub(m: re.Match) -> str:
        value = _resolve(m.group(1), bindings)
        return m.group(0) if value is None else value

    return _PLACEHOLDER_RE.sub(_sub, template)


def parse_for_each_source(raw: Optional[str]) -> Optional[List[Any]]:
    """Parse a Repeat block's repeat_for_each_source field.

    Accepts:
      - A JSON array literal: '["a", "b", "c"]' or '[{"id": 1}, ...]'
      - An empty / whitespace-only string → None (falls back to count)
      - None → None

    Returns None on parse failure so the caller can fall back to the
    count-based iteration plan.  Intentionally does not support
    artifact-reference syntax yet — that's a future extension once
    the cross-block reference grammar is defined.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed
