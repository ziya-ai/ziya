"""
Characterization (golden-master) tests for _update_code_block_tracker in
app.streaming_tool_executor.StreamingToolExecutor.

PURPOSE: pin the tracker's CURRENT behavior before making it
inline-vs-block aware (step 2a). The tracker is an incremental line-state
machine called per-delta on the hottest streaming path; it has
historically been unstable, so we capture exact current verdicts —
INCLUDING the known false-positive — and let the step-2a diff show
precisely which cases flip.

KNOWN BUG (captured, not yet fixed here): a prose line that, after
stripping leading whitespace, BEGINS with a triple-backtick fence marker
toggles in_block even when the model is merely quoting the marker in
narrative text. That fabricates an "unclosed block" at stream end and
(pre-step-1) fired a continuation loop.

Tests marked KNOWN_BUG assert the buggy current behavior on purpose;
step 2a flips them and updates the assertion in the same diff.
"""

import pytest

from app.streaming_tool_executor import StreamingToolExecutor


@pytest.fixture
def tracker_fn():
    # _update_code_block_tracker uses only self for logging, so a bare
    # instance (no __init__ side effects needed) suffices to exercise it.
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    return inst._update_code_block_tracker


def _fresh():
    return {'in_block': False, 'block_type': None, 'accumulated_content': ''}


def _feed(fn, tracker, *chunks):
    """Feed text chunks incrementally, returning final (in_block, block_type)."""
    for c in chunks:
        fn(c, tracker)
    return tracker['in_block'], tracker['block_type']


class TestRealFences:
    """Genuine fenced blocks — must track open/close correctly."""

    def test_lang_tagged_open_then_close(self, tracker_fn):
        t = _fresh()
        assert _feed(tracker_fn, t, "```python\n") == (True, 'python')
        assert _feed(tracker_fn, t, "code\n", "```\n") == (False, None)

    def test_bare_fence_open_then_close(self, tracker_fn):
        t = _fresh()
        assert _feed(tracker_fn, t, "```\n") == (True, None)
        assert _feed(tracker_fn, t, "x\n", "```\n") == (False, None)

    def test_diff_block(self, tracker_fn):
        t = _fresh()
        assert _feed(tracker_fn, t, "```diff\n")[1] == 'diff'
        assert _feed(tracker_fn, t, "```\n")[0] is False

    def test_open_block_stays_open_across_deltas(self, tracker_fn):
        t = _fresh()
        _feed(tracker_fn, t, "```python\n")
        # content arriving in fragments must not close it
        assert _feed(tracker_fn, t, "x = ", "1\n", "y = 2\n")[0] is True

    def test_wider_closer_closes(self, tracker_fn):
        # CommonMark: closer must have >= opener backticks.
        t = _fresh()
        _feed(tracker_fn, t, "````python\n")
        assert _feed(tracker_fn, t, "code\n", "````\n")[0] is False

    def test_narrower_fence_inside_wider_is_content(self, tracker_fn):
        # ``` inside a ```` block is content, not a closer.
        t = _fresh()
        _feed(tracker_fn, t, "````\n")
        assert _feed(tracker_fn, t, "```\n")[0] is True  # still open
        assert _feed(tracker_fn, t, "````\n")[0] is False


class TestNotFences:
    """Lines that look fence-ish but should NOT toggle block state."""

    def test_inline_code_span_midline(self, tracker_fn):
        # Backticks mid-line (inline code) never start with ``` after strip,
        # so the tracker already ignores them. Pin that.
        t = _fresh()
        assert _feed(tracker_fn, t, "use the `foo` function here\n") == (False, None)

    def test_triple_backtick_midline_ignored(self, tracker_fn):
        # ``` appearing mid-sentence (not at line start) is ignored today.
        t = _fresh()
        assert _feed(tracker_fn, t, "the marker ``` appears here\n") == (False, None)


class TestKnownBugQuotedFenceAtLineStart:
    """The false-positive that destabilized the continuation loop.

    A prose line that begins (after whitespace strip) with ``` toggles
    in_block today, even though the model is quoting the marker, not
    opening a block. Step 2a will flip these to (False, ...).
    """

    def test_KNOWN_BUG_line_starting_with_quoted_fence(self, tracker_fn):
        t = _fresh()
        # e.g. a sentence that wraps so a line starts with the marker, or a
        # list item whose text begins with it.
        in_block, _ = _feed(tracker_fn, t, "```diff in narrative prose, not a real block\n")
        # AFTER step 2a-narrow: a fence info string containing a space is
        # treated as prose, NOT an opener — so no spurious block opens.
        assert in_block is False

    def test_midline_quoted_fence_is_safe_today(self, tracker_fn):
        t = _fresh()
        # A marker quoted MID-line (not at line start) does NOT toggle
        # in_block — the tracker only acts on lines starting with the
        # fence after strip. This case is already safe; pin it.
        _feed(tracker_fn, t, "Discussing the ```python marker in text\n")
        _feed(tracker_fn, t, "and continuing the sentence normally.\n")
        assert t['in_block'] is False  # mid-line marker never opens a block


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))

"""Regression tests for StreamingToolExecutor._update_code_block_tracker.

The tracker's fence state gates two consequential behaviors at stream end:

1. If ``in_block`` is True at message_stop, the fence continuation loop
   fires a fresh model call with ``suppress_tools=True``.  A model that
   wants to call a tool in that continuation cannot — it emits tool-call
   shaped *text* instead, which is never executed (hallucinated results).
2. While ``in_block`` is True, scannable-text hallucination detection is
   skipped in process_text_delta.

So a tracker that wrongly believes a fence is still open both *causes*
fabrication and *disables* the detector for it.  The historical bug: a
lang-tagged fence line was treated as an opener even when narrower than
the enclosing fence (CommonMark says it is content), so a wide fence
quoting a narrower one (`````` wrapping ```) ended the stream stuck
"in a block".  These tests pin the CommonMark width discipline while
preserving the deliberate implicit-close recovery heuristic.

The tracker method is extracted via AST rather than importing
app.streaming_tool_executor, which has heavy import-time dependencies.
"""
import ast
import re
import textwrap
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "app" / "streaming_tool_executor.py"


class _SilentLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _load_tracker_fn():
    src = _MODULE_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_update_code_block_tracker":
            fn_src = ast.get_source_segment(src, node)
            ns = {"re": re, "logger": _SilentLogger(), "Dict": dict, "Any": object}
            exec(textwrap.dedent(fn_src), ns)
            return ns["_update_code_block_tracker"]
    raise AssertionError("_update_code_block_tracker not found in streaming_tool_executor.py")


@pytest.fixture(scope="module")
def update_tracker():
    return _load_tracker_fn()


def _fresh_tracker():
    return {
        "in_block": False,
        "block_type": None,
        "fence_transitions": 0,
        "backtick_count": 0,
        "accumulated_content": "",
    }


def _run(update_tracker, text):
    tracker = _fresh_tracker()
    update_tracker(None, text, tracker)
    return tracker


class TestNestedNarrowerFences:
    """A fence line narrower than the open fence is content (CommonMark)."""

    def test_wide_fence_quoting_narrower_lang_fence_closes(self, update_tracker):
        # The exact reported failure shape: 6-tick plotly fence quoting a
        # 3-tick plotly block.  Pre-fix: inner opener implicitly closed the
        # outer, inner closer closed the wrong fence, real outer closer
        # re-opened as a bare fence -> in_block=True at stream end.
        text = (
            "``````plotly\n"
            '{"data": []}\n'
            "```plotly\n"
            '{"x": 1}\n'
            "```\n"
            "prose\n"
            "``````\n"
            "after"
        )
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is False
        assert tracker["block_type"] is None

    def test_thinking_fence_quoting_triple_fence_closes(self, update_tracker):
        text = (
            "`````thinking:step-1\n"
            "discussion of fences\n"
            "```python\n"
            "x = 1\n"
            "```\n"
            "`````"
        )
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is False

    def test_bare_wide_fence_quoting_lang_fence_closes(self, update_tracker):
        text = "````\n```python\nx = 1\n```\n````"
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is False

    def test_wide_fence_genuinely_unterminated_stays_open(self, update_tracker):
        # Same shape but no outer closer: tracker must still report open,
        # and with the OUTER fence's identity (not the quoted inner one).
        text = (
            "``````plotly\n"
            '{"data": []}\n'
            "```plotly\n"
            '{"x": 1}\n'
            "```\n"
        )
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is True
        assert tracker["backtick_count"] == 6
        assert tracker["block_type"] == "plotly"


class TestRecoveryHeuristicPreserved:
    """Same-or-wider lang-tagged fence while in a block still implicitly
    closes and reopens — the deliberate recovery for a model omitting the
    closer between two blocks."""

    def test_missing_closer_between_blocks(self, update_tracker):
        text = "```mermaid\ngraph TD\n```vega-lite\n{\"a\": 1}\n```"
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is False

    def test_missing_closer_leaves_second_block_open(self, update_tracker):
        text = "```mermaid\ngraph TD\n```vega-lite\n{\"a\": 1}\n"
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is True
        assert tracker["block_type"] == "vega-lite"


class TestBaseline:
    """Basic open/close behavior unaffected by the width fix."""

    def test_simple_closed_block(self, update_tracker):
        tracker = _run(update_tracker, "```python\nx = 1\n```")
        assert tracker["in_block"] is False

    def test_genuinely_unterminated_block(self, update_tracker):
        tracker = _run(update_tracker, '```plotly\n{"data": [1,')
        assert tracker["in_block"] is True
        assert tracker["block_type"] == "plotly"
        assert tracker["backtick_count"] == 3

    def test_narrow_bare_fence_does_not_close_wide_block(self, update_tracker):
        # Closer-side width discipline (pre-existing behavior).
        text = "````python\nx = 1\n```\n"
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is True
        assert tracker["backtick_count"] == 4

    def test_spaced_info_string_does_not_open_block(self, update_tracker):
        # 2a-narrow: a fence whose info string contains a space is treated
        # as PROSE quoting a fence marker (a wrapped sentence or the model
        # discussing ```diff), NOT a real opener — genuine info strings are
        # single tokens (python, diff, tsx). So no block opens at all, which
        # is strictly stronger than the older "open an untyped block" path:
        # nothing can leak into a continuation prompt because there is no
        # open block.
        #
        # ACCEPTED LIMITATION: this also declines to open a block for the
        # rare attribute syntax some renderers allow, e.g.
        # `lang title="x"`. Not observed in practice; if that pattern is
        # ever needed, narrow the suppression (require an implausible FIRST
        # token, not merely any space) rather than reverting this.
        tracker = _run(update_tracker, "```Acknowledged. I won't fabricate\n")
        assert tracker["in_block"] is False
        assert tracker["block_type"] is None


class TestFenceTransitionsCounter:
    """The monotonic fence_transitions counter (step 0 of block-continue).

    Read across non-prefill continuation rounds as a one-directional
    "fences are churning" reset signal. It must increment on every real
    transition (open / close / implicit close+reopen) and must NOT
    increment on content lines or on the 2a-narrow prose suppression.
    Over-counting only ever extends the stall budget, so generous counting
    is acceptable; under-counting (missing a real transition) is the only
    risk and is what these rows guard against.
    """

    def test_open_increments(self, update_tracker):
        tracker = _run(update_tracker, "```python\n")
        assert tracker["in_block"] is True
        assert tracker["fence_transitions"] == 1

    def test_open_then_close_increments_twice(self, update_tracker):
        tracker = _run(update_tracker, "```python\nx = 1\n```\n")
        assert tracker["in_block"] is False
        assert tracker["fence_transitions"] == 2

    def test_close_then_reopen_counts_each_transition(self, update_tracker):
        # A multi-block round: open A, close A, open B. Three transitions.
        text = "```python\nx = 1\n```\n\n```diff\n+y\n"
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is True
        assert tracker["fence_transitions"] == 3

    def test_content_lines_do_not_increment(self, update_tracker):
        # Open (1 transition) then several content lines — no further bumps.
        text = "```python\na = 1\nb = 2\nc = 3\n"
        tracker = _run(update_tracker, text)
        assert tracker["in_block"] is True
        assert tracker["fence_transitions"] == 1

    def test_prose_suppression_does_not_increment(self, update_tracker):
        # 2a-narrow: a spaced info string opens no block, so it is not a
        # transition and must not bump the counter.
        tracker = _run(update_tracker, "```Acknowledged. I won't fabricate\n")
        assert tracker["in_block"] is False
        assert tracker["fence_transitions"] == 0

    def test_no_fences_no_transitions(self, update_tracker):
        tracker = _run(update_tracker, "just some prose with no fences at all\n")
        assert tracker["fence_transitions"] == 0
