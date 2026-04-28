"""
Exhaustive tests for the hallucination detection subsystem.

Three modules are tested:
  fake_shell_detector  -- structural detection of fabricated shell sessions
  shingle_index        -- session-scoped fingerprint parroting detection
  region_extraction    -- scannable prose region extraction

Sections:
  A. fake_shell_detector -- patterns that SHOULD fire
  B. fake_shell_detector -- patterns that should NOT fire (false-positive guard)
  C. shingle_index       -- registration behaviour
  D. shingle_index       -- detection / matching
  E. region_extraction   -- scannable region extraction
  F. Integration         -- end-to-end scenarios combining all three
"""
import time

import pytest

from app.hallucination.fake_shell_detector import (
    FakeShellMatch,
    detect_fake_shell_session,
)
from app.hallucination.region_extraction import (
    extract_scannable_regions,
    scannable_text,
)
from app.hallucination.shingle_index import ShingleIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fence(lang: str, body: str, close: bool = True) -> str:
    """Build a Markdown fenced code block."""
    tag = f"```{lang}" if lang else "```"
    parts = [tag, body]
    if close:
        parts.append("```")
    return "\n".join(parts) + "\n"


def _open_fence(lang: str, body: str) -> str:
    """Build an unclosed (still-streaming) code fence."""
    return _fence(lang, body, close=False)


# ---------------------------------------------------------------------------
# Section A: fake_shell_detector — SHOULD detect
# ---------------------------------------------------------------------------

class TestFakeShellDetector_ShouldDetect:

    # --- Signal 1: grep-n output ---

    def test_grep_output_in_unmarked_fence(self):
        """Exact pattern from user complaint: grep -n output inside a plain ``` fence."""
        text = _fence("", (
            "1414:                        // Look up parent from XML\n"
            "1416:                        const parentId = cellElement?.getAttribute('parent');\n"
            "1417:                        const parentCell = parentId ? cellMap.get(parentId) : defaultParent;\n"
        ))
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "grep_output"

    def test_grep_output_in_bash_fence(self):
        """grep -n output inside a bash-typed fence."""
        text = _fence("bash", (
            "$ grep -n \"addCell\" frontend/src/plugins/d3/drawioPlugin.ts | head -10\n"
            "1414:                        // Look up parent from XML\n"
            "1416:                        const parentId = cellElement?.getAttribute('parent');\n"
            "1417:                        const parentCell = parentId ? cellMap.get(parentId) : defaultParent;\n"
        ))
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal in ("grep_output", "prompt_with_output")

    def test_grep_output_exactly_three_lines_boundary(self):
        """Exactly 3 grep-n lines is the minimum threshold — should fire."""
        text = _fence("", "10: first line content\n20: second line content\n30: third line content\n")
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "grep_output"

    def test_grep_output_many_lines(self):
        """More than 3 grep lines — definitely fires."""
        lines = "\n".join(f"{i*10}: some code content on line {i}" for i in range(1, 8))
        text = _fence("", lines + "\n")
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "grep_output"

    def test_grep_output_open_fence_fires_early(self):
        """Open (unclosed) fence with 3+ grep lines — fires without waiting for close."""
        text = _open_fence("", "42: def foo():\n43:     return bar\n44:     # some comment\n")
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "grep_output"

    def test_grep_output_no_prompt_line(self):
        """grep output can appear without any preceding $ command line."""
        body = "\n".join(f"{i}: content of line {i} with real code here" for i in range(1, 5))
        text = _fence("", body + "\n")
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "grep_output"

    def test_grep_output_with_workspace_paths(self):
        """Realistic fabricated grep output referencing the actual workspace."""
        body = (
            "$ grep -n 'detect_fake' /Users/dcohn/workspace/ziya-0.4.0.1/app/text_delta_processor.py\n"
            "284:    if state.conversation_id:\n"
            "290:        _shell_match = detect_fake_shell_session(state.assistant_text)\n"
            "295:            state.hallucination_detected = True\n"
        )
        text = _fence("bash", body)
        result = detect_fake_shell_session(text)
        assert result is not None

    # --- Signal 2: $ / # prompt with output lines ---

    def test_dollar_prompt_with_output_bash_fence(self):
        """$ prompt + 3 output lines inside bash fence."""
        text = _fence("bash", (
            "$ ls -la /tmp\n"
            "total 64\n"
            "drwxrwxrwt  15 root  wheel   480 Apr 24 17:00 .\n"
            "drwxr-xr-x  22 root  wheel   704 Apr 20 10:00 ..\n"
        ))
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "prompt_with_output"

    def test_dollar_prompt_with_output_sh_fence(self):
        text = _fence("sh", "$ echo hello\nhello\nworld\n")
        assert detect_fake_shell_session(text) is not None

    def test_dollar_prompt_with_output_shell_fence(self):
        text = _fence("shell", "$ cat /etc/hosts\n127.0.0.1  localhost\n::1        localhost\n")
        assert detect_fake_shell_session(text) is not None

    def test_dollar_prompt_with_output_zsh_fence(self):
        text = _fence("zsh", "$ pwd\n/Users/dcohn/workspace/ziya-0.4.0.1\n/Users/dcohn/workspace\n")
        assert detect_fake_shell_session(text) is not None

    def test_dollar_prompt_with_output_console_fence(self):
        text = _fence("console", "$ python3 --version\nPython 3.12.0\nInstalled from brew\n")
        assert detect_fake_shell_session(text) is not None

    def test_dollar_prompt_with_output_terminal_fence(self):
        text = _fence("terminal", "$ git status\nOn branch main\nYour branch is up to date.\n")
        assert detect_fake_shell_session(text) is not None

    def test_hash_prompt_root_shell(self):
        """# prompt (root shell) is also a valid shell prompt marker."""
        text = _fence("bash", (
            "# apt-get update\n"
            "Reading package lists...\n"
            "Building dependency tree...\n"
            "Reading state information...\n"
        ))
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "prompt_with_output"

    def test_prompt_no_language_tag_first_line_detection(self):
        """No language tag but first line is a $ prompt — still detects."""
        text = _fence("", (
            "$ grep -rn \"hallucination\" app/\n"
            "app/hallucination/__init__.py:5:Hallucination detection subsystem.\n"
            "app/text_delta_processor.py:22:from app.hallucination import (\n"
        ))
        result = detect_fake_shell_session(text)
        assert result is not None

    def test_multi_command_session(self):
        """Multiple $ commands interleaved with output — real session shape."""
        text = _fence("bash", "$ cd /tmp\n$ ls\nfile1.txt\nfile2.txt\ndir1/\n")
        result = detect_fake_shell_session(text)
        assert result is not None

    def test_prompt_open_fence_fires_early(self):
        """Open bash fence: $ + 2 output lines fires without close."""
        text = _open_fence("bash", "$ find . -name '*.py'\n./app/main.py\n./app/utils.py\n")
        result = detect_fake_shell_session(text)
        assert result is not None

    def test_second_fence_is_bad_first_is_clean(self):
        """First fence is clean Python; second fence has fake shell — should fire on second."""
        clean = _fence("python", "def foo():\n    return 42\n")
        fake = _fence("bash", "$ ls\nfile1\nfile2\nfile3\n")
        result = detect_fake_shell_session(clean + "\nSome prose.\n\n" + fake)
        assert result is not None

    def test_fence_body_capped_at_300_chars(self):
        """The fence_body diagnostic field is capped at 300 characters."""
        long_body = "\n".join(f"{i}: {'x' * 60}" for i in range(1, 20))
        text = _fence("bash", long_body)
        result = detect_fake_shell_session(text)
        assert result is not None
        assert len(result.fence_body) <= 300

    def test_returns_fakeshellmatch_dataclass(self):
        """Return type is FakeShellMatch with the expected fields."""
        text = _fence("bash", "$ ls\nfile1\nfile2\nfile3\n")
        result = detect_fake_shell_session(text)
        assert isinstance(result, FakeShellMatch)
        assert result.reason
        assert result.signal
        assert isinstance(result.fence_body, str)


# ---------------------------------------------------------------------------
# Section B: fake_shell_detector — should NOT detect (false-positive guard)
# ---------------------------------------------------------------------------

class TestFakeShellDetector_ShouldNotDetect:

    def test_clean_python_code(self):
        text = _fence("python", "def hello():\n    print('world')\n")
        assert detect_fake_shell_session(text) is None

    def test_clean_typescript_code(self):
        text = _fence("typescript", "const greet = (name: string): void => {\n  console.log(`Hello ${name}`);\n};\n")
        assert detect_fake_shell_session(text) is None

    def test_bash_fence_command_only_no_output(self):
        """bash fence with just a single command line and no output."""
        text = _fence("bash", "grep -n 'foo' bar.py\n")
        assert detect_fake_shell_session(text) is None

    def test_bash_fence_command_and_one_output_line(self):
        """$ prompt with only 1 output line — below the 2-line threshold."""
        text = _fence("bash", "$ echo hello\nhello\n")
        assert detect_fake_shell_session(text) is None

    def test_two_grep_lines_below_threshold(self):
        """Only 2 grep-style lines — below the 3-line threshold."""
        text = _fence("", "10: first line content here\n20: second line content here\n")
        assert detect_fake_shell_session(text) is None

    def test_bash_fence_comments_only(self):
        text = _fence("bash", "# Run this to install\n# pip install -r requirements.txt\n")
        assert detect_fake_shell_session(text) is None

    def test_bash_fence_command_then_blank_line(self):
        """Command followed by a blank line — no real output."""
        text = _fence("bash", "$ ls -la\n\n")
        assert detect_fake_shell_session(text) is None

    def test_empty_text(self):
        assert detect_fake_shell_session("") is None

    def test_no_fences_at_all(self):
        text = "This is plain prose. No code blocks. Just text here.\n"
        assert detect_fake_shell_session(text) is None

    def test_open_fence_one_grep_line_not_enough(self):
        """Open fence with only 1 grep line accumulated — insufficient evidence."""
        text = _open_fence("", "42: def foo():\n")
        assert detect_fake_shell_session(text) is None

    def test_open_fence_two_grep_lines_not_enough(self):
        """Open fence with 2 grep lines — still below threshold."""
        text = _open_fence("", "42: def foo():\n43:     return bar\n")
        assert detect_fake_shell_session(text) is None

    def test_typescript_object_with_number_keys_indented(self):
        """Indented TypeScript object keys that look like grep format."""
        text = _fence("typescript", "const map = {\n  1: 'one',\n  2: 'two',\n  3: 'three',\n};\n")
        # Leading spaces mean ^\d+ won't match
        assert detect_fake_shell_session(text) is None

    def test_diff_fence_not_flagged(self):
        """diff output has +/- lines, not grep-n lines."""
        text = _fence("diff", "@@ -1,3 +1,4 @@\n-old line\n+new line\n context\n")
        assert detect_fake_shell_session(text) is None

    def test_prose_mentioning_grep_output_no_fence(self):
        """Prose that describes grep output — no fence, no detection."""
        text = "The grep output looks like `42: def foo():` with line numbers prefixed.\n"
        assert detect_fake_shell_session(text) is None

    def test_numbered_list_outside_fence(self):
        """Markdown numbered list items outside any fence."""
        text = "1: First item\n2: Second item\n3: Third item\n"
        assert detect_fake_shell_session(text) is None

    def test_showing_command_user_should_run(self):
        """
        Legitimate usage: showing a command for the user to run,
        with no fabricated output in the fence.
        """
        text = (
            "To find all usages:\n\n"
            "```bash\n"
            "grep -rn 'hallucination' app/\n"
            "```\n\n"
            "This will list every file that imports from the hallucination module.\n"
        )
        assert detect_fake_shell_session(text) is None

    def test_all_clean_fences_no_detection(self):
        """Multiple clean fences of different types — none should fire."""
        text = (
            _fence("python", "x = 1\n") +
            _fence("typescript", "const x = 1;\n") +
            _fence("bash", "pip install ziya\n") +
            _fence("json", '{"key": "value"}\n')
        )
        assert detect_fake_shell_session(text) is None


# ---------------------------------------------------------------------------
# Section C: shingle_index — registration
# ---------------------------------------------------------------------------

class TestShingleIndex_Registration:

    def setup_method(self):
        self.index = ShingleIndex(min_result_length=20)

    def test_register_valid_result_returns_true(self):
        ok = self.index.register("conv1", "tool1", "shell", "word " * 30)
        assert ok is True
        assert self.index.session_size("conv1") == 1

    def test_register_result_below_min_length_rejected(self):
        idx = ShingleIndex(min_result_length=100)
        ok = idx.register("conv1", "tool1", "shell", "too short text")
        assert ok is False
        assert idx.session_size("conv1") == 0

    def test_register_empty_string_rejected(self):
        ok = self.index.register("conv1", "tool1", "shell", "")
        assert ok is False
        assert self.index.session_size("conv1") == 0

    def test_register_empty_conversation_id_rejected(self):
        ok = self.index.register("", "tool1", "shell", "word " * 30)
        assert ok is False

    def test_register_empty_tool_use_id_rejected(self):
        ok = self.index.register("conv1", "", "shell", "word " * 30)
        assert ok is False

    def test_reregister_same_id_replaces_not_appends(self):
        text = "the quick brown fox jumps over the lazy dog " * 3
        self.index.register("conv1", "tool1", "shell", text)
        self.index.register("conv1", "tool1", "shell", text + " extra")
        assert self.index.session_size("conv1") == 1

    def test_multiple_tools_accumulate_in_session(self):
        text = "result content for tool " * 5
        self.index.register("conv1", "tool1", "shell", text)
        self.index.register("conv1", "tool2", "grep", text)
        assert self.index.session_size("conv1") == 2

    def test_session_evicts_oldest_at_max(self):
        idx = ShingleIndex(max_results_per_session=3, min_result_length=10)
        for i in range(5):
            idx.register("conv1", f"tool{i}", "shell", f"result for iteration {i} " * 5)
        assert idx.session_size("conv1") == 3

    def test_separate_sessions_are_independent(self):
        text = "some tool result content " * 5
        self.index.register("conv_a", "tool1", "shell", text)
        assert self.index.session_size("conv_a") == 1
        assert self.index.session_size("conv_b") == 0

    def test_clear_session_removes_all_entries(self):
        text = "some tool result content " * 5
        self.index.register("conv1", "tool1", "shell", text)
        self.index.clear_session("conv1")
        assert self.index.session_size("conv1") == 0

    def test_clear_nonexistent_session_no_error(self):
        self.index.clear_session("does_not_exist")  # should not raise


# ---------------------------------------------------------------------------
# Section D: shingle_index — detection / matching
# ---------------------------------------------------------------------------

_RICH_RESULT = (
    "The deployment failed at stage gamma because the health check "
    "timed out after 30 seconds. Host i-0abc123def456 failed to "
    "respond to the /health endpoint. See CloudWatch logs for details. "
    "The rollback completed successfully at 17:42 UTC. "
    "Affected service: PaymentProcessor version 2.4.1."
)


class TestShingleIndex_Detection:

    def setup_method(self):
        self.index = ShingleIndex(min_result_length=50)
        self.index.register("conv1", "tool1", "mcp_run_shell_command", _RICH_RESULT)

    def test_verbatim_reproduction_high_confidence(self):
        match = self.index.check("conv1", _RICH_RESULT)
        assert match is not None
        assert match.confidence == "high"
        assert match.matched_tool_use_id == "tool1"
        assert match.matched_tool_name == "mcp_run_shell_command"

    def test_partial_excerpt_still_matches(self):
        probe = "The deployment failed at stage gamma because the health check timed out after 30 seconds"
        match = self.index.check("conv1", probe)
        assert match is not None

    def test_completely_unrelated_text_no_match(self):
        probe = "The weather today is sunny with a high of 72 degrees Fahrenheit in Seattle."
        match = self.index.check("conv1", probe)
        assert match is None

    def test_empty_probe_returns_none(self):
        assert self.index.check("conv1", "") is None

    def test_empty_session_returns_none(self):
        fresh = ShingleIndex()
        assert fresh.check("conv1", _RICH_RESULT) is None

    def test_wrong_conversation_id_returns_none(self):
        match = self.index.check("conv_other", _RICH_RESULT)
        assert match is None

    def test_skip_after_timestamp_filters_out_recent(self):
        """skip_after_timestamp set to a time before registration → fingerprint excluded."""
        past = time.time() - 1000
        match = self.index.check("conv1", _RICH_RESULT, skip_after_timestamp=past)
        assert match is None

    def test_skip_after_timestamp_includes_old_fingerprint(self):
        """skip_after_timestamp set to future → fingerprint old enough → included."""
        future = time.time() + 1000
        match = self.index.check("conv1", _RICH_RESULT, skip_after_timestamp=future)
        assert match is not None

    def test_skip_after_timestamp_none_disables_filter(self):
        match = self.index.check("conv1", _RICH_RESULT, skip_after_timestamp=None)
        assert match is not None

    def test_best_match_selected_among_multiple_fingerprints(self):
        """When multiple fingerprints are registered, the strongest match wins."""
        self.index.register("conv1", "tool2", "grep", "some other content with different words " * 5)
        match = self.index.check("conv1", _RICH_RESULT)
        assert match is not None
        assert match.matched_tool_use_id == "tool1"  # original is strongest

    def test_single_long_line_triggers_line_hash_match(self):
        """A single verbatim long line from the result triggers a line-hash match."""
        long_line = (
            "Host i-0abc123def456 failed to respond to the /health endpoint. "
            "See CloudWatch logs for details."
        )
        match = self.index.check("conv1", long_line)
        assert match is not None

    def test_match_includes_metadata(self):
        """ShingleMatch carries tool_use_id, tool_name, overlap counts, confidence."""
        match = self.index.check("conv1", _RICH_RESULT)
        assert match is not None
        assert isinstance(match.shingle_overlap, int)
        assert isinstance(match.line_matches, int)
        assert match.confidence in ("high", "low")
        assert isinstance(match.registered_at, float)


# ---------------------------------------------------------------------------
# Section E: region_extraction
# ---------------------------------------------------------------------------

class TestRegionExtraction:

    def test_plain_prose_fully_scannable(self):
        text = "This is plain prose.\nIt has two lines.\n"
        full = scannable_text(text)
        assert "plain prose" in full
        assert "two lines" in full

    def test_fenced_block_body_excluded(self):
        text = "Before fence.\n```python\ncode here\n```\nAfter fence.\n"
        full = scannable_text(text)
        assert "code here" not in full
        assert "Before fence" in full
        assert "After fence" in full

    def test_tilde_fence_excluded(self):
        text = "Prose.\n~~~bash\nshell code\n~~~\nMore prose.\n"
        full = scannable_text(text)
        assert "shell code" not in full
        assert "Prose" in full
        assert "More prose" in full

    def test_indented_code_block_excluded(self):
        text = "Normal line.\n    indented code block\nNormal again.\n"
        full = scannable_text(text)
        assert "indented code block" not in full
        assert "Normal line" in full
        assert "Normal again" in full

    def test_blockquote_excluded(self):
        text = "Normal.\n> this is a quote\nNormal again.\n"
        full = scannable_text(text)
        assert "this is a quote" not in full
        assert "Normal again" in full

    def test_inline_backtick_spans_stripped(self):
        text = "The function `foo()` returns None.\n"
        full = scannable_text(text)
        assert "foo()" not in full
        assert "returns None" in full

    def test_multi_backtick_span_stripped(self):
        text = "Use ``backtick ` inside`` for escaping.\n"
        full = scannable_text(text)
        assert "backtick" not in full

    def test_multiple_regions_returned_for_interleaved_fences(self):
        text = (
            "First region.\n"
            "```\ncode\n```\n"
            "Second region.\n"
            "```\nmore code\n```\n"
            "Third.\n"
        )
        regions = extract_scannable_regions(text)
        combined = "".join(regions)
        assert "First region" in combined
        assert "Second region" in combined
        assert "Third" in combined
        assert "code" not in combined

    def test_unclosed_fence_rest_of_text_excluded(self):
        text = "Before.\n```\nunclosed fence content\nno close marker\n"
        full = scannable_text(text)
        assert "unclosed fence content" not in full
        assert "Before" in full

    def test_empty_text_returns_empty(self):
        assert extract_scannable_regions("") == []
        assert scannable_text("") == ""

    def test_only_prose_no_fences(self):
        text = "line one\nline two\nline three\n"
        regions = extract_scannable_regions(text)
        assert len(regions) == 1
        assert "line one" in regions[0]


# ---------------------------------------------------------------------------
# Section F: Integration scenarios
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_exact_user_complaint_pattern(self):
        """
        The actual examples from the user's complaint: AI wrote grep -n output
        inside a plain code fence without calling any tool.
        """
        text = (
            "Let me verify what's actually happening.\n\n"
            "```\n"
            " cd /Users/dcohn/workspace/ziya-0.4.0.1 && "
            "grep -n \"graph.addCell\\|parentCell\" frontend/src/plugins/d3/drawioPlugin.ts | head -10\n"
            "1414:                        // Look up parent from XML\n"
            "1416:                        const parentId = cellElement?.getAttribute('parent');\n"
            "1417:                        const parentCell = parentId ? cellMap.get(parentId) : defaultParent;\n"
            "1419:                        console.log(`📐 DrawIO: Adding cell`);\n"
            "```\n"
        )
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal == "grep_output"

    def test_streaming_simulation_fires_on_open_fence(self):
        """
        Simulate text arriving in small chunks (streaming cadence).
        The detector should fire as soon as evidence threshold is met
        in an open fence -- no need to wait for the closing ```.

        Chunk layout:
          chunk 0: $ command line             → not enough (0 output lines)
          chunk 1: 1st grep output line       → not enough (1 output line)
          chunk 2: 2nd grep output line       → fires (1 prompt + 2 output)
        """
        prefix = "OK, parent IS read from XML. Let me check:\n\n```bash\n"
        chunks = [
            "$ grep -n 'addCell' drawioPlugin.ts\n",
            "1414: // Look up parent from XML\n",
            "1416: const parentId = cellElement?.getAttribute('parent');\n",
        ]
        accumulated = prefix
        fired_at = None
        for i, chunk in enumerate(chunks):
            accumulated += chunk
            if detect_fake_shell_session(accumulated) is not None:
                fired_at = i
                break

        assert fired_at is not None, "Detector never fired"
        assert fired_at <= 2, f"Fired too late at chunk {fired_at}; should fire by chunk 2"

    def test_fake_shell_fires_shingle_does_not_for_invented_output(self):
        """
        A fabricated shell session where the output was never produced by
        a real tool call: fake-shell check fires, shingle check is silent
        (nothing to match against).
        """
        index = ShingleIndex(min_result_length=50)
        fake_text = _fence(
            "bash",
            "$ ls app/hallucination\n__init__.py\nshingle_index.py\nregion_extraction.py\n"
        )
        shingle_match = index.check("conv1", fake_text)
        fake_shell_match = detect_fake_shell_session(fake_text)

        assert fake_shell_match is not None, "fake_shell_detector should have fired"
        assert shingle_match is None, "shingle check should be silent (no prior tool result)"

    def test_shingle_fires_fake_shell_does_not_for_parroted_prose(self):
        """
        The model reproduces real tool output as prose (outside any fence):
        shingle check fires, fake-shell check is silent.
        """
        index = ShingleIndex(min_result_length=50)
        real_result = _RICH_RESULT
        index.register("conv1", "tool1", "search", real_result)

        # Model reproduces it as plain prose (no code fence)
        prose_reproduction = f"The tool reported: {real_result}"

        shingle_match = index.check("conv1", prose_reproduction)
        fake_shell_match = detect_fake_shell_session(prose_reproduction)

        assert shingle_match is not None, "shingle check should have fired on parroted prose"
        assert fake_shell_match is None, "fake_shell_detector should be silent (no fence)"

    def test_clean_response_with_example_command_passes_both_checks(self):
        """
        Legitimate response: shows a command the user should run,
        no fabricated output, no parroted tool result.
        Both checks pass cleanly.
        """
        index = ShingleIndex(min_result_length=50)
        text = (
            "Here's how you'd search for usages:\n\n"
            "```bash\n"
            "grep -rn 'hallucination' app/\n"
            "```\n\n"
            "This will list every file that imports from the hallucination module.\n"
        )
        assert detect_fake_shell_session(text) is None
        assert index.check("conv1", text) is None

    def test_grep_output_not_caught_by_region_extraction(self):
        """
        A fake grep session is inside a code fence, so region_extraction
        correctly excludes it from the scannable prose regions.
        The fake_shell_detector (not region_extraction) is the right tool for it.
        """
        text = (
            "Let me check:\n\n"
            "```\n"
            "1414: const parentId = cellElement?.getAttribute('parent');\n"
            "1416: const parentCell = parentId ? cellMap.get(parentId) : defaultParent;\n"
            "1417: console.log('parent', parentCell?.getId());\n"
            "```\n"
        )
        # Region extractor should exclude the fence body
        scannable = scannable_text(text)
        assert "1414" not in scannable

        # But fake-shell detector should catch it
        assert detect_fake_shell_session(text) is not None


# ---------------------------------------------------------------------------
# Section G: streaming boundary splits
# ---------------------------------------------------------------------------
#
# Real streams split mid-word, not cleanly at line boundaries.  These tests
# simulate progressively longer assistant_text accumulating one chunk at a
# time and assert exactly when detection fires.
# ---------------------------------------------------------------------------

class TestStreamingBoundarySplits:

    def test_G1_char_by_char_delivery_fires_at_threshold(self):
        """Deliver a fake grep session one char at a time; find first firing index."""
        full = (
            "```bash\n"
            "$ grep -n foo bar.py\n"
            "10: foo = 1\n"
            "11: foo = 2\n"
            "12: foo = 3\n"
            "```\n"
        )
        first_fire = None
        for i in range(1, len(full) + 1):
            if detect_fake_shell_session(full[:i]) is not None:
                first_fire = i
                break
        assert first_fire is not None, "detector never fired on complete text"
        third_grep_end = full.index("12: foo = 3\n") + len("12: foo = 3\n")
        assert first_fire <= third_grep_end + 1, (
            f"fired at {first_fire}, expected by {third_grep_end + 1}"
        )

    def test_G2_mid_fence_tag_split_no_false_fire(self):
        """Fence opening split mid-tag: ```bas must not fire on the prefix."""
        prefix = "```bas"
        assert detect_fake_shell_session(prefix) is None

    def test_G3_mid_prompt_split_no_false_fire(self):
        """Prompt line split mid-word: $ gre (no newline) must not fire."""
        text = "```bash\n$ gre"
        assert detect_fake_shell_session(text) is None

    def test_G4_mid_output_line_split_pins_current_behaviour(self):
        """Two complete grep lines plus a partial third without trailing newline."""
        text = (
            "```bash\n"
            "10: first\n"
            "11: second\n"
            "12: thi"
        )
        result = detect_fake_shell_session(text)
        # Pin whichever behaviour is current; neither detection nor
        # non-detection is wrong here, but the result must be stable.
        assert result is None or result.signal == 'grep_output'

    def test_G5_threshold_crossing_single_delta(self):
        """A single delta adds 2 grep lines at once, crossing the threshold."""
        before = "```bash\n10: first\n"
        after = before + "11: second\n12: third\n"
        assert detect_fake_shell_session(before) is None
        assert detect_fake_shell_session(after) is not None

    def test_G6_fires_at_exact_threshold_line(self):
        """3 grep lines fires; 2 does not.  Strict inclusivity at boundary."""
        two_lines = "```\n10: a\n11: b\n```\n"
        three_lines = "```\n10: a\n11: b\n12: c\n```\n"
        assert detect_fake_shell_session(two_lines) is None
        assert detect_fake_shell_session(three_lines) is not None

    def test_G7_clean_code_streams_without_firing(self):
        """Streaming a clean python file must never fire at any prefix length."""
        full = (
            "```python\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(x, y):\n"
            "    return x * y\n"
            "```\n"
        )
        for i in range(1, len(full) + 1):
            assert detect_fake_shell_session(full[:i]) is None, (
                f"false positive at prefix length {i}: {full[:i]!r}"
            )

    def test_G8_prompt_plus_output_fires_when_two_output_lines_present(self):
        """$ prompt + output lines: fires when second output line completes."""
        chunks = [
            "```bash\n",
            "$ ls\n",
            "file1.txt\n",
            "file2.txt\n",
        ]
        accum = ""
        fire_chunk = None
        for idx, c in enumerate(chunks):
            accum += c
            if detect_fake_shell_session(accum) is not None:
                fire_chunk = idx
                break
        assert fire_chunk == 3, (
            f"expected fire at chunk 3 (second output line), got {fire_chunk}"
        )


# ---------------------------------------------------------------------------
# Section H: false-positive hazards
# ---------------------------------------------------------------------------
#
# Inputs that superficially resemble fake shell output.  Some still fire --
# that is documented as expected behaviour ("the model should call the tool,
# not fake a session").  Others must not fire.
# ---------------------------------------------------------------------------

class TestFalsePositiveHazards:

    def test_H1_user_shell_output_quoted_back_documents_limitation(self):
        """
        Known limitation: user-provided shell output echoed inside a fence
        is indistinguishable from fabrication by text alone.  Fires today.
        """
        text = (
            "You showed me this grep output earlier:\n\n"
            "```\n"
            "10: foo\n"
            "11: bar\n"
            "12: baz\n"
            "```\n"
            "Let me analyse it.\n"
        )
        assert detect_fake_shell_session(text) is not None

    def test_H2_tutorial_with_output_label_still_fires(self):
        """
        Tutorial illustrating grep output.  Fires: the rule is "don't fake
        output" -- the model should call the tool or describe prose.
        """
        text = (
            "When you run grep, the output looks like:\n\n"
            "```\n"
            "10: match one\n"
            "11: match two\n"
            "12: match three\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is not None

    def test_H3_markdown_numbered_list_outside_fence(self):
        text = (
            "Steps to reproduce:\n"
            "1. First step\n"
            "2. Second step\n"
            "3. Third step\n"
        )
        assert detect_fake_shell_session(text) is None

    def test_H4_config_file_in_ini_fence(self):
        text = (
            "```ini\n"
            "# Database configuration\n"
            "# Set the host here\n"
            "host = localhost\n"
            "port = 5432\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is None

    def test_H5_shell_script_source_not_session(self):
        """
        Raw bash script source (no $ prompt) inside a bash fence.
        Must not fire: no prompt line -> not a session transcript.
        """
        text = (
            "```bash\n"
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "export FOO=bar\n"
            "echo hello\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is None

    def test_H6_numeric_id_table_outside_fence(self):
        text = (
            "| ID | Name |\n"
            "|----|------|\n"
            "| 1  | foo  |\n"
            "| 2  | bar  |\n"
            "| 3  | baz  |\n"
        )
        assert detect_fake_shell_session(text) is None

    def test_H7_todo_comments_with_numbers(self):
        text = (
            "```python\n"
            "# TODO: 1234 fix this\n"
            "# TODO: 5678 fix that\n"
            "# TODO: 9012 fix the other\n"
            "def foo():\n"
            "    pass\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is None

    def test_H8_diff_with_plus_minus_lines(self):
        text = (
            "```diff\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            "-old line\n"
            "+new line one\n"
            "+new line two\n"
            " context line\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is None


# ---------------------------------------------------------------------------
# Section I: performance / pathological input
# ---------------------------------------------------------------------------
#
# Guards against regex backtracking and quadratic loops.  Timings are
# generous sanity bounds, not strict SLAs.
# ---------------------------------------------------------------------------

class TestPerformance:

    def test_I1_large_fence_with_many_grep_lines(self):
        body = "".join(f"{i}: line content number {i}\n" for i in range(1000))
        text = f"```bash\n{body}```\n"
        start = time.perf_counter()
        result = detect_fake_shell_session(text)
        elapsed = time.perf_counter() - start
        assert result is not None
        assert elapsed < 0.1, f"took {elapsed:.3f}s, expected < 0.1s"

    def test_I2_many_consecutive_clean_fences(self):
        single = "```python\nx = 1\n```\n"
        text = single * 200
        start = time.perf_counter()
        result = detect_fake_shell_session(text)
        elapsed = time.perf_counter() - start
        assert result is None
        assert elapsed < 0.2, f"took {elapsed:.3f}s, expected < 0.2s"

    def test_I3_long_single_line(self):
        text = "```bash\n" + ("x" * 50_000) + "\n```\n"
        start = time.perf_counter()
        detect_fake_shell_session(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.05, f"took {elapsed:.3f}s, expected < 0.05s"

    def test_I4_nested_fence_markers_no_backtracking(self):
        text = "```bash\n" + ("foo ``` bar\n" * 500) + "```\n"
        start = time.perf_counter()
        detect_fake_shell_session(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.05, f"took {elapsed:.3f}s, expected < 0.05s"


# ---------------------------------------------------------------------------
# Section J: exact user-complaint regressions
# ---------------------------------------------------------------------------
#
# Byte-for-byte samples of fabricated shell sessions from real conversations.
# ---------------------------------------------------------------------------

class TestUserComplaintRegressions:

    def test_J1_verbatim_grep_output_from_conversation(self):
        text = (
            "```bash\n"
            "$ cd /Users/dcohn/workspace/ziya-0.4.0.1 && grep -n "
            "\"graph.addCell\\|parentCell\" "
            "frontend/src/plugins/d3/drawioPlugin.ts | head -10\n"
            "1414:                        // Look up parent from XML\n"
            "1416:                        const parentId = cellElement?.getAttribute('parent');\n"
            "1417:                        const parentCell = parentId ? cellMap.get(parentId) : defaultParent;\n"
            "1419:                        console.log('parent', parentCell?.getId());\n"
            "1420:                        console.log('parent', parentCell?.getId());\n"
            "```\n"
        )
        result = detect_fake_shell_session(text)
        assert result is not None
        assert result.signal in ('grep_output', 'prompt_with_output')

    def test_J2_invented_cat_output(self):
        text = (
            "```bash\n"
            "$ cat config.yaml\n"
            "database:\n"
            "  host: localhost\n"
            "  port: 5432\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is not None

    def test_J3_invented_ls_output(self):
        text = (
            "```bash\n"
            "$ ls -la /Users/dcohn/workspace\n"
            "drwxr-xr-x  10 dcohn staff  320 Jan 01 12:00 ziya-0.4.0.1\n"
            "drwxr-xr-x   5 dcohn staff  160 Jan 01 12:00 other-project\n"
            "```\n"
        )
        assert detect_fake_shell_session(text) is not None

    def test_J4_open_fence_mid_streaming_fires(self):
        """Streaming fence still open when detector sees it."""
        text = (
            "Let me check the grep output:\n\n"
            "```bash\n"
            "$ grep -n foo bar.py\n"
            "10: foo = 1\n"
            "11: foo = 2\n"
            "12: foo = 3\n"
        )
        assert detect_fake_shell_session(text) is not None
