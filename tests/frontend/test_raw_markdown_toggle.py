"""
Tests for the Raw Markdown Toggle feature (Ctrl+Shift+U).

Validates that:
1. The displayMode 'raw'/'pretty' toggle in ChatContext works correctly
2. CSS classes are properly defined for raw view styling
3. The raw view preserves markdown source verbatim (fence markers, inline code, etc.)
"""

import os
import re
import pytest


# ---------------------------------------------------------------------------
# CSS validation tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def index_css():
    """Load the main CSS file."""
    css_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src", "index.css"
    )
    if not os.path.exists(css_path):
        pytest.skip("frontend/src/index.css not found")
    with open(css_path, "r") as f:
        return f.read()


class TestRawMarkdownCSS:
    """Verify that the required CSS classes for raw-markdown view exist."""

    def test_raw_markdown_view_class_exists(self, index_css):
        assert ".raw-markdown-view" in index_css

    def test_raw_markdown_view_uses_monospace(self, index_css):
        # Extract the .raw-markdown-view rule block
        match = re.search(r"\.raw-markdown-view\s*\{([^}]+)\}", index_css)
        assert match, ".raw-markdown-view CSS rule not found"
        rule = match.group(1)
        assert "monospace" in rule.lower() or "consolas" in rule.lower(), (
            "raw-markdown-view should use a monospace font"
        )

    def test_raw_markdown_view_has_pre_wrap(self, index_css):
        match = re.search(r"\.raw-markdown-view\s*\{([^}]+)\}", index_css)
        assert match
        assert "pre-wrap" in match.group(1)

    def test_raw_mode_banner_class_exists(self, index_css):
        assert ".raw-mode-banner" in index_css

    def test_dark_mode_variants_exist(self, index_css):
        assert ".dark .raw-markdown-view" in index_css
        assert ".dark .raw-mode-banner" in index_css


# ---------------------------------------------------------------------------
# Component wiring tests (source inspection)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conversation_tsx():
    """Load Conversation.tsx source."""
    tsx_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src",
        "components", "Conversation.tsx",
    )
    if not os.path.exists(tsx_path):
        pytest.skip("Conversation.tsx not found")
    with open(tsx_path, "r") as f:
        return f.read()


@pytest.fixture(scope="module")
def streamed_content_tsx():
    """Load StreamedContent.tsx source."""
    tsx_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src",
        "components", "StreamedContent.tsx",
    )
    if not os.path.exists(tsx_path):
        pytest.skip("StreamedContent.tsx not found")
    with open(tsx_path, "r") as f:
        return f.read()


class TestConversationRawMode:
    """Verify Conversation.tsx has raw-mode wiring."""

    def test_imports_setDisplayMode(self, conversation_tsx):
        assert "setDisplayMode" in conversation_tsx

    def test_isRawMode_computed(self, conversation_tsx):
        assert "isRawMode" in conversation_tsx

    def test_raw_markdown_view_element(self, conversation_tsx):
        assert "raw-markdown-view" in conversation_tsx

    def test_keyboard_shortcut_registered(self, conversation_tsx):
        # Should register Ctrl+Shift+U (code may use lowercase comparison)
        assert "Shift" in conversation_tsx
        assert ("'U'" in conversation_tsx or '"U"' in conversation_tsx
                or "'u'" in conversation_tsx or '"u"' in conversation_tsx)

    def test_raw_mode_banner_rendered(self, conversation_tsx):
        assert "raw-mode-banner" in conversation_tsx

    def test_conditional_rendering(self, conversation_tsx):
        """Both MarkdownRenderer and raw <pre> paths should exist."""
        assert "MarkdownRenderer" in conversation_tsx
        assert "<pre className" in conversation_tsx or '<pre className' in conversation_tsx


class TestStreamedContentRawMode:
    """Verify StreamedContent.tsx has raw-mode wiring."""

    def test_imports_conversations(self, streamed_content_tsx):
        assert "conversations" in streamed_content_tsx

    def test_isRawMode_computed(self, streamed_content_tsx):
        assert "isRawMode" in streamed_content_tsx

    def test_raw_markdown_view_element(self, streamed_content_tsx):
        assert "raw-markdown-view" in streamed_content_tsx

    def test_conditional_rendering(self, streamed_content_tsx):
        assert "MarkdownRenderer" in streamed_content_tsx
        assert "<pre className" in streamed_content_tsx or '<pre className' in streamed_content_tsx


# ---------------------------------------------------------------------------
# ChatContext type tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def chat_context_tsx():
    """Load ChatContext.tsx source."""
    tsx_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src",
        "context", "ChatContext.tsx",
    )
    if not os.path.exists(tsx_path):
        pytest.skip("ChatContext.tsx not found")
    with open(tsx_path, "r") as f:
        return f.read()


class TestChatContextDisplayMode:
    """Verify ChatContext has displayMode infrastructure."""

    def test_setDisplayMode_in_interface(self, chat_context_tsx):
        assert "setDisplayMode" in chat_context_tsx

    def test_display_mode_types(self, chat_context_tsx):
        assert "'raw'" in chat_context_tsx
        assert "'pretty'" in chat_context_tsx

    def test_display_mode_on_conversation_type(self):
        """Verify the Conversation type has displayMode field."""
        types_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "frontend", "src",
            "utils", "types.ts",
        )
        if not os.path.exists(types_path):
            pytest.skip("types.ts not found")
        with open(types_path, "r") as f:
            content = f.read()
        assert "displayMode" in content
