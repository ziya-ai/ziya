"""
Tests for the B1 self-assessment helper.

Covers the parser (every shape we expect from the model and a few
adversarial ones), the failure-decision rule, signature generation,
tag stripping, and the assertion that the prompt instruction
literally contains an example tag the model can mimic.
"""
from __future__ import annotations

import pytest

from app.utils.completion_check import (
    SELF_ASSESSMENT_INSTRUCTION,
    is_failure,
    parse_self_assessment,
    signature_for,
    strip_assessment_tag,
)


class TestParse:
    def test_well_formed_self_closing(self):
        text = '<self_assessment objective_met="true" rationale="all good" />'
        assert parse_self_assessment(text) == {
            "objective_met": "true",
            "rationale": "all good",
        }

    def test_partial_verdict(self):
        text = '... <self_assessment objective_met="partial" rationale="skipped UI" />'
        assert parse_self_assessment(text)["objective_met"] == "partial"

    def test_false_verdict(self):
        text = 'work\n<self_assessment objective_met="false" rationale="blocked" />'
        assert parse_self_assessment(text) == {
            "objective_met": "false",
            "rationale": "blocked",
        }

    def test_attribute_reordering(self):
        text = '<self_assessment rationale="r" objective_met="true" />'
        assert parse_self_assessment(text)["objective_met"] == "true"

    def test_single_quotes(self):
        text = "<self_assessment objective_met='true' rationale='ok' />"
        assert parse_self_assessment(text) == {
            "objective_met": "true",
            "rationale": "ok",
        }

    def test_no_self_close(self):
        # Some models omit the trailing slash even when asked for self-closing.
        text = '<self_assessment objective_met="true" rationale="ok">'
        assert parse_self_assessment(text)["objective_met"] == "true"

    def test_extra_whitespace(self):
        text = '<  self_assessment   objective_met = "true"   rationale = "ok"   />'
        assert parse_self_assessment(text)["objective_met"] == "true"

    def test_case_insensitive_tag(self):
        text = '<Self_Assessment objective_met="true" rationale="ok" />'
        assert parse_self_assessment(text)["objective_met"] == "true"

    def test_unknown_verdict_normalised(self):
        # Model emits some other word — we don't trust it.
        text = '<self_assessment objective_met="maybe" rationale="who knows" />'
        out = parse_self_assessment(text)
        assert out["objective_met"] == "unknown"
        assert out["rationale"] == "who knows"

    def test_missing_attributes(self):
        text = "<self_assessment />"
        assert parse_self_assessment(text) == {"objective_met": "unknown", "rationale": ""}

    def test_no_tag_returns_none(self):
        assert parse_self_assessment("just a regular response") is None

    def test_empty_input(self):
        assert parse_self_assessment("") is None
        assert parse_self_assessment(None) is None  # type: ignore[arg-type]

    def test_multiple_tags_takes_last(self):
        # Model rehearsed earlier in its output — only the final verdict counts.
        text = (
            'Earlier draft: <self_assessment objective_met="true" rationale="rehearsal" />\n'
            'Real verdict: <self_assessment objective_met="false" rationale="actually no" />'
        )
        out = parse_self_assessment(text)
        assert out == {"objective_met": "false", "rationale": "actually no"}

    def test_rationale_with_inner_quotes(self):
        # Single quotes inside a double-quoted rationale should survive.
        text = '<self_assessment objective_met="true" rationale="ran the user\'s tests" />'
        assert parse_self_assessment(text)["rationale"] == "ran the user's tests"

    def test_tag_with_other_text_around_it(self):
        text = (
            "## Result\n\n✅ Done.\n\n"
            '<self_assessment objective_met="true" rationale="all green" />\n'
        )
        assert parse_self_assessment(text) == {
            "objective_met": "true",
            "rationale": "all green",
        }


class TestIsFailure:
    def test_none_is_not_failure(self):
        assert is_failure(None) is False

    def test_true_is_not_failure(self):
        assert is_failure({"objective_met": "true"}) is False

    def test_false_is_failure(self):
        assert is_failure({"objective_met": "false"}) is True

    def test_partial_is_failure(self):
        # The d3 case — model worked around the blocker; user wants to know.
        assert is_failure({"objective_met": "partial"}) is True

    def test_unknown_is_not_failure(self):
        # Don't punish the user for a malformed tag; surface separately.
        assert is_failure({"objective_met": "unknown"}) is False

    def test_missing_key(self):
        assert is_failure({}) is False


class TestSignature:
    def test_success_has_no_signature(self):
        assert signature_for({"objective_met": "true"}) is None
        assert signature_for(None) is None

    def test_failure_signature(self):
        assert signature_for({"objective_met": "false"}) == "self_assessment_false"

    def test_partial_signature(self):
        assert signature_for({"objective_met": "partial"}) == "self_assessment_partial"


class TestStrip:
    def test_strips_tag(self):
        text = 'Result text.\n<self_assessment objective_met="true" rationale="ok" />'
        assert strip_assessment_tag(text) == "Result text."

    def test_idempotent_when_no_tag(self):
        assert strip_assessment_tag("plain text") == "plain text"

    def test_strips_multiple(self):
        text = (
            '<self_assessment objective_met="true" rationale="x" />\n'
            'middle\n'
            '<self_assessment objective_met="true" rationale="y" />'
        )
        out = strip_assessment_tag(text)
        assert "self_assessment" not in out
        assert "middle" in out

    def test_handles_empty(self):
        assert strip_assessment_tag("") == ""
        assert strip_assessment_tag(None) is None  # type: ignore[arg-type]


class TestPromptInstruction:
    def test_contains_literal_example(self):
        # The model is more likely to format correctly when shown a
        # literal example in the system prompt.
        assert "<self_assessment" in SELF_ASSESSMENT_INSTRUCTION
        assert 'objective_met="true|false|partial"' in SELF_ASSESSMENT_INSTRUCTION

    def test_describes_verdict_values(self):
        text = SELF_ASSESSMENT_INSTRUCTION.lower()
        assert "true" in text and "false" in text and "partial" in text
