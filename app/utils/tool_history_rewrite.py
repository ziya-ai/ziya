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

Only the *encoding* changes, from something the renderer and the
fake-tool detector treat as special to something that is inert text.

The envelope uses ‹ › (U+2039/U+203A), not the ``<tool_result>`` tags of
``app.mcp.tool_result_demarcation``: those tags mark a *live* result in a
user-role tool_result block, and an assistant-role message containing them
would itself look like fabricated tool output.  Any ``<tool_result`` or
``‹tool_result`` lookalike inside a body is defanged so a body can never
close the envelope early.  The rewrite never touches user messages.

Redundant-body elision
----------------------
On top of the re-encoding, a body may be replaced by a one-line note when
it is PROVABLY REDUNDANT with a later result in the same history — that is,
when the same bytes are still in the model's view further down:

  * ``contained``: a read of file P whose body appears verbatim inside a
    LATER read of the same file P (a full read after a partial one, or a
    re-read of an unchanged file);
  * ``duplicate``: an identical body for the same tool recurring later
    (the same command re-run with the same output).

Both rules require the content to be literally present downstream, so the
model's recall is unaffected — it is the earlier *copy* that goes, never
the information.  Nothing is ever elided from the chat record itself; this
is replay-time only.  Two things are deliberately NOT done:

  * A re-read of a file whose content CHANGED does not elide the earlier
    read.  The persisted header is ``file read: PATH`` with no line range,
    so "different content, same path" is indistinguishable from "a
    different range of the same file", and eliding the latter would lose
    lines the model may still need.
  * Nothing is summarised, truncated, or digested.  Any size-based or
    age-based elision is a separate, weaker guarantee and lives elsewhere.

Eliding an earlier message when a LATER read arrives changes that earlier
message's bytes, so the prompt-cache prefix is invalidated from that point
on that turn.  This is accepted: the trade is one cache miss for a smaller
prompt on every turn after.  With elision disabled the rewrite is a pure
function of each message and cached prefixes are unaffected.

Kill switches: ``ZIYA_DISABLE_TOOL_HISTORY_REWRITE=1`` (everything),
``ZIYA_DISABLE_TOOL_RESULT_ELISION=1`` (elision only).  Elision is also a
per-project setting, ``contextManagement.elide_redundant_tool_results``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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

# --- Redundancy detection -------------------------------------------------

# Tools whose body is a file's contents and whose header names the path.
_FILE_READ_TOOLS = frozenset({"file_read", "read_file"})
_SHELL_TOOLS = frozenset({"run_shell_command"})
# "file read: app/x.py" — the builtin's display label (streaming_tool_executor).
_FILE_READ_LABEL_RE = re.compile(r"file read:\s*(\S+)", re.IGNORECASE)
# Shell bodies begin with the echoed command line: "$ cat path".
_SHELL_ECHO_RE = re.compile(r"^\$\s*(.+?)\s*$", re.MULTILINE)
# Commands whose output is (a range of) one file's contents.  The last
# non-flag token is the path.  Pipes/redirects disqualify.
_SHELL_READ_CMDS = ("cat ", "head ", "tail ", "sed -n ", "less ", "nl ")

# Bodies shorter than this are never elided: the note would be nearly as
# long as the body, and tiny bodies are the ones most likely to collide
# ("ok", "", "[]") without being the same result in any useful sense.
MIN_ELIDABLE_CHARS = 200


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


def _non_overlapping(blocks: List[ToolBlock]) -> List[ToolBlock]:
    """The blocks the rewrite will actually replace, in order.

    Shared by the planner and the rewriter so an elision decision made for
    "the Nth block of this text" lands on the same block.
    """
    out: List[ToolBlock] = []
    pos = 0
    for b in blocks:
        if b.start < pos:
            continue  # overlapping match; keep the earlier one
        out.append(b)
        pos = b.end
    return out


def render_envelope(block: ToolBlock, elision_note: Optional[str] = None) -> str:
    """Render one block.  With ``elision_note`` the body is replaced by it."""
    body = elision_note if elision_note is not None else _defang(block.body)
    return "\n".join([
        _OPEN_FMT.format(
            trust=classify_trust(block.tool),
            tool=block.tool.replace('"', "'"),
            label=_clean_label(block.header),
        ),
        body,
        _CLOSE,
    ])


def rewrite_assistant_text(text: str,
                           elisions: Optional[Dict[int, str]] = None) -> str:
    """Replace every rendered tool block in one assistant message.

    ``elisions`` maps a block's ordinal (its index among the blocks of this
    text, as returned by ``_non_overlapping(find_tool_blocks(text))``) to
    the note that replaces its body.
    """
    if not text or ("tool:" not in text and "TOOL_BLOCK_START" not in text
                    and "TOOL_MARKER" not in text):
        return text
    blocks = _non_overlapping(find_tool_blocks(text))
    if blocks:
        out: List[str] = []
        pos = 0
        for ordinal, b in enumerate(blocks):
            out.append(text[pos:b.start])
            note = elisions.get(ordinal) if elisions else None
            out.append(render_envelope(b, note))
            pos = b.end
        out.append(text[pos:])
        text = "".join(out)
    return _ORPHAN_MARKER_RE.sub('', text)


# --- Planning: which bodies are redundant with a later one ---------------

def _norm_tool(name: str) -> str:
    n = name or ""
    while n.startswith("mcp_"):
        n = n[4:]
    return n


def _norm_path(p: str) -> str:
    p = p.strip().strip("'\"`").rstrip(",;")
    if p.startswith("/"):
        return os.path.normpath(p)
    return os.path.normpath(p).lstrip("./")


def read_path_for_block(b: ToolBlock) -> Optional[str]:
    """The file path this block is a read of, or None if it is not a read."""
    tool = _norm_tool(b.tool)
    if tool in _FILE_READ_TOOLS:
        m = _FILE_READ_LABEL_RE.search(b.header or "")
        return _norm_path(m.group(1)) if m else None
    if tool in _SHELL_TOOLS:
        m = _SHELL_ECHO_RE.search(b.body or "")
        cmd = (m.group(1) if m else (b.header or "")).strip()
        cmd = re.sub(r"^Shell:\s*\$?\s*", "", cmd)
        if "|" in cmd or ">" in cmd or "&&" in cmd or ";" in cmd:
            return None
        for prefix in _SHELL_READ_CMDS:
            if cmd.startswith(prefix):
                toks = [t for t in cmd.split() if not t.startswith("-")]
                if len(toks) >= 2:
                    return _norm_path(toks[-1])
    return None


def payload_for_block(b: ToolBlock) -> str:
    """The body with the shell's echoed ``$ command`` header removed.

    Containment is judged on what the tool RETURNED, not on the command that
    produced it: ``sed -n 1,40p f`` and ``cat f`` echo different first lines
    but the former's output is a prefix of the latter's.
    """
    body = b.body or ""
    if _norm_tool(b.tool) in _SHELL_TOOLS and body.startswith("$ "):
        nl = body.find("\n")
        return body[nl + 1:] if nl >= 0 else ""
    return body


def _text_segments(content: Any) -> List[str]:
    """The text segments of a message, in the order ``_rewrite_content`` visits them."""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [blk["text"] for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
                and isinstance(blk.get("text"), str)]
    return []


def _note_contained(path: str, n: int) -> str:
    return (f"[Ziya: {n:,}-char result body elided on replay. The same file "
            f"({path}) is read again later in this conversation and that later "
            f"result contains this content in full; refer to it. Nothing was "
            f"removed from the record.]")


def _note_duplicate(n: int) -> str:
    return (f"[Ziya: {n:,}-char result body elided on replay. An identical "
            f"result body appears again later in this conversation; refer to "
            f"it. Nothing was removed from the record.]")


def plan_elisions(history: List[Any]) -> Dict[Tuple[int, int], Dict[int, str]]:
    """Decide which tool bodies are redundant with a later one.

    Returns ``{(msg_idx, seg_idx): {block_ordinal: note}}`` for every body
    that should be replaced.  Pure and side-effect free; exposed so the
    survey script and tests can inspect decisions directly.
    """
    # (msg_idx, seg_idx, ordinal, tool, path|None, payload)
    entries: List[Tuple[int, int, int, str, Optional[str], str]] = []
    for mi, msg in enumerate(history):
        if not (isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES):
            continue
        for si, text in enumerate(_text_segments(msg.get("content"))):
            if "tool:" not in text and "TOOL_BLOCK_START" not in text:
                continue
            for oi, b in enumerate(_non_overlapping(find_tool_blocks(text))):
                entries.append((mi, si, oi, _norm_tool(b.tool),
                                read_path_for_block(b), payload_for_block(b)))

    plan: Dict[Tuple[int, int], Dict[int, str]] = {}
    for i, (mi, si, oi, tool, path, payload) in enumerate(entries):
        if len(payload) < MIN_ELIDABLE_CHARS:
            continue
        note: Optional[str] = None
        for (mj, _sj, _oj, tool_j, path_j, payload_j) in entries[i + 1:]:
            if mj <= mi:
                continue  # only a LATER message can stand in for this one
            if path and path_j == path and payload in payload_j:
                note = _note_contained(path, len(payload))
                break
            if tool_j == tool and payload_j == payload:
                note = _note_duplicate(len(payload))
                break
        if note:
            plan.setdefault((mi, si), {})[oi] = note
    return plan


# --- Settings ------------------------------------------------------------

def is_enabled() -> bool:
    return os.environ.get("ZIYA_DISABLE_TOOL_HISTORY_REWRITE", "").strip() \
        not in ("1", "true", "TRUE", "yes")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip() in ("1", "true", "TRUE", "yes")


def resolve_elide_redundant(project_root: Optional[str] = None) -> bool:
    """Read ``contextManagement.elide_redundant_tool_results`` for the project.

    Same resolution path as ``chat_context_files.resolve_auto_add_token_limit``.
    Falls back to the model default when the project or setting is absent,
    and never raises.  The env kill switch wins over the setting.
    """
    if _env_truthy("ZIYA_DISABLE_TOOL_RESULT_ELISION"):
        return False
    # getattr: tolerate a ContextManagementSettings that predates this field.
    try:
        from app.models.project import ContextManagementSettings
        default = bool(getattr(ContextManagementSettings(),
                               "elide_redundant_tool_results", True))
    except Exception:
        default = True
    try:
        from app.context import get_project_root_or_none
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home

        root = (project_root or get_project_root_or_none()
                or os.environ.get("ZIYA_USER_CODEBASE_DIR"))
        if not root:
            return default
        project = ProjectStorage(get_ziya_home()).get_by_path(root)
        if not project or not project.settings:
            return default
        cm = project.settings.contextManagement
        val = getattr(cm, "elide_redundant_tool_results", None) if cm else None
        return default if val is None else bool(val)
    except Exception as e:
        logger.debug(f"resolve_elide_redundant: {e}")
        return default


# --- Entry point ---------------------------------------------------------

def _rewrite_content(content: Any,
                     elisions_by_seg: Optional[Dict[int, Dict[int, str]]]) -> Any:
    if isinstance(content, str):
        return rewrite_assistant_text(
            content, (elisions_by_seg or {}).get(0))
    if isinstance(content, list):
        new = []
        seg = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                block = {**block, "text": rewrite_assistant_text(
                    block["text"], (elisions_by_seg or {}).get(seg))}
                seg += 1
            new.append(block)
        return new
    return content


def rewrite_tool_history(history: List[Any],
                         elide_redundant: Optional[bool] = None) -> List[Any]:
    """Rewrite tool blocks in every assistant message of ``history``.

    ``history`` is the processed_chat_history list built in
    ``build_messages_for_streaming`` — dicts with ``type`` and ``content``.
    Non-dict entries and non-assistant messages pass through untouched.
    Returns a new list; input is not mutated.

    ``elide_redundant`` — replace bodies that are provably redundant with a
    later result (see module docstring).  ``None`` resolves the project
    setting; tests pass an explicit value.
    """
    if not is_enabled():
        return history
    if elide_redundant is None:
        elide_redundant = resolve_elide_redundant()
    plan = plan_elisions(history) if elide_redundant else {}

    out: List[Any] = []
    rewritten = 0
    for mi, msg in enumerate(history):
        if isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES:
            content = msg.get("content")
            by_seg: Dict[int, Dict[int, str]] = {}
            for (pmi, psi), notes in plan.items():
                if pmi == mi:
                    by_seg[psi] = notes
            new_content = _rewrite_content(content, by_seg or None)
            if new_content is not content:
                rewritten += 1
                msg = {**msg, "content": new_content}
        out.append(msg)
    if rewritten:
        n_elided = sum(len(v) for v in plan.values())
        logger.debug("🧹 TOOL_HISTORY_REWRITE: rewrote %d assistant message(s), "
                     "elided %d redundant body(ies)", rewritten, n_elided)
    return out


__all__ = [
    "ToolBlock",
    "MIN_ELIDABLE_CHARS",
    "find_tool_blocks",
    "read_path_for_block",
    "payload_for_block",
    "plan_elisions",
    "render_envelope",
    "rewrite_assistant_text",
    "rewrite_tool_history",
    "resolve_elide_redundant",
    "is_enabled",
]
