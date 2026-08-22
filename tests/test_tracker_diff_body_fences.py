"""A fence inside a diff body must not wedge the code-block tracker open.

Reproduces the loop in which `block_continue` fired repeatedly against a
COMPLETE response. Two compounding faults:

  1. `line.strip()` discarded the diff line prefix, so a context line holding
     a fence ('   ```html-mockup') read as a real fence.
  2. That fence closed the diff early, so the diff's own closer had nothing to
     close and opened a phantom bare block — leaving in_block=True at stream
     end, which the decider treats as objective evidence of truncation.

Restores the invariant that frontend/src/components/fenceScanner.ts already
documents for the same reason (see matchFenceClose's diff-scoped exception).
"""
import pytest

from app.streaming_tool_executor import StreamingToolExecutor
from app.text_delta_processor import _FENCE_BEARING_BLOCK_TYPES


def _track(text):
    """Drive the real tracker; returns the resulting state dict."""
    tracker = {
        'in_block': False, 'block_type': None, 'backtick_count': 0,
        'accumulated_content': '', 'fence_transitions': 0, 'fence_indent': 0,
    }
    # Unbound call: the method does not touch self, but the signature is
    # (self, text, tracker) — verified, not assumed.
    StreamingToolExecutor._update_code_block_tracker(None, text, tracker)
    return tracker


THE_BUG = (
    "Fix:\n\n"
    "```diff\n"
    "--- a/app/agents/prompts.py\n"
    "+++ b/app/agents/prompts.py\n"
    "@@ -1,2 +1,3 @@\n"
    " - Example syntax:\n"
    "   ```html-mockup\n"
    "   <div>x</div>\n"
    "+  <p>y</p>\n"
    "   ```\n"
    "```\n\n"
    "Done."
)


def test_the_bug_tracker_ends_closed():
    assert _track(THE_BUG)['in_block'] is False


def test_the_bug_no_phantom_block_type():
    # The phantom was an untyped bare block; assert we never land in one.
    t = _track(THE_BUG)
    assert t['block_type'] is None and t['in_block'] is False


def test_diff_stays_active_while_body_is_consumed():
    head = THE_BUG.split("   ```html-mockup")[0] + "   ```html-mockup\n"
    t = _track(head)
    assert t['in_block'] is True
    assert t['block_type'] == 'diff'


@pytest.mark.parametrize("prefix", ["   ", " ", "  "])
def test_indented_fence_in_diff_body_is_content(prefix):
    text = f"```diff\n@@ -1 +1 @@\n{prefix}```python\n{prefix}```\n```\n"
    assert _track(text)['in_block'] is False


def test_column0_removed_added_fences_still_ignored():
    # '-'/'+' prefixed fences never reached column 0 even before the fix.
    text = "```diff\n--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-```py\n+```python\n```\n"
    assert _track(text)['in_block'] is False


def test_two_sequential_diffs():
    text = "```diff\n-a\n+b\n```\n\ntext\n\n```diff\n-c\n+d\n```\n"
    assert _track(text)['in_block'] is False


def test_atomic_recovery_heuristic_preserved():
    # A missing closer between two diagram blocks must still be recovered —
    # that is why the implicit close/reopen heuristic exists.
    assert _track("```mermaid\ngraph TD\nA-->B\n```vega-lite\n{}\n```\n")['in_block'] is False


def test_genuinely_open_diff_still_detected():
    # Real truncation must keep reporting an open block, or this fix would
    # defeat the mechanism it repairs.
    assert _track("```diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")['in_block'] is True


def test_genuinely_open_diagram_still_detected():
    assert _track("```mermaid\ngraph TD\nA-->B\n")['in_block'] is True


def test_over_indented_fence_is_not_a_fence():
    assert _track("    ```python\nx = 1\n")['in_block'] is False


def test_three_space_indent_is_a_fence():
    assert _track("   ```python\nx = 1\n")['in_block'] is True


def test_wide_fence_quoting_narrow_still_guarded():
    assert _track("``````plotly\n```plotly\n{}\n```\n``````\n")['in_block'] is False


def test_markdown_is_not_fence_bearing():
    # Intentional: markdown bodies carry column-0 fences that no parser can
    # disambiguate, so they rely on the wider-outer-fence guard instead.
    assert 'markdown' not in _FENCE_BEARING_BLOCK_TYPES
    assert 'diff' in _FENCE_BEARING_BLOCK_TYPES


# --- variant-modifier fences -------------------------------------------------
# The prose-suppression heuristic ("a space means prose") predates variant
# modifiers. Once ```html-mockup figure became legitimate, that heuristic
# dropped the opener AND turned the real closer into a phantom opener, while
# hiding genuine truncation of the same block.


def test_closed_figure_fence_ends_closed():
    assert _track("```html-mockup figure\n<div>x</div>\n```\n")['in_block'] is False


def test_open_figure_fence_is_detected():
    assert _track("```html-mockup figure\n<div>x</div>\n")['in_block'] is True


def test_figure_fence_records_base_language():
    t = _track("```html-mockup figure\n<div>x</div>\n")
    assert t['block_type'] == 'html-mockup'


@pytest.mark.parametrize("info", [
    "html-mockup figure", "ui-mockup inline", "mockup bare",
    "html-mockup   figure", "HTML-Mockup FIGURE",
])
def test_every_modifier_spelling_opens_and_closes(info):
    assert _track(f"```{info}\n<div>x</div>\n```\n")['in_block'] is False
    assert _track(f"```{info}\n<div>x</div>\n")['in_block'] is True


def test_prose_with_spaces_is_still_suppressed():
    # The heuristic's original purpose must survive: a wrapped sentence
    # beginning with a fence marker is not an opener.
    t = _track("```Acknowledged. I won't fabricate that\nmore prose\n")
    assert t['in_block'] is False


def test_unknown_lang_with_modifier_is_still_suppressed():
    # Only recognised base languages are exempted; anything else stays prose.
    assert _track("```notalang figure\nbody\n")['in_block'] is False
