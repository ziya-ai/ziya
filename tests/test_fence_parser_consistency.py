"""Cross-consistency guard between the two live fence parsers.

§1 of the continuation-system handoff asked to collapse "three hand-rolled
fence parsers" into one authority. Characterization showed the tree has
already moved past that premise:

  * Parser A -- app.hallucination.region_extraction -- is the authority
    (open_fence_at / extract_fenced_regions), whole-buffer, width-disciplined.
  * Parser B -- app.hallucination.fake_shell_detector -- is ALREADY migrated
    onto A (it imports extract_fenced_regions); it has no private fence loop.
  * Parser C -- StreamingToolExecutor._update_code_block_tracker -- is the
    only remaining independent parser. It is incremental (fed per-delta on
    the hot streaming path) and drives the `open_fence` continuation branch.

Physically merging C onto A is a NET REGRESSION, not a cleanup, because C
diverges from A in two DELIBERATE, DOCUMENTED ways (see region_extraction.py
and the tracker source):

  D1. Fence grammar. A's _FENCE_RE recognises ``` and ~~~ fences with
      optional leading indentation. C recognises backtick fences only.
  D2. Info-string-with-space suppression. C treats an opener whose info
      string contains a space (e.g. "``` js the thing") as prose and does
      NOT open a block -- a guard added to stop a documented recursive
      continuation-loop incident. A has no such suppression.

So the durable §1 artifact is THIS test, not a merge: it
  (1) asserts A and C AGREE on the common fence grammar,
  (2) pins D1 and D2 as EXPECTED divergences (so an accidental future
      change to either parser fails here and is reviewed on purpose), and
  (3) locks C's production feeding contract: C is only ever handed
      line-complete text (the _block_opening_buffer layer in
      text_delta_processor guarantees this), and under that contract its
      end-state matches A.
"""
import pytest

from app.streaming_tool_executor import StreamingToolExecutor
from app.hallucination.region_extraction import open_fence_at


# --- Parser C harness -------------------------------------------------------

@pytest.fixture
def tracker_fn():
    inst = StreamingToolExecutor.__new__(StreamingToolExecutor)
    return inst._update_code_block_tracker


def _fresh():
    return {'in_block': False, 'block_type': None,
            'fence_transitions': 0, 'accumulated_content': ''}


def _c_open_after(fn, text, *, split_lines=False):
    """Feed *text* to parser C and return its end-of-text in_block bool.

    split_lines mirrors the production contract: text_delta_processor only
    ever forwards line-complete chunks to the tracker (a fence marker is
    never split across a chunk boundary). When split_lines is True we feed
    one complete line at a time to prove chunk-shape independence under that
    contract; when False we feed the whole buffer at once.
    """
    tracker = _fresh()
    if split_lines:
        # Preserve newlines on each piece so line semantics are identical.
        parts = text.splitlines(keepends=True)
        for p in parts:
            fn(p, tracker)
    else:
        fn(text, tracker)
    return bool(tracker['in_block'])


# --- Parser A harness -------------------------------------------------------

def _a_open_after(text):
    """Parser A's open-fence state at end of text -> bool."""
    return open_fence_at(text, len(text)) is not None


# ---------------------------------------------------------------------------
# (1) Common grammar: A and C must AGREE on backtick fences without the
#     two documented divergence triggers.
# ---------------------------------------------------------------------------

# Each case is (text, expected_open_at_end).
_AGREEMENT_CASES = [
    ("no code here at all", False),
    ("```python\nx = 1\n```\n", False),               # opened and closed
    ("```python\nx = 1\n", True),                     # opened, unclosed
    ("```\nbare fence\n```\n", False),
    ("```\nbare fence still open\n", True),
    ("prose\n```js\ncode\n```\nmore prose\n", False),
    ("text before\n```\nopen block trailing\n", True),
    # narrower fence quoted inside a wider one is inert content (both parsers
    # use width discipline: close needs >= opening width)
    ("````\n```\nstill inside wider fence\n", True),
    ("````\n```\n````\n", False),                     # wider fence closed
    ("a `inline` span only\n", False),                # inline never opens block
]


@pytest.mark.parametrize("text,expected", _AGREEMENT_CASES)
def test_A_and_C_agree_on_common_grammar(tracker_fn, text, expected):
    a = _a_open_after(text)
    c = _c_open_after(tracker_fn, text)
    assert a == expected, f"Parser A disagreed with expectation: {text!r}"
    assert c == expected, f"Parser C disagreed with expectation: {text!r}"
    assert a == c, f"Parsers A and C diverged on common-grammar case: {text!r}"


# ---------------------------------------------------------------------------
# (2) Documented divergences. These are asserted AS divergences on purpose.
#     If a future change makes them agree (or flips either side), this test
#     fails and forces a deliberate review rather than silent drift.
# ---------------------------------------------------------------------------

def test_D1_tilde_fence_diverges(tracker_fn):
    """A recognises ~~~ fences; C is backtick-only. Documented divergence."""
    text = "~~~python\nx = 1\n"
    assert _a_open_after(text) is True    # A: tilde fence is open
    assert _c_open_after(tracker_fn, text) is False  # C: ignores tilde


def test_D2_info_string_space_diverges(tracker_fn):
    """C suppresses an opener whose info string contains a space (prose
    guard against a documented recursive-loop incident); A does not."""
    text = "```js the thing that broke\ncontent\n"
    assert _a_open_after(text) is True    # A: it's an open fence
    assert _c_open_after(tracker_fn, text) is False  # C: treated as prose


# ---------------------------------------------------------------------------
# (3) Production feeding contract: under line-complete chunking (what
#     text_delta_processor guarantees), C's end-state is independent of
#     chunk shape and still matches the whole-buffer result.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", _AGREEMENT_CASES)
def test_C_line_complete_chunking_is_invariant(tracker_fn, text, expected):
    whole = _c_open_after(tracker_fn, text, split_lines=False)
    per_line = _c_open_after(tracker_fn, text, split_lines=True)
    assert whole == per_line == expected, (
        f"C not invariant under line-complete chunking: {text!r} "
        f"(whole={whole}, per_line={per_line})"
    )


def test_C_multichunk_open_block_matches_whole(tracker_fn):
    """A block opened in one line-complete chunk and left unclosed stays
    open across chunk boundaries (the invariant the open_fence branch
    relies on)."""
    text = "intro line\n```python\nprint(1)\n"
    assert _c_open_after(tracker_fn, text, split_lines=True) is True
    assert _c_open_after(tracker_fn, text, split_lines=False) is True
