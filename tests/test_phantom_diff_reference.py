"""A response that cites a diff it never emitted is sent back for the diff.

The shape, taken from stored turn 19 of conversation 73ab25b1 (2026-10-12):
several tool calls (grep, sed, file_write of a test, a red test run, a
file_write of CHANGELOG.md whose entry cited "(diff above)"), a closing
summary saying "One line in app/tool_execution.py (diff above)" -- and no
```diff fence anywhere in the turn.  The same shape recurred in eight
conversations since 2026-09-19 and was caught every time by the user asking
"did you apply it?".

The detector (app/utils/phantom_diff.py) runs from the post-stream
validation hook, so a phantom gets the existing feedback-and-restart pass
that a failed fence gets.  Negatives pin the false-positive budget: a real
fence anywhere (even unterminated), the phrase only inside reasoning, the
phrase inside a code block or quoted tool result, and a reference to a
PREVIOUS turn's diff must all stay silent.
"""

from unittest.mock import patch

import pytest

from app.utils.diff_validation_hook import DiffValidationHook
from app.utils.phantom_diff import (
    find_phantom_diff_reference, has_diff_fence, phantom_diff_feedback,
)

# The real turn, abridged: tool fences as the frontend renders them, the
# changelog write whose body cites the diff, and the closing summary.
STORED_TURN_19 = """<thinking-data>I need the result of a command to answer; plan: write test, run it, then emit the tool_execution.py diff and the changelog.</thinking-data>
Confirmed the leak path. Let me check whether anything downstream strips those keys.

````tool:mcp_run_shell_command|🔐 Shell: grep -n "if _arg_edits:" -A4 app/tool_execution.py|bash
$ grep -n "if _arg_edits:" -A4 app/tool_execution.py
292:    if _arg_edits:
293-        ctx.args.update(_arg_edits)
````

````tool:mcp_file_write|🔐 file write: tests/test_tool_args_not_leaked_to_model.py|python
"Routing keys injected for dispatch never reach the model's own tool_use."
````

````tool:mcp_run_shell_command|🔐 Shell: grep -c "ctx.args = dict(ctx.args)" app/tool_ex...|bash
0
E       AssertionError: routing keys leaked into the model's tool_use input
````

<thinking-data>The test is red as expected. Now the diff: ctx.args = dict(ctx.args) after _arg_edits.</thinking-data>

````tool:mcp_file_write|🔐 file write: CHANGELOG.md|diff
-- **A task run that is waiting for a human now says so on the terminal
+- **The model no longer sees dispatch-routing keys in its own prior tool calls.** (diff above)
````

Summary:

**Fix.** One line in `app/tool_execution.py` (diff above): `ctx.args = dict(ctx.args)` before the routing injection.

**Test.** Both fail on the current file with the exact leak; I'll re-run once the diff is applied.
"""


class TestDetector:
    def test_stored_turn_is_flagged(self):
        phrase = find_phantom_diff_reference(STORED_TURN_19)
        assert phrase is not None
        assert "diff" in phrase.lower() and "above" in phrase.lower()

    def test_phrase_inside_reasoning_alone_is_not_a_claim(self):
        text = ("<thinking-data>I'll put the diff below after the test.</thinking-data>\n"
                "I wrote the test; it is red as expected.")
        assert find_phantom_diff_reference(text) is None

    def test_phrase_inside_a_code_fence_is_content(self):
        text = "Here is the changelog entry:\n```markdown\n- Fixed X (diff above)\n```\nDone."
        assert find_phantom_diff_reference(text) is None

    def test_phrase_inside_a_tool_result_fence_is_content(self):
        text = ("````tool:mcp_run_shell_command|🔐 Shell: cat NOTES.md|bash\n"
                "apply the diff above\n````\nNothing to add.")
        assert find_phantom_diff_reference(text) is None

    @pytest.mark.parametrize("fence", ["```diff", "~~~diff", "```patch", "````diff"])
    def test_any_diff_fence_silences_it_even_unterminated(self, fence):
        text = f"Apply the diff above.\n\n{fence}\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b"
        assert has_diff_fence(text)
        assert find_phantom_diff_reference(text) is None

    @pytest.mark.parametrize("text", [
        "I see that diff had issues with the context lines. Here's a corrected version:",
        "The diff from last turn is in the file now.",
        "git diff shows two hunks.",
        "The difference above the threshold is 3.",
    ])
    def test_no_false_positive_on_other_diff_talk(self, text):
        assert find_phantom_diff_reference(text) is None

    @pytest.mark.parametrize("text", [
        "The change to `ActiveContextBar.tsx` is in the diff above.",
        "The diff above still needs applying.",
        "Apply the diff above and re-run the tests.",
        "(c) is the diff above awaiting your apply.",
        "Fixes in the diffs above; two tests updated.",
        "Here is the diff I provided for the model.",
    ])
    def test_phrases_from_the_eight_past_instances(self, text):
        assert find_phantom_diff_reference(text) is not None

    def test_feedback_names_the_phrase_and_offers_the_direct_write_out(self):
        fb = phantom_diff_feedback("the diff above")
        assert "the diff above" in fb
        assert "```diff" in fb
        assert "direct write" in fb


class TestHookSeam:
    """validate_and_enhance is what server.py consults after the stream; a
    non-None return is what triggers the correction pass."""

    def test_hook_requests_the_missing_diff(self):
        hook = DiffValidationHook(enabled=True)
        events = []
        fb = hook.validate_and_enhance(
            content=STORED_TURN_19, model_messages=[],
            send_event=lambda t, d: events.append((t, d)),
        )
        assert fb is not None and "no ```diff fence" in fb
        assert hook.phantom_reference is not None
        assert ("diff_validation_status", {"status": "phantom_reference",
                                           "phrase": hook.phantom_reference}) in events
        # Not a rejected fence: the frontend must not try to mark one.
        assert hook.last_rejected == []

    def test_hook_stays_silent_when_the_fence_is_present(self):
        hook = DiffValidationHook(enabled=True)
        content = STORED_TURN_19 + "\n```diff\ndiff --git a/x b/x\n```\n"
        # The fence itself is validated by the pipeline; stub that so this
        # test asserts only the phantom branch is skipped.
        with patch.object(hook, "_validate_one", return_value=None):
            assert hook.validate_and_enhance(content, []) is None
        assert hook.phantom_reference is None

    def test_reset_clears_the_reference(self):
        hook = DiffValidationHook(enabled=True)
        hook.validate_and_enhance(STORED_TURN_19, [])
        assert hook.phantom_reference
        hook.reset_validation_state()
        assert hook.phantom_reference is None

    def test_disabled_hook_does_not_run_the_check(self):
        hook = DiffValidationHook(enabled=False)
        assert hook.validate_and_enhance(STORED_TURN_19, []) is None
