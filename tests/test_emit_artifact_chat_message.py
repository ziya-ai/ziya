"""emit_artifact must dispatch chat-message renders the way render_diagram does.

Regression: the diagram path in emit_artifact posted every type to the
/render harness.  'chat-message' has no plugin there, so the page waited
out its 30s safety timeout and every chat-message emit in the GFX Stage 2
run (36 of 348 parts, all six chat-message defects x 2 themes x 3 cycles)
was recorded as "Diagram render FAILED at emit time" while render_diagram
on the identical definition succeeded.  The seam asserted here: a
chat-message emit reaches ``render_chat_message`` and never touches the
diagram renderer; a plugin type does the reverse.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

emit_mod = pytest.importorskip("app.mcp.tools.emit_artifact")

from app.utils.task_artifacts import (  # noqa: E402
    finish_artifact_collection, start_artifact_collection,
)

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def tool():
    return emit_mod.EmitArtifactTool()


@pytest.mark.asyncio
@pytest.mark.parametrize("dtype", ["chat-message", "chat-markdown", "Chat-Message"])
async def test_chat_message_emit_uses_chat_screenshot_not_render_harness(
    tool, tmp_path, dtype,
):
    token = start_artifact_collection(
        block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
    )
    try:
        with patch("app.utils.chat_screenshot.render_chat_message",
                   new=AsyncMock(return_value=(FAKE_PNG, {"dom": {}}))) as chat, \
             patch("app.services.diagram_renderer.get_diagram_renderer") as harness:
            result = await tool.execute(
                name="fixed_light",
                diagram={"type": dtype, "definition": "$$x^2$$", "theme": "dark",
                         "role": "human"},
            )
    finally:
        parts = finish_artifact_collection(token)

    assert "recorded" in result["content"][0]["text"]
    harness.assert_not_called()
    chat.assert_awaited_once()
    kwargs = chat.await_args.kwargs
    assert chat.await_args.args[0] == "$$x^2$$"
    assert kwargs["theme"] == "dark"
    assert kwargs["role"] == "human"

    assert len(parts) == 1
    part = parts[0]
    assert part["part_type"] == "file" and part["rendered"] is True
    assert Path(part["file_uri"]).exists()
    assert "FAILED" not in str(part.get("text", ""))


@pytest.mark.asyncio
async def test_plugin_type_still_uses_render_harness(tool, tmp_path):
    token = start_artifact_collection(
        block_id="t", artifacts_dir=str(tmp_path / "artifacts"), run_id="r",
    )
    try:
        with patch("app.utils.chat_screenshot.render_chat_message",
                   new=AsyncMock()) as chat, \
             patch("app.services.diagram_renderer.get_diagram_renderer") as harness:
            renderer = AsyncMock()
            renderer.render_diagram_with_diagnostics = AsyncMock(
                return_value=(FAKE_PNG, {"console_warnings": [], "console_errors": []}),
            )
            harness.return_value = renderer
            await tool.execute(
                name="fixed_light",
                diagram={"type": "mermaid", "definition": "graph LR\n A-->B"},
            )
    finally:
        parts = finish_artifact_collection(token)

    chat.assert_not_awaited()
    renderer.render_diagram_with_diagnostics.assert_awaited_once()
    assert parts[0]["rendered"] is True
