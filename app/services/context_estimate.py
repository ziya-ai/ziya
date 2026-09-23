"""
Server-side estimate of what the NEXT prompt will cost, per file.

The token gauge used to re-derive "what is in context" from the folder
tree: walk checked keys, sum node token_counts.  The tree is built with the
project's ignore rules and never carried the line-number prefixes the prompt
builder adds, so a pinned file under a .gitignore'd directory counted as 0
and every counted file was ~30% light.  One fork of 31 files showed <200k in
the UI while Bedrock billed 878k.

This module renders each requested file EXACTLY as get_combined_docs_from_files
will (File: header, ``[NNN ] `` prefix per line, escaped backticks) and prices
the rendered text with the calibrator's learned chars/token for the model
and file type.  The learned ratio is measured on that same rendered text
(the executor's calibration extracts ``File:`` blocks from the sent system
prompt), so no separate prefix correction is needed.  No file state is
touched; this is read-only.

Pure: the caller injects the base directory, calibrator, and tool
definitions so the route stays thin and the behaviour is unit-testable.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.utils.logging_utils import logger

# Same annotation get_annotated_content emits for an unchanged line; changed
# lines use '+' / '*' in the state slot, which is the same width.
LINE_PREFIX = "[{n:03d} ] "


def render_prompt_file(display_path: str, content: str) -> str:
    """The bytes get_combined_docs_from_files appends for one file."""
    lines = content.splitlines()
    body = "\n".join(
        (LINE_PREFIX.format(n=i) + line).replace('`', '\\`')
        for i, line in enumerate(lines, 1)
    )
    return f"File: {display_path}\n" + body + "\n\n"


@dataclass
class FileEstimate:
    path: str
    # ok | missing | directory | binary | not_allowed
    status: str
    chars: int = 0          # rendered prompt chars (header + prefixes + content)
    raw_chars: int = 0      # file content chars only
    lines: int = 0
    tokens: int = 0
    ratio: float = 0.0
    ratio_source: str = ""


@dataclass
class ContextEstimate:
    files: List[FileEstimate]
    file_tokens: int
    prefix_tokens: int          # share of file_tokens attributable to headers/prefixes
    overhead_tokens: int        # tools + fixed system boilerplate
    overhead_source: str        # learned_baseline | schema_chars | none
    mcp_tool_count: int
    builtin_tool_count: int
    model_key: Optional[str]
    model_family: str
    unreadable: List[str] = field(default_factory=list)  # paths not 'ok', for the UI to flag

    @property
    def total_tokens(self) -> int:
        return self.file_tokens + self.overhead_tokens

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


def _ratio_for(calibrator, ext: str, model_family: str, model_id: Optional[str]) -> tuple:
    ratio, source = calibrator.get_display_ratio(ext, model_family=model_family, model_id=model_id)
    if ratio <= 0:
        ratio, source = 4.1, "fallback"
    if source in ("global", "fallback"):
        # Non-type-specific tiers get the legacy per-type density fudge, as
        # estimate_tokens_fast does; learned per-type ratios already carry it.
        from app.utils.directory_util import get_file_type_multiplier
        ratio = ratio / get_file_type_multiplier("x" + ext)
    return ratio, source


def estimate_files(
    files: Iterable[str],
    base_dir: str,
    calibrator,
    model_family: str,
    model_id: Optional[str],
    resolve: Optional[Callable[[str, str], str]] = None,
) -> List[FileEstimate]:
    from app.utils.file_utils import (
        resolve_external_path, ExternalPathNotAllowed, is_processable_file, read_file_content,
    )
    resolve = resolve or resolve_external_path
    out: List[FileEstimate] = []
    for path in files:
        path = str(path)
        try:
            full = resolve(path, base_dir)
        except ExternalPathNotAllowed:
            out.append(FileEstimate(path, "not_allowed"))
            continue
        if os.path.isdir(full):
            out.append(FileEstimate(path, "directory"))
            continue
        if not os.path.isfile(full):
            out.append(FileEstimate(path, "missing"))
            continue
        if not is_processable_file(full):
            out.append(FileEstimate(path, "binary"))
            continue
        try:
            content = read_file_content(full) or ""
        except (OSError, UnicodeDecodeError) as e:
            logger.debug(f"context-estimate: unreadable {path}: {e}")
            out.append(FileEstimate(path, "binary"))
            continue
        rendered = render_prompt_file(path, content)
        _, ext = os.path.splitext(path.lower())
        ratio, source = _ratio_for(calibrator, ext or ".unknown", model_family, model_id)
        out.append(FileEstimate(
            path=path, status="ok", chars=len(rendered), raw_chars=len(content),
            lines=len(content.splitlines()), tokens=math.ceil(len(rendered) / ratio),
            ratio=round(ratio, 4), ratio_source=source,
        ))
    return out


def estimate_overhead(
    calibrator, model_family: str, model_id: Optional[str],
    mcp_tool_defs: List[Dict[str, Any]], builtin_tool_defs: List[Dict[str, Any]],
) -> tuple:
    """(tokens, source).  Prefers the overhead the calibrator learned from
    real usage (tools + fixed system text, per family); otherwise prices the
    schema JSON by the model's global ratio.  Tool schemas tokenize ~15%
    denser than source (measured: fable 2.58 vs 3.11 chars/token), hence the
    0.85."""
    learned = calibrator.get_baseline_overhead(model_family)
    if learned and learned > 0:
        return int(learned), "learned_baseline"
    defs = list(mcp_tool_defs) + list(builtin_tool_defs)
    if not defs:
        return 0, "none"
    chars = len(json.dumps(defs))
    ratio, _ = calibrator.get_display_ratio(None, model_family=model_family, model_id=model_id)
    return math.ceil(chars / (max(ratio, 1.0) * 0.85)), "schema_chars"


def estimate_context(
    files: Iterable[str], base_dir: str, calibrator, *,
    model_family: str, model_id: Optional[str],
    mcp_tool_defs: List[Dict[str, Any]], builtin_tool_defs: List[Dict[str, Any]],
) -> ContextEstimate:
    fes = estimate_files(files, base_dir, calibrator, model_family, model_id)
    file_tokens = sum(f.tokens for f in fes)
    # Prefix share: rendered chars minus raw chars, priced at each file's ratio.
    prefix_tokens = sum(
        math.ceil((f.chars - f.raw_chars) / f.ratio) for f in fes if f.status == "ok" and f.ratio > 0
    )
    overhead, src = estimate_overhead(calibrator, model_family, model_id, mcp_tool_defs, builtin_tool_defs)
    return ContextEstimate(
        files=fes, file_tokens=file_tokens, prefix_tokens=prefix_tokens,
        overhead_tokens=overhead, overhead_source=src,
        mcp_tool_count=len(mcp_tool_defs), builtin_tool_count=len(builtin_tool_defs),
        model_key=calibrator._normalize_model_key(model_id) if model_id else None,
        model_family=model_family,
        unreadable=[f.path for f in fes if f.status != "ok"],
    )
