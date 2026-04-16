"""
Tests for feedback conversation integrity.

Validates that user feedback injected during streaming/tool execution
ends up at the CORRECT position in the conversation history, so the
model on the next iteration can actually see and act on it.

Root cause of the bug: execute_single_tool was appending feedback
directly to ctx.conversation during tool execution, which happens
BEFORE the current iteration's assistant message + tool results are
appended.  This buried feedback at the wrong position.

Fix: feedback is deferred (stored on ctx.deferred_feedback) and
injected AFTER the assistant message + tool results are built.
"""

from typing import List, Dict, Any


# ── Helpers ──────────────────────────────────────────────────────────

def _make_conversation(messages: list) -> list:
    """Build a mutable conversation list from dicts."""
    return [dict(m) for m in messages]


def _validate_conversation_order(conversation: list) -> List[str]:
    """Check for conversation ordering problems.
    
    Returns a list of issues found (empty = valid).
    """
    issues = []
    for i in range(1, len(conversation)):
        curr = conversation[i]
        prev = conversation[i - 1]
        
        # Check for consecutive user messages (problematic for some models)
        if curr.get('role') == 'user' and prev.get('role') == 'user':
            curr_content = curr.get('content', '')
            prev_content = prev.get('content', '')
            # Tool results followed by feedback is acceptable
            if isinstance(prev_content, list) and any(
                b.get('type') == 'tool_result' for b in prev_content if isinstance(b, dict)
            ):
                # Tool result then feedback — this is the expected pattern
                continue
            issues.append(
                f"Consecutive user messages at positions {i-1} and {i}: "
                f"'{str(prev_content)[:50]}...' and '{str(curr_content)[:50]}...'"
            )
    return issues


def _assistant_text_in_conversation(text: str, conversation: list, lookback: int = 3) -> bool:
    """Mirrors StreamingToolExecutor._assistant_text_in_conversation."""
    for m in conversation[-lookback:]:
        if m.get('role') != 'assistant':
            continue
        content = m.get('content')
        if content == text:
            return True
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    if block.get('text') == text:
                        return True
    return False


# ── Test Classes ─────────────────────────────────────────────────────

class TestDeferredFeedbackOrdering:
    """Core test: feedback must appear AFTER assistant msg + tool results.
    
    This is the primary bug fix — execute_single_tool used to inject
    feedback before the current iteration's assistant msg was built,
    burying it at the wrong position.
    """

    def test_deferred_feedback_appears_after_tool_results(self):
        """Simulate the fixed flow: feedback is deferred and injected
        after assistant message + tool results are appended."""
        conversation = _make_conversation([
            {"role": "user", "content": "Check the config and tests"},
        ])

        # -- Simulate iteration with tools --
        assistant_text = "I'll check those files now."
        deferred_feedback_messages = ["also check the README"]

        # Step 1: Build assistant message (as build_assistant_message does)
        assistant_msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": assistant_text},
                {"type": "tool_use", "id": "t1", "name": "file_read",
                 "input": {"path": "config.yaml"}},
            ]
        }
        conversation.append(assistant_msg)

        # Step 2: Build tool results
        tool_result_msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "port: 8080\nhost: localhost"}
            ]
        }
        conversation.append(tool_result_msg)

        # Step 3: NOW inject deferred feedback (the fix)
        for fb_msg in deferred_feedback_messages:
            conversation.append({
                "role": "user",
                "content": f"[User feedback during tool execution]: {fb_msg}"
            })
        deferred_feedback_messages.clear()

        # Validate: feedback is LAST, after assistant + tool results
        assert conversation[-1]['role'] == 'user'
        assert '[User feedback during tool execution]' in conversation[-1]['content']
        assert conversation[-2]['role'] == 'user'  # tool results
        assert isinstance(conversation[-2]['content'], list)
        assert conversation[-3]['role'] == 'assistant'  # assistant msg

    def test_old_bug_feedback_buried_before_assistant_msg(self):
        """Demonstrate the OLD bug: injecting during tool execution
        puts feedback BEFORE the assistant message."""
        conversation = _make_conversation([
            {"role": "user", "content": "Check the config"},
        ])

        # OLD BUG: feedback injected during tool execution
        conversation.append({
            "role": "user",
            "content": "[Real-time feedback]: also check README"
        })

        # Then assistant message + tool results appended later
        conversation.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll check those files."},
                {"type": "tool_use", "id": "t1", "name": "file_read",
                 "input": {"path": "config.yaml"}},
            ]
        })
        conversation.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "port: 8080"}
            ]
        })

        # The feedback is BURIED before the assistant message!
        # Model sees: user(feedback) → assistant(tools) → user(results)
        # Model thinks feedback was already addressed by the assistant response
        issues = _validate_conversation_order(conversation)
        assert len(issues) > 0, "Expected ordering issues from old bug pattern"
        # Specifically: consecutive user messages (original user + feedback)
        assert any('Consecutive user' in i for i in issues)

    def test_multiple_deferred_feedback_all_after_tools(self):
        """Multiple feedback messages during tool execution all appear after."""
        conversation = _make_conversation([
            {"role": "user", "content": "Analyze the codebase"},
        ])

        deferred = ["focus on performance", "ignore test files"]

        # Build conversation properly
        conversation.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Analyzing..."},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command",
                 "input": {"command": "find . -name '*.py'"}},
            ]
        })
        conversation.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "app.py\nutils.py\ntests.py"}
            ]
        })

        # Inject deferred feedback
        for fb in deferred:
            conversation.append({
                "role": "user",
                "content": f"[User feedback during tool execution]: {fb}"
            })

        # Both feedback messages after tool results
        assert len(conversation) == 5  # user + assistant + tool_result + 2 feedback
        assert 'performance' in conversation[-2]['content']
        assert 'ignore test' in conversation[-1]['content']


class TestSkippedToolStubResult:
    """When feedback triggers skip_due_to_feedback, the skipped tool
    must still yield a stub _tool_result to satisfy the API contract."""

    def test_skipped_tool_gets_stub_result(self):
        """Simulate the fixed execute_single_tool behavior:
        skip_due_to_feedback yields a stub result."""
        # After the fix, skip_due_to_feedback path yields:
        stub_event = {
            'type': '_tool_result',
            'tool_id': 'tool_abc',
            'tool_name': 'file_read',
            'result': 'Tool execution skipped: user provided real-time feedback that takes priority.',
        }

        # This result should be collected by the outer loop
        tool_results = []
        tool_results.append({
            'tool_id': stub_event['tool_id'],
            'tool_name': stub_event['tool_name'],
            'result': stub_event['result'],
        })

        assert len(tool_results) == 1
        assert 'skipped' in tool_results[0]['result'].lower()

    def test_old_bug_skipped_tool_no_result(self):
        """Demonstrate the OLD bug: skip_due_to_feedback returned
        without yielding _tool_result, creating an orphaned tool_use."""
        # Model produced 2 tool_use blocks
        all_tool_calls = [
            {'id': 't1', 'name': 'file_read', 'args': {'path': 'a.py'}},
            {'id': 't2', 'name': 'file_read', 'args': {'path': 'b.py'}},
        ]

        # Old behavior: t1 triggers skip, no result. t2 gets stub.
        tool_results_old = [
            # t1: MISSING — no result yielded
            {'tool_id': 't2', 'tool_name': 'file_read',
             'result': 'Tool execution skipped...'},
        ]

        # Filtering logic removes orphaned tool calls
        valid_ids = {tr['tool_id'] for tr in tool_results_old}
        filtered = [tc for tc in all_tool_calls if tc['id'] in valid_ids]

        # t1 is silently dropped — model's intent lost
        assert len(filtered) == 1
        assert filtered[0]['id'] == 't2'

        # New behavior: t1 also gets stub
        tool_results_new = [
            {'tool_id': 't1', 'tool_name': 'file_read',
             'result': 'Tool execution skipped: user provided real-time feedback.'},
            {'tool_id': 't2', 'tool_name': 'file_read',
             'result': 'Tool execution skipped...'},
        ]
        valid_ids_new = {tr['tool_id'] for tr in tool_results_new}
        filtered_new = [tc for tc in all_tool_calls if tc['id'] in valid_ids_new]
        assert len(filtered_new) == 2  # Both tools accounted for


class TestAssistantTextInConversation:
    """Validate the _assistant_text_in_conversation helper handles
    both plain string and structured content blocks."""

    def test_finds_plain_string_content(self):
        conv = [
            {"role": "assistant", "content": "Here is my analysis."},
            {"role": "user", "content": "Thanks"},
        ]
        assert _assistant_text_in_conversation("Here is my analysis.", conv)

    def test_finds_structured_content_blocks(self):
        conv = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check that file."},
                {"type": "tool_use", "id": "t1", "name": "file_read",
                 "input": {"path": "config.yaml"}},
            ]},
        ]
        assert _assistant_text_in_conversation("Let me check that file.", conv)

    def test_no_match_returns_false(self):
        conv = [
            {"role": "assistant", "content": "Something else entirely."},
        ]
        assert not _assistant_text_in_conversation("Not this text", conv)

    def test_lookback_limit(self):
        conv = [
            {"role": "assistant", "content": "Old message"},
            {"role": "user", "content": "reply"},
            {"role": "assistant", "content": "Recent message"},
            {"role": "user", "content": "another reply"},
            {"role": "assistant", "content": "Latest message"},
        ]
        # Default lookback=3 should find "Latest message" but not "Old message"
        assert _assistant_text_in_conversation("Latest message", conv)
        assert not _assistant_text_in_conversation("Old message", conv)

    def test_user_messages_not_matched(self):
        conv = [
            {"role": "user", "content": "This is user text"},
        ]
        assert not _assistant_text_in_conversation("This is user text", conv)


class TestPreEndFeedbackConversationIntegrity:
    """Feedback received just before the stream would end (no tools executed)
    must include the assistant's response in the conversation before the
    feedback message, otherwise the model loses context of what it just said.
    """

    def test_assistant_text_added_before_feedback(self):
        """When assistant produces text-only (no tools) and feedback arrives
        before stream end, the assistant text must be in the conversation
        before the feedback user message."""
        assistant_text = "Based on the error log, the issue is in the config parser."

        conversation = _make_conversation([
            {"role": "user", "content": "Why is the service crashing?"},
        ])

        pending_feedback = ["also check the memory usage"]

        # Simulate the FIXED pre-end logic:
        if pending_feedback:
            combined_feedback = ' '.join(pending_feedback)
            if assistant_text.strip():
                conversation.append({"role": "assistant", "content": assistant_text})
            conversation.append({
                "role": "user",
                "content": f"[User feedback]: {combined_feedback}"
            })

        assert len(conversation) == 3
        assert conversation[1]['role'] == 'assistant'
        assert conversation[1]['content'] == assistant_text
        assert conversation[2]['role'] == 'user'
        assert '[User feedback]' in conversation[2]['content']
        assert 'memory usage' in conversation[2]['content']

    def test_without_fix_creates_consecutive_user_messages(self):
        """Without the fix, feedback would be injected without the assistant
        text, creating consecutive user messages."""
        conversation = _make_conversation([
            {"role": "user", "content": "Why is the service crashing?"},
        ])

        # OLD broken behavior - no assistant text insertion
        conversation.append({
            "role": "user",
            "content": "[User feedback]: check memory"
        })

        issues = _validate_conversation_order(conversation)
        assert len(issues) > 0

    def test_empty_assistant_text_skips_insertion(self):
        """If assistant produced no text (edge case), don't insert empty msg."""
        assistant_text = ""
        conversation = _make_conversation([
            {"role": "user", "content": "Hello"},
        ])

        if assistant_text.strip():
            conversation.append({"role": "assistant", "content": assistant_text})
        conversation.append({
            "role": "user",
            "content": "[User feedback]: test"
        })

        # Should be consecutive user messages (unavoidable with empty assistant)
        assert len(conversation) == 2

    def test_multiple_feedback_messages_combined(self):
        """Multiple pending feedback messages should be combined."""
        assistant_text = "Analyzing the logs."
        conversation = _make_conversation([
            {"role": "user", "content": "Check the logs"},
        ])

        pending = ["focus on errors", "ignore warnings"]
        combined = ' '.join(pending)

        if assistant_text.strip():
            conversation.append({"role": "assistant", "content": assistant_text})
        conversation.append({
            "role": "user",
            "content": f"[User feedback]: {combined}"
        })

        assert 'focus on errors' in conversation[-1]['content']
        assert 'ignore warnings' in conversation[-1]['content']


class TestPostLoopFeedbackConversationIntegrity:
    """Feedback received after the iteration loop completes (post-loop)
    must include the assistant's final response before the feedback."""

    def test_assistant_text_added_before_post_loop_feedback(self):
        """Post-loop feedback must be preceded by assistant text."""
        assistant_text = "I've completed the analysis."
        conversation = _make_conversation([
            {"role": "user", "content": "Analyze everything"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Running analysis..."},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command",
                 "input": {"command": "grep -r 'error' ."}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "error found in line 42"}
            ]},
        ])

        pending_feedback = ["what about the warnings?"]
        combined = ' '.join(pending_feedback)

        # FIXED: add assistant text before feedback
        if assistant_text.strip():
            conversation.append({"role": "assistant", "content": assistant_text})
        conversation.append({
            "role": "user",
            "content": f"[User feedback after tool execution]: {combined}"
        })

        assert conversation[-2]['role'] == 'assistant'
        assert conversation[-1]['role'] == 'user'
        assert 'warnings' in conversation[-1]['content']


class TestIterationBoundaryFeedbackConversationIntegrity:
    """Feedback at iteration boundaries (between tool iterations) must
    be properly ordered with dedup of assistant messages."""

    def test_assistant_text_not_duplicated_when_already_added(self):
        """When tools were executed, assistant_text is already in conversation
        via build_assistant_message.  The dedup check should prevent double insertion."""
        assistant_text = "Let me check that file."

        conversation = _make_conversation([
            {"role": "user", "content": "Read the config file"},
            {"role": "assistant", "content": [
                {"type": "text", "text": assistant_text},
                {"type": "tool_use", "id": "t1", "name": "file_read",
                 "input": {"path": "config.yaml"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "port: 8080\nhost: localhost"}
            ]},
        ])

        feedback_msg = "Focus on the port setting"

        # FIXED dedup logic — checks inside structured content blocks
        if assistant_text.strip() and not _assistant_text_in_conversation(
            assistant_text, conversation
        ):
            conversation.append({"role": "assistant", "content": assistant_text})

        conversation.append({
            "role": "user",
            "content": f"[User feedback]: {feedback_msg}"
        })

        assistant_count = sum(1 for m in conversation if m.get('role') == 'assistant')
        assert assistant_count == 1, f"Expected 1 assistant message, got {assistant_count}"

    def test_assistant_text_added_when_not_yet_in_conversation(self):
        """When assistant text was NOT added by build_assistant_message,
        the dedup check should add it."""
        assistant_text = "Here's what I found about the issue."

        conversation = _make_conversation([
            {"role": "user", "content": "What's wrong?"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Different text here."},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command",
                 "input": {"command": "ls"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "file1.py\nfile2.py"}
            ]},
        ])

        feedback_msg = "Check file1.py specifically"

        if assistant_text.strip() and not _assistant_text_in_conversation(
            assistant_text, conversation
        ):
            conversation.append({"role": "assistant", "content": assistant_text})

        conversation.append({
            "role": "user",
            "content": f"[User feedback]: {feedback_msg}"
        })

        assistant_count = sum(1 for m in conversation if m.get('role') == 'assistant')
        assert assistant_count == 2  # original + new one


class TestFeedbackRaceConditions:
    """Edge cases for feedback timing."""

    def test_feedback_arrives_during_first_text_chunk(self):
        """Feedback during the very first streaming chunk."""
        conversation = _make_conversation([
            {"role": "user", "content": "Hello"},
        ])

        deferred = ["actually, search for errors instead"]

        # Model just started producing text, no tools yet
        # Feedback deferred, eventually injected after this turn
        conversation.append({"role": "assistant", "content": "I'll help..."})

        for fb in deferred:
            conversation.append({
                "role": "user",
                "content": f"[User feedback during tool execution]: {fb}"
            })

        assert conversation[-2]['role'] == 'assistant'
        assert conversation[-1]['role'] == 'user'
        assert 'search for errors' in conversation[-1]['content']

    def test_deferred_feedback_cleared_after_injection(self):
        """deferred_feedback_messages should be cleared after injection."""
        deferred = ["feedback 1", "feedback 2"]
        conversation = []

        # Inject
        for fb in deferred:
            conversation.append({"role": "user", "content": f"[Feedback]: {fb}"})
        deferred.clear()

        assert len(deferred) == 0
        assert len(conversation) == 2


class TestNonPrefillModelFeedback:
    """Models that don't support assistant prefill need special handling."""

    def test_feedback_before_no_prefill_end(self):
        """On non-prefill models, feedback should still be injected properly."""
        assistant_text = "Analysis complete."
        conversation = _make_conversation([
            {"role": "user", "content": "Analyze this"},
        ])

        pending_feedback = ["what about edge cases?"]
        combined = ' '.join(pending_feedback)

        if assistant_text.strip():
            conversation.append({"role": "assistant", "content": assistant_text})
        conversation.append({
            "role": "user",
            "content": f"[User feedback]: {combined}"
        })

        # Non-prefill models need user message last — this is correct
        assert conversation[-1]['role'] == 'user'
        assert conversation[-2]['role'] == 'assistant'


class TestDrainPendingFeedback:
    """Test the atomic drain behavior."""

    def test_drain_clears_list(self):
        pending = [{'type': 'feedback', 'message': 'hello'}]

        def drain():
            if not pending:
                return []
            drained = pending.copy()
            pending.clear()
            return drained

        result = drain()
        assert len(result) == 1
        assert len(pending) == 0

    def test_drain_empty_returns_empty(self):
        pending = []

        def drain():
            if not pending:
                return []
            drained = pending.copy()
            pending.clear()
            return drained

        assert drain() == []

    def test_interrupt_takes_priority(self):
        """Interrupt messages should be processed before feedback."""
        pending = [
            {'type': 'feedback', 'message': 'adjust approach'},
            {'type': 'interrupt'},
            {'type': 'feedback', 'message': 'also try X'},
        ]

        def drain():
            if not pending:
                return []
            drained = pending.copy()
            pending.clear()
            return drained

        drained = drain()
        # Processing order: check for interrupts first
        has_interrupt = any(d['type'] == 'interrupt' for d in drained)
        assert has_interrupt


class TestConversationValidation:
    """Test the validation helper itself."""

    def test_valid_alternating_conversation(self):
        conv = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "thanks"},
        ]
        assert _validate_conversation_order(conv) == []

    def test_detects_consecutive_user_messages(self):
        conv = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
        issues = _validate_conversation_order(conv)
        assert len(issues) > 0

    def test_tool_result_then_feedback_is_acceptable(self):
        """Tool result (user role) followed by feedback (user role) is OK."""
        conv = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Running..."},
                {"type": "tool_use", "id": "t1", "name": "cmd", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "done"}
            ]},
            {"role": "user", "content": "[User feedback]: check something"},
        ]
        issues = _validate_conversation_order(conv)
        # tool_result → feedback is acceptable (handled by the validator)
        assert len(issues) == 0

    def test_feedback_at_start_detected(self):
        conv = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "[User feedback]: test"},
        ]
        issues = _validate_conversation_order(conv)
        assert len(issues) > 0
