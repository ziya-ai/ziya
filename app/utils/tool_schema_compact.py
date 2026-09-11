"""Lossless compaction of tool input schemas before they reach the model.

Every tool definition is re-sent on every request, so the tool block is a
fixed per-turn tax.  Pydantic's ``model_json_schema()`` — and many external
MCP servers — pad that block with keys that carry no information the model
can act on:

* ``title`` on the root and on every property (a re-spelling of the name);
* ``anyOf: [{type: X}, {type: "null"}]`` plus ``default: null`` for every
  optional field, where "not in ``required``" already says the same thing;
* ``$schema`` draft URIs;
* a root ``description`` that is the Pydantic model's docstring
  ("Input schema for file_read.") duplicating the tool description.

Measured on the 42 default builtins this is ~27% of schema bytes, roughly
2k tokens per request, with no property renamed, no ``required`` entry
moved and no ``enum`` altered.  Small models with 8k contexts feel this
directly; frontier models get the same bytes back in their budget.

The compacted dict is what ``metadata["input_schema"]`` carries, and that
key is also the schema the streaming executor validates incoming calls
against (``validate_tool_args_against_schema``).  Everything removed here
is therefore chosen so that validation is unaffected: the validator reads
only ``required`` and per-property ``enum``, and its type coercion
(``_schema_expected_type``) already treats the collapsed ``type: X`` and
the original ``anyOf`` identically.

Rules the compactor deliberately does NOT apply:

* ``anyOf`` is only collapsed on properties that are *not* required.  A
  required-but-nullable field keeps its union so ``null`` stays expressible.
* Non-null ``default`` values are kept; ``default: 10`` is information.
* ``additionalProperties`` is kept; strict grammar-constrained servers
  (llama.cpp ``--jinja``) honour it.
* ``$ref``/``$defs`` are left alone — inlining is a per-provider concern
  handled by ``app.utils.schema_refs``.

Property *names* are never touched.  A tool may legitimately have a
parameter called ``title`` or ``default``; the walker distinguishes schema
keywords from the user-named children under ``properties`` / ``$defs`` /
``patternProperties`` and only strips keywords on schema nodes.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet

# Containers whose *keys* are user-chosen names and whose values are schema
# nodes.  Keys here must never be treated as schema keywords.
_NAMED_CHILD_MAPS: FrozenSet[str] = frozenset(
    {"properties", "$defs", "definitions", "patternProperties"}
)
# Keywords whose value is a single schema node.
_SCHEMA_CHILDREN: FrozenSet[str] = frozenset(
    {"items", "additionalProperties", "not", "if", "then", "else",
     "propertyNames", "contains"}
)
# Keywords whose value is a list of schema nodes.
_SCHEMA_CHILD_LISTS: FrozenSet[str] = frozenset(
    {"anyOf", "oneOf", "allOf", "prefixItems"}
)


def _is_null_member(node: Any) -> bool:
    """True for the ``{"type": "null"}`` arm Pydantic adds to Optional fields.

    Tolerates a stray ``title`` alongside; anything else (a description, an
    enum) means the null arm carries meaning and must be preserved.
    """
    return (
        isinstance(node, dict)
        and node.get("type") == "null"
        and set(node) <= {"type", "title"}
    )


def _compact_node(node: Any, *, is_root: bool, strip_root_description: bool) -> Any:
    """Compact one schema node, recursing into schema-valued keywords."""
    if isinstance(node, list):
        return [_compact_node(n, is_root=False, strip_root_description=False) for n in node]
    if not isinstance(node, dict):
        return node

    out: Dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key == "$schema":
            continue
        if key == "default" and value is None:
            continue
        if is_root and strip_root_description and key == "description":
            continue

        if key in _NAMED_CHILD_MAPS and isinstance(value, dict):
            required = frozenset(node.get("required") or ()) if key == "properties" else frozenset()
            out[key] = {
                name: _compact_property(
                    sub, optional=(key == "properties" and name not in required)
                )
                for name, sub in value.items()
            }
            continue
        if key in _SCHEMA_CHILDREN and isinstance(value, dict):
            out[key] = _compact_node(value, is_root=False, strip_root_description=False)
            continue
        if key in _SCHEMA_CHILD_LISTS and isinstance(value, list):
            out[key] = [
                _compact_node(m, is_root=False, strip_root_description=False) for m in value
            ]
            continue
        out[key] = value
    return out


def _compact_property(prop: Any, *, optional: bool) -> Any:
    """Compact a property node and, if optional, collapse ``anyOf[X, null]``.

    Sibling keys on the property (``description``, ``default``) win over the
    same keys on the surviving arm, matching JSON Schema's treatment of
    keywords adjacent to a combinator.
    """
    if not isinstance(prop, dict):
        return prop
    compacted = _compact_node(prop, is_root=False, strip_root_description=False)
    any_of = compacted.get("anyOf")
    if not optional or not isinstance(any_of, list):
        return compacted
    members = [m for m in any_of if not _is_null_member(m)]
    if len(members) != 1 or len(members) == len(any_of) or not isinstance(members[0], dict):
        return compacted
    merged = dict(members[0])
    for key, value in compacted.items():
        if key != "anyOf":
            merged[key] = value
    return merged


def compact_tool_schema(schema: Any, *, strip_root_description: bool = False) -> Any:
    """Return a compacted copy of a JSON-Schema tool input schema.

    Args:
        schema: The schema dict (typically ``Model.model_json_schema()`` or
            an MCP server's ``inputSchema``).  Non-dict input is returned
            unchanged so callers can pass whatever they were handed.
        strip_root_description: Drop the root-level ``description``.  Set
            for Pydantic-derived schemas, where it is the model docstring
            and duplicates the tool's own description.  Leave unset for
            external MCP schemas, whose root description (if any) is the
            server's and may carry usage guidance.

    The input is not mutated.
    """
    if not isinstance(schema, dict):
        return schema
    return _compact_node(schema, is_root=True, strip_root_description=strip_root_description)


__all__ = ["compact_tool_schema"]
