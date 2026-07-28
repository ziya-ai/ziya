"""
Image tool-result compaction helpers.

A tool result carrying structured image content blocks (e.g. from
render_diagram) reaches the model exactly one way: as part of the
tool_result message appended to the conversation for the NEXT LLM call.
(The ``tool_result_for_model`` stream event is display-loop plumbing —
every consumer drops it, so it never feeds the model.)

The intended lifecycle is therefore two-phase:

  1. Append the image blocks to the conversation INTACT so the next
     model call actually sees the image (vision input).
  2. After that call has consumed it, compact the blocks to their text
     summary so subsequent iterations don't re-send hundreds of KB of
     base64 per diagram.

Historically both phases were collapsed into one — the compaction ran
*before* the append — so the model never saw any image.  These helpers
implement the two phases separately; extracted from
app/streaming_tool_executor.py so the behavior is unit-testable without
driving the full streaming loop.
"""

from typing import Any, Dict, List

# Placeholder used when the image had no accompanying text block but HAS
# been delivered to the model once.
IMAGE_SEEN_PLACEHOLDER = (
    "[Image result — delivered to the model in the iteration it was produced]"
)

# Placeholder used when the active provider's tool-result format cannot
# carry image content blocks at all, so the image was never deliverable.
IMAGE_OMITTED_PLACEHOLDER = (
    "[Image result omitted — the active provider cannot accept image "
    "content in tool results]"
)


def has_image_blocks(content: Any) -> bool:
    """True iff ``content`` is a content-block list containing an image."""
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "image" for b in content
    )


def image_blocks_to_text(
    content: List[Any], placeholder: str = IMAGE_SEEN_PLACEHOLDER,
) -> str:
    """Reduce a content-block list to its text parts (or a placeholder)."""
    texts = [
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    joined = " ".join(t for t in texts if t).strip()
    return joined or placeholder


def compact_prior_image_results(conversation: List[Dict[str, Any]]) -> int:
    """Phase 2: sweep the conversation for tool_result blocks still
    carrying image content and replace each with its text summary.

    Called at every iteration boundary BEFORE new tool results are
    appended — anything found here was appended in a prior iteration and
    has been seen by exactly one model call, so the base64 payload is now
    dead weight.

    Only Anthropic/Bedrock-format messages (``content`` lists holding
    ``tool_result`` blocks) can carry image lists; other provider formats
    are skipped structurally.  Mutates ``conversation`` in place and
    returns the number of blocks compacted.
    """
    compacted = 0
    for msg in conversation:
        if not isinstance(msg, dict):
            continue
        mc = msg.get("content")
        if not isinstance(mc, list):
            continue
        for blk in mc:
            if not (isinstance(blk, dict) and blk.get("type") == "tool_result"):
                continue
            if has_image_blocks(blk.get("content")):
                blk["content"] = image_blocks_to_text(blk["content"])
                compacted += 1
    return compacted
