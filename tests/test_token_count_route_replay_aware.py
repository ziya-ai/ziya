"""
/api/token-count must count assistant history in the form the model will
actually receive, not the form the browser saved.

server.py runs assistant messages through rewrite_tool_history before the
prompt is built: tool blocks are re-encoded and any inline
``<img src="data:image/…">`` (what renderToolPart.ts prepends for a
view_image / render_diagram / recall_image result) becomes a one-line note.
The token bar's Chat figure was computed from message.content verbatim, so
one 100 KB PNG showed as tens of thousands of tokens that are never sent.

Two hops: the frontend has to send ``role`` (the rewrite touches only
assistant turns), and the route has to apply the rewrite before counting.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.routes.token_routes import (
    TokenCountMessage, TokenCountRequest, count_tokens,
)
from app.utils.tool_history_rewrite import INLINE_IMAGE_NOTE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

B64 = "iVBORw0KGgo" * 2000  # ~22 KB of base64, as text
IMG_TAG = (f'<img src="data:image/png;base64,{B64}" alt="Rendered diagram" '
           'style="max-width:100%;border-radius:8px;margin:8px 0;" />')
FENCE = ("````tool:mcp_view_image|🔐 View Image|text\n"
         "Image file .ziya/gfx-sweep/drift/x.png (PNG, 21.9 KB)\n````")
PROSE_BEFORE = "Looking at the golden render."
PROSE_AFTER = "The layout is correct."
WITH_IMAGE = f"{PROSE_BEFORE}\n\n{IMG_TAG}\n\n{FENCE}\n\n{PROSE_AFTER}"
WITHOUT_IMAGE = f"{PROSE_BEFORE}\n\n{INLINE_IMAGE_NOTE}\n\n{FENCE}\n\n{PROSE_AFTER}"


def _count(messages):
    req = TokenCountRequest(messages=[TokenCountMessage(**m) for m in messages])
    return asyncio.run(count_tokens(req))["token_count"]


def test_assistant_inline_image_is_not_counted_as_text():
    with_img = _count([{"role": "assistant", "content": WITH_IMAGE}])
    without = _count([{"role": "assistant", "content": WITHOUT_IMAGE}])
    # Positive control that the fixture is expensive when counted verbatim:
    # 22 KB of base64 is thousands of tokens on any tokenizer.
    verbatim = _count([{"role": "human", "content": WITH_IMAGE}])
    assert verbatim > without + 2000
    # The assistant count is the replayed form, not the saved form.  Loose
    # bound: fence re-encoding shifts a handful of tokens either way.
    assert abs(with_img - without) < 40, (with_img, without, verbatim)
    assert with_img < verbatim / 3


def test_human_content_is_counted_as_saved():
    # The rewrite touches assistant turns only; a user message is sent as-is.
    verbatim = _count([{"role": "human", "content": WITH_IMAGE}])
    assert verbatim > 2000
    # No role (older frontend) also counts as saved rather than guessing.
    assert _count([{"content": WITH_IMAGE}]) == verbatim


def test_muted_messages_still_excluded_and_images_still_priced():
    msgs = [
        {"role": "assistant", "content": WITH_IMAGE, "muted": True},
        {"role": "human", "content": "hi",
         "images": [{"width": 1000, "height": 1000, "mediaType": "image/png"}]},
    ]
    n = _count(msgs)
    # 1 MP image ~1500 tokens (see _estimate_image_tokens) plus "hi".
    assert 1490 < n < 1520, n


def test_frontend_sends_role_to_the_estimator():
    # Seam: without the role the route cannot tell an assistant turn from a
    # user one and the whole fix is inert.
    src = open(os.path.join(
        ROOT, "frontend", "src", "components", "TokenCountDisplay.tsx"),
        encoding="utf-8").read()
    start = src.index("const getTokenCountForMessages")
    body = src[start:src.index("return postTokenCount", start)]
    assert "role: msg.role" in body
