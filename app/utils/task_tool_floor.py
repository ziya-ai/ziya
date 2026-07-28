"""
Task-scope tool allowlist: the always-available floor and the matcher.

A Task block's ``scope.tools`` is a strict allowlist — anything not
named is filtered out of the run.  Two facts make a bare
``name in scope.tools`` test the wrong primitive:

1. **Naming asymmetry.**  ``create_secure_mcp_tools`` prefixes tools
   coming from external MCP servers with ``mcp_`` but leaves builtin
   ("[DIRECT]") tools unprefixed.  A card author writes what they see
   in the tool catalog — usually the unprefixed name — so a scope
   listing ``run_shell_command`` must still match the registered
   ``mcp_run_shell_command``.  Matching on the normalized (prefix-
   stripped) name in both directions removes a whole class of
   "scope silently amputated the tool I asked for" failures.

2. **Executor-owned plumbing.**  A few tools are not the task's
   business to grant — the harness itself depends on them.
   ``emit_artifact`` is how any output survives the task sandbox at
   all (``Artifact.outputs`` is drained from its collector), and
   ``render_diagram`` is the only way a task can see what it
   produced.  A scope that omits them doesn't restrict the task, it
   breaks it: the executor still injects the "declare your artifacts"
   instruction, so the model is told to emit and given nothing to
   emit with.  These are unioned in unconditionally.

Both the enforcement site (``app/agents/task_executor.py``) and the
prompt that *describes* the allowlist to the model
(``app/utils/session_context_prompt.py``) resolve through here, so
the two can never disagree — the mismatch that produced models
reporting "no render_diagram tooling was available in this task
scope" while holding a working ``render_diagram`` in their tool
payload.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set

# Tools always exposed to a Task block regardless of its scope.
#
# Deliberately tiny and read-only/inert with respect to the project:
# none of these can write a file, run a command, or reach the network.
# Granting them unconditionally widens no meaningful attack surface,
# while omitting them breaks the run.
ALWAYS_AVAILABLE_TOOLS: frozenset = frozenset({
    # Output pipeline — without this a task's work cannot leave its
    # sandbox, yet the executor unconditionally instructs it to emit.
    "emit_artifact",
    # Visual verification — the counterpart to emit_artifact for any
    # task whose product is a rendered diagram.
    "render_diagram",
    # Silent task-tree bookkeeping. Internal tools the harness treats
    # as always-on in every other surface.
    "bead_create",
    "bead_complete",
    "bead_status",
})


def normalize_tool_name(name: str) -> str:
    """Strip ``mcp_`` prefixes so scope entries match registered names.

    Mirrors ``StreamingToolExecutor._normalize_tool_name`` (repeated
    prefixes, stray ``$``/``_`` separators) without importing it — this
    module must stay importable from the prompt layer, which has no
    business pulling in the streaming executor.
    """
    normalized = name or ""
    while normalized.startswith("mcp_") or "_mcp_" in normalized:
        normalized = normalized.replace("mcp_", "", 1)
        normalized = normalized.lstrip("$_")
    return normalized


def effective_tool_names(scope_tools: Optional[Iterable[str]]) -> Set[str]:
    """Return the set of tool names a task may call, floor included.

    An empty/None ``scope_tools`` means "no restriction"; the caller
    must not filter at all in that case, so this returns an empty set
    to signal it (rather than returning just the floor, which would
    wrongly narrow an unrestricted task down to five tools).
    """
    requested = {t for t in (scope_tools or []) if t}
    if not requested:
        return set()
    return requested | set(ALWAYS_AVAILABLE_TOOLS)


def tool_is_allowed(tool_name: str, allowed: Set[str]) -> bool:
    """True if ``tool_name`` is permitted by the ``allowed`` name set.

    Matching is normalized on both sides, so ``run_shell_command`` in a
    scope matches a registered ``mcp_run_shell_command`` and vice
    versa.  An empty ``allowed`` set means unrestricted.
    """
    if not allowed:
        return True
    if tool_name in allowed:
        return True
    norm = normalize_tool_name(tool_name)
    return norm in allowed or norm in {normalize_tool_name(a) for a in allowed}


def filter_tools_by_scope(
    tools: List[Any], scope_tools: Optional[Iterable[str]],
) -> List[Any]:
    """Filter a list of tool objects down to a scope's allowlist.

    ``tools`` items need only expose a ``name``.  Returns ``tools``
    unchanged when the scope imposes no restriction.
    """
    allowed = effective_tool_names(scope_tools)
    if not allowed:
        return tools
    return [t for t in tools if tool_is_allowed(getattr(t, "name", ""), allowed)]


def unmatched_scope_tools(
    tools: List[Any], scope_tools: Optional[Iterable[str]],
) -> List[str]:
    """Names the scope asked for that no registered tool provides.

    Reported into ``Artifact.decisions`` so a typo'd or retired tool
    name surfaces to the user instead of vanishing.  Floor tools are
    excluded — they were not requested by the author, so their absence
    (e.g. the diagram category disabled) is not the author's error.
    """
    requested = [t for t in (scope_tools or []) if t]
    if not requested:
        return []
    present = {getattr(t, "name", "") for t in tools}
    present |= {normalize_tool_name(n) for n in present}
    return sorted(
        n for n in requested
        if n not in present and normalize_tool_name(n) not in present
    )
