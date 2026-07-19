"""
Tests for the <progress note="..."/> tag contract — extraction,
stripping, and the incremental ProgressTagScanner that survives
chunk-split tags in the streamed text.
"""

import pytest

from app.utils.completion_check import (
    ProgressTagScanner,
    extract_progress_notes,
    strip_progress_tags,
)


class TestExtract:
    def test_single_tag(self):
        assert extract_progress_notes(
            'x <progress note="reviewed 3/10 diffs" /> y'
        ) == ["reviewed 3/10 diffs"]

    def test_multiple_tags_in_order(self):
        text = '<progress note="a"/> mid <progress note="b"/>'
        assert extract_progress_notes(text) == ["a", "b"]

    def test_single_quotes_and_case(self):
        assert extract_progress_notes(
            "<PROGRESS Note='staging files'/>"
        ) == ["staging files"]

    def test_tag_without_note_is_skipped(self):
        assert extract_progress_notes("<progress/> <progress note=''/>") == []

    def test_note_capped_at_200_chars(self):
        long = "x" * 500
        [note] = extract_progress_notes(f'<progress note="{long}"/>')
        assert len(note) == 200

    def test_empty_text(self):
        assert extract_progress_notes("") == []


class TestStrip:
    def test_strips_all_tags(self):
        out = strip_progress_tags('a <progress note="n1"/> b <progress note="n2" /> c')
        assert "<progress" not in out
        assert "a" in out and "b" in out and "c" in out

    def test_idempotent_on_clean_text(self):
        assert strip_progress_tags("plain text") == "plain text"

    def test_empty(self):
        assert strip_progress_tags("") == ""


class TestScanner:
    def test_whole_tag_in_one_chunk(self):
        s = ProgressTagScanner()
        assert s.feed('working <progress note="phase 1 done"/> more') == ["phase 1 done"]

    def test_tag_split_across_chunks(self):
        s = ProgressTagScanner()
        assert s.feed('text <progress no') == []
        assert s.feed('te="halfway th') == []
        assert s.feed('ere"/> tail') == ["halfway there"]

    def test_each_note_reported_once(self):
        s = ProgressTagScanner()
        assert s.feed('<progress note="a"/>') == ["a"]
        # Subsequent unrelated chunks must not re-report.
        assert s.feed(" continuing") == []
        assert s.feed('<progress note="b"/>') == ["b"]

    def test_two_tags_in_one_chunk(self):
        s = ProgressTagScanner()
        assert s.feed('<progress note="a"/><progress note="b"/>') == ["a", "b"]

    def test_buffer_bounded_without_tags(self):
        s = ProgressTagScanner()
        for _ in range(100):
            s.feed("no tags here, just a lot of prose " * 5)
        assert len(s._buf) <= ProgressTagScanner._KEEP

    def test_partial_tag_survives_buffer_cap(self):
        s = ProgressTagScanner()
        # Long prose then a partial tag at the tail — the tail must be
        # retained through the cap so the tag still completes.
        s.feed(("prose " * 300) + '<progress note="alm')
        assert s.feed('ost done"/>') == ["almost done"]

    def test_noteless_tag_consumed_silently(self):
        s = ProgressTagScanner()
        assert s.feed("<progress/>") == []
        # Buffer advanced past it — no stuck state.
        assert s.feed('<progress note="next"/>') == ["next"]

    def test_none_and_empty_chunks_safe(self):
        s = ProgressTagScanner()
        assert s.feed("") == []
        assert s.feed(None) == []  # type: ignore[arg-type]
        assert s.feed('<progress note="ok"/>') == ["ok"]
