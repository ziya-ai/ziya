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
close the envelope early; the same defang is applied to the prose between
blocks, so an envelope the MODEL wrote as bare text (a fabricated result,
see text_delta_processor's RAW patterns) is not replayed intact as an
in-context example.  The rewrite never touches user messages.

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

Tributary fold
--------------
A "quest turn" — an assistant message with several tool calls and prose
after the last one — is replayed as its pre-hunt prose, a placeholder, and
its final prose, once enough further human turns exist (the grace).  The
placeholder names the calls and a content-addressed ``tb-…`` handle under
which ``recall_tributary`` returns the span from the record.  Unlike
elision this reaches the interim narration between calls, which is the
part of a hunt no other lever touches.  The rule is purely structural
(``plan_folds``); nothing judges value, and the record is untouched.  See
design/tributary-fold.md.  Blocks inside a folded span are not in the
model's view, so ``plan_elisions`` must not use them as stand-ins for an
earlier body — folds are planned first and passed in.

Kill switches: ``ZIYA_DISABLE_TOOL_HISTORY_REWRITE=1`` (everything),
``ZIYA_DISABLE_TOOL_RESULT_ELISION=1`` (elision only),
``ZIYA_DISABLE_TRIBUTARY_FOLD=1`` (fold only).  Elision and folding are
also per-project settings under ``contextManagement``; both default on.
Recall of a folded span: app/utils/tributary_recall.py (``recall_tributary``).
"""
from __future__ import annotations

import hashlib
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

# Any envelope lookalike, in either bracket alphabet, plus the
# UTF-8-as-Latin-1 mojibake of U+2039 (U+201A U+00C4 U+00B9) that a model
# parroting the envelope has been observed to emit.  Applied to block
# bodies AND to the prose between blocks: a fabricated envelope the model
# wrote as bare assistant text must not be replayed intact, or the next
# turn sees its own fabrication as accepted history.
_LOOKALIKE_RE = re.compile(
    r'(?:[<\u2039]|\u201a\u00c4\u00b9)(\s*/?\s*tool_result\b)',
    re.IGNORECASE,
)

_OPEN_FMT = '‹tool_result trust="{trust}" tool="{tool}" label="{label}"›'
_CLOSE = '‹/tool_result›'

# Inline rendered image (render_diagram / view_image / recall_image).  The
# frontend prepends ``<img src="data:image/…;base64,…">`` to the tool block
# (renderToolPart.ts) so the user sees the picture; that tag then sits in
# message.content and is replayed as TEXT on every later turn.  As text a
# 100 KB PNG is tens of thousands of tokens the model cannot see as an
# image at all.  The model already saw the real image in the turn it was
# produced, via the tool_result content block; nothing later needs the
# bytes.  Matched on the src, not the whole tag shape, so a hand-edited or
# older tag is still caught.
_INLINE_IMAGE_RE = re.compile(
    r'<img\b[^>]*?\bsrc=["\']data:image/[^"\']*["\'][^>]*?>',
    re.IGNORECASE | re.DOTALL,
)

INLINE_IMAGE_NOTE = (
    "[Ziya: rendered image elided on replay. You saw this image in the turn "
    "it was produced and any conclusion you drew from it then stands. "
    "Re-run the render tool if you need to look again.]"
)


def elide_inline_images(text: str) -> str:
    """Replace every inline ``data:image`` <img> tag with a short note."""
    if not text or "data:image" not in text:
        return text
    return _INLINE_IMAGE_RE.sub(INLINE_IMAGE_NOTE, text)


# A Context Size Notice persisted into an assistant message by older builds
# (the streaming executor used to yield it as visible text).  Its numbers
# describe one request of one past turn; replayed, the model reads them as
# the current size and repeats "~86% of the limit" turns later.  The live
# notice is now model-only and never persisted, so this only cleans history.
#
# The iteration-budget warnings were streamed the same way and go stale the
# same way ("2 iterations remaining" in a reply from three turns ago), so
# they are stripped too.  Each is a single paragraph on one line.
_NOTICE_HEADERS = (
    "Context Size Notice", "Iteration Budget", "Iteration Limit Notice",
    "Iteration Limit Warning", "FINAL ITERATION",
)
_CONTEXT_NOTICE_RE = re.compile(
    r'\n*(?:⚠️|🚨) \*\*(?:' + "|".join(map(re.escape, _NOTICE_HEADERS))
    + r'):\*\*[^\n]*(?:\n|$)\n*')


def strip_context_notices(text: str) -> str:
    """Remove persisted budget/size notice paragraphs from assistant text."""
    if not text or not any(h in text for h in _NOTICE_HEADERS):
        return text
    return _CONTEXT_NOTICE_RE.sub("\n\n", text)

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
                           elisions: Optional[Dict[int, str]] = None,
                           fold: Optional["FoldSpan"] = None) -> str:
    """Replace every rendered tool block in one assistant message.

    ``elisions`` maps a block's ordinal (its index among the blocks of this
    text, as returned by ``_non_overlapping(find_tool_blocks(text))``) to
    the note that replaces its body.

    ``fold`` replaces the whole span from the first to the last block —
    calls, results and the prose between them — with its placeholder; the
    prose before the first block and after the last is kept.  Ordinals are
    used rather than offsets because the image/notice stripping below
    shifts offsets but never adds or removes a block.
    """
    if not text:
        return text
    # Inline image bytes go first: the tag sits OUTSIDE the tool block, so
    # block matching never sees it, and it must go whether or not the
    # message has any block at all.
    text = strip_context_notices(elide_inline_images(text))
    has_blocks = "tool:" in text or "TOOL_BLOCK_START" in text
    if (not has_blocks and "TOOL_MARKER" not in text
            and not _LOOKALIKE_RE.search(text)):
        return text
    blocks = _non_overlapping(find_tool_blocks(text)) if has_blocks else []
    if fold is not None and len(blocks) > fold.last_ordinal:
        a = blocks[fold.first_ordinal].start
        z = blocks[fold.last_ordinal].end
        folded = _defang(text[:a]) + fold.placeholder + _defang(text[z:])
        return _ORPHAN_MARKER_RE.sub('', folded)
    # Prose between (and outside) real blocks is defanged too: an envelope
    # lookalike there is a fabrication the model wrote as bare text, and
    # replaying it verbatim teaches the model that the shape is accepted.
    # The envelopes rendered below are appended AFTER defanging their
    # surroundings, so genuine re-encoded results are never altered.
    out: List[str] = []
    pos = 0
    for ordinal, b in enumerate(blocks):
        out.append(_defang(text[pos:b.start]))
        note = elisions.get(ordinal) if elisions else None
        # show_document bodies are for the user only: the live tool result
        # gave the model a summary, and replay must not hand it the text.
        if note is None:
            from app.utils.show_document import is_show_document, replay_note
            if is_show_document(b.tool):
                note = replay_note(b.header)
        out.append(render_envelope(b, note))
        pos = b.end
    out.append(_defang(text[pos:]))
    text = "".join(out)
    return _ORPHAN_MARKER_RE.sub('', text)


# --- Transcript view: tool blocks collapsed to one line ------------------

# Group brackets chatApi writes around a batch of tool calls
# (frontend/src/utils/toolGroupDisplay.ts).  Pure display markup.
_TOOL_GROUP_RE = re.compile(r'<!--\s*TOOL_GROUP_(?:START|END):[^>]*-->\n?')


def collapse_tool_blocks(text: str) -> Tuple[str, int, int]:
    """Replace every rendered tool block with a one-line stub.

    For reading a past conversation as a transcript (chat_read).  A
    tool-heavy assistant turn is mostly tool OUTPUT: one measured turn was
    534,000 characters, nearly all of it the bodies of file and web reads,
    with the model's own conclusions after them.  Truncating that message
    keeps the head, so a reader saw the first tool body and none of the
    prose, and had to fish for the conclusions with dozens of searches.

    Returns ``(text, n_blocks, n_chars_elided)``.  Inline image bytes and
    group brackets are removed as well; prose is left untouched.
    """
    if not text:
        return text, 0, 0
    text = _TOOL_GROUP_RE.sub('', elide_inline_images(text))
    if "tool:" not in text and "TOOL_BLOCK_START" not in text:
        return _ORPHAN_MARKER_RE.sub('', text), 0, 0
    blocks = _non_overlapping(find_tool_blocks(text))
    out: List[str] = []
    pos = 0
    elided = 0
    for b in blocks:
        out.append(text[pos:b.start])
        elided += b.end - b.start
        out.append(f"[tool {b.tool}: {_clean_label(b.header)} "
                   f"({len(b.body):,} chars of output not shown)]")
        pos = b.end
    out.append(text[pos:])
    return _ORPHAN_MARKER_RE.sub('', "".join(out)), len(blocks), elided


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


def plan_elisions(history: List[Any],
                  folds: Optional[Dict[Tuple[int, int], "FoldSpan"]] = None
                  ) -> Dict[Tuple[int, int], Dict[int, str]]:
    """Decide which tool bodies are redundant with a later one.

    Returns ``{(msg_idx, seg_idx): {block_ordinal: note}}`` for every body
    that should be replaced.  Pure and side-effect free; exposed so the
    survey script and tests can inspect decisions directly.

    ``folds`` (from ``plan_folds``) names spans that will not be replayed.
    Blocks inside them are left out entirely: they cannot stand in for an
    earlier body (the bytes are not downstream in the model's view) and a
    decision about them would be moot.
    """
    folds = folds or {}
    # (msg_idx, seg_idx, ordinal, tool, path|None, payload)
    entries: List[Tuple[int, int, int, str, Optional[str], str]] = []
    for mi, msg in enumerate(history):
        if not (isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES):
            continue
        for si, text in enumerate(_text_segments(msg.get("content"))):
            if "tool:" not in text and "TOOL_BLOCK_START" not in text:
                continue
            fold = folds.get((mi, si))
            for oi, b in enumerate(_non_overlapping(find_tool_blocks(text))):
                if fold is not None and fold.first_ordinal <= oi <= fold.last_ordinal:
                    continue
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


# --- Planning: tributary folds (design/tributary-fold.md) ----------------

_HUMAN_TYPES = frozenset({"human", "user"})
FOLD_HANDLE_PREFIX = "tb-"

# Conservative "returned nothing" shapes over the payload.  Undercounting
# is fine; the count in the placeholder is informational.
_EMPTY_RESULT_RE = re.compile(
    r"^(?:\(no output\)|no output\.?|no match(?:es)?(?: found)?\.?|"
    r"not found\.?|\[\]|\{\}|null|none)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FoldSettings:
    enabled: bool = True      # default on since recall_tributary shipped
    grace_turns: int = 2      # human messages that must follow a quest turn
    min_calls: int = 2        # tool blocks required for a turn to be a quest
    min_chars: int = 1500     # spans shorter than this are left alone
    max_labels: int = 12      # call labels listed in the placeholder
    # Nothing folds until the conversation's UNFOLDED replay would use this
    # fraction of the context limit; then the oldest eligible spans fold,
    # oldest first, until the projected size is back under the limit minus
    # FOLD_PRESSURE_HYSTERESIS.  Below pressure a fold is pure cost: the
    # model loses verbatim text (diff context lines, exact paths) it had in
    # view and has to notice that it needs to recall it.  0 folds every
    # eligible span regardless of pressure (the pre-gate behaviour).
    pressure_ratio: float = 0.8


# Fold down to (pressure_ratio - this) rather than to the threshold itself.
# Each new fold rewrites one old message and so misses the prompt cache
# from that message on; folding one span per turn while hovering at the
# threshold would do that every turn.  A batch of folds, then a stretch of
# stable replay, is the cheaper shape.
FOLD_PRESSURE_HYSTERESIS = 0.15


@dataclass(frozen=True)
class FoldPressure:
    """How full the context was on the previous turn, as if nothing had
    been folded.  ``tokens`` is the assembled size plus what that turn's
    folds saved, so it grows with the conversation and never drops because
    a fold happened — which keeps the fold prefix monotone (see plan_folds)."""
    tokens: int
    limit: int
    # chars/token the measured payload was actually billed at, when the
    # measurement recorded its text size.  Savings are converted at THIS
    # ratio so they stay in the same units as ``tokens``; None falls back
    # to the learned ratio (_chars_to_tokens).
    chars_per_token: Optional[float] = None


def _chars_to_tokens(chars: int) -> int:
    """Learned chars/token ratio for a DELTA (no fixed baseline), chars/4 fallback."""
    if chars <= 0:
        return 0
    try:
        from app.utils.token_calibrator import get_token_calibrator
        return int(get_token_calibrator().estimate_tokens_from_chars(
            chars, include_baseline=False))
    except Exception:  # noqa: BLE001
        return int(chars / 4)


def _saved_tokens(chars: int, pressure: Optional[FoldPressure]) -> int:
    """Tokens a fold of ``chars`` saves, in the units of ``pressure.tokens``.

    The recorded pressure is what the provider billed (or the pre-send
    estimate); converting savings at a *different*, independently learned
    ratio mixes units.  With that ratio wrong by 5x (a user's bucket had
    learned 13.4 chars/token) the gate overfolded 970k -> 350k on one turn,
    then reconstructed the unfolded size as ~550k and folded nothing the
    next, alternating every turn until an unfolded payload crossed the
    1M limit."""
    if chars <= 0:
        return 0
    if pressure is not None and pressure.chars_per_token:
        return int(chars / pressure.chars_per_token)
    return _chars_to_tokens(chars)


# Only ratios in this band are believed as a payload's own chars/token.  A
# text-char count against a bill that included many image tokens comes out
# low; a count against a bill that excluded cache writes comes out high.
# Either way the learned ratio is the better fallback.
_PAYLOAD_RATIO_BAND = (1.0, 8.0)


def _baseline_tokens() -> int:
    """Fixed per-request overhead (tool schemas, static prompt) the bill
    includes and the payload's text chars do not; 0 when unknown."""
    try:
        from app.utils.token_calibrator import get_token_calibrator
        cal = get_token_calibrator()
        return max(0, int(cal.get_baseline_overhead(cal._get_current_model_family())))
    except Exception:  # noqa: BLE001
        return 0


def _payload_ratio(tokens: int, chars: Optional[int]) -> Optional[float]:
    """chars/token the measured payload was billed at, or None."""
    if not chars or chars <= 0 or tokens <= 0:
        return None
    content_tokens = tokens - _baseline_tokens()
    if content_tokens <= 0:
        return None
    ratio = chars / content_tokens
    lo, hi = _PAYLOAD_RATIO_BAND
    return ratio if lo <= ratio <= hi else None


@dataclass(frozen=True)
class FoldSpan:
    """One folded tributary: which blocks of a text segment it covers and
    the placeholder that replaces them.  Ordinals, not offsets — see
    ``rewrite_assistant_text``."""
    handle: str
    first_ordinal: int
    last_ordinal: int
    span_chars: int
    labels: Tuple[str, ...]
    n_empty: int
    n_notes: int
    placeholder: str


def fold_handle(span_text: str) -> str:
    """Content address of a tributary: the exact record substring, hashed."""
    digest = hashlib.sha1(span_text.encode("utf-8", "replace")).hexdigest()
    return FOLD_HANDLE_PREFIX + digest[:10]


def fold_placeholder(handle: str, labels: Tuple[str, ...], n_empty: int,
                     n_notes: int, span_chars: int, max_labels: int) -> str:
    """The text replayed in place of a folded span.

    Absolute counts only — nothing relative to the message's age or
    position — so the bytes never change once a span has folded and the
    prompt-cache prefix is not invalidated on later turns.
    """
    shown = labels[:max_labels]
    out = [f"[Ziya: tributary {handle} folded on replay — {len(labels)} tool "
           f"calls ({n_empty} returned nothing), {n_notes} interim notes, "
           f"~{span_chars / 4:,.0f} tokens. Calls:"]
    out += [f"   {_defang(lab)[:160]}" for lab in shown]
    if len(labels) > len(shown):
        out.append(f"   … {len(labels) - len(shown)} more")
    out.append(f' Full span: recall_tributary(handle="{handle}"); '
               f'mode="commands" for the calls alone, mode="results" for '
               f'bodies, grep= to slice. The text that follows was written '
               f'with the whole span in view and stands.]')
    return "\n".join(out)


def _is_empty_result(b: ToolBlock) -> bool:
    p = payload_for_block(b).strip()
    return not p or bool(_EMPTY_RESULT_RE.match(p))


def classify_fold(text: str, settings: FoldSettings) -> Optional[FoldSpan]:
    """Apply the structural rule to one assistant text segment.

    Returns a ``FoldSpan`` when the segment has at least ``min_calls``
    blocks, non-empty prose after the last block, and a span of at least
    ``min_chars``; otherwise None.  The grace is the caller's concern.
    Works on the raw record text so the handle addresses the record.
    """
    if not text or ("tool:" not in text and "TOOL_BLOCK_START" not in text):
        return None
    blocks = _non_overlapping(find_tool_blocks(text))
    if len(blocks) < settings.min_calls:
        return None
    if not text[blocks[-1].end:].strip():
        return None  # ends on a block: aborted, or the result is the deliverable
    span = text[blocks[0].start:blocks[-1].end]
    if len(span) < settings.min_chars:
        return None
    n_notes = sum(1 for a, b in zip(blocks, blocks[1:])
                  if text[a.end:b.start].strip())
    labels = tuple(_clean_label(b.header) or _norm_tool(b.tool) for b in blocks)
    n_empty = sum(1 for b in blocks if _is_empty_result(b))
    handle = fold_handle(span)
    return FoldSpan(
        handle=handle, first_ordinal=0, last_ordinal=len(blocks) - 1,
        span_chars=len(span), labels=labels, n_empty=n_empty, n_notes=n_notes,
        placeholder=fold_placeholder(handle, labels, n_empty, n_notes,
                                     len(span), settings.max_labels),
    )


def fold_saving_chars(span: FoldSpan) -> int:
    """Characters a fold removes from replay, net of its placeholder."""
    return max(0, span.span_chars - len(span.placeholder))


def plan_folds(history: List[Any],
               settings: FoldSettings,
               pressure: Optional[FoldPressure] = None) -> Dict[Tuple[int, int], FoldSpan]:
    """Decide which assistant segments replay as their reward only.

    A segment is ELIGIBLE when ``classify_fold`` accepts it and at least
    ``settings.grace_turns`` human messages follow it in ``history``.
    Muted messages are never in ``history`` (the frontend drops them), so
    counting here is counting the replayed history.  A message carrying
    ``foldPinned`` is skipped.

    With ``settings.pressure_ratio > 0`` eligibility is not enough: nothing
    folds until ``pressure`` (the previous turn's unfolded size) reaches that
    fraction of the limit, and then eligible spans fold OLDEST FIRST only
    until the projected size is under ``pressure_ratio -
    FOLD_PRESSURE_HYSTERESIS``.  Unknown pressure (``None``: first turn after
    a restart, a caller with no conversation) folds nothing — unknown must
    not read as "full".  Because the unfolded size only grows, the folded
    set is a prefix that only extends, so a span folded once stays folded
    and its placeholder bytes stay in the cached prompt prefix.

    Pure; returns ``{}`` when disabled.
    """
    if not settings.enabled:
        return {}
    ratio = float(settings.pressure_ratio or 0)
    if ratio > 0:
        if pressure is None or pressure.limit <= 0:
            return {}
        if pressure.tokens < ratio * pressure.limit:
            return {}
    n = len(history)
    humans_after = [0] * (n + 1)   # human messages strictly after index i
    for i in range(n - 1, -1, -1):
        msg = history[i]
        is_human = isinstance(msg, dict) and msg.get("type") in _HUMAN_TYPES
        humans_after[i] = humans_after[i + 1] + (1 if is_human else 0)

    plan: Dict[Tuple[int, int], FoldSpan] = {}
    for mi, msg in enumerate(history):
        if not (isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES):
            continue
        if msg.get("foldPinned"):
            continue
        if humans_after[mi + 1] < settings.grace_turns:
            continue
        for si, text in enumerate(_text_segments(msg.get("content"))):
            span = classify_fold(text, settings)
            if span is not None:
                plan[(mi, si)] = span
    if ratio <= 0 or not plan:
        return plan
    # Oldest first (history order == dict insertion order), stopping once
    # the projected size is under the low-water mark.
    target = max(0.0, ratio - FOLD_PRESSURE_HYSTERESIS) * pressure.limit
    remaining = float(pressure.tokens)
    chosen: Dict[Tuple[int, int], FoldSpan] = {}
    for key, span in plan.items():
        if remaining <= target:
            break
        chosen[key] = span
        remaining -= _saved_tokens(fold_saving_chars(span), pressure)
    return chosen


# conversation_id -> chars the most recent replay's folds removed.  Read
# together with the handoff pressure cache (the assembled, i.e. folded,
# size) to reconstruct the UNFOLDED size the gate compares against.
_fold_savings: Dict[str, int] = {}


def fold_pressure_for(conversation_id: Optional[str]) -> Optional[FoldPressure]:
    """The previous turn's unfolded size for ``conversation_id``, or None
    when no turn has been measured yet (server start, new conversation)."""
    if not conversation_id:
        return None
    try:
        from app.utils.handoff_prelude import (
            get_context_pressure_chars, get_context_pressure_detail)
        hit = get_context_pressure_detail(conversation_id)
        chars = get_context_pressure_chars(conversation_id) if hit else None
    except Exception:  # noqa: BLE001
        return None
    if not hit:
        return None
    tokens, limit = hit
    saved = _fold_savings.get(conversation_id, 0)
    ratio = _payload_ratio(int(tokens), chars)
    measured = FoldPressure(tokens=int(tokens), limit=int(limit), chars_per_token=ratio)
    return FoldPressure(tokens=int(tokens) + _saved_tokens(saved, measured),
                        limit=int(limit), chars_per_token=ratio)


def record_fold_savings(conversation_id: Optional[str], saved_chars: int) -> None:
    if conversation_id:
        _fold_savings[conversation_id] = max(0, int(saved_chars))


def clear_fold_savings() -> None:
    _fold_savings.clear()


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


def resolve_fold_settings(project_root: Optional[str] = None) -> FoldSettings:
    """Read the ``contextManagement.fold_*`` fields for the project.

    Same resolution path as ``resolve_elide_redundant``; never raises.
    Fields a ContextManagementSettings predates fall back to the
    ``FoldSettings`` defaults.  The env kill switch wins over the setting.
    """
    if _env_truthy("ZIYA_DISABLE_TRIBUTARY_FOLD"):
        return FoldSettings(enabled=False)
    base = FoldSettings()
    cm: Any = None
    try:
        from app.models.project import ContextManagementSettings
        cm = ContextManagementSettings()  # model defaults
        from app.context import get_project_root_or_none
        from app.storage.projects import ProjectStorage
        from app.utils.paths import get_ziya_home

        root = (project_root or get_project_root_or_none()
                or os.environ.get("ZIYA_USER_CODEBASE_DIR"))
        if root:
            project = ProjectStorage(get_ziya_home()).get_by_path(root)
            if project and project.settings and project.settings.contextManagement:
                cm = project.settings.contextManagement
    except Exception as e:
        logger.debug(f"resolve_fold_settings: {e}")

    def _get(name: str, default: Any) -> Any:
        val = getattr(cm, name, None) if cm is not None else None
        return default if val is None else val

    try:
        return FoldSettings(
            enabled=bool(_get("fold_tributaries", base.enabled)),
            grace_turns=int(_get("fold_grace_turns", base.grace_turns)),
            min_calls=int(_get("fold_min_calls", base.min_calls)),
            min_chars=int(_get("fold_min_chars", base.min_chars)),
            max_labels=int(_get("fold_placeholder_max_labels", base.max_labels)),
            pressure_ratio=float(_get("fold_pressure_ratio", base.pressure_ratio)),
        )
    except (TypeError, ValueError) as e:
        logger.debug(f"resolve_fold_settings: bad value, using defaults: {e}")
        return base


# --- Entry point ---------------------------------------------------------

def _rewrite_content(content: Any,
                     elisions_by_seg: Optional[Dict[int, Dict[int, str]]],
                     folds_by_seg: Optional[Dict[int, FoldSpan]] = None) -> Any:
    el = elisions_by_seg or {}
    fo = folds_by_seg or {}
    if isinstance(content, str):
        return rewrite_assistant_text(content, el.get(0), fo.get(0))
    if isinstance(content, list):
        new = []
        seg = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                block = {**block, "text": rewrite_assistant_text(
                    block["text"], el.get(seg), fo.get(seg))}
                seg += 1
            new.append(block)
        return new
    return content


def _rewrite_content_images_only(content: Any) -> Any:
    if isinstance(content, str):
        return elide_inline_images(content)
    if isinstance(content, list):
        return [
            {**blk, "text": elide_inline_images(blk["text"])}
            if isinstance(blk, dict) and blk.get("type") == "text"
            and isinstance(blk.get("text"), str) else blk
            for blk in content
        ]
    return content


def rewrite_tool_history(history: List[Any],
                         elide_redundant: Optional[bool] = None,
                         fold: Optional[Any] = None,
                         conversation_id: Optional[str] = None,
                         pressure: Optional[FoldPressure] = None) -> List[Any]:
    """Rewrite tool blocks in every assistant message of ``history``.

    ``history`` is the processed_chat_history list built in
    ``build_messages_for_streaming`` — dicts with ``type`` and ``content``.
    Non-dict entries and non-assistant messages pass through untouched.
    Returns a new list; input is not mutated.

    ``elide_redundant`` — replace bodies that are provably redundant with a
    later result (see module docstring).  ``None`` resolves the project
    setting; tests pass an explicit value.

    ``fold`` — a ``FoldSettings``, a bool (enabled with defaults), or
    ``None`` to resolve the project setting.  Folds are planned before
    elisions so a body inside a folded span is never a stand-in.

    ``conversation_id`` lets the fold gate read the previous turn's context
    pressure for this conversation (``fold_pressure_for``) and records what
    this replay's folds saved for the next turn's reading.  ``pressure``
    overrides the lookup (tests).  With neither, pressure is unknown and a
    pressure-gated setting folds nothing.
    """
    if not is_enabled():
        # The kill switch is for the block RE-ENCODING.  Replaying base64
        # image bytes as assistant text is never wanted, so that pass
        # stays on.  Identity when there is nothing to strip, so callers
        # (and the existing kill-switch test) can rely on ``is``.
        if not any(
            isinstance(m, dict) and m.get("type") in _ASSISTANT_TYPES
            and "data:image" in str(m.get("content", ""))
            for m in history
        ):
            return history
        return [
            {**msg, "content": _rewrite_content_images_only(msg.get("content"))}
            if isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES
            else msg
            for msg in history
        ]
    if elide_redundant is None:
        elide_redundant = resolve_elide_redundant()
    if fold is None:
        fold_settings = resolve_fold_settings()
    elif isinstance(fold, bool):
        fold_settings = FoldSettings(enabled=fold)
    else:
        fold_settings = fold
    if pressure is None:
        pressure = fold_pressure_for(conversation_id)
    folds = plan_folds(history, fold_settings, pressure)
    if conversation_id:
        record_fold_savings(conversation_id,
                            sum(fold_saving_chars(s) for s in folds.values()))
    plan = plan_elisions(history, folds) if elide_redundant else {}

    out: List[Any] = []
    rewritten = 0
    for mi, msg in enumerate(history):
        if isinstance(msg, dict) and msg.get("type") in _ASSISTANT_TYPES:
            content = msg.get("content")
            by_seg: Dict[int, Dict[int, str]] = {}
            for (pmi, psi), notes in plan.items():
                if pmi == mi:
                    by_seg[psi] = notes
            fold_by_seg: Dict[int, FoldSpan] = {
                psi: span for (pmi, psi), span in folds.items() if pmi == mi}
            new_content = _rewrite_content(content, by_seg or None,
                                           fold_by_seg or None)
            if new_content is not content:
                rewritten += 1
                msg = {**msg, "content": new_content}
        out.append(msg)
    if rewritten:
        n_elided = sum(len(v) for v in plan.values())
        folded_chars = sum(s.span_chars for s in folds.values())
        logger.debug("🧹 TOOL_HISTORY_REWRITE: rewrote %d assistant message(s), "
                     "elided %d redundant body(ies), folded %d tributary(ies) "
                     "(%d chars)", rewritten, n_elided, len(folds), folded_chars)
    return out


__all__ = [
    "ToolBlock",
    "FoldSettings",
    "FoldSpan",
    "FoldPressure",
    "FOLD_HANDLE_PREFIX",
    "FOLD_PRESSURE_HYSTERESIS",
    "fold_saving_chars",
    "fold_pressure_for",
    "record_fold_savings",
    "clear_fold_savings",
    "INLINE_IMAGE_NOTE",
    "MIN_ELIDABLE_CHARS",
    "elide_inline_images",
    "find_tool_blocks",
    "read_path_for_block",
    "payload_for_block",
    "plan_elisions",
    "plan_folds",
    "classify_fold",
    "fold_handle",
    "fold_placeholder",
    "resolve_fold_settings",
    "render_envelope",
    "rewrite_assistant_text",
    "rewrite_tool_history",
    "resolve_elide_redundant",
    "is_enabled",
]
