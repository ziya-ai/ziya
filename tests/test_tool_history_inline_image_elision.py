"""
Inline rendered images must not be replayed to the model as text.

renderToolPart.ts prepends ``<img src="data:image/png;base64,…">`` to the
tool block for any image-carrying result (render_diagram, view_image,
recall_image — tool_execution.py sets ``image_data`` for all three).  That
tag lives in message.content and getApiResponse sends content verbatim, so
without this pass every later turn re-sends the base64 as assistant TEXT:
useless to the model as vision, and ~tens of thousands of tokens per image.

The model saw the real image once, in the turn it was produced, through the
tool_result content block.  From the next turn on, only a note remains.
"""
from __future__ import annotations

import os

import pytest

from app.utils import tool_history_rewrite as thr
from app.utils.tool_history_rewrite import (
    INLINE_IMAGE_NOTE, elide_inline_images, rewrite_tool_history,
)

B64 = "iVBORw0KGgo" * 400  # ~4.4 KB, stands in for a real PNG
IMG_TAG = (f'<img src="data:image/png;base64,{B64}" alt="Rendered diagram" '
           'style="max-width:100%;border-radius:8px;margin:8px 0;" />')
FENCE = ("````tool:mcp_view_image|🔐 View Image|text\n"
         "Image file .ziya/gfx-sweep/drift/x.png (PNG, 4.3 KB)\n````")
# Exactly what renderToolPart.display yields, spliced into assistant text.
ASSISTANT = f"Looking at the golden.\n\n{IMG_TAG}\n\n{FENCE}\n\nThe render is fine."


def _history():
    return [
        {"type": "human", "content": "check the golden"},
        {"type": "ai", "content": ASSISTANT},
        {"type": "human", "content": "and now?"},
    ]


def test_view_image_data_uri_is_replaced_on_replay():
    out = rewrite_tool_history(_history(), elide_redundant=False)
    ai = out[1]["content"]
    assert "data:image" not in ai
    assert B64 not in ai
    assert INLINE_IMAGE_NOTE in ai
    # Surrounding prose and the (re-encoded) tool block survive.
    assert "Looking at the golden." in ai
    assert "The render is fine." in ai
    assert "‹tool_result" in ai and "PNG, 4.3 KB" in ai


def test_positive_control_unpatched_history_carries_the_bytes():
    # Guards the test itself: the fixture really does contain what the
    # rewrite is supposed to remove.
    assert B64 in _history()[1]["content"]


def test_user_messages_are_never_touched():
    hist = [{"type": "human", "content": f"look: {IMG_TAG}"}]
    out = rewrite_tool_history(hist, elide_redundant=False)
    assert out[0]["content"] == hist[0]["content"]


def test_elided_even_when_block_rewrite_is_kill_switched(monkeypatch):
    monkeypatch.setenv("ZIYA_DISABLE_TOOL_HISTORY_REWRITE", "1")
    assert not thr.is_enabled()
    out = rewrite_tool_history(_history(), elide_redundant=False)
    ai = out[1]["content"]
    assert B64 not in ai and INLINE_IMAGE_NOTE in ai
    # Block re-encoding stayed off: the fence is still a fence.
    assert "````tool:mcp_view_image" in ai


def test_content_block_list_form():
    hist = [{"type": "assistant", "content": [
        {"type": "text", "text": f"a {IMG_TAG} b"},
        {"type": "tool_use", "id": "t1", "name": "view_image", "input": {}},
    ]}]
    out = rewrite_tool_history(hist, elide_redundant=False)
    blocks = out[0]["content"]
    assert blocks[0]["text"] == f"a {INLINE_IMAGE_NOTE} b"
    assert blocks[1] == hist[0]["content"][1]


@pytest.mark.parametrize("tag", [
    "<img src='data:image/jpeg;base64,AAAA'>",
    "<IMG SRC=\"data:image/svg+xml;base64,AAAA\"/>",
    '<img alt="x" src="data:image/webp;base64,AAAA" />',
])
def test_tag_variants(tag):
    assert elide_inline_images(f"pre {tag} post") == f"pre {INLINE_IMAGE_NOTE} post"


def test_non_data_images_and_plain_text_untouched():
    s = 'see <img src="https://example.com/a.png"> and data:image mentioned in prose'
    assert elide_inline_images(s) == s
    assert elide_inline_images("") == ""
