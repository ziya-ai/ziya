"""An investigation must not end with "want me to look into X?".

Reported 2026-11-16, after many earlier reports: asked for research or a
root cause, the model returned part of the way there with a list of threads
it could follow and a question about whether to follow them.  Worse on
newer models, which take prompt wording literally.

What the assembled prompt said at the time:
  * the second sentence of the base template was "ask for clarification
    rather than declaring your surety unless you are absolutely certain" --
    mid-investigation one is never certain, so the literal reading is "ask";
  * HANDLING MISSING CONTEXT told the model to ask the user for a file
    ("CONTEXT NEEDED") instead of reading it;
  * the only "invoke tools yourself rather than asking" rule lived in
    extensions keyed to opus4.7 / opus4.8, so no later model received it;
  * nothing anywhere said an investigation runs to completion.

The rule is in the base template (app/agents/prompts.py) so every model on
every endpoint gets it.  As in test_prompt_no_meeting_offers.py, the
assertions that matter are on the assembled system prompt.
"""
import pytest

from app.agents import prompts

_HEADING = "INVESTIGATIONS RUN TO COMPLETION:"
_CHANGES_HEADING = "IMPORTANT: When making changes:"
_MIN_REAL_SYSTEM_PROMPT = 2000   # the silent-fallback stub is 66 chars


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(text: str) -> str:
    """The rule's own block: heading through the next ALL-CAPS section."""
    return _flat(text.split(_HEADING, 1)[1].split("WRITING FOR OTHER PEOPLE:", 1)[0])


def _system(endpoint: str, model_id: str) -> str:
    from app.utils.precision_prompt_system import precision_system
    messages = precision_system.build_messages(
        request_path="/streaming_tools",
        model_info={"endpoint": endpoint, "model_id": model_id},
        files=[], question="why did the deploy fail", chat_history=[],
        system_prompt_addition="", conv_start_ts=None,
        conversation_id="test-investigations-complete",
    )
    first = messages[0]
    return first["content"] if isinstance(first, dict) else getattr(first, "content", "")


def test_rule_is_in_the_base_template():
    assert _HEADING in prompts.template
    body = _section(prompts.template)
    assert "Never end an investigation by offering to continue it" in body
    assert "A hypothesis you can test is not a finding" in body
    assert "work all of them" in body
    # the stop conditions are stated, so the rule is not "never stop"
    assert "needs something only the user has" in body
    assert "the next step changes something" in body
    # and it says which later rules it does not yield to
    assert "They never limit how far you look" in body


def test_rule_precedes_the_scope_limits_it_qualifies():
    t = prompts.template
    assert _CHANGES_HEADING in t                      # control: anchor exists
    assert t.index(_HEADING) < t.index(_CHANGES_HEADING)


def test_the_blanket_ask_for_clarification_line_is_gone():
    flat = _flat(prompts.template)
    assert "ask for clarification rather than declaring your surety" not in flat
    assert "When you are not certain, find out" in flat
    assert "do not ask the user for anything you can establish yourself" in flat
    # asking is still allowed for what looking cannot answer
    assert "Ask only for what looking cannot tell you" in flat


def test_missing_context_is_read_not_requested():
    block = _flat(prompts.template.split("CRITICAL: HANDLING MISSING CONTEXT:", 1)[1]
                  .split("When presenting multiple diffs", 1)[0])
    assert "Read it yourself" in block
    assert "Do not ask the user for a file you can read" in block
    # CONTEXT NEEDED survives only as the no-tool fallback
    assert block.index("Read it yourself") < block.index("CONTEXT NEEDED")
    assert "Only when no tool you hold can read it" in block
    assert "Ask for the specific file content first" not in block


def test_template_still_formats():
    """The template is a format string: a stray brace in the new text would
    raise at prompt assembly and fall back to the stub."""
    rendered = prompts.conversational_prompt.format_messages(
        tools="", codebase="", question="q", chat_history=[], agent_scratchpad=[])
    assert _HEADING in rendered[0].content


@pytest.mark.parametrize("endpoint,model_id", [
    ("bedrock", "test"),
    ("openai", "gpt-test"),
    ("google", "gemini-test"),
    ("local", "qwen2.5-coder:7b"),
])
def test_rule_reaches_the_assembled_system_prompt(endpoint, model_id):
    system = _system(endpoint, model_id)
    # positive control: the real prompt was assembled, not the fallback stub
    assert len(system) >= _MIN_REAL_SYSTEM_PROMPT
    assert "You are an excellent coder" in system
    flat = _flat(system)
    assert flat.count(_HEADING) == 1
    assert flat.count("Never end an investigation by offering to continue it") == 1
    assert "ask for clarification rather than declaring your surety" not in flat
