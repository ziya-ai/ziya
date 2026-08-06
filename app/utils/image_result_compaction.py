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

Retention window
----------------
Phase 2 originally compacted EVERY prior image, giving each render a
lifetime of exactly one model call.  In interactive chat that is close to
"one turn".  Under a Task Card it is not: the whole run is a single turn
made of many tool iterations, so an image vanished one iteration after it
was produced while the model kept working for another twenty.  The
observable failure was not cost but epistemics — the model would see its
own earlier "the render is broken" with no image behind it and retract a
correct conclusion.  ``keep_recent``/``max_bytes`` make the window a
policy rather than a constant.
"""

from typing import Any, Dict, Iterable, List, Optional, Set

# Placeholder for an image that HAS been delivered to the model and is now
# elided.  Deliberately addressed to the model, not to a human reading the
# transcript: the previous wording ("delivered to the model in the
# iteration it was produced") stated a fact about plumbing and said nothing
# about whether earlier conclusions still hold, so the model treated its
# own prior observation as unsupported and re-judged it.
IMAGE_SEEN_PLACEHOLDER = (
    "[Image elided to save context. You DID see this image when it was "
    "produced; any conclusion you drew from it then was based on direct "
    "observation and remains valid. Do NOT retract, soften, or re-litigate "
    "it merely because the pixels are no longer in view. If you genuinely "
    "need to look again, re-run the render tool.]"
)

# Placeholder used when the active provider's tool-result format cannot
# carry image content blocks at all, so the image was never deliverable.
IMAGE_OMITTED_PLACEHOLDER = (
    "[Image result omitted — the active provider cannot accept image "
    "content in tool results]"
)

# Mode-aware defaults.  Interactive turns keep only the newest image: the
# user can see the render in the UI, and the next human message re-anchors
# the thread.  Batch runs (Task Cards, delegates, goals) have no human in
# the loop to re-anchor, and the whole run is ONE turn, so they keep a
# short window.
DEFAULT_KEEP_RECENT_INTERACTIVE = 1
DEFAULT_KEEP_RECENT_BATCH = 3

# Ceiling on retained base64 payload, in characters.  A window expressed
# only as a count is unbounded in bytes — three 4 MB plotly renders is not
# the same budget as three 40 KB mermaid ones.
DEFAULT_MAX_IMAGE_BYTES = 6 * 1024 * 1024


def recall_hint(handle: Optional[str]) -> str:
    """The sentence that makes an elided image retrievable.

    Kept separate from the placeholder because it is only truthful when a
    stash actually succeeded — advertising a handle that ``retrieve`` will
    refuse is worse than saying nothing, since the model would then read a
    failed recall as evidence its earlier observation was unsound.
    """
    if not handle:
        return ""
    return (
        f" If you need to look again, call recall_image with "
        f"handle=\"{handle}\" — this returns the SAME pixels, not a "
        f"re-render, so it cannot disagree with what you saw."
    )


def has_image_blocks(content: Any) -> bool:
    """True iff ``content`` is a content-block list containing an image."""
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "image" for b in content
    )


def image_blocks_to_text(
    content: List[Any], placeholder: str = IMAGE_SEEN_PLACEHOLDER,
    notice: Optional[str] = None,
) -> str:
    """Reduce a content-block list to its text parts (or a placeholder).

    ``notice`` is APPENDED to surviving text rather than replacing it.
    This distinction is the whole point: every real image result carries a
    descriptive text block (render_diagram always emits one), so a
    placeholder used only as a no-text fallback never fired on the actual
    path — the model saw a bare "Rendered mermaid diagram (PNG, 42.0 KB)"
    with no indication an image had ever been there, which is precisely
    what led it to disown its own visual findings.

    Left as None by callers for whom the notice would be a lie — notably
    the provider-cannot-accept-images path, where the image was never
    delivered at all.
    """
    texts = [
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    joined = " ".join(t for t in texts if t).strip()
    if joined:
        return f"{joined}\n\n{notice}" if notice else joined
    return notice or placeholder


def image_payload_bytes(content: Any) -> int:
    """Approximate retained cost of a content-block list, in characters of
    base64.  Text blocks are ignored — they survive compaction anyway, so
    they are not part of what the window is budgeting."""
    if not isinstance(content, list):
        return 0
    total = 0
    for b in content:
        if isinstance(b, dict) and b.get("type") == "image":
            data = (b.get("source") or {}).get("data")
            if isinstance(data, str):
                total += len(data)
    return total


def _image_result_blocks(
    conversation: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Every tool_result block in the conversation still carrying an image,
    in document (oldest-first) order.  Only Anthropic/Bedrock-format
    messages can hold image lists; other provider formats are skipped
    structurally."""
    found: List[Dict[str, Any]] = []
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
                found.append(blk)
    return found


def compact_prior_image_results(
    conversation: List[Dict[str, Any]],
    keep_recent: int = 0,
    max_bytes: Optional[int] = None,
    pinned_tool_use_ids: Optional[Iterable[str]] = None,
    recall_scope: Optional[str] = None,
) -> int:
    """Phase 2: sweep the conversation for tool_result blocks still
    carrying image content and replace each with its text summary.

    Called at every iteration boundary BEFORE new tool results are
    appended — anything found here was appended in a prior iteration, so
    it has been seen at least once.

    ``keep_recent`` retains that many of the NEWEST image results
    verbatim, so a batch run iterating on a diagram can still see what it
    just produced.  ``max_bytes`` caps the retained base64 payload; a
    single render larger than the remaining budget ends the window rather
    than blowing it.  ``pinned_tool_use_ids`` are retained regardless of
    both, for images the caller explicitly asked to hold.

    When ``recall_scope`` is given (a conversation or run id), each
    compacted image is stashed in ``app.utils.image_recall`` under that
    scope and its handle embedded in the replacement text, so the model
    can page it back into view.  Omit it to compact destructively — the
    behavior callers had before recall existed.

    The default (``keep_recent=0``) is the original keep-nothing behavior,
    so callers that pass no policy are unaffected.

    Mutates ``conversation`` in place and returns the number of blocks
    compacted.
    """
    blocks = _image_result_blocks(conversation)
    if not blocks:
        return 0

    keep: Set[int] = set()
    budget = max_bytes
    # Newest-first: the window is anchored at the present, not the past.
    for blk in reversed(blocks):
        if len(keep) >= max(keep_recent, 0):
            break
        cost = image_payload_bytes(blk.get("content"))
        if budget is not None and cost > budget:
            break
        keep.add(id(blk))
        if budget is not None:
            budget -= cost

    if pinned_tool_use_ids:
        pins = set(pinned_tool_use_ids)
        for blk in blocks:
            if blk.get("tool_use_id") in pins:
                keep.add(id(blk))

    compacted = 0
    for blk in blocks:
        if id(blk) in keep:
            continue
        handle = None
        if recall_scope:
            # Stash BEFORE the reference is dropped.  A failed stash yields
            # no handle and therefore no recall hint, so the text never
            # promises a lookup that would fail.
            from app.utils import image_recall
            handle = image_recall.stash(
                blk["content"], scope=recall_scope,
                label=image_blocks_to_text(blk["content"], "")[:120],
            )
        blk["content"] = image_blocks_to_text(
            blk["content"],
            notice=IMAGE_SEEN_PLACEHOLDER + recall_hint(handle),
        )
        compacted += 1
    return compacted
