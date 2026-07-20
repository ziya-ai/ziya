"""Tests for the Claude-family visual-first reinforcement prompt extension.

Pins that claude_family_extension injects the VISUAL-FIRST REINFORCEMENT
block, that it lands ahead of the tool-usage rules (placement is
load-bearing: it must sit next to the concision pressure it counteracts),
and that the disable flag still short-circuits cleanly.
"""

from app.extensions.prompt_extensions.claude_extensions import claude_family_extension


def _ctx(enabled=True):
    return {"config": {"enabled": enabled}}


class TestVisualFirstReinforcement:
    def test_reinforcement_present(self):
        out = claude_family_extension("base prompt", _ctx())
        assert "VISUAL-FIRST REINFORCEMENT:" in out

    def test_reinforcement_precedes_tool_usage_rules(self):
        out = claude_family_extension("base prompt", _ctx())
        assert out.index("VISUAL-FIRST REINFORCEMENT:") < out.index("TOOL USAGE PRIORITIZATION:")

    def test_disabled_returns_prompt_unchanged(self):
        out = claude_family_extension("base prompt", _ctx(enabled=False))
        assert out == "base prompt"

    def test_inserted_after_preservation_section_when_present(self):
        prompt = "CRITICAL: INSTRUCTION PRESERVATION:\nrules here\n\nrest of prompt"
        out = claude_family_extension(prompt, _ctx())
        assert "VISUAL-FIRST REINFORCEMENT:" in out
        assert out.index("CRITICAL: INSTRUCTION PRESERVATION:") < out.index("VISUAL-FIRST REINFORCEMENT:")
        assert "rest of prompt" in out

    def test_honesty_binding_present(self):
        out = claude_family_extension("base prompt", _ctx())
        assert "never an" in out
        assert "invented one" in out
