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
  {{previous.outputs.NAME}}      a named emit_artifact part from the prior iteration
  {{all.summaries}}      all prior iterations' summaries (propagate: all)
  {{var.KEY}}            read-only run-scoped variable from a State block
  {{sibling("block-id")}}        a named block's artifact summary (run-scoped lookup)
  {{sibling("block-id").summary}}  / .decisions field access on that artifact
  {{sibling("block-id").outputs.NAME}}  a named emit_artifact part from that block

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

from ..models.task_card import Artifact, ArtifactPart


# Two placeholder shapes:
#   - dotted name:        {{previous.summary}}, {{var.KEY}}, {{index}}
#   - function call:      {{sibling("block-id")}} with optional .field suffix
# The function form needs its own alternative because a block id can
# contain characters (quotes, parens, hyphens) outside the dotted-name
# class.  The whole inner expression is captured in group 1 and parsed
# by _resolve, which dispatches on whether it looks like sibling(...).
_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*("
    r"sibling\(\s*['\"][^'\"]+['\"]\s*\)(?:\.[a-zA-Z_][a-zA-Z0-9_.]*)?"
    r"|[a-zA-Z_][a-zA-Z0-9_.]*"
    r")\s*\}\}"
)

# Parses sibling("id") / sibling('id').field / sibling("id").outputs.NAME
# → (block_id, dotted_field_path_or_None).  The field group admits dots so
# the two-segment ``outputs.NAME`` form parses in one pass.
_SIBLING_CALL_RE = re.compile(
    r"^sibling\(\s*['\"]([^'\"]+)['\"]\s*\)(?:\.([a-zA-Z_][a-zA-Z0-9_.]*))?$"
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


def _part_name(part: Any) -> str:
    """The author-facing ``name`` of an artifact part.

    ``ArtifactPart`` declares no ``name`` field — ``emit_artifact``
    supplies one and it rides along via ``model_config extra="allow"``
    (see app/utils/task_artifacts.py::build_part).  Parts also reach us
    as plain dicts, because ``Artifact.outputs`` is only coerced to
    ``ArtifactPart`` at construction: an artifact whose outputs were
    assigned after the fact holds dicts.  Handle both.
    """
    if isinstance(part, dict):
        return str(part.get("name") or "")
    return str(getattr(part, "name", "") or "")


def _part_payload(part: Any) -> Any:
    """The substantive content of a part, preferring structured data.

    ``data`` first because the point of naming a part in a template is
    usually to hand a STRUCTURE to the next block; falls back to
    ``text`` then ``file_uri`` so a reference to any part type resolves
    to something meaningful.
    """
    keys = ("data", "text", "file_uri")
    if isinstance(part, dict):
        for key in keys:
            got = part.get(key)
            if got is not None:
                return got
        return None
    for key in keys:
        got = getattr(part, key, None)
        if got is not None:
            return got
    return None


def find_output_part(
    artifact: Optional[Artifact], name: str,
) -> Optional[Any]:
    """Return the LAST part named ``name`` in ``artifact.outputs``.

    Last-wins rather than first-wins: a task that emits a part, notices
    a problem and re-emits under the same name means the correction, and
    honoring the superseded first copy is the wrong default for a value
    that may drive a fan-out.

    Exported because the block executor resolves a Repeat's ``for_each``
    source through the same lookup and the two must not drift.
    """
    if artifact is None or not name:
        return None
    found = None
    for part in artifact.outputs or []:
        if _part_name(part) == name:
            found = part
    return found


def _drill(value: Any, path: List[str]) -> Any:
    """Walk a dotted path into a payload, returning the VALUE not a string.

    The value-returning counterpart to ``_render_item``, which JSON-encodes
    whatever it resolves.  Both are needed and neither substitutes for the
    other: ``_render_item`` is right when its result IS the rendered
    output, and wrong when the result is about to become one element of a
    list that is itself JSON-encoded.  Reusing it there double-encodes —
    projecting ``meta`` across two parts yields
    ``["{\\"n\\": 1}", "{\\"n\\": 2}"]``, a list of JSON strings rather
    than a list of objects, which a downstream ``for_each`` would iterate
    as opaque text.

    Returns ``None`` for a path that does not resolve, so the caller can
    drop misses rather than emitting nulls into the gathered array: an
    iteration whose part lacks the projected key has nothing to
    contribute, and a null placeholder would be indistinguishable from a
    worker that genuinely reported null.
    """
    cur: Any = value
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def find_all_output_parts(
    artifact: Optional[Artifact], name: str,
) -> List[Any]:
    """Return EVERY part named ``name``, in list order.

    The plural counterpart to ``find_output_part``, and deliberately a
    separate function rather than a flag on it.  Last-wins is correct for
    the singular form's purpose — a task that emits a part, notices a
    problem and re-emits under the same name means the correction — but
    wrong across a loop, where each iteration's part belongs to a
    different worker and none supersedes another.

    That distinction was invisible until fan-outs got wide: a Repeat
    accumulates every iteration's outputs onto its artifact (correctly),
    so a 60-wide audit loop emitting ``audit`` per iteration holds all 60
    parts — while every template reference resolved to iteration 59
    alone and the other 59 were unreachable.  Nothing reported an error;
    the value was simply the last one.

    Order is list order, which for a Repeat is iteration order in both
    the serial and parallel paths (``asyncio.gather`` returns results in
    dispatch order regardless of completion order).  Order matters
    because the point of a gathered list is usually to correlate the Nth
    result with the Nth item of the roster that produced it.

    Returns ``[]`` rather than ``None`` for an absent name so callers can
    iterate unconditionally.
    """
    if artifact is None or not name:
        return []
    return [
        part for part in (artifact.outputs or [])
        if _part_name(part) == name
    ]


def _resolve_artifact_field(
    artifact: Optional[Artifact], path: List[str],
) -> Optional[str]:
    """Resolve a dotted field path against an Artifact.

    Shared by {{previous}}, {{previous_sibling}} and {{sibling("id")}}
    so all three expose an identical field surface — previously each
    open-coded summary/decisions and returned "" for anything else,
    which is how ``outputs`` came to be documented in
    design/task-cards.md but unimplemented.
    """
    if artifact is None:
        return ""
    if not path:
        return artifact.summary or ""
    head = path[0]
    if head == "summary":
        return artifact.summary or ""
    if head == "decisions":
        return "\n".join(artifact.decisions or [])
    if head == "outputs":
        # Bare {{...outputs}} is left literal (None): a heterogeneous
        # part list is not a renderable value, and an author who wrote
        # it meant to name a part.  This is the ONE field-path shape
        # that stays literal; see the return below for why others do not.
        if len(path) < 2:
            return None
        part = find_output_part(artifact, path[1])
        if part is None:
            # Named part absent: empty, matching the "no result yet"
            # convention.  Distinguishing "not emitted" from "emitted
            # empty" is a validator's job, not the renderer's.
            return ""
        return _render_item(_part_payload(part), path[2:])
    if head == "outputs_all":
        # The loop-aware plural: every iteration's part under one name,
        # rendered as a JSON array.  Exists because ``outputs.NAME`` is
        # last-wins and therefore reports one worker's result for a whole
        # fan-out (see find_all_output_parts).
        #
        # Bare {{...outputs_all}} stays literal for the same reason bare
        # ``outputs`` does — name a part or write nothing.
        if len(path) < 2:
            return None
        parts = find_all_output_parts(artifact, path[1])
        # A trailing dotted path projects ONE field across iterations
        # ({{...outputs_all.audit.subsystem}} -> ["alpha","beta"]), which
        # is the shape that lets a gathered fan-out drive a later
        # for_each; with no path, the whole payloads are returned.
        payloads = [_part_payload(p) for p in parts]
        if len(path) > 2:
            payloads = [_drill(p, path[2:]) for p in payloads]
            payloads = [p for p in payloads if p is not None]
        # Always a JSON array, INCLUDING when empty.  "" would make a
        # downstream for_each source unresolvable and fail the block,
        # whereas "[]" is honestly "nothing to iterate over".
        try:
            return json.dumps(payloads, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(payloads)
    # Unknown field on a PRESENT artifact renders empty, not literal.
    # Long-standing documented behavior (test_previous_unknown_field_empty,
    # test_sibling_unknown_field_renders_empty): once the artifact itself
    # resolved, the reference is "known head, unavailable data" — the same
    # case as {{previous.summary}} on iteration 0.
    return ""


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
    if isinstance(cur, str):
        return cur
    # Same JSON-not-repr rule as the no-path branch above.  Without this
    # a resolved list rendered as "['a', 'b']" — Python repr with single
    # quotes, which is not valid JSON and therefore unparseable by
    # parse_for_each_source, silently breaking a structured fan-out.
    try:
        return json.dumps(cur, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(cur)


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
    # {{previous.summary}} on iteration 0.
    sib = _SIBLING_CALL_RE.match(name)
    if sib:
        block_id, field_path = sib.group(1), sib.group(2)
        art = bindings.sibling_artifacts.get(block_id)
        if art is None:
            return ""
        return _resolve_artifact_field(
            art, field_path.split(".") if field_path else [],
        )
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
        # {{previous}} alone → the summary, for convenience.
        return _resolve_artifact_field(bindings.previous, rest)
    if head == "previous_sibling":
        # Prior sibling in the enclosing sequence (distinct from
        # {{previous}}, which is the prior loop iteration).
        if bindings.previous_sibling is None:
            return ""
        return _resolve_artifact_field(bindings.previous_sibling, rest)
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
    # Bare {{NAME}} naming a DECLARED state variable, checked last so the
    # reserved heads above always win and can never be shadowed by a
    # variable that happens to share their name.  This is not a relaxation
    # of the unknown-placeholder rule: an undeclared name still returns
    # None and stays literal, so typos surface exactly as before.  It
    # exists because a declared-but-bare reference is unambiguous intent,
    # and the previous behaviour rendered the braces verbatim into the
    # model's instructions — a silent no-op that read to the agent as a
    # literal, unfillable placeholder rather than as an authoring error.
    if head in bindings.variables:
        return _render_item(bindings.variables.get(head), rest)
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


def parse_for_each_source(
    raw: Optional[str], *, strict: bool = False,
) -> Optional[List[Any]]:
    """Parse a Repeat block's repeat_for_each_source field.

    Accepts:
      - A JSON array literal: '["a", "b", "c"]' or '[{"id": 1}, ...]'
      - Text that CONTAINS a JSON array (e.g. a planner task's summary:
        'Here is the plan: ["a", "b"]') — the first embedded array wins
      - An empty / whitespace-only string → None (falls back to count)
      - None → None

    Returns None on parse failure so the caller can fall back to the
    count-based iteration plan.  Artifact-reference syntax
    ({{sibling("plan-id")}} etc.) is rendered by the block executor
    BEFORE this function is called — see
    app.agents.block_executor._render_for_each_source — so by the time
    the text arrives here it is plain prose or JSON.

    ``strict=True`` disables the prose-scraping fallback and accepts
    only a whole-string JSON array.  Used for the precise source form
    ({{...outputs.NAME}}), where the author named an exact structured
    part and scanning its rendering for an incidental '[' would defeat
    the point of asking precisely.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        return parsed
    if strict:
        return None
    # Fallback: extract the first JSON array embedded in surrounding
    # text.  This is what makes artifact-sourced fan-out practical: a
    # planner task's artifact summary is prose that CONTAINS an array,
    # not a bare array literal.
    return _extract_json_array(raw)


def _extract_json_array(text: str) -> Optional[List[Any]]:
    """Return the first parseable JSON array embedded in ``text``.

    Scans each '[' position and attempts a raw_decode from it; the
    first decode that yields a list wins.  Non-array JSON values and
    unparseable bracket runs are skipped.  Returns None when no
    embedded array exists.
    """
    decoder = json.JSONDecoder()
    idx = text.find("[")
    while idx != -1:
        try:
            parsed, _end = decoder.raw_decode(text, idx)
        except ValueError:
            pass
        else:
            if isinstance(parsed, list):
                return parsed
        idx = text.find("[", idx + 1)
    return None