"""Tests for the artifact summary truncation helper.

The d3 task post-mortem highlighted that ``task_executor`` was
silently slicing the model's final summary at 2000 chars — cutting
mid-sentence without any indication, so users couldn't tell whether
the model had stopped or the system had truncated.  These tests
pin the new behaviour: a higher cap, soft cut at a paragraph or
sentence boundary near the cap, and an explicit truncation marker
when the cap is actually hit.
"""

import pytest

from app.utils.artifact_summary import truncate_summary, SUMMARY_CAP


class TestPassThrough:
    def test_empty_string(self):
        out = truncate_summary("")
        assert out == ""

    def test_short_text_unchanged(self):
        text = "A brief result."
        assert truncate_summary(text) == text

    def test_text_at_cap_unchanged(self):
        text = "x" * SUMMARY_CAP
        out = truncate_summary(text)
        assert out == text
        assert "truncated" not in out

    def test_text_just_under_cap_unchanged(self):
        text = "x" * (SUMMARY_CAP - 1)
        assert truncate_summary(text) == text


class TestTruncation:
    def test_marker_appended_when_truncated(self):
        text = "x" * (SUMMARY_CAP * 2)
        out = truncate_summary(text)
        assert "summary truncated" in out.lower()
        assert len(out) <= SUMMARY_CAP + 200  # marker has bounded overhead

    def test_marker_includes_full_length(self):
        text = "x" * (SUMMARY_CAP * 2)
        out = truncate_summary(text)
        # The marker should report the original character count so
        # the user knows how much was elided, not just "it was long."
        # Marker formats numbers with thousands separators (``:,``).
        assert f"{SUMMARY_CAP * 2:,}" in out

    def test_soft_cut_prefers_paragraph_boundary(self):
        # Many paragraphs with the boundary just before the cap.
        head = ("para line.\n\n" * (SUMMARY_CAP // 12))[:SUMMARY_CAP - 50]
        tail = "FINAL_PARAGRAPH_MARKER " * 200
        text = head + "\n\n" + tail
        out = truncate_summary(text)
        # Cut should land at the \n\n boundary, not mid-word in tail.
        assert "FINAL_PARAGRAPH_MARKER" not in out.split("[")[0]

    def test_soft_cut_prefers_sentence_when_no_paragraph(self):
        # No \n\n; use ". " as the soft boundary instead.
        # Build head that is *exactly* SUMMARY_CAP - 50 chars long so
        # appending the literal ". " + tail crosses the cap and forces
        # truncation.  ``"sentence here. "`` is 15 chars, so we need
        # at least ceil((SUMMARY_CAP - 50) / 15) repetitions before
        # slicing.
        unit = "sentence here. "
        repetitions = (SUMMARY_CAP - 50) // len(unit) + 2
        head = (unit * repetitions)[:SUMMARY_CAP - 50]
        assert len(head) == SUMMARY_CAP - 50, (
            "test setup invariant: head must be exactly SUMMARY_CAP-50"
        )
        tail = "AFTER_SENT " * 200  # 2200 chars, well past the cap
        text = head + ". " + tail
        out = truncate_summary(text)
        # The marker must be present (truncation actually happened),
        # and AFTER_SENT must NOT appear in the visible head — the
        # soft cut should land at the literal ". " boundary just
        # before the tail.
        assert "summary truncated" in out
        assert "AFTER_SENT" not in out.split("[summary truncated")[0]

    def test_hard_cut_when_no_boundary_found(self):
        # No paragraph or sentence breaks — must still truncate.
        text = "x" * (SUMMARY_CAP * 2)
        out = truncate_summary(text)
        assert out  # non-empty
        assert "summary truncated" in out.lower()

    def test_marker_format_stable(self):
        text = "x" * (SUMMARY_CAP * 2)
        out = truncate_summary(text)
        # Marker should be on its own line and visually distinct so
        # frontend/log parsers can spot it without false positives
        # against paragraph text mentioning the word "truncated".
        assert "\n\n[summary truncated" in out


class TestExplicitCap:
    def test_caller_can_override_cap_smaller(self):
        text = "abcdefghij" * 100
        out = truncate_summary(text, cap=50)
        # The marker text itself is several lines; the body before it
        # must be no longer than the override cap (with small slack
        # for soft-boundary search).
        body = out.split("\n\n[summary truncated")[0]
        assert len(body) <= 50 + 16  # +16 for soft-boundary search window

    def test_caller_can_override_cap_larger(self):
        text = "y" * 5000
        out = truncate_summary(text, cap=10000)
        assert out == text  # under the override cap

    def test_zero_or_negative_cap_uses_default(self):
        text = "y" * (SUMMARY_CAP * 2)
        # Defensive: callers passing 0 shouldn't accidentally produce
        # an empty summary.  Treat as default.
        out = truncate_summary(text, cap=0)
        assert "summary truncated" in out.lower()
        out2 = truncate_summary(text, cap=-1)
        assert "summary truncated" in out2.lower()


class TestPathologicalInput:
    def test_only_whitespace(self):
        # Whitespace-only summaries pass through (the executor's
        # .strip() handles the actual cleanup; this helper shouldn't
        # eat content the caller might want to inspect).
        out = truncate_summary("   \n\t  ")
        assert out == "   \n\t  "

    def test_unicode_within_cap(self):
        text = "café— 漢字 ✓\n" * 10
        assert truncate_summary(text) == text

    def test_unicode_over_cap(self):
        # Multi-byte chars count as one each (str length in Python).
        text = "漢字" * (SUMMARY_CAP // 2 + 100)
        out = truncate_summary(text)
        assert "summary truncated" in out.lower()

    def test_text_with_existing_truncation_marker_still_works(self):
        # Pathological: text that already contains the marker text.
        # Helper shouldn't get confused into thinking it was already
        # truncated; it just truncates again on top.
        text = ("[summary truncated] " + "x" * SUMMARY_CAP * 2)
        out = truncate_summary(text)
        assert out.lower().count("summary truncated") >= 2

    def test_idempotent_within_cap(self):
        text = "short"
        assert truncate_summary(truncate_summary(text)) == text
