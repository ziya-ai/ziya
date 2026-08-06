"""Text-run seam at tool-call boundaries in task execution.

``task_executor`` appends to ``collected_text`` only for ``ctype ==
"text"`` chunks.  A tool call switches the stream to ``tool_display``,
which emits no text, so the prose before the call and the prose after it
land as ADJACENT entries and ``"".join(collected_text)`` welds them into
one line:

    "...to place them below.The root cause is clear from VexFlow source"

The diagnostic that distinguishes this from a lost newline: a following
``## Summary`` renders as literal text rather than a heading, because a
heading only parses at line start.  No newline was ever present to lose,
so nothing downstream (markdown ``breaks``, sanitizers, the streaming
optimizer) can be at fault -- all of those preserve newlines that exist.

``full_text`` feeds ``Artifact.summary`` via ``truncate_summary``, so the
defect is persisted, not merely a live-display artifact.

These tests exercise the seam rule against the joined-text contract
rather than driving the full executor (which needs a live model, tool
registry, and scope ledger).  The rule under test:

    on a tool_display chunk, if collected_text is non-empty and its last
    entry does not already end in a newline, append "\\n\\n"
"""

import pytest

from app.utils.artifact_summary import truncate_summary


def accumulate(chunks):
    """Mirror of ``task_executor``'s collected_text accumulation, including
    the tool-boundary seam.  Returns the joined text a caller would see as
    ``full_text``."""
    collected: list[str] = []
    for chunk in chunks:
        ctype = chunk.get("type")
        if ctype == "text":
            content = chunk.get("content", "")
            if content:
                collected.append(content)
        elif ctype == "tool_display":
            if collected and not collected[-1].endswith("\n"):
                collected.append("\n\n")
    return "".join(collected)


class TestToolBoundarySeam:
    def test_prose_either_side_of_a_tool_call_is_not_welded(self):
        """The reported defect: two sentences glued with no separator."""
        text = accumulate([
            {"type": "text", "content": "Let me inspect the placement API."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "The root cause is clear."},
        ])
        assert "below.The" not in text
        assert "API.The" not in text, "prose welded across the tool boundary"
        assert text == (
            "Let me inspect the placement API.\n\n"
            "The root cause is clear."
        )

    def test_heading_after_a_tool_call_lands_at_line_start(self):
        """The load-bearing symptom.  A markdown heading only parses at line
        start, so the seam is what decides whether "## Summary" becomes an
        <h2> or literal text in the middle of a sentence."""
        text = accumulate([
            {"type": "text", "content": "The setLine chain is valid."},
            {"type": "tool_display", "tool_name": "run_shell_command"},
            {"type": "text", "content": "## Summary\n\nDefect taken."},
        ])
        assert "valid.## Summary" not in text
        lines = text.split("\n")
        assert "## Summary" in lines, (
            "heading must start its own line to parse as a heading"
        )

    def test_model_supplied_trailing_newline_is_not_doubled(self):
        """A model that already ended its run with a break must not get a
        third blank line -- the seam is a repair, not an unconditional
        paragraph insert."""
        text = accumulate([
            {"type": "text", "content": "Checking the config.\n"},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Found the issue."},
        ])
        assert text == "Checking the config.\nFound the issue."
        assert "\n\n\n" not in text

    def test_trailing_blank_line_already_present_is_left_alone(self):
        text = accumulate([
            {"type": "text", "content": "Reading the file.\n\n"},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Done."},
        ])
        assert text == "Reading the file.\n\nDone."
        assert "\n\n\n" not in text

    def test_leading_tool_call_adds_no_leading_whitespace(self):
        """A task whose first action is a tool call must not start its
        summary with a blank line."""
        text = accumulate([
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Starting analysis."},
        ])
        assert text == "Starting analysis."
        assert not text.startswith("\n")

    def test_consecutive_tool_calls_add_only_one_break(self):
        """Back-to-back tool calls with no intervening text must not
        accumulate a blank line per call."""
        text = accumulate([
            {"type": "text", "content": "Checking both files."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Both look correct."},
        ])
        assert text == "Checking both files.\n\nBoth look correct."
        assert "\n\n\n" not in text

    def test_many_boundaries_across_a_long_run(self):
        """The shape of a real multi-step task: alternating prose and tool
        calls.  Every seam must be bridged exactly once."""
        chunks = []
        for i in range(5):
            chunks.append({"type": "text", "content": f"Step {i} prose."})
            chunks.append({"type": "tool_display", "tool_name": "file_read"})
        chunks.append({"type": "text", "content": "Final answer."})
        text = accumulate(chunks)
        for i in range(5):
            assert f"Step {i} prose." in text
        assert "prose.Step" not in text
        assert "prose.Final" not in text
        assert "\n\n\n" not in text
        assert len(text.split("\n\n")) == 6

    def test_newlines_within_a_contiguous_text_run_are_untouched(self):
        """The seam rule must not alter text the model streamed as one run;
        only the gap the executor itself creates is repaired."""
        text = accumulate([
            {"type": "text", "content": "Line one.\nLine two.\nLine three."},
        ])
        assert text == "Line one.\nLine two.\nLine three."

    def test_delta_fragmentation_does_not_change_the_result(self):
        """Providers split text arbitrarily.  A seam decision keyed on the
        last entry must survive the same prose arriving as many small
        deltas, including one that ends exactly on a newline."""
        whole = accumulate([
            {"type": "text", "content": "Reading the config file now."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "It parses cleanly."},
        ])
        fragmented = accumulate([
            {"type": "text", "content": "Reading the "},
            {"type": "text", "content": "config file "},
            {"type": "text", "content": "now."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "It parses "},
            {"type": "text", "content": "cleanly."},
        ])
        assert whole == fragmented

    def test_seam_decision_uses_last_entry_not_whole_text(self):
        """A newline earlier in the run must NOT suppress the seam -- only a
        newline at the very end of the accumulated text should."""
        text = accumulate([
            {"type": "text", "content": "Intro line.\nSecond line."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "After the call."},
        ])
        assert text == "Intro line.\nSecond line.\n\nAfter the call."
        assert "line.After" not in text

    def test_empty_text_chunks_do_not_open_a_run(self):
        """``if content:`` skips empty strings, so an empty chunk before a
        tool call must not cause a leading break."""
        text = accumulate([
            {"type": "text", "content": ""},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Body."},
        ])
        assert text == "Body."

    def test_whitespace_only_chunk_counts_as_a_trailing_newline(self):
        """A newline-only delta is truthy and is appended, so it must
        satisfy the seam check rather than being bridged again."""
        text = accumulate([
            {"type": "text", "content": "Prose."},
            {"type": "text", "content": "\n"},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "More."},
        ])
        assert text == "Prose.\nMore."
        assert "\n\n" not in text


class TestPersistedSummary:
    """``full_text`` feeds ``Artifact.summary``, so the seam must survive
    into the stored artifact -- this is the half a frontend-only fix would
    have left broken."""

    def test_summary_preserves_the_seam(self):
        text = accumulate([
            {"type": "text", "content": "Investigated the renderer."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Root cause is the default line."},
        ])
        summary = truncate_summary(text.strip())
        assert "renderer.Root" not in summary
        assert "Investigated the renderer." in summary
        assert "Root cause is the default line." in summary

    def test_summary_strip_does_not_reintroduce_welding(self):
        """``full_text.strip()`` trims the ends; interior seams must
        survive it."""
        text = accumulate([
            {"type": "text", "content": "First."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Second."},
            {"type": "tool_display", "tool_name": "file_read"},
            {"type": "text", "content": "Third."},
        ])
        summary = truncate_summary(text.strip())
        for welded in ("First.Second", "Second.Third"):
            assert welded not in summary

    def test_trailing_seam_is_trimmed_by_strip(self):
        """A task ending on a tool call leaves a trailing seam; ``strip()``
        must remove it so the summary has no trailing blank line."""
        text = accumulate([
            {"type": "text", "content": "Ran the checks."},
            {"type": "tool_display", "tool_name": "run_shell_command"},
        ])
        assert text == "Ran the checks.\n\n"
        assert truncate_summary(text.strip()) == "Ran the checks."


class TestCliSinkUnaffected:
    """The seam is deliberately NOT emitted as a ``task_text_delta``: the
    CLI sink already calls ``_break_text()`` on ``task_tool_call``, so a
    whitespace delta would add blank lines there.  This pins that the CLI
    event stream is unchanged."""

    @pytest.mark.asyncio
    async def test_cli_output_has_no_extra_blank_lines(self):
        import io
        import sys
        from app.cli_card_runner import _StdoutSink

        events = [
            {"type": "task_started", "block_name": "t"},
            {"type": "task_text_delta", "content": "Let me inspect the API."},
            {"type": "task_tool_call", "tool_name": "file_read"},
            {"type": "task_text_delta", "content": "The root cause is clear."},
            {"type": "task_finished", "ok": True},
        ]

        buf = io.StringIO()
        saved = sys.stdout
        sys.stdout = buf
        try:
            sink = _StdoutSink()
            for event in events:
                await sink.send_json(event)
        finally:
            sys.stdout = saved

        out = buf.getvalue()
        assert "\n\n\n" not in out, "seam leaked into the CLI event stream"
        # The CLI supplies its own single break around the tool line.
        assert "Let me inspect the API.\n" in out
        assert "The root cause is clear." in out
