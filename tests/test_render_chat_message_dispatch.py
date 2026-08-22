"""
Seam tests for the chat-message branch of render_diagram.

The module under the branch (app/utils/chat_screenshot.py) has its own
28-test suite; these tests cover the CONNECTION — the exact class of
defect where both halves are correct but never meet:

  1. execute(type="chat-message") must reach _render_chat_message (not the
     plugin gate, not the LaTeX branch, not an unsupported error).
  2. _workspace_path must be FORWARDED, not discarded.  The pre-existing
     code popped it into the void; the chat-message renderer needs it to
     resolve which project to seed.  A revert of that one line would leave
     every render resolving against the server cwd with no error.
  3. The neighboring dispatch paths (LaTeX, unsupported-type error) must
     be unaffected, and the unsupported error must now mention
     chat-message so a model who guesses a wrong type learns it exists.

All tests drive the real execute() with the renderer method stubbed, so
no browser or server is needed.
"""
import asyncio
import unittest
from unittest.mock import patch

from app.mcp.tools.diagram_render import (
    CHAT_MESSAGE_TYPES,
    SUPPORTED_DIAGRAM_TYPES,
    RenderDiagramTool,
)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestChatMessageDispatch(unittest.TestCase):
    def _execute_capturing(self, **kwargs):
        """Run execute() with _render_chat_message stubbed; return the call."""
        captured = {}

        async def fake(self_, definition, *, theme, role, workspace_path):
            captured.update(
                definition=definition, theme=theme, role=role,
                workspace_path=workspace_path,
            )
            return {"content": [{"type": "text", "text": "stubbed"}]}

        tool = RenderDiagramTool()
        with patch.object(RenderDiagramTool, "_render_chat_message", fake):
            result = run(tool.execute(**kwargs))
        return captured, result

    def test_chat_message_reaches_renderer(self):
        captured, result = self._execute_capturing(
            type="chat-message", definition="Some $x^2$ markdown.",
        )
        self.assertEqual(captured.get("definition"), "Some $x^2$ markdown.")
        self.assertEqual(result["content"][0]["text"], "stubbed")

    def test_all_aliases_dispatch(self):
        # Every alias in CHAT_MESSAGE_TYPES must route to the same branch;
        # membership in the set alone proves nothing about dispatch order.
        for alias in sorted(CHAT_MESSAGE_TYPES):
            captured, _ = self._execute_capturing(
                type=alias, definition="body",
            )
            self.assertEqual(
                captured.get("definition"), "body",
                f"alias {alias!r} did not reach _render_chat_message",
            )

    def test_workspace_path_forwarded_not_discarded(self):
        captured, _ = self._execute_capturing(
            type="chat-message", definition="d",
            _workspace_path="/some/project/root",
        )
        self.assertEqual(captured.get("workspace_path"), "/some/project/root")

    def test_role_and_theme_forwarded(self):
        captured, _ = self._execute_capturing(
            type="chat-message", definition="d", role="human", theme="dark",
        )
        self.assertEqual(captured.get("role"), "human")
        self.assertEqual(captured.get("theme"), "dark")

    def test_role_defaults_to_assistant(self):
        captured, _ = self._execute_capturing(type="markdown", definition="d")
        self.assertEqual(captured.get("role"), "assistant")

    def test_case_and_whitespace_normalized(self):
        captured, _ = self._execute_capturing(
            type="  Chat-Message ", definition="d",
        )
        self.assertEqual(captured.get("definition"), "d")


class TestNeighboringPathsUnaffected(unittest.TestCase):
    def test_chat_types_not_in_plugin_set(self):
        # If a chat-message alias leaks into SUPPORTED_DIAGRAM_TYPES, the
        # plugin gate would admit it and the browser orchestrator would hang
        # looking for a plugin that does not exist.
        self.assertFalse(CHAT_MESSAGE_TYPES & SUPPORTED_DIAGRAM_TYPES)

    def test_unsupported_type_error_mentions_chat_message(self):
        tool = RenderDiagramTool()
        result = run(tool.execute(type="uml", definition="whatever"))
        text = result["content"][0]["text"]
        self.assertIn("Error", text)
        self.assertIn("chat-message", text)

    def test_unsupported_type_never_reaches_chat_renderer(self):
        called = []

        async def fake(self_, *a, **k):
            called.append(True)
            return {"content": [{"type": "text", "text": "x"}]}

        tool = RenderDiagramTool()
        with patch.object(RenderDiagramTool, "_render_chat_message", fake):
            run(tool.execute(type="uml", definition="whatever"))
        self.assertEqual(called, [])

    def test_latex_still_dispatches_before_everything(self):
        # The LaTeX branch precedes chat-message; a reorder that put the
        # chat branch first would only matter if the sets overlapped, but
        # assert the LaTeX path still works end-to-end at dispatch level.
        called = []

        async def fake_latex(self_, *a, **k):
            called.append(a)
            return {"content": [{"type": "text", "text": "latex"}]}

        tool = RenderDiagramTool()
        with patch.object(RenderDiagramTool, "_render_latex_direct", fake_latex):
            result = run(tool.execute(type="tikz", definition="\\draw;"))
        self.assertTrue(called)
        self.assertEqual(result["content"][0]["text"], "latex")


if __name__ == "__main__":
    unittest.main()
