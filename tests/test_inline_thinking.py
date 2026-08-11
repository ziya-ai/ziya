"""Tests for app.utils.inline_thinking.

Pins the reported regression: a model emits reasoning as literal tags in
its TEXT stream, and the frontend strip regex requires the closing tag --
so the opener rendered raw mid-stream, then the whole span was deleted the
moment the closer arrived.

Invariant asserted throughout: reasoning content never appears in the
residual answer text, and no tag literal survives into it.

Tags are constructed from TAG_NAMES rather than written literally.  The
rewrite this scanner supersedes mutated such literals in any text passing
through the delta pipeline, including test source.
"""
import pytest

from app.utils.inline_thinking import (
    CLOSERS,
    MAX_HOLDBACK,
    OPENERS,
    TAG_NAMES,
    InlineThinkingState,
    _find_first_tag,
    _pending_prefix_len,
    flush,
    scan,
)

OPEN = {n: '<' + n + '>' for n in TAG_NAMES}
CLOSE = {n: '</' + n + '>' for n in TAG_NAMES}
ALL_TAGS = OPENERS + CLOSERS


def feed(deltas, state=None, **kw):
    """Run deltas through the scanner; returns (events, text, state)."""
    st = state or InlineThinkingState()
    events, text = [], []
    for d in deltas:
        evs, out = scan(d, st, '0ms', **kw)
        events.extend(evs)
        text.append(out)
    return events, ''.join(text), st


def reasoning(events) -> str:
    return ''.join(e.get('content', '') for e in events
                   if e['type'] == 'thinking' and not e.get('done'))


def dones(events) -> int:
    return sum(1 for e in events
               if e['type'] == 'thinking' and e.get('done'))


def no_raw_tag(text: str) -> bool:
    return not any(t in text for t in ALL_TAGS)


# --- complete blocks ----------------------------------------------------

@pytest.mark.parametrize('name', TAG_NAMES)
def test_complete_block_every_spelling(name):
    events, text, st = feed(['A', OPEN[name] + 'why' + CLOSE[name] + 'B'])
    assert reasoning(events) == 'why'
    assert dones(events) == 1
    assert text == 'AB'
    assert not st.open
    assert no_raw_tag(text)


@pytest.mark.parametrize('name', TAG_NAMES)
def test_reasoning_never_enters_answer_text(name):
    _, text, _ = feed([OPEN[name] + 'secret' + CLOSE[name] + 'answer'])
    assert 'secret' not in text
    assert text == 'answer'


def test_two_blocks_emit_two_dones():
    o, c = OPEN['thinking'], CLOSE['thinking']
    events, text, _ = feed([o + 'a' + c, 'X', o + 'b' + c + 'Y'])
    assert reasoning(events) == 'ab'
    assert dones(events) == 2
    assert text == 'XY'


def test_empty_block_leaves_no_tag():
    o, c = OPEN['thinking-data'], CLOSE['thinking-data']
    events, text, _ = feed([o + c + 'q'])
    assert reasoning(events) == ''
    assert dones(events) == 1
    assert text == 'q'


def test_longer_spelling_closer_not_read_as_shorter():
    """'</thinking>' must not be matched inside '</thinking-data>'."""
    o, c = OPEN['thinking-data'], CLOSE['thinking-data']
    events, text, st = feed([o + 'body' + c + 'tail'])
    assert reasoning(events) == 'body'
    assert dones(events) == 1
    assert text == 'tail'
    assert not st.open


def test_mismatched_closer_still_closes():
    """Strict pairing would strand the rest of the response inside the
    block.  Any closer closes."""
    events, text, st = feed([OPEN['reasoning'] + 'r' + CLOSE['thinking'] + 'ans'])
    assert reasoning(events) == 'r'
    assert dones(events) == 1
    assert text == 'ans'
    assert not st.open


# --- ordering -----------------------------------------------------------

def test_text_before_opener_defers_the_block():
    """Deferral keeps a thinking marker from being ordered ahead of the
    answer text that precedes it, since callers extend events before
    appending text."""
    events, text, st = feed(['answer' + OPEN['thinking'] + 'reason'])
    assert events == []
    assert text == 'answer'
    assert st.carry == OPEN['thinking'] + 'reason'
    evs2, out2 = scan('', st, '0ms')
    assert reasoning(evs2) == 'reason'
    assert out2 == ''


# --- streaming granularity ---------------------------------------------

def test_block_streams_incrementally_not_buffered():
    st = InlineThinkingState()
    e1, o1 = scan(OPEN['thinking'] + 'first ', st, '0ms')
    assert reasoning(e1) == 'first '
    assert dones(e1) == 0
    assert st.open
    assert o1 == ''
    e2, _ = scan('second ', st, '0ms')
    assert reasoning(e2) == 'second '
    assert dones(e2) == 0
    e3, o3 = scan(CLOSE['thinking'] + 'done', st, '0ms')
    assert dones(e3) == 1
    assert o3 == 'done'


def test_holdback_bound_matches_longest_tag():
    assert MAX_HOLDBACK == max(len(t) for t in ALL_TAGS) - 1


@pytest.mark.parametrize('split', range(1, len(OPEN['thinking-data'])))
def test_opener_split_at_every_offset(split):
    o, c = OPEN['thinking-data'], CLOSE['thinking-data']
    src = o + 'reason' + c + 'tail'
    events, text, st = feed([src[:split], src[split:]])
    assert reasoning(events) == 'reason'
    assert dones(events) == 1
    assert text == 'tail'
    assert no_raw_tag(text)


@pytest.mark.parametrize('split', range(1, len(CLOSE['thinking-data'])))
def test_closer_split_at_every_offset(split):
    o, c = OPEN['thinking-data'], CLOSE['thinking-data']
    src = o + 'reason' + c + 'tail'
    cut = src.index(c) + split
    events, text, st = feed([src[:cut], src[cut:]])
    assert reasoning(events) == 'reason'
    assert dones(events) == 1
    assert text == 'tail'
    assert not st.open


def test_partial_opener_is_withheld_outside_block():
    st = InlineThinkingState()
    _, out = scan('answer<thin', st, '0ms')
    assert out == 'answer'
    assert st.carry == '<thin'


def test_partial_closer_is_withheld_inside_block():
    st = InlineThinkingState(open=True)
    events, out = scan('reason</thin', st, '0ms')
    assert reasoning(events) == 'reason'
    assert out == ''
    assert st.carry == '</thin'


def test_partial_closer_not_withheld_outside_block():
    """Outside a block a closer is prose, not structure, so there is
    nothing to resolve -- withholding it would delay text for no reason.
    The stray closer must still survive intact across the split."""
    events, text, st = feed(['answer</thin', 'king> more'])
    assert events == []
    assert text == 'answer</thinking> more'
    assert st.carry == ''
    assert not st.open


def test_false_prefix_is_released_on_next_delta():
    events, text, st = feed(['value is <th', 'reshold> here'])
    assert text == 'value is <threshold> here'
    assert events == []
    assert st.carry == ''


def test_pending_prefix_ignores_complete_tag():
    assert _pending_prefix_len('x' + OPEN['thinking'], OPENERS) == 0


def test_pending_prefix_finds_longest_candidate():
    assert _pending_prefix_len('a</thinking-', CLOSERS) == len('</thinking-')


def test_find_first_tag_absent():
    assert _find_first_tag('nothing here', OPENERS) == (-1, None)


def test_find_first_tag_returns_earliest():
    src = 'x' + OPEN['reasoning'] + OPEN['thinking']
    idx, tag = _find_first_tag(src, OPENERS)
    assert idx == 1
    assert tag == OPEN['reasoning']


# --- code-block protection (the diff-corruption case) -------------------

def test_in_code_block_passes_tags_through():
    """A diff or code sample containing these tags is content, not
    structure.  The prior str.replace had no such guard, which is how it
    corrupted diffs of this feature."""
    src = '+    text = replace(' + OPEN['reasoning'] + ')'
    events, text, st = feed([src], in_code_block=True)
    assert events == []
    assert text == src
    assert not st.open


def test_in_code_block_still_closes_an_already_open_block():
    """A fence opened INSIDE reasoning must not trap the scanner."""
    st = InlineThinkingState(open=True)
    events, text = scan('code' + CLOSE['thinking'] + 'after', st,
                        '0ms', in_code_block=True)
    assert reasoning(events) == 'code'
    assert dones(events) == 1
    assert text == 'after'
    assert not st.open


def test_in_code_block_drains_carry():
    st = InlineThinkingState(carry='pre')
    _, text = scan('fix', st, '0ms', in_code_block=True)
    assert text == 'prefix'
    assert st.carry == ''


def test_inline_code_span_mention_is_not_structural():
    src = 'the `' + OPEN['thinking'] + '` tag matters'
    events, text, st = feed([src])
    assert events == []
    assert text == src
    assert not st.open


# --- flush --------------------------------------------------------------

def test_flush_emits_deferred_opener_as_reasoning():
    _, text, st = feed(['answer' + OPEN['thinking'] + 'tail'])
    assert text == 'answer'
    events, residual = flush(st, '0ms')
    assert reasoning(events) == 'tail'
    assert residual == ''
    assert st.open
    assert st.carry == ''


def test_flush_emits_trailing_partial_opener_as_text():
    st = InlineThinkingState()
    scan('answer<thin', st, '0ms')
    assert st.carry == '<thin'
    events, residual = flush(st, '0ms')
    assert events == []
    assert residual == '<thin'
    assert st.carry == ''


def test_flush_is_noop_without_carry():
    assert flush(InlineThinkingState(), '0ms') == ([], '')


def test_flush_inside_open_block_emits_carry_as_reasoning():
    st = InlineThinkingState(open=True, carry='</thin')
    events, residual = flush(st, '0ms')
    assert reasoning(events) == '</thin'
    assert residual == ''


# --- state lifecycle ----------------------------------------------------

def test_unclosed_block_stays_open_for_force_close():
    _, text, st = feed([OPEN['thinking'] + 'never closed'])
    assert st.open
    assert text == ''


def test_fresh_state_does_not_inherit_open_block():
    """Per-iteration construction is what prevents cross-iteration bleed."""
    _, _, st1 = feed([OPEN['thinking'] + 'dangling'])
    assert st1.open
    _, text2, st2 = feed(['plain answer'])
    assert not st2.open
    assert text2 == 'plain answer'


def test_carry_does_not_leak_across_states():
    _, _, st = feed(['tail<thin'])
    assert st.carry == '<thin'
    assert InlineThinkingState().carry == ''


def test_timestamp_is_propagated():
    events, _, _ = feed([OPEN['thinking'] + 'r' + CLOSE['thinking']])
    assert all(e.get('timestamp') == '0ms' for e in events)
