"""
show_document: put a file in front of the user without the model retyping it.

The server reads the file and the user sees it as a rendered block in the
assistant message.  The model receives only a summary (path, line range,
size, SHA-256, headings), so it has no file text to parrot and cannot alter
what is shown.  Design: design/harness-small-items.md, item 1.

Wire shape.  The tool returns ``{"content": <summary JSON>, "path": ...,
"_document_display": <markdown>}``.  ``app.tool_execution`` pops
``_document_display`` after signature verification and uses it as the
``tool_display`` text only; ``content`` is what reaches the model.  The
frontend persists the display as an ordinary tool block, so reload and export
include it, and ``app.utils.tool_history_rewrite`` replaces that body with a
note when the history is replayed to the model.

The displayed markdown starts with a caption comment (``CAPTION_PREFIX``)
that the frontend reads for the header and strips before rendering.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

DISPLAY_KEY = "_document_display"
CAPTION_PREFIX = "<!-- ziya:show_document "
MAX_DISPLAY_BYTES = 512 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_HEADINGS_IN_SUMMARY = 100

_MARKDOWN_EXTS = {".md", ".markdown", ".mdx"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_CODE_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".json": "json", ".jsonl": "json", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".html": "html", ".xml": "xml",
    ".css": "css", ".sh": "bash", ".bash": "bash", ".rs": "rust",
    ".go": "go", ".java": "java", ".rb": "ruby", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".sql": "sql", ".csv": "text",
    ".txt": "text", ".log": "text", ".ini": "ini", ".cfg": "ini",
}

_ATX_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A line that would close the chat's four-backtick tool fence.
_LONG_BACKTICK_RE = re.compile(r"^( {0,3})(`{4,})(.*)$")


def _error(message: str, headings: Optional[List[str]] = None) -> Dict[str, Any]:
    # The executor forwards only ``message`` for an error result, so the
    # heading list the caller needs to retry with goes into the text.
    if headings:
        message += "\nHeadings:\n" + "\n".join(f"  {h}" for h in headings)
    return {"error": True, "message": message}


def list_headings(lines: List[str]) -> List[Dict[str, Any]]:
    """ATX headings outside fenced code, as ``{level, text, line}`` (1-based)."""
    out: List[Dict[str, Any]] = []
    fence: Optional[str] = None
    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        m = _FENCE_OPEN_RE.match(line)
        if m:
            run = m.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line.strip()[len(run):].strip():
                fence = None
            continue
        if fence is not None:
            continue
        h = _ATX_RE.match(line)
        if h:
            out.append({"level": len(h.group(1)), "text": h.group(2).strip(), "line": i})
    return out


def _norm_heading(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^#{1,6}\s*", "", s)
    return re.sub(r"\s+", " ", s).casefold()


def select_section(lines: List[str], heading: str
                   ) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """(start, end) 1-based inclusive for *heading*'s section, or an error.

    The section runs from the heading to the line before the next heading of
    the same or a higher level.  Exact (case-insensitive) match first, then a
    unique prefix match.
    """
    heads = list_headings(lines)
    want = _norm_heading(heading)
    hits = [h for h in heads if _norm_heading(h["text"]) == want]
    if not hits:
        hits = [h for h in heads if _norm_heading(h["text"]).startswith(want)]
    if not hits:
        return None, f"No heading matches {heading!r}."
    if len(hits) > 1:
        where = ", ".join(f"line {h['line']}" for h in hits[:10])
        return None, f"Heading {heading!r} is ambiguous ({where}); use start_line/end_line."
    h = hits[0]
    end = len(lines)
    for nxt in heads:
        if nxt["line"] > h["line"] and nxt["level"] <= h["level"]:
            end = nxt["line"] - 1
            break
    return (h["line"], end), None


def escape_for_tool_fence(text: str) -> str:
    """Rewrite lines that would close the chat's four-backtick tool fence.

    A leading run of four or more backticks becomes the same number of
    tildes.  A tilde fence renders like a backtick fence, and the rewrite is
    applied to opener and closer alike, so nested fenced blocks still pair.
    """
    if "````" not in text:
        return text
    out = []
    for line in text.split("\n"):
        m = _LONG_BACKTICK_RE.match(line)
        out.append(m.group(1) + "~" * len(m.group(2)) + m.group(3) if m else line)
    return "\n".join(out)


def _longest_leading_tildes(text: str) -> int:
    best = 0
    for line in text.split("\n"):
        m = re.match(r"^ {0,3}(~+)", line)
        if m:
            best = max(best, len(m.group(1)))
    return best


def _caption(**fields: Any) -> str:
    body = " ".join(f'{k}="{str(v).replace(chr(34), chr(39))}"' for k, v in fields.items())
    return f"{CAPTION_PREFIX}{body} -->"


def build_display(path_str: str, resolved: str, *, heading: Optional[str] = None,
                  start_line: Optional[int] = None, end_line: Optional[int] = None,
                  render: str = "auto") -> Dict[str, Any]:
    """Read *resolved* and build the tool result for show_document.

    *resolved* must already have passed file_read's path validation.
    """
    ext = os.path.splitext(resolved)[1].lower()
    if ext in _IMAGE_EXTS:
        return _error(f"{path_str} is an image; show_document does not display images.")
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        return _error(f"Cannot read {path_str}: {exc}")

    if ext == ".pdf":
        pages = None
        try:
            from pypdf import PdfReader  # optional
            pages = len(PdfReader(resolved).pages)
        except Exception:
            pages = None
        page_txt = f", {pages} pages" if pages is not None else ""
        display = (_caption(path=path_str, bytes=size, kind="pdf") + "\n"
                   f"📄 **{path_str}** — PDF{page_txt}. PDFs are not rendered inline; "
                   "open the file, or ask for specific pages.")
        summary = {"path": path_str, "kind": "pdf", "pages": pages, "bytes": size,
                   "shown_to_user": True,
                   "note": "PDF not rendered inline; the user was shown its name and page count."}
        return {"content": json.dumps(summary), "path": path_str, DISPLAY_KEY: display}

    if size > MAX_FILE_BYTES:
        return _error(f"{path_str} is {size:,} bytes, over the {MAX_FILE_BYTES:,}-byte read limit.")
    with open(resolved, "rb") as fh:
        raw = fh.read()
    file_sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    heads = list_headings(lines)

    if heading and (start_line or end_line):
        return _error("Give either heading or start_line/end_line, not both.")
    if heading:
        rng, err = select_section(lines, heading)
        if err:
            return _error(err, headings=[f"{'#' * h['level']} {h['text']} (line {h['line']})"
                                         for h in heads[:MAX_HEADINGS_IN_SUMMARY]])
        start, end = rng
    elif start_line or end_line:
        start = max(1, int(start_line or 1))
        end = min(total, int(end_line or total))
        if start > end:
            return _error(f"Empty line range {start}-{end} (file has {total} lines).")
    else:
        start, end = 1, total

    selected = "".join(lines[start - 1:end])
    shown_bytes = len(selected.encode("utf-8"))
    if shown_bytes > MAX_DISPLAY_BYTES:
        return _error(
            f"{path_str} selection is {shown_bytes:,} bytes, over the "
            f"{MAX_DISPLAY_BYTES:,}-byte display limit. Show one section with "
            "'heading' or a line range.",
            headings=[f"{'#' * h['level']} {h['text']} (line {h['line']})"
                      for h in heads[:MAX_HEADINGS_IN_SUMMARY]],
        )

    mode = render if render in ("markdown", "code") else (
        "markdown" if ext in _MARKDOWN_EXTS else "code")
    body = escape_for_tool_fence(selected.rstrip("\n"))
    if mode == "code":
        fence = "~" * max(3, _longest_leading_tildes(body) + 1)
        body = f"{fence}{_CODE_LANG.get(ext, 'text')}\n{body}\n{fence}"

    display = _caption(path=path_str, lines=f"{start}-{end}", total_lines=total,
                       bytes=shown_bytes, sha256=file_sha) + "\n" + body
    summary = {
        "path": path_str,
        "lines": [start, end],
        "total_lines": total,
        "shown_bytes": shown_bytes,
        "file_bytes": len(raw),
        "sha256": file_sha,
        "render": mode,
        "headings": [f"{'#' * h['level']} {h['text']}"
                     for h in heads if start <= h["line"] <= end][:MAX_HEADINGS_IN_SUMMARY],
        "shown_to_user": True,
        "note": ("The user can see this content rendered in the chat. "
                 "Do not repeat or retype it; refer to it by section."),
    }
    return {"content": json.dumps(summary), "path": path_str, DISPLAY_KEY: display}


def show(path_str: str, project_root: str, **kwargs: Any) -> Dict[str, Any]:
    """Validate *path_str* exactly as file_read does, then build the result."""
    from app.mcp.tools.fileio import _get_all_readable_prefixes, _resolve_and_validate
    try:
        resolved = _resolve_and_validate(
            path_str, project_root, allowed_absolute_prefixes=_get_all_readable_prefixes())
    except ValueError as exc:
        return _error(str(exc))
    if not resolved.exists():
        return _error(f"File not found: {path_str}")
    if not resolved.is_file():
        return _error(f"Not a file: {path_str}")
    return build_display(path_str, str(resolved), **kwargs)


def is_show_document(tool_name: str) -> bool:
    n = tool_name or ""
    while n.startswith("mcp_"):
        n = n[4:]
    return n == "show_document"


def replay_note(header: str) -> str:
    """Body that replaces a show_document block when history is replayed."""
    return (f"[Ziya: document shown to the user ({header.strip() or 'show_document'}). "
            "Its content is not part of the model context; read the file if you need it.]")
