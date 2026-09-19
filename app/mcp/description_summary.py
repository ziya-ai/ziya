"""Reduce a registry service description to a one-line headline.

Registry providers frequently hand back an entire README as the
``serviceDescription`` — markdown headings, a byline of links, install
snippets, tables of every tool. Wherever the UI or the config needs a *name*
or a one-line summary (the MCP status panel header, the installed-services
list, the ``description`` field written to mcp_config.json), that blob is
unusable. This module extracts the first real prose sentence instead.

Mirror of ``frontend/src/utils/mcpDescriptionSummary.ts``; keep the two in
sync (both are covered by fixtures built from real registry payloads under
tests/fixtures/mcp_descriptions/).
"""
import re

DEFAULT_MAX_LEN = 160
_MIN_PROSE_LEN = 20
_MIN_SENTENCE_LEN = 40
_BYLINE_SEGMENT_MAX = 60

_FENCE_RE = re.compile(r"```[\s\S]*?(```|$)")
_HTML_RE = re.compile(r"<[^>]+>")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# "](url)" left behind when an upstream parser truncated a nested badge link.
_ORPHAN_LINK_TAIL_RE = re.compile(r"\]\([^)]*\)")
# Leading run of emoji / symbols / punctuation before the first word.
_LEADING_SYMBOLS_RE = re.compile(r"^[^\w(\"']+", re.UNICODE)
_INLINE_MARKUP_RE = re.compile(r"(\*\*|__|`|~~)")
_EMPHASIS_RE = re.compile(r"(?<![A-Za-z0-9])[*_](?=\S)|(?<=\S)[*_](?![A-Za-z0-9])")
_WS_RE = re.compile(r"\s+")
_HEADING_RE = re.compile(r"^#{1,6}\s*")
_HR_RE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _clean_inline(text: str) -> str:
    text = _INLINE_MARKUP_RE.sub("", text)
    text = _EMPHASIS_RE.sub("", text)
    text = _LEADING_SYMBOLS_RE.sub("", text.strip())
    return _WS_RE.sub(" ", text).strip()


def _is_structural(line: str) -> bool:
    """True for lines that carry layout, not prose."""
    s = line.strip()
    if not s:
        return True
    if s.startswith("#"):
        return True
    if _HR_RE.match(s):
        return True
    if _LIST_RE.match(s):
        return True
    if s.startswith(">") or s.startswith("|"):
        return True
    # A byline / nav row: "Source Code | #channel | Owner (alias@) | ...".
    # Prose that merely mentions "a | b | c" has long segments around the pipes.
    if s.count("|") >= 2 and max(len(seg.strip()) for seg in s.split("|")) < _BYLINE_SEGMENT_MAX:
        return True
    return False


def summarize_description(raw, max_len: int = DEFAULT_MAX_LEN) -> str:
    """Return a single-line headline for a possibly-markdown description.

    Picks the first paragraph that reads as prose (not a heading, byline,
    rule, list, quote, table or code block), reduces it to its leading
    sentence(s), and caps the length on a word boundary. Falls back to the
    first heading, then to the first non-empty line, so something is always
    returned for non-empty input.
    """
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.replace("\r\n", "\n")
    text = _FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _HTML_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    if "](" in text:
        # Badge residue such as "name MCP server](https://…svg)](https://…) 🐍 -
        # real description": drop the tails, then everything up to the
        # " - " separator the awesome-list format puts before the prose.
        text = _ORPHAN_LINK_TAIL_RE.sub(" ", text)
        head, sep, tail = text.partition(" - ")
        if sep and len(tail.strip()) >= _MIN_PROSE_LEN:
            text = tail

    chosen = ""
    first_heading = ""
    first_line = ""
    for para in re.split(r"\n\s*\n", text):
        lines = [ln for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        for ln in lines:
            s = ln.strip()
            if not first_heading and s.startswith("#"):
                first_heading = _clean_inline(_HEADING_RE.sub("", s))
            if not first_line and not _HR_RE.match(s):
                first_line = _clean_inline(_LIST_RE.sub("", _HEADING_RE.sub("", s)))
        prose = [ln for ln in lines if not _is_structural(ln)]
        if not prose:
            continue
        candidate = _clean_inline(" ".join(prose))
        if len(candidate) >= _MIN_PROSE_LEN:
            chosen = candidate
            break

    if not chosen:
        chosen = first_heading or first_line
    if not chosen:
        return ""

    # Leading sentence(s): keep adding until the headline is substantive.
    sentences = _SENTENCE_SPLIT_RE.split(chosen)
    headline = ""
    for sent in sentences:
        nxt = (headline + " " + sent).strip() if headline else sent
        if headline and len(nxt) > max_len:
            break
        headline = nxt
        if len(headline) >= _MIN_SENTENCE_LEN:
            break

    if len(headline) > max_len:
        cut = headline[: max_len - 1]
        space = cut.rfind(" ")
        if space >= max_len // 2:
            cut = cut[:space]
        headline = cut.rstrip(" ,;:-—") + "…"
    return headline
