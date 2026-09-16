"""Prompt assembly must never silently fall back to the 66-char stub.

``PrecisionPromptSystem.build_messages`` wraps its whole body in
``except Exception`` and, on any failure, returns
``_fallback_build_messages`` — a two-message prompt whose system content
is *66 characters*.  Tools still work in that state and streaming looks
normal, so the failure is invisible from the outside: no AGENTS.md, no
session-context block, no skills catalog, no memory section, no
timestamps.  Two independent bugs were found in that state live:

1. ``app/agents/prompts.py`` contained a raw ``{"type":"chord",...}``
   JSON example inside the LangChain prompt template.  LangChain's
   formatter reads ``{`` as a placeholder opener, so it raised
   ``KeyError: '"type"'`` on *every* request.  Literal braces in the
   template must be doubled.

2. ``build_messages`` rebound ``logger`` inside its outer ``except``
   handler.  Python scopes that binding to the whole function, so the
   module-level ``logger`` was shadowed and all eight inner handlers
   (``logger.debug("... unavailable")``) raised ``UnboundLocalError``
   instead of logging — converting any recoverable sub-failure into the
   silent full fallback.

These tests fail against either bug.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# The system prompt is tens of KB; the fallback stub is 66 chars.  Anything
# under this bound means we got the stub (or something equally broken).
MIN_REAL_SYSTEM_PROMPT = 2000


def _system_content(messages):
    first = messages[0]
    return first["content"] if isinstance(first, dict) else getattr(first, "content", "")


def test_build_messages_does_not_fall_back():
    """A plain chat request must produce the real prompt, not the stub."""
    from app.utils.precision_prompt_system import precision_system

    messages = precision_system.build_messages(
        request_path="/streaming_tools",
        model_info={"endpoint": "bedrock", "model_id": "test"},
        files=[],
        question="hello",
        chat_history=[],
        system_prompt_addition="",
        conv_start_ts=None,
        conversation_id="test-conversation-id",
    )
    system = _system_content(messages)
    assert len(system) >= MIN_REAL_SYSTEM_PROMPT, (
        f"system prompt is {len(system)} chars — build_messages fell back to the "
        f"stub. Check the server log for 'Error in precision system'. Content: {system!r}"
    )
    # The stub has no session context; the real prompt always does.
    joined = "\n".join(
        (m["content"] if isinstance(m, dict) else str(getattr(m, "content", "")))
        for m in messages
    )
    assert "## Session Context" in joined


def test_prompt_template_has_no_unescaped_braces():
    """Every ``{...}`` in the template must be a real placeholder or doubled.

    LangChain formats the template with ``str.format`` semantics, so a raw
    JSON/JS example (``{"type": ...}``, ``{ shapeId: ... }``) raises KeyError
    at request time.  Known placeholders are listed explicitly so adding a
    new one is a deliberate act.
    """
    from app.agents import prompts

    known = {"question", "codebase", "tools", "AST_AVAILABLE",
             "AST_CONTEXT", "AST_TOKEN_COUNT", "agent_scratchpad",
             "chat_history", "input"}
    template = prompts.template

    # Remove doubled braces first: they are correctly escaped literals.
    stripped = template.replace("{{", "").replace("}}", "")
    offenders = [
        m.group(0) for m in re.finditer(r"\{([^{}]*)\}", stripped)
        if m.group(1).strip() not in known
    ]
    assert not offenders, (
        "unescaped literal braces in the prompt template (double them as "
        f"'{{{{' / '}}}}'): {offenders}"
    )


def test_build_messages_does_not_shadow_module_logger():
    """No assignment to ``logger`` inside build_messages.

    Rebinding it anywhere in the function — including inside an exception
    handler at the very bottom — makes the name function-local for the whole
    body, so every earlier ``logger.*`` call raises UnboundLocalError.
    """
    from app.utils.precision_prompt_system import PrecisionPromptSystem

    source = inspect.getsource(PrecisionPromptSystem.build_messages)
    tree = ast.parse(source.lstrip() if source.startswith(" ") else source)

    assigned = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "logger":
                    assigned.append(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == "logger":
                    assigned.append(node.lineno)

    assert not assigned, (
        "build_messages assigns to 'logger', shadowing the module-level "
        f"logger for the entire function (relative lines {assigned}). Use the "
        "module logger, or bind a differently-named local."
    )


@pytest.mark.parametrize("module_path", ["utils/precision_prompt_system.py"])
def test_no_logger_rebinding_in_any_prompt_builder(module_path):
    """Same trap, checked file-wide: a function that logs must not also
    rebind ``logger`` locally."""
    tree = ast.parse((APP / module_path).read_text())
    bad = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        assigns, uses = [], False
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "logger":
                        assigns.append(node.lineno)
            elif (isinstance(node, ast.Attribute)
                  and isinstance(node.value, ast.Name)
                  and node.value.id == "logger"):
                uses = True
        if assigns and uses:
            bad.append((fn.name, assigns))
    assert not bad, f"functions that both use and rebind 'logger': {bad}"
