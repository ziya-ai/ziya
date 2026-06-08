"""
Tests for the Layer B structure gate + parroting corroboration.

The structural fake-shell-session detector is too noisy to abort on its
own — tutorial content, multi-line commands, and heredocs all trip the
heuristic.  The gate in process_text_delta drops false positives by:

  1. If the fence body parrots prior real tool output (in-fence
     parroting that Layer A's normal probe misses): abort.
  2. Else, if the streamed text contains blank-line paragraph breaks:
     suppress.  Real tool output never enters the streamed text path
     (it goes through tool_display events), so blank-line breaks here
     mean the model is in 'writing prose' mode.
  3. Else (dense, structureless response): abort.  That is the
     unambiguous fabrication shape.
"""
import time
import uuid

import pytest
from unittest.mock import MagicMock

from app.text_delta_processor import process_text_delta, TextDeltaState
from app.hallucination.shingle_index import (
    register_tool_result,
    clear_session,
)


# ---------------------------------------------------------------------------
# Helpers (duplicated from test_text_delta_processor.py to avoid cross-file
# import coupling — these are tiny mocks that any new test file can recreate).
# ---------------------------------------------------------------------------

def _make_executor():
    """Minimal mock executor for process_text_delta."""
    executor = MagicMock()
    executor._normalize_fence_spacing.side_effect = lambda text, tracker: text
    executor._update_code_block_tracker.return_value = None
    executor._block_opening_buffer = ""
    # Fake-tool-fence accumulator state — process_text_delta uses these
    # to buffer ```tool:NAME``` mimicry blocks.  Set explicitly so the
    # MagicMock doesn't auto-create them as nested mocks (which then
    # fail the `> 0` comparison).
    executor._fake_tool_buffer = ""
    executor._fake_tool_ticks = 0
    optimizer = MagicMock()
    optimizer.add_content.side_effect = lambda t: [t] if t else []
    optimizer.flush_remaining.return_value = ""
    executor._content_optimizer = optimizer
    return executor


def _make_state(**overrides) -> TextDeltaState:
    defaults = dict(
        assistant_text="",
        viz_buffer="",
        in_viz_block=False,
        code_block_tracker={
            'in_block': False, 'block_type': None, 'accumulated_content': ''
        },
        iteration_start_time=0.0,
    )
    defaults.update(overrides)
    return TextDeltaState(**defaults)


def _push_in_chunks(executor, state, text, chunk_size=128):
    """Feed text through process_text_delta in small chunks so the
    256-char probe boundary is naturally crossed mid-stream."""
    for i in range(0, len(text), chunk_size):
        process_text_delta(executor, text[i:i + chunk_size], state)


# ---------------------------------------------------------------------------
# Test data — assembled from parts so triple-backtick fences live in
# variables, not in source-line code-block markers that could be
# misinterpreted by tooling.
# ---------------------------------------------------------------------------

FENCE = "`" * 3
BASH_OPEN = FENCE + "bash"


def _wrap_bash(body: str) -> str:
    """Wrap *body* in a bash-tagged fence."""
    return f"{BASH_OPEN}\n{body}{FENCE}\n"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFakeShellStructureGate:
    """Verifies the three branches of the Layer B abort gate."""

    def test_tutorial_fence_with_paragraph_breaks_does_not_abort(self):
        """Original false positive: bash-tagged tutorial fence in
        markdown prose with paragraph breaks before/after.  No prior
        tool output, so parroting cannot match.  Structure gate must
        suppress abort."""
        conv_id = f"test-tutorial-{uuid.uuid4().hex[:8]}"
        try:
            executor = _make_executor()
            state = _make_state(conversation_id=conv_id)
            body = (
                "# Get your project ID and conversation ID\n"
                'curl -H "X-Project-Root: /path/to/project" \\\n'
                "     http://localhost:6969/api/v1/foo | jq\n"
            )
            tutorial = (
                "Here is how to query the endpoint:\n\n"
                + _wrap_bash(body)
                + "\nYou should see a JSON response.\n"
            )
            _push_in_chunks(executor, state, tutorial)
            assert not state.hallucination_detected, (
                "Tutorial fence with paragraph-break structure triggered abort"
            )
        finally:
            clear_session(conv_id)

    def test_dense_fabrication_without_structure_aborts(self):
        """Pure dense fabrication: shell-shape output with no paragraph
        breaks anywhere.  Structure gate recognizes this as the
        unambiguous fabrication shape and aborts."""
        conv_id = f"test-dense-{uuid.uuid4().hex[:8]}"
        try:
            executor = _make_executor()
            state = _make_state(conversation_id=conv_id)
            body = (
                "$ ls /var/log\n"
                "auth.log\n"
                "syslog\n"
                "kern.log\n"
                "messages\n"
            )
            dense = "Let me check.\n" + _wrap_bash(body)
            padding = "x" * 240
            _push_in_chunks(executor, state, padding + dense)
            assert state.hallucination_detected, (
                "Dense unstructured fabrication did not trigger abort"
            )
        finally:
            clear_session(conv_id)

    def test_paragraph_breaks_inside_body_alone_suppress_abort(self):
        """Even with no prose around the fence, a body containing a
        blank line between command sections is multi-section content,
        not dense fabrication.  The blank line propagates into the
        streamed text, and the structure gate suppresses."""
        conv_id = f"test-body-breaks-{uuid.uuid4().hex[:8]}"
        try:
            executor = _make_executor()
            state = _make_state(conversation_id=conv_id)
            body = (
                "# Set up the environment\n"
                "export FOO=bar\n"
                "\n"
                "# Run the command\n"
                "$ curl example.com\n"
                "Some output here\n"
                "More output\n"
            )
            structured = _wrap_bash(body)
            padding = "x" * 240
            _push_in_chunks(executor, state, padding + structured)
            assert not state.hallucination_detected, (
                "Body with section-break structure triggered abort"
            )
        finally:
            clear_session(conv_id)

    def test_parroting_inside_fence_aborts_even_with_structure(self):
        """Even when the response has paragraph breaks (would normally
        suppress), the parroting check fires first when the fence body
        fingerprint-matches a prior real tool result.  Reproducing
        real prior output is deterministically bad."""
        conv_id = f"test-parrot-{uuid.uuid4().hex[:8]}"
        prior_result = (
            "$ find . -name 'fake_shell_detector.py'\n"
            "./app/hallucination/fake_shell_detector.py\n"
            "./tests/test_fake_shell_detector.py\n"
            "./build/cache/fake_shell_detector.py.cache\n"
            "./docs/internal/fake_shell_detector_design.md\n"
        )
        register_tool_result(
            conv_id, "tool-prior-001", "run_shell_command", prior_result
        )
        try:
            executor = _make_executor()
            state = _make_state(
                conversation_id=conv_id,
                iteration_start_time=time.time() + 1.0,
            )
            response = (
                "I already know what that returns:\n\n"
                + _wrap_bash(prior_result)
                + "\nSo the file is in the expected location.\n"
            )
            _push_in_chunks(executor, state, response)
            assert state.hallucination_detected, (
                "Parroted prior tool output inside structured response was "
                "suppressed"
            )
            assert state.parrot_match is not None
            assert state.parrot_match['tool_name'] == 'run_shell_command'
        finally:
            clear_session(conv_id)
