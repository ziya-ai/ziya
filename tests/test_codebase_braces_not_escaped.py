"""The file context reaches the model with its braces intact.

precision_prompt_system once doubled every '{' in the codebase before
passing it as the {codebase} template variable.  str.format() does not
re-parse substituted values, so the model received '{{a,b}}' and '${{x}}'
for source that read '{a,b}' and '${x}'.  The escape was skipped whenever
any file in context already held a '{{' (e.g. JSX style={{...}}), so the
corruption came and went with the set of files in context.

Observed 2026-10-22: with only markdown skill files in context the model
read `connectivity_ping_{{drop_rate,rtt,jitter}}_acc` and reported the
doubled braces as a display artefact.  The next turn, with a .tsx file in
context, the same file showed single braces.
"""
import inspect

import pytest


BRACED = (
    "File: a.md\n[001 ] - `x_{a,b}_acc`\n"
    "File: b.ts\n[001 ] const s = `${c.id}:${n}`;\n"
)
BRACED_WITH_JSX = BRACED + "File: c.tsx\n[001 ] <div style={{ x: 1 }} />\n"


def _build_messages(file_context):
    """Run the real template with the real substitution path."""
    from app.agents.prompts_manager import get_extended_prompt
    prompt = get_extended_prompt(
        model_name="sonnet4.6", model_family="claude", endpoint="bedrock",
        context={"has_tools": False, "mcp_enabled": False,
                 "native_tools_available": True},
    )
    return prompt.format_messages(codebase=file_context, tools="", question="q")


@pytest.mark.parametrize("ctx", [BRACED, BRACED_WITH_JSX],
                         ids=["no-jsx-in-context", "jsx-in-context"])
def test_template_substitution_keeps_braces_verbatim(ctx):
    system = _build_messages(ctx)[0].content
    assert "`x_{a,b}_acc`" in system
    assert "`${c.id}:${n}`" in system
    assert "{{a,b}}" not in system
    assert "${{" not in system


def test_precision_prompt_does_not_escape_the_codebase_value():
    """The seam: the source no longer contains the escaping step at all,
    independent of which other files are in context."""
    from app.utils import precision_prompt_system as pps
    src = inspect.getsource(pps)
    assert "escape_braces_preserve_template_literals" not in src
    assert ".replace('{', '{{')" not in src
    assert "codebase=file_context" in src


def test_positive_control_escaped_value_would_leak():
    """Documents WHY: an escaped value is emitted verbatim by format_messages,
    so escaping here could only ever corrupt what the model sees."""
    esc = BRACED.replace("{", "{{").replace("}", "}}")
    system = _build_messages(esc)[0].content
    assert "x_{{a,b}}_acc" in system
