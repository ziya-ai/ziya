"""Streaming markdown renderer for terminal output using rich."""

import re
from rich.console import Console
from rich.markdown import Markdown


class StreamingMarkdownRenderer:
    """Renders streamed markdown chunks to the terminal via rich.

    Buffers incoming text by line and groups lines into renderable blocks
    (paragraphs, code blocks, tables). Complete blocks are flushed through
    rich.Markdown for proper ANSI rendering (bold, code highlighting, etc.).

    Usage::

        renderer = StreamingMarkdownRenderer()
        for chunk in stream:
            renderer.feed(chunk)
        renderer.flush()
    """

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self._chunk_buf = ""      # raw chars waiting for a newline
        self._block_buf = ""      # complete lines waiting for a block boundary
        self._in_code_block = False
        self._code_fence = ""

    def feed(self, chunk: str) -> None:
        """Accept a streaming chunk and render any complete blocks."""
        self._chunk_buf += chunk
        self._drain_lines()

    def flush(self) -> None:
        """Render whatever remains in the buffers (call at end of stream)."""
        # Push the trailing partial line into the block buffer
        if self._chunk_buf:
            self._block_buf += self._chunk_buf
            self._chunk_buf = ""
        if self._block_buf:
            self._render_block(self._block_buf)
            self._block_buf = ""
        self._in_code_block = False

    def _drain_lines(self) -> None:
        """Extract complete lines from the chunk buffer and process them."""
        while "\n" in self._chunk_buf:
            line, self._chunk_buf = self._chunk_buf.split("\n", 1)
            self._process_line(line)

    def _process_line(self, line: str) -> None:
        stripped = line.strip()

        # Inside a code block – accumulate until closing fence
        if self._in_code_block:
            self._block_buf += line + "\n"
            if self._is_closing_fence(stripped):
                self._render_block(self._block_buf)
                self._block_buf = ""
                self._in_code_block = False
            return

        # Opening code fence
        fence_match = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence_match:
            # Flush pending paragraph first
            if self._block_buf:
                self._render_block(self._block_buf)
                self._block_buf = ""
            self._in_code_block = True
            self._code_fence = fence_match.group(1)
            self._block_buf = line + "\n"
            return

        # Blank line – paragraph boundary
        if not stripped:
            if self._block_buf:
                self._render_block(self._block_buf)
                self._block_buf = ""
            self.console.print()
            return

        # Regular line – accumulate into current block
        self._block_buf += line + "\n"

    def _is_closing_fence(self, stripped: str) -> bool:
        """Check whether *stripped* closes the current code block."""
        if not self._code_fence:
            return False
        fence_char = self._code_fence[0]
        fence_len = len(self._code_fence)
        # A closing fence is the same char repeated at least as many times,
        # with nothing else on the line.
        if not stripped.startswith(fence_char * fence_len):
            return False
        return all(c == fence_char for c in stripped)

    def _render_block(self, text: str) -> None:
        """Render a completed markdown block through rich."""
        text = text.rstrip("\n")
        if not text:
            return
        md = Markdown(text)
        self.console.print(md)
