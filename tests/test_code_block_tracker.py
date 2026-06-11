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

    def test_implausible_lang_tag_treated_as_untyped(self, update_tracker):
        # Prose after backticks must not become block_type (it gets
        # interpolated into the continuation prompt).
        tracker = _run(update_tracker, "```Acknowledged. I won't fabricate\n")
        assert tracker["in_block"] is True
        assert tracker["block_type"] is None
