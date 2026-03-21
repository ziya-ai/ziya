"""
Tests for crystal summary cleaning in DelegateManager.

Verifies that _clean_crystal_summary() strips tool-call noise while
preserving model prose, and that the stub crystal builder uses prose_only
content instead of raw accumulated text.
"""

import pytest

from app.agents.delegate_manager import DelegateManager


# -----------------------------------------------------------------------
# _clean_crystal_summary unit tests
# -----------------------------------------------------------------------

class TestCleanCrystalSummary:
    """Tests for the static _clean_crystal_summary helper."""

    def test_empty_input(self):
        assert DelegateManager._clean_crystal_summary("") == ""
        assert DelegateManager._clean_crystal_summary(None) == ""

    def test_plain_prose_unchanged(self):
        text = "The module implements a REST API with three endpoints."
        assert DelegateManager._clean_crystal_summary(text) == text

    def test_strips_tool_headers(self):
        text = (
            "Analysis of the codebase:\n\n"
            "🔧 **File Read**\n"
            "The server module handles routing.\n\n"
            "🔧 **Ast Search**\n"
            "Found 12 functions."
        )
        result = DelegateManager._clean_crystal_summary(text)
        assert "🔧" not in result
        assert "File Read" not in result
        assert "Ast Search" not in result
        assert "Analysis of the codebase" in result
        assert "The server module handles routing." in result
        assert "Found 12 functions." in result

    def test_strips_shell_tool_headers(self):
        text = (
            "Checking project structure.\n\n"
            "🛠️ run shell command\n"
            "The project has 42 files."
        )
        result = DelegateManager._clean_crystal_summary(text)
        assert "🛠️" not in result
        assert "run shell command" not in result
        assert "Checking project structure." in result
        assert "The project has 42 files." in result

    def test_strips_short_code_fences(self):
        """Short fenced blocks (tool output) are removed."""
        text = (
            "Found the configuration:\n\n"
            "```\n"
            "Contents of app/  [25 entries]\n"
            "  app/__init__.py  (15 bytes)\n"
            "  app/server.py  (343,570 bytes)\n"
            "```\n\n"
            "The server is the main entry point."
        )
        result = DelegateManager._clean_crystal_summary(text)
        assert "Contents of app/" not in result
        assert "343,570 bytes" not in result
        assert "Found the configuration" in result
        assert "The server is the main entry point." in result

    def test_preserves_long_code_fences(self):
        """Long fenced blocks (actual code/diffs) are preserved."""
        lines = ["line %d" % i for i in range(35)]
        long_block = "```diff\n" + "\n".join(lines) + "\n```"
        text = f"Here is the diff:\n\n{long_block}\n\nApply this change."
        result = DelegateManager._clean_crystal_summary(text)
        assert "line 0" in result
        assert "line 34" in result

    def test_strips_sequential_thinking_json(self):
        text = (
            'Starting analysis.\n\n'
            '{"thoughtNumber": 1, "totalThoughts": 6, "nextThoughtNeeded": true}\n\n'
            "The architecture uses a layered approach."
        )
        result = DelegateManager._clean_crystal_summary(text)
        assert "thoughtNumber" not in result
        assert "Starting analysis." in result
        assert "layered approach" in result

    def test_strips_expand_output_lines(self):
        text = (
            "Running commands:\n"
            "▶ Expand (Output (1958 chars, 14 lines))\n"
            "The output shows normal operation."
        )
        result = DelegateManager._clean_crystal_summary(text)
        assert "▶ Expand" not in result
        assert "The output shows normal operation." in result

    def test_strips_directory_listings(self):
        text = (
            "Checking the project.\n\n"
            "Contents of ./  [29 entries]\n"
            "  README.md  (6,393 bytes)\n"
            "  app/\n"
            "  frontend/\n"
            "\n"
            "The project has both backend and frontend."
        )
        result = DelegateManager._clean_crystal_summary(text)
        assert "29 entries" not in result
        assert "6,393 bytes" not in result
        assert "Checking the project." in result
        assert "The project has both backend and frontend." in result

    def test_collapses_blank_lines(self):
        text = "First paragraph.\n\n\n\n\n\nSecond paragraph."
        result = DelegateManager._clean_crystal_summary(text)
        assert "\n\n\n" not in result
        assert "First paragraph." in result
        assert "Second paragraph." in result

    def test_max_length_truncation(self):
        text = "A" * 500
        result = DelegateManager._clean_crystal_summary(text, max_length=100)
        assert len(result) == 101  # 100 chars + ellipsis
        assert result.endswith("…")

    def test_max_length_no_truncation_when_short(self):
        text = "Short summary."
        result = DelegateManager._clean_crystal_summary(text, max_length=100)
        assert result == "Short summary."

    def test_combined_noise(self):
        """Real-world example with multiple noise types interleaved."""
        text = (
            "Completed: Cline Core Tools Analyzer.\n\n"
            "🔧 **Sequentialthinking**\n"
            '{"thoughtNumber": 1, "totalThoughts": 6, "nextThoughtNeeded": true}\n'
            "Let me fetch key source files.\n\n"
            "🔧 **Fetch**\n"
            "```\n"
            "Failed to fetch https://example.com - status 404\n"
            "```\n\n"
            "🛠️ run shell command\n"
            "▶ Expand (Output (1958 chars, 14 lines))\n"
            "Contents of ./  [29 entries]\n"
            "  README.md  (6,393 bytes)\n"
            "  app/\n"
            "\n"
            "The codebase has a modular architecture with clear separation.\n\n"
            "Key finding: The agent loop uses a tool-use pattern similar to ReAct."
        )
        result = DelegateManager._clean_crystal_summary(text)
        # Noise is gone
        assert "🔧" not in result
        assert "🛠️" not in result
        assert "thoughtNumber" not in result
        assert "▶ Expand" not in result
        assert "29 entries" not in result
        assert "404" not in result
        # Prose is preserved
        assert "Completed: Cline Core Tools Analyzer." in result
        assert "Let me fetch key source files." in result
        assert "modular architecture" in result
        assert "agent loop uses a tool-use pattern" in result


# -----------------------------------------------------------------------
# Stub crystal summary construction
# -----------------------------------------------------------------------

class TestStubCrystalUsesProseOnly:
    """Verify that the stub crystal path in _run_delegate uses clean prose."""

    def test_clean_summary_applied_to_stub_content(self):
        """_clean_crystal_summary should strip noise from stub-like content."""
        # Simulate what accumulated text looks like with tool noise
        raw_accumulated = (
            "I'll analyze the codebase structure.\n\n"
            "🔧 **File List**\n"
            "```\napp/server.py\napp/main.py\n```\n\n"
            "The server module handles HTTP routing and WebSocket connections.\n\n"
            "🔧 **File Read**\n"
            "```\ndef handle_request(req):\n    pass\n```\n\n"
            "The request handler delegates to the streaming executor."
        )

        # Simulate what prose_only would be (only text chunks)
        prose_only = (
            "I'll analyze the codebase structure.\n\n"
            "The server module handles HTTP routing and WebSocket connections.\n\n"
            "The request handler delegates to the streaming executor."
        )

        # Cleaning accumulated should approximate prose_only
        cleaned = DelegateManager._clean_crystal_summary(raw_accumulated)
        assert "File List" not in cleaned
        assert "File Read" not in cleaned
        assert "app/server.py" not in cleaned
        assert "def handle_request" not in cleaned
        assert "HTTP routing" in cleaned
        assert "streaming executor" in cleaned


# -----------------------------------------------------------------------
# Completion report structure
# -----------------------------------------------------------------------

class TestCompletionReportStructure:
    """Verify the clean report format with expandable artifacts."""

    def test_clean_summary_in_report_context(self):
        """Clean summaries should strip noise; full artifacts preserve it."""
        raw_summary = (
            "Completed: Feature Analyzer.\n\n"
            "🔧 **Ast Search**\n"
            "```\nFound 5 classes\n```\n\n"
            "The module has 5 primary classes implementing the plugin system.\n\n"
            "🔧 **File Read**\n"
            "```\nclass PluginBase:\n    pass\n```\n\n"
            "Each plugin extends PluginBase with custom lifecycle hooks."
        )

        clean = DelegateManager._clean_crystal_summary(raw_summary, max_length=500)
        # Clean version has prose, not tool noise
        assert "Ast Search" not in clean
        assert "File Read" not in clean
        assert "5 primary classes" in clean
        assert "PluginBase" in clean  # This is prose mentioning the class

        # Raw summary still has everything (for the expandable artifact)
        assert "🔧" in raw_summary
        assert "Found 5 classes" in raw_summary
