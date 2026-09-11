"""Tests for app.utils.inline_code_spans and its two consumers in the
streaming executor's completion scanner.

Anchored on the 2026-09-07 incident: a clean end_turn reply containing the
4-backtick span ```` ```diff ```` was judged truncated twice in a row --
first as "open_inline" (fence-strip regex mangled the span), then, after
the fence normaliser inserted a blank line INSIDE the span, as "open_fence".
"""
import pytest

from app.utils.inline_code_spans import (
    backtick_runs,
    ends_inside_code_span,
    has_unmatched_run,
    last_paragraph,
    pair_code_spans,
    strip_fenced_blocks,
)

# Built by construction so the source itself never holds a fence-looking run.
B = '`'
SPAN4 = B * 4 + ' ' + B * 3 + 'diff ' + B * 4          # ```` ```diff ````
FENCE = B * 3

# The exact tail of the iteration-1 reply from the incident log.
INCIDENT_TEXT = (
    "- **Fence-aware while open.** If a fence opener appears in the buffer "
    "while `state.open` is true, treat that as evidence the \"block\" was a "
    "misfire: emit `done`, reopen scanning as text from the fence onward. "
    f"Real reasoning models don't put {SPAN4} blocks inside their thinking. "
    "Cheap, local, testable with the same delta-replay harness.\n"
    "- **Bound the open block.** Cap a block at N characters.\n\n"
    "I'd do the first one now. Which?"
)


# --- pairing -------------------------------------------------------------

def test_runs_are_maximal():
    assert backtick_runs(f'a {B}b{B * 3}c') == [(2, 1), (4, 3)]


def test_equal_length_runs_pair_and_interior_runs_are_content():
    spans, unmatched = pair_code_spans(SPAN4)
    assert spans == [(0, len(SPAN4))]
    assert unmatched == []


def test_unequal_run_is_literal_not_an_opener():
    spans, unmatched = pair_code_spans(f'{B}a{B} and {B * 2}b{B}')
    assert spans == [(0, 3)]
    assert [n for _, n in unmatched] == [2, 1]


def test_has_unmatched_run_streaming_view():
    assert has_unmatched_run(f'the inline {B * 4} ') is True      # closer not yet arrived
    assert has_unmatched_run(f'the inline {SPAN4} mention') is False
    assert has_unmatched_run('no ticks') is False


def test_last_paragraph_splits_on_blank_line():
    assert last_paragraph('a\n\nb\n  \nc') == 'c'
    assert last_paragraph('one paragraph') == 'one paragraph'


# --- fence stripping -----------------------------------------------------

def test_strip_removes_complete_fence_and_keeps_prose():
    text = f'before\n{FENCE}py\nuse {B}x{B}\n{FENCE}\nafter'
    assert strip_fenced_blocks(text) == 'before\nafter'


def test_strip_line_starting_with_code_span_is_not_a_fence():
    # CommonMark 4.5: a backtick fence's info string cannot contain a backtick.
    text = f'{SPAN4} is a span, not a fence'
    assert strip_fenced_blocks(text) == text


def test_strip_closer_must_be_at_least_as_wide():
    text = f'{B * 4}\n{FENCE}\nstill inside\n{B * 4}\nout'
    assert strip_fenced_blocks(text) == 'out'


def test_strip_unclosed_fence_swallows_remainder():
    assert strip_fenced_blocks(f'a\n{FENCE}\n{B}dangling') == 'a'


# --- the verdict ---------------------------------------------------------

def test_incident_reply_is_not_truncated():
    """The exact false positive: a closed 4-tick span with 3 ticks inside."""
    assert ends_inside_code_span(INCIDENT_TEXT) is False


def test_incident_reply_via_executor_helper():
    from app.streaming_tool_executor import StreamingToolExecutor
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    assert inst._has_unclosed_inline_code(INCIDENT_TEXT) is False


def test_genuine_truncation_still_detected():
    assert ends_inside_code_span(f'The bug is clear: {B}DirectoryBrowser') is True


def test_truncation_after_closed_spans():
    assert ends_inside_code_span(f'{B}a{B} then {B}b') is True


def test_unmatched_run_in_earlier_paragraph_is_not_evidence():
    # A span cannot cross a blank line, so the stray tick is literal.
    assert ends_inside_code_span(f'use the {B} key\n\nAll done.') is False


def test_unmatched_run_followed_by_closed_span_is_literal():
    # The verdict is about the LAST run only: the double-tick run has no
    # partner, but the text does not END inside it.
    assert ends_inside_code_span(f'{B * 2}x {B}y{B}') is False
    assert ends_inside_code_span(f'{B * 2}x {B}y{B} done') is False


def test_fence_with_inner_ticks_then_clean_end():
    assert ends_inside_code_span(f'{FENCE}\nuse {B}x{B}\n{FENCE}') is False
    assert ends_inside_code_span(f'{FENCE}\ncode\n{FENCE} then {B}inline') is True


# --- fence normaliser ----------------------------------------------------

@pytest.fixture
def normalize():
    from app.streaming_tool_executor import StreamingToolExecutor
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    return inst._normalize_fence_spacing


def test_normalizer_does_not_split_a_span_in_one_chunk(normalize):
    text = f'the inline {SPAN4} mention'
    assert normalize(text, {}) == text


def test_normalizer_does_not_split_a_span_across_chunks(normalize):
    # Chunk boundary fell after the opener; the closer is still to come.
    preceding = f'trips on the inline {B * 4}'
    chunk = f' {FENCE}diff'
    assert normalize(chunk, {}, preceding=preceding) == chunk


def test_normalizer_still_repairs_a_glued_opener(normalize):
    assert normalize(f'four layers:{FENCE}mermaid', {}) == f'four layers:\n\n{FENCE}mermaid'


def test_normalizer_repairs_when_earlier_spans_are_closed(normalize):
    text = f'see {B}x{B} then:{FENCE}mermaid'
    assert normalize(text, {}, preceding='prior {B}a{B} para') == f'see {B}x{B} then:\n\n{FENCE}mermaid'


def test_normalizer_unmatched_run_in_earlier_paragraph_does_not_suppress(normalize):
    assert (normalize(f'now:{FENCE}mermaid', {}, preceding=f'stray {B}\n\nnew para')
            == f'now:\n\n{FENCE}mermaid')


def test_normalizer_stub_signature_compat(normalize):
    # Existing tests stub this with (text, tracker); the new arg is keyword-only.
    assert normalize('plain', {}) == 'plain'
