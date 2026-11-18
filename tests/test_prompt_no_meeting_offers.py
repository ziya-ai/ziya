"""Drafts for other people must never offer to meet or walk anyone through it.

Users report this daily: a forum or Slack post the model drafts ends with
"happy to walk you through it" or "glad to hop on a call", an offer the user
never made and readers find unwelcome.  The rule lives in the base template
(app/agents/prompts.py), not a model-family extension, so every model on
every endpoint gets it.  These tests assert on the assembled system prompt,
which is what the model actually receives, not just the template string.
"""
import pytest

from app.agents import prompts

_RULE_HEADING = "WRITING FOR OTHER PEOPLE:"
_MIN_REAL_SYSTEM_PROMPT = 2000   # the silent-fallback stub is 66 chars


def _system(endpoint: str, model_id: str) -> str:
    from app.utils.precision_prompt_system import precision_system
    messages = precision_system.build_messages(
        request_path="/streaming_tools",
        model_info={"endpoint": endpoint, "model_id": model_id},
        files=[], question="draft a forum reply", chat_history=[],
        system_prompt_addition="", conv_start_ts=None,
        conversation_id="test-no-meeting-offers",
    )
    first = messages[0]
    return first["content"] if isinstance(first, dict) else getattr(first, "content", "")


def test_rule_is_in_the_base_template():
    assert _RULE_HEADING in prompts.template
    body = " ".join(prompts.template.split(_RULE_HEADING, 1)[1].split("\n\n", 1)[0].split())
    assert "walk anyone through it, meet, get on a call" in body
    assert '"happy to chat"' in body
    assert "End the draft when its content ends" in body


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
    assert _RULE_HEADING in system
    assert " ".join(system.split()).count("never offer, on the user's behalf") == 1
