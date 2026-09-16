"""
Rewrite rendered tool-result blocks in assistant history for the model.

The frontend persists tool results into ``message.content`` in the same
encoding it uses to *render* them (chatApi.ts, tool_display handler):

  1. a four-backtick fence whose info string is ``tool:NAME|HEADER|SYNTAX``
     (shell, file, and most other tools), or
  2. an HTML-comment pair ``<!-- TOOL_BLOCK_START:NAME|HEADER|ID -->`` …
     ``<!-- TOOL_BLOCK_END:NAME|ID -->`` wrapping a JSON payload meant for
     MarkdownRenderer (search-type tools with hierarchicalResults).

Neither was designed as model input, but both are replayed verbatim on every
later turn because history is sent as-is.  After a few dozen tool-heavy
turns the model's context holds dozens of in-context "examples" of an
assistant turn that *contains tool output as text*, and it starts producing
that text itself instead of calling the tool (df488630 turn 47: a narrated
file_write with a guessed ``{'success': True, …}`` body).  The rendered
header also carries the ``|🔐`` badge that Layer B's parrot detector keys
on — the history literally teaches the pattern the detector punishes.

This module rewrites each block into a plain demarcated envelope::

    ‹tool_result trust="high" tool="run_shell_command" label="Shell: ls"›
    …full body, unchanged…
    ‹/tool_result›

Nothing is dropped or truncated: the body is kept whole so the model's
recall of what a file contained or what a command printed is unaffected.
Only the *encoding* changes, from something the renderer and the
fake-tool detector treat as special to something that is inert text.

The envelope uses ‹ › (U+2039/U+203A), not the ``<tool_result>`` tags of
``app.mcp.tool_result_demarcation``: those tags mark a *live* result in a
user-role tool_result block, and an assistant-role message containing them
would itself look like fabricated tool output.  Any ``<tool_result`` or
``‹tool_result`` lookalike inside a body is defanged so a body can never
close the envelope early.

The rewrite is a pure function of the message text, so a message rewrites
to the same bytes on every turn and prompt-cache prefixes over history are
unaffected.  It never touches user messages.

Kill switch: ``ZIYA_DISABLE_TOOL_HISTORY_REWRITE=1``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.mcp.tool_result_demarcation import classify_trust
from app.utils.logging_utils import logger

_ASSISTANT_TYPES = frozenset({"ai", "assistant"})

# Shape 1: fence.  Same-width closer on its own line (backreference), so a
# three-backtick fence inside a four-backtick body is body, not a closer.
_FENCE_RE = re.compile(
    r'^(`{3,})tool:([^|\n`]+)\|([^\n]*?)\|([^\n|]*)\n(.*?)\n\1[ \t]*$',
    re.MULTILINE | re.DOTALL,
)

# Shape 2: HTML-comment block.  The header may itself contain ``|`` (search
# queries do), so it is everything up to `` -->`` and the trailing
# ``|toolu_…`` id is peeled off afterwards.
_HTML_RE = re.compile(
    r'<!-- TOOL_BLOCK_START:([^|\s]+)\|(.*?) -->\n(.*?)\n'
    r'<!-- TOOL_BLOCK_END:\1(?:\|[^>\s]*)? -->',
    re.DOTALL,
)
_TRAILING_ID_RE = re.compile(r'\|toolu_[A-Za-z0-9_]+$')

# Placeholder left when a stream ended before its tool_display arrived.
_ORPHAN_MARKER_RE = re.compile(r'<!-- TOOL_MARKER:[^>]*-->\n?')

# Any envelope lookalike inside a body, in either bracket alphabet.
_LOOKALIKE_RE = re.compile(r'[<‹](\s*/?\s*tool_result\b)', re.IGNORECASE)

_OPEN_FMT = '‹tool_result trust="{trust}" tool="{tool}" label="{label}"›'
_CLOSE = '‹/tool_result›'


@dataclass(frozen=True)
class ToolBlock:
    kind: str        # 'fence' | 'html'
    start: int
    end: int
    tool: str
    header: str
    syntax: str      # fence only; '' for html
    body: str        # fence: verbatim; html: unpacked JSON as text


def _clean_label(header: str) -> str:
    """Drop the UI verification badge and normalise whitespace/quotes."""
    label = header.replace("🔐", " ")
    label = re.sub(r'\s+', ' ', label).strip()
    return label.replace('"', "'")


def _defang(body: str) -> str:
    return _LOOKALIKE_RE.sub(lambda m: '[' + m.group(1), body)


def _unpack_structured(payload: str) -> str:
    """Render the MarkdownRenderer JSON payload as plain text.

    Falls back to the raw payload if it is not the expected shape, so a
    block is never silently emptied.
    """
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return payload
    if not isinstance(data, dict):
        return payload
    parts: List[str] = []
    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary.strip())
    results = data.get("hierarchicalResults")
    if isinstance(results, list):
        for r in results:
            if not isinstance(r, dict):
                continue
            title = r.get("title")
            content = r.get("content")
            if isinstance(title, str) and title.strip():
                parts.append(title.strip())
            if isinstance(content, str) and content.strip():
                parts.append(content.rstrip())
    if not parts:
        return payload
    return "\n".join(parts)


def find_tool_blocks(text: str) -> List[ToolBlock]:
    """Locate every rendered tool block in ``text``, in document order."""
    blocks: List[ToolBlock] = []
    for m in _FENCE_RE.finditer(text):
        blocks.append(ToolBlock(
            kind="fence", start=m.start(), end=m.end(),
            tool=m.group(2).strip(), header=m.group(3),
            syntax=m.group(4).strip(), body=m.group(5),
        ))
    for m in _HTML_RE.finditer(text):
        header = _TRAILING_ID_RE.sub('', m.group(2))
        blocks.append(ToolBlock(
            kind="html", start=m.start(), end=m.end(),
            tool=m.group(1).strip(), header=header, syntax='',
            body=_unpack_structured(m.group(3)),
        ))
    blocks.sort(key=lambda b: b.start)
    return blocks


def render_envelope(block: ToolBlock) -> str:
    return "\n".join([
        _OPEN_FMT.format(
            trust=classify_trust(block.tool),
            tool=block.tool.replace('"', "'"),
            label=_clean_label(block.header),
        ),
        _defang(block.body),
        _CLOSE,
    ])


def rewrite_assistant_text(text: str) -> str:
    """Replace every rendered tool block in one assistant message."""
    if not text or ("tool:" not in text and "TOOL_BLOCK_START" not in text
                    and "TOOL_MARKER" not in text):
        return text
    blocks = find_tool_blocks(text)
    if blocks:
        out: List[str] = []
        pos = 0
        for b in blocks:
            if b.start < pos:
                continue  # overlapping match; keep the earlier one
            out.append(text[pos:b.start])
            out.append(render_envelope(b))
            pos = b.end
        out.append(text[pos:])
        text = "".join(out)
    return _ORPHAN_MARKER_RE.sub('', text)


def _rewrite_content(content: Any) -> Any:
    if isinstance(content, str):
        return rewrite_assistant_text(content)
    if isinstance(content, list):
        new = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                block = {**block, "text": rewrite_assistant_text(block["text"])}
            new.append(block)
        return new
    return content


def is_enabled() -> bool:
    return os.environ.get("ZIYA_DISABLE_TOOL_HISTORY_REWRITE", "").strip() \
        not in ("1", "true", "TRUE", "yes")


def rewrite_tool_history(history: List[Any]) -> List[Any]:
    """Rewrite tool blocks in every assistant message of ``history``.

    ``history`` is the processed_chat_history list built in
    ``build_messages_for_streaming`` — dicts with ``type`` and ``content``.
    Non-dict entries and non-assistant messages pass through untouched.
    Returns a new list; input is not mutated.
    """
    if not is_enabled():
        return history
    out: List[Any] = []
    rewritten = 0
    for msg in history:
        if isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES:
            content = msg.get("content")
            new_content = _rewrite_content(content)
            if new_content is not content:
                rewritten += 1
                msg = {**msg, "content": new_content}
        out.append(msg)
    if rewritten:
        logger.debug("🧹 TOOL_HISTORY_REWRITE: rewrote %d assistant message(s)", rewritten)
    return out


__all__ = [
    "ToolBlock",
    "find_tool_blocks",
    "render_envelope",
    "rewrite_assistant_text",
    "rewrite_tool_history",
    "is_enabled",
]
