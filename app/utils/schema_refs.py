"""JSON Schema $ref inlining for providers that reject recursive schemas.

Some OpenAI-compatible endpoints (notably Meta's api.meta.ai) reject any tool
schema containing `$ref`, responding with:

    400 - Recursive JSON schemas are not currently supported

Bedrock and OpenAI proper tolerate `$ref`, so schemas produced by MCP servers
or Pydantic (`model_json_schema()` emits `$defs` + `$ref` for any nested or
self-referential model) pass through unchanged there. On a strict endpoint a
single recursive tool poisons the entire request, because tool definitions are
sent as one array.

The naive fix is to strip `$ref`/`$defs` outright, which is what the Gemini
sanitizer does. That is lossy in a way that matters here: a schema like
`{"blocks": {"items": {"$ref": "#/$defs/Block"}}}` collapses to
`{"items": {}}`, leaving the model no information about what a block is. It
would still call the tool, just blindly.

Instead this module *inlines* referenced subschemas and cuts only where the
reference cycles back on itself, replacing the cycle point with a permissive
open object. Non-recursive `$ref`s are fully expanded and lose nothing; a
recursive one keeps its outermost level of real structure.
"""

from __future__ import annotations

from typing import Any, FrozenSet, Optional

# How many nested expansions of the *same* pointer chain to allow before
# cutting. The cycle check below already terminates true self-reference, so
# this is a secondary guard against pathological but acyclic ref chains
# causing exponential expansion.
_MAX_REF_DEPTH = 4

# Substituted at a cycle cut. Deliberately permissive: the real shape is the
# enclosing schema, which the model has already seen, so constraining this
# further would reject valid nested input.
_TRUNCATED_NODE = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "Nested structure of the same shape as the parent "
        "(recursion truncated for provider compatibility)."
    ),
}


def _resolve_json_pointer(root: Any, pointer: str) -> Optional[Any]:
    """Resolve a local JSON pointer ('#/a/b/0') against root.

    Returns None for external refs, malformed pointers, or missing targets;
    callers substitute an opaque node in that case rather than failing the
    whole request over one bad schema.
    """
    if not isinstance(pointer, str) or not pointer.startswith("#"):
        return None

    node = root
    for raw_part in pointer.lstrip("#").strip("/").split("/"):
        if not raw_part:
            continue
        # RFC 6901 escapes: ~1 is '/', ~0 is '~'. Order matters.
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def inline_json_schema_refs(schema: Any) -> Any:
    """Return an equivalent schema with all local $refs inlined.

    Cycles are cut with a permissive open object. The `$defs` / `definitions`
    containers are removed once their contents have been inlined — leaving them
    would defeat the purpose, since they hold the recursive refs themselves.

    A schema with no $refs is returned structurally unchanged.
    """
    if not isinstance(schema, dict):
        return schema

    root = schema

    def walk(node: Any, seen: FrozenSet[str], depth: int) -> Any:
        if isinstance(node, list):
            return [walk(item, seen, depth) for item in node]
        if not isinstance(node, dict):
            return node

        ref = node.get("$ref")
        if isinstance(ref, str):
            # Revisiting a pointer already on this branch means a cycle.
            if ref in seen or depth >= _MAX_REF_DEPTH:
                return dict(_TRUNCATED_NODE)
            target = _resolve_json_pointer(root, ref)
            if not isinstance(target, (dict, list)):
                return dict(_TRUNCATED_NODE)
            expanded = walk(target, seen | {ref}, depth + 1)
            # Keys alongside $ref (e.g. an overriding description) win over
            # the referenced definition, per JSON Schema 2019-09 semantics.
            siblings = {
                key: walk(value, seen, depth)
                for key, value in node.items()
                if key != "$ref"
            }
            if isinstance(expanded, dict) and siblings:
                merged = dict(expanded)
                merged.update(siblings)
                return merged
            return expanded

        return {
            key: walk(value, seen, depth)
            for key, value in node.items()
            if key not in ("$defs", "definitions")
        }

    result = walk(root, frozenset(), 0)
    if isinstance(result, dict):
        result.pop("$defs", None)
        result.pop("definitions", None)
    return result
