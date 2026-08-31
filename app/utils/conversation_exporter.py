"""
Conversation Export Utility

Exports conversations to formats suitable for paste services (GitHub Gist and others)
with full preservation of formatting, code blocks, diffs, and visualizations.
"""

import base64
import re
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

# Pygments provides pure-Python syntax highlighting for the fenced code blocks
# emitted by the regex fallback exporter (HTML-01). It is imported defensively:
# HTML export must NEVER hard-fail merely because a dependency is missing, so a
# failed import degrades to the previous uncolored <pre><code> output.
try:
    from pygments import highlight as _pygments_highlight
    from pygments.lexers import get_lexer_by_name as _pygments_get_lexer
    from pygments.formatters import HtmlFormatter as _PygmentsHtmlFormatter
    from pygments.util import ClassNotFound as _PygmentsClassNotFound
    _PYGMENTS_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when pygments absent
    _PYGMENTS_AVAILABLE = False
    _PygmentsClassNotFound = Exception  # type: ignore


# KaTeX server-side math rendering for the regex fallback exporter (HTML-03).
# The fallback previously left ``$...$`` / ``$$...$$`` as literal escaped LaTeX
# text — check_math_rendering measured 0 ``.katex`` elements and FAILED. KaTeX
# is a frontend-only Node module (there is no pure-Python equivalent), so we
# shell out to ``node`` to render each expression to self-contained MathML
# (``output: "mathml"`` — native browser math, no external fonts/CSS, so the
# standalone document stays self-contained). This is a BEST-EFFORT enhancement:
# if node or the katex module is absent, or a render fails, the math degrades
# gracefully to its original escaped LaTeX text and the export never hard-fails
# (the architectural dual-mode contract — same discipline as _PYGMENTS_AVAILABLE).
def _find_katex_node_modules() -> Optional[str]:
    """Locate the frontend ``node_modules`` dir that contains ``katex``.

    Returns the node_modules path (for NODE_PATH) or ``None`` when katex is not
    installed. Node resolves ``require('katex')`` relative to the executed
    script, not the CWD, so we must point NODE_PATH at the dir explicitly.
    """
    # app/utils/conversation_exporter.py -> repo root is parents[2].
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / 'frontend' / 'node_modules'
    if (candidate / 'katex' / 'package.json').is_file():
        return str(candidate)
    return None


_NODE_BIN = shutil.which('node')
_KATEX_NODE_MODULES = _find_katex_node_modules()
_KATEX_AVAILABLE = bool(_NODE_BIN and _KATEX_NODE_MODULES)

# A single Node program renders a whole batch of expressions in one process
# start (KaTeX is imported once), reading a JSON array of {tex, display} on
# stdin and writing a JSON array of MathML strings (or null on per-item error)
# on stdout. Kept deliberately tiny and side-effect free.
_KATEX_RENDER_JS = (
    'const katex=require("katex");'
    'let d="";process.stdin.on("data",c=>d+=c);'
    'process.stdin.on("end",()=>{'
    'let items;try{items=JSON.parse(d)}catch(e){process.stdout.write("[]");return}'
    'const out=items.map(it=>{try{'
    'return katex.renderToString(String(it.tex),'
    '{throwOnError:false,displayMode:!!it.display,output:"mathml"})'
    '}catch(e){return null}});'
    'process.stdout.write(JSON.stringify(out))});'
)


def _render_math_batch(items: List[Dict[str, Any]]) -> List[Optional[str]]:
    """Render a batch of LaTeX expressions to KaTeX MathML via Node.

    ``items`` is a list of ``{"tex": str, "display": bool}``. Returns a list of
    MathML strings positionally aligned with the input; an element is ``None``
    when that individual expression failed to render. Returns an all-``None``
    list (never raises) when KaTeX/node is unavailable or the subprocess fails,
    so the caller falls back to the original escaped LaTeX text.
    """
    if not items:
        return []
    if not _KATEX_AVAILABLE:
        return [None] * len(items)
    env = dict(os.environ)
    env['NODE_PATH'] = _KATEX_NODE_MODULES  # type: ignore[assignment]
    try:
        proc = subprocess.run(
            [_NODE_BIN, '-e', _KATEX_RENDER_JS],
            input=json.dumps(items),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    except Exception:  # pragma: no cover - defensive: never fail the export
        return [None] * len(items)
    if proc.returncode != 0 or not proc.stdout.strip():
        return [None] * len(items)
    try:
        rendered = json.loads(proc.stdout)
    except Exception:  # pragma: no cover - defensive
        return [None] * len(items)
    if not isinstance(rendered, list) or len(rendered) != len(items):
        return [None] * len(items)
    # Only trust strings that actually carry KaTeX output; drop anything else.
    return [r if (isinstance(r, str) and 'class="katex' in r) else None for r in rendered]


def _viz_fingerprint(source: str) -> str:
    """Content fingerprint for matching captured diagrams to code blocks.

    Must stay in sync with the data-viz-source-hash attribute set by
    D3Renderer.tsx.
    """
    normalized = source.strip()
    return f"{len(normalized)}:{normalized[:64]}"

# All visualization code-fence languages recognised by the exporter.
#
# LaTeX languages are derived from the backend profile registry rather than
# hand-listed.  Hand-listing meant only 'circuitikz' was recognised, while
# 'chemfig', 'tikz' and 'tikz-cd' were fully supported renderers whose fences
# this exporter silently ignored: they exported as plain code blocks with no
# embedded diagram and no "paste into a renderer" hint.
#
# Module-scope import is safe: latex_profiles imports only logging, dataclasses
# and typing, so it adds no startup cost and cannot cycle.  This module already
# depends on app.services elsewhere (diagram_renderer, imported lazily because
# Playwright may be absent -- not the case here).
#
# Alternation order is deliberately NOT sorted longest-first.  Every use of
# _VIZ_TYPES_RE places a delimiter immediately after the capture group (a
# newline, or '">'), so a 'tikz' alternative cannot shadow a 'tikz-cd' fence --
# the regex backtracks and matches the longer name.  Verified at all three
# call sites.
from app.services.latex_profiles import PROFILES as _LATEX_PROFILES

_VIZ_TYPES = (
    'graphviz', 'mermaid', 'vega-lite', 'd3', 'joint',
    'packet', 'railroad', 'wavedrom', 'flamegraph', 'timeline',
    'drawio', 'designinspector',
) + tuple(_LATEX_PROFILES)
_VIZ_TYPES_RE = '|'.join(_VIZ_TYPES)

logger = logging.getLogger(__name__)

def export_conversation_for_paste(
    messages: List[Dict[str, Any]],
    format_type: str = 'markdown',
    target: str = 'public',  # Target paste service ID (extensible via plugins)
    captured_diagrams: Optional[List[Dict[str, Any]]] = None,
    version: str = '0.3.8',
    model: str = 'unknown',
    provider: str = 'unknown'
) -> Dict[str, Any]:
    """
    Export a conversation in a format suitable for paste services.
    
    Args:
        messages: List of conversation messages
        format_type: 'markdown' or 'html'
        target: Target paste service ID (extensible via plugins)
        captured_diagrams: List of captured visualization data URIs with metadata
        version: Ziya version
        model: Model name/alias
        provider: Provider name (bedrock, google, etc.)
        
    Returns:
        Dictionary with exported content and metadata
    """
    # Create diagram lookup keyed by content fingerprint.
    # Falls back to sourceHash sent from the frontend capture utility.
    diagram_by_hash = {}
    if captured_diagrams:
        for diagram in captured_diagrams:
            h = diagram.get('sourceHash')
            if h:
                diagram_by_hash[h] = diagram
    
    if format_type == 'html':
        content = _export_as_html(messages, target, version, model, provider, diagram_by_hash)
        filename = f"ziya_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    else:
        content = _export_as_markdown(messages, target, version, model, provider, diagram_by_hash)
        filename = f"ziya_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    
    return {
        "content": content,
        "filename": filename,
        "format": format_type,
        "target": target,
        "size": len(content),
        "message_count": len(messages),
        "diagrams_count": len(diagram_by_hash)
    }

def _extract_diagram_specs(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract diagram code-fence specs from message content.

    Returns a list of dicts with keys: type, definition, fingerprint.
    """
    specs = []
    viz_pattern = r'```(' + _VIZ_TYPES_RE + r')\n(.*?)```'
    for msg in messages:
        content = msg.get('content', '')
        if not content:
            continue
        for match in re.finditer(viz_pattern, content, re.DOTALL):
            viz_type = match.group(1)
            source = match.group(2)
            fp = _viz_fingerprint(source)
            specs.append({
                'type': viz_type,
                'definition': source.strip(),
                'fingerprint': fp,
            })
    return specs

async def render_diagrams_server_side(
    messages: List[Dict[str, Any]],
    theme: str = 'light',
    format: str = 'svg',
    server_port: int = 6969,
) -> Dict[str, Dict[str, Any]]:
    """Render all diagrams in messages using the headless Playwright renderer.

    Returns a dict keyed by content fingerprint, matching the
    ``diagram_by_hash`` structure expected by the embedding functions.
    Each value has: dataUri, type ('svg'|'png'), sourceHash.

    Falls back gracefully when Playwright is not installed -- returns
    an empty dict so the exporter produces source-code-only output.
    """
    specs = _extract_diagram_specs(messages)
    if not specs:
        return {}

    try:
        from app.services.diagram_renderer import get_diagram_renderer
    except ImportError:
        logger.info("Playwright not installed -- diagrams exported as source code")
        return {}

    try:
        renderer = await get_diagram_renderer(server_port)
    except Exception as exc:
        logger.warning("Could not start headless renderer: %s", exc)
        return {}

    diagram_by_hash: Dict[str, Dict[str, Any]] = {}

    for spec in specs:
        fp = spec['fingerprint']
        if fp in diagram_by_hash:
            continue  # already rendered (duplicate diagram)

        try:
            image_bytes = await renderer.render_diagram(
                {
                    'type': spec['type'],
                    'definition': spec['definition'],
                    'theme': theme,
                },
                format=format,
            )

            if format == 'svg':
                b64 = base64.b64encode(image_bytes).decode('utf-8')
                data_uri = f"data:image/svg+xml;base64,{b64}"
            else:
                b64 = base64.b64encode(image_bytes).decode('utf-8')
                data_uri = f"data:image/png;base64,{b64}"

            diagram_by_hash[fp] = {
                'dataUri': data_uri,
                'type': format,
                'sourceHash': fp,
            }
            logger.info("Rendered %s diagram (%d bytes)", spec['type'], len(image_bytes))
        except Exception as exc:
            logger.warning("Failed to render %s diagram: %s", spec['type'], exc)

    logger.info("Server-side rendering: %d/%d diagrams rendered",
                len(diagram_by_hash), len(specs))
    return diagram_by_hash

async def export_conversation_rendered(
    messages: List[Dict[str, Any]],
    format_type: str = 'markdown',
    target: str = 'public',
    theme: str = 'light',
    version: str = '0.3.8',
    model: str = 'unknown',
    provider: str = 'unknown',
    server_port: int = 6969,
) -> Dict[str, Any]:
    """Export a conversation with server-side rendered diagrams.

    Unlike ``export_conversation_for_paste`` (which relies on client-
    captured data URIs), this function uses the headless Playwright
    renderer to produce diagram images server-side.  Use this for
    backend-initiated exports (plugin targets, CLI, API consumers).
    """
    diagram_by_hash = await render_diagrams_server_side(
        messages, theme=theme, format='svg', server_port=server_port,
    )

    return export_conversation_for_paste(
        messages=messages,
        format_type=format_type,
        target=target,
        captured_diagrams=list(diagram_by_hash.values()),
        version=version,
        model=model,
        provider=provider,
    )

# Match a run of backticks that starts a line (a candidate code-fence line).
# Tool output routinely contains ``` runs (shell output, nested code); the
# wrapper fence must be LONGER than the longest such run or the inner run closes
# the wrapper early and the rest of the output leaks out as prose (defect MD-04).
_MD_BACKTICK_RUN_RE = re.compile(r'^[ \t]*(`{3,})', re.MULTILINE)


def _fence_for_content(content: str, minimum: int = 3) -> str:
    """Return a backtick fence guaranteed to wrap ``content`` without early close.

    CommonMark closes a fenced block on the first line whose leading backtick run
    is >= the opener's length (with no info string). So the wrapper must use MORE
    backticks than the longest fence-like run inside the content. Returns a string
    of at least ``minimum`` backticks, one longer than the longest interior run.
    """
    longest = 0
    for m in _MD_BACKTICK_RUN_RE.finditer(content or ""):
        longest = max(longest, len(m.group(1)))
    ticks = max(minimum, longest + 1)
    return "`" * ticks


def _clean_tool_blocks(content: str) -> str:
    """
    Replace HTML comment tool blocks with formatted output.
    Converts: <!-- TOOL_BLOCK_START:mcp_tool|Header -->...<!-- TOOL_BLOCK_END:mcp_tool -->
    Also converts|syntax ... ```` fenced blocks
    To: Formatted markdown section with tool output
    """

    # Pattern to match tool blocks
    # Markers include an optional tool-use ID suffix (|toolu_xxx) added by chatApi.ts.
    # Match both formats so the replacement fires and post-tool markdown is preserved.
    pattern = r'<!-- TOOL_BLOCK_START:(mcp_\w+)\|(.+?)(?:\|toolu_[^>]+)? -->\s*(.*?)\s*<!-- TOOL_BLOCK_END:\1(?:\|toolu_[^>]+)? -->'

    def replace_tool_block(match):
        tool_name = match.group(1)
        display_header = match.group(2).strip()
        tool_content = match.group(3).strip()

        # Parse display_header which may contain a syntax hint suffix: "Header|sh"
        header_parts = display_header.rsplit('|', 1)
        header_text = header_parts[0].strip()
        syntax_hint = header_parts[1].strip() if len(header_parts) > 1 and len(header_parts[1].strip()) <= 12 else ''

        # Use the display header directly — it already contains the
        # pretty-printed representation built by the streaming pipeline
        # (e.g. "🔧 Shell Command: $ ls -la"). No recomposition needed.
        formatted = f"\n**{header_text}**\n\n"

        # Use syntax hint for the code fence language when available
        fence_lang = syntax_hint if syntax_hint else ''
        # MD-04: pick a fence longer than any ``` run inside the tool output so
        # an inner fence cannot close the wrapper early and leak output as prose.
        fence = _fence_for_content(tool_content)
        formatted += (
            f"<details>\n<summary>Tool Output</summary>\n\n"
            f"{fence}{fence_lang}\n{tool_content}\n{fence}\n\n"
            f"</details>\n"
        )

        return formatted

    cleaned = re.sub(pattern, replace_tool_block, content, flags=re.DOTALL)

    # Handle backtick-fenced tool blocks: the default format for non-hierarchical tool results.
    # The fence uses 4 backticks so it can contain 3-backtick content.
    fence_pattern = r'(`{4,})tool:(mcp_\w+)\|([^\n]+)\n([\s\S]*?)\1'

    def replace_fence_tool_block(match):
        fence = match.group(1)
        tool_name = match.group(2)
        header_and_syntax = match.group(3).strip()
        tool_content = match.group(4).strip()

        # Header format: "displayHeader|syntax" (e.g. "Shell: $ ls -la|bash")
        parts = header_and_syntax.rsplit('|', 1)
        header_text = parts[0].strip()
        syntax_hint = ''
        if len(parts) > 1 and len(parts[1].strip()) <= 12:
            syntax_hint = parts[1].strip()
            # "text" is a placeholder, not a real language
            if syntax_hint == 'text':
                syntax_hint = ''

        formatted = f"\n**{header_text}**\n\n"

        fence_lang = syntax_hint if syntax_hint else ''
        # MD-04: fence longer than any ``` run inside the tool output.
        fence = _fence_for_content(tool_content)
        formatted += (
            f"<details>\n<summary>Tool Output</summary>\n\n"
            f"{fence}{fence_lang}\n{tool_content}\n{fence}\n\n"
            f"</details>\n"
        )

        return formatted

    cleaned = re.sub(fence_pattern, replace_fence_tool_block, cleaned)

    return cleaned

def _clean_thinking_blocks(content: str) -> str:
    """
    Replace thinking code blocks with formatted sections.
    Converts: ```thinking:step-N ... ``` to formatted thinking sections
    """

    # Pattern to match thinking blocks
    pattern = r'```thinking:step-(\d+)\n(.*?)```'

    def replace_thinking_block(match):
        step_number = match.group(1)
        thinking_content = match.group(2).strip()

        # Remove the "🤔 **Thought N/M**" header if present (we'll add our own)
        thinking_content = re.sub(r'^🤔 \*\*Thought \d+/\d+\*\*\n+', '', thinking_content)

        # Remove status suffixes like "_Continuing..._" or "_✅ Complete._"
        thinking_content = re.sub(r'\n+_.*?_\s*$', '', thinking_content)

        # Format as a collapsible section
        formatted = f"\n<details>\n<summary>💭 Reasoning (Step {step_number})</summary>\n\n{thinking_content}\n\n</details>\n"

        return formatted

    cleaned = re.sub(pattern, replace_thinking_block, content, flags=re.DOTALL)
    return cleaned

def _process_content_for_export(content: str) -> str:
    """Process content to clean up tool and thinking blocks for export."""
    # Drop superseded diffs first (MD-01): the UI fades them to opacity 0.45
    # but markdown has no opacity, so a retained stale diff is indistinguishable
    # from the live one. Runs on the raw content — the tool/thinking cleanup
    # below does not touch ```diff fences, so ordering is safe either way.
    content = _strip_superseded_diffs(content)
    # Drop live-session UI chrome (MD-02): the auto-added-context banner and the
    # checking-context spinner instruct the reader to click UI that does not
    # exist in an exported document. Removing them is not information loss.
    content = _strip_ui_chrome(content)
    content = _clean_tool_blocks(content)
    content = _clean_thinking_blocks(content)
    return content


# ---------------------------------------------------------------------------
# Superseded-diff detection (export hygiene, defect MD-01).
#
# When an assistant message diffs the SAME file twice in one turn, the second
# diff supersedes the first. The chat UI (MarkdownRenderer.tsx ~line 3916,
# driven by frontend/src/utils/diffUtils.ts findSupersededDiffParts) merely
# FADES the stale diff to opacity 0.45 — it stays rendered. Markdown has no
# opacity, so a retained stale diff is indistinguishable from the live one and
# is actively misleading in an export.
#
# Supersession is computed at RENDER time on the frontend and is never
# persisted, so there is no marker the backend can read. These functions are a
# FAITHFUL PORT of the diffUtils.ts algorithm (extractDiffFilePath,
# parseHunkRanges, extractNoPosLocators, rangesOverlap, isSequentialPair,
# findSupersededDiffIndices, findSupersededDiffParts). Keep them in exact sync
# with the TS source; the shared golden-case test
# (tests/export_fidelity/test_superseded_diff_port.py) guards against drift.
# ---------------------------------------------------------------------------

_DIFF_HUNK_RANGE_RE = re.compile(r'@@ -(\d+)(?:,(\d+))? \+')
_DIFF_NOPOS_RE = re.compile(r'^@@.*@@\s*ZIYA_NOPOS\s*(.*)$', re.MULTILINE)
_DIFF_GIT_HEADER_RE = re.compile(r'^diff --git .*$', re.MULTILINE)


def _diff_extract_file_path(diff_content: str) -> Optional[str]:
    """Port of diffUtils.extractDiffFilePath: target path of a single-file diff."""
    for line in diff_content.split('\n'):
        if line.startswith('+++ b/'):
            return line[6:].strip()
        if line.startswith('+++ '):
            path = line[4:].strip()
            if path.startswith('b/'):
                path = path[2:]
            if path == '/dev/null':
                continue
            return path
    return None


def _diff_parse_hunk_ranges(diff_content: str) -> List[tuple]:
    """Port of diffUtils.parseHunkRanges: original-file [start, end] ranges."""
    ranges: List[tuple] = []
    for match in _DIFF_HUNK_RANGE_RE.finditer(diff_content):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if start == 0 and count == 0:
            continue  # new-file creation
        end = start + max(count - 1, 0)
        ranges.append((start, end))
    return ranges


def _diff_extract_nopos_locators(diff_content: str) -> set:
    """Port of diffUtils.extractNoPosLocators: ZIYA_NOPOS functional locators."""
    locators = set()
    for match in _DIFF_NOPOS_RE.finditer(diff_content):
        loc = match.group(1).strip()
        if loc:
            locators.add(loc)
    return locators


def _diff_ranges_overlap(a: List[tuple], b: List[tuple]) -> bool:
    """Port of diffUtils.rangesOverlap: >50% overlap of the smaller hunk."""
    for a_start, a_end in a:
        for b_start, b_end in b:
            if a_start <= b_end and b_start <= a_end:
                overlap_start = max(a_start, b_start)
                overlap_end = min(a_end, b_end)
                overlap_size = overlap_end - overlap_start + 1
                smaller_hunk = min(a_end - a_start + 1, b_end - b_start + 1)
                if smaller_hunk > 0 and overlap_size / smaller_hunk > 0.5:
                    return True
    return False


def _diff_is_sequential_pair(earlier_diff: str, later_diff: str) -> bool:
    """Port of diffUtils.isSequentialPair.

    Earlier diff is predominantly subtractive (removing code to make way) and
    the later adds new content -> complementary, NOT a supersession.
    """
    earlier_adds = earlier_removes = later_adds = 0
    for line in earlier_diff.split('\n'):
        if (line.startswith('@@') or line.startswith('diff ')
                or line.startswith('---') or line.startswith('+++')):
            continue
        if line.startswith('+'):
            earlier_adds += 1
        elif line.startswith('-'):
            earlier_removes += 1
    for line in later_diff.split('\n'):
        if (line.startswith('@@') or line.startswith('diff ')
                or line.startswith('---') or line.startswith('+++')):
            continue
        if line.startswith('+'):
            later_adds += 1
    return earlier_removes > 0 and earlier_adds <= 1 and later_adds > 0


def _diff_find_superseded_indices(diffs: List[str]) -> set:
    """Port of diffUtils.findSupersededDiffIndices.

    Given ordered single-file diff strings, return the indices superseded by a
    later diff for the same file with overlapping hunk ranges.
    """
    if len(diffs) <= 1:
        return set()
    file_paths = [_diff_extract_file_path(d) for d in diffs]
    hunk_ranges = [_diff_parse_hunk_ranges(d) for d in diffs]
    nopos_locators = [_diff_extract_nopos_locators(d) for d in diffs]
    superseded = set()
    for i in range(len(diffs)):
        if not file_paths[i]:
            continue
        for j in range(i + 1, len(diffs)):
            if file_paths[j] != file_paths[i]:
                continue
            # Both are new-file diffs for the same path — later wins.
            if len(hunk_ranges[i]) == 0 and len(hunk_ranges[j]) == 0:
                superseded.add(i)
                break
            # Synthesized (ZIYA_NOPOS) hunks: judge by shared named locator.
            if nopos_locators[i] or nopos_locators[j]:
                shares_locator = any(loc in nopos_locators[j] for loc in nopos_locators[i])
                if shares_locator and not _diff_is_sequential_pair(diffs[i], diffs[j]):
                    superseded.add(i)
                    break
                continue  # different / unknown locators -> independent changes
            if _diff_ranges_overlap(hunk_ranges[i], hunk_ranges[j]):
                if _diff_is_sequential_pair(diffs[i], diffs[j]):
                    continue
                superseded.add(i)
                break
    return superseded


def _diff_flatten_block(block: str) -> List[str]:
    """Split one ```diff fence body into its per-``diff --git``-header sections.

    Mirrors the flatten step of diffUtils.findSupersededDiffParts: a headerless
    or single-file block is one unit; a multi-file block splits at each header.
    """
    starts = [m.start() for m in _DIFF_GIT_HEADER_RE.finditer(block)]
    if len(starts) <= 1:
        return [block]
    sections: List[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(block)
        sections.append(block[start:end].strip())
    return sections


def _find_superseded_diff_parts(diff_blocks: List[str]) -> Dict[int, set]:
    """Port of diffUtils.findSupersededDiffParts.

    ``diff_blocks`` is the ordered list of ```diff fence bodies. Returns a map
    blockIndex -> set of superseded file-section indices within that block.
    """
    flattened: List[Dict[str, Any]] = []
    for block_index, block in enumerate(diff_blocks):
        sections = _diff_flatten_block(block)
        if len(sections) == 1:
            flattened.append({'blockIndex': block_index, 'fileIndex': 0, 'text': block})
        else:
            for file_index, text in enumerate(sections):
                flattened.append({'blockIndex': block_index, 'fileIndex': file_index, 'text': text})
    superseded_flat = _diff_find_superseded_indices([p['text'] for p in flattened])
    result: Dict[int, set] = {}
    for flat_index in superseded_flat:
        part = flattened[flat_index]
        result.setdefault(part['blockIndex'], set()).add(part['fileIndex'])
    return result


# Matches a fenced ``diff`` code block, capturing the fence run, the body, and
# the closing fence. The opening fence run length is back-referenced so the
# block closes on a fence of the SAME length (a longer inner ``` cannot close
# a 4-backtick wrapper), and re.MULTILINE anchors it to a line start so an
# inline "```diff" inside prose is not treated as a block opener.
_MD_DIFF_FENCE_RE = re.compile(
    r'^(?P<fence>`{3,})[ \t]*diff[ \t]*\n(?P<body>.*?)(?P=fence)[ \t]*$',
    re.DOTALL | re.MULTILINE,
)


def _strip_superseded_diffs(content: str) -> str:
    """Drop superseded diffs from a message so the export shows only live ones.

    Applies the ported diffUtils supersession algorithm across all ```diff
    fences in ``content``. A block whose file-sections are ALL superseded is
    removed entirely (its fence too); a block with only SOME sections
    superseded keeps the surviving sections. Non-diff content is untouched, so
    surrounding prose is never collateral-damaged.
    """
    matches = list(_MD_DIFF_FENCE_RE.finditer(content))
    if len(matches) <= 1:
        return content  # a single diff block can never be superseded

    blocks = [m.group('body') for m in matches]
    superseded_map = _find_superseded_diff_parts(blocks)
    if not superseded_map:
        return content

    # Rebuild the content, replacing each affected fence. Splice by span so
    # untouched regions are preserved byte-for-byte.
    out: List[str] = []
    cursor = 0
    for block_index, match in enumerate(matches):
        out.append(content[cursor:match.start()])
        dropped = superseded_map.get(block_index)
        if not dropped:
            out.append(match.group(0))  # unchanged fence
        else:
            fence = match.group('fence')
            sections = _diff_flatten_block(match.group('body'))
            if len(sections) == 1:
                # The whole block is superseded: drop the fence entirely.
                pass
            else:
                kept = [s for idx, s in enumerate(sections) if idx not in dropped]
                if kept:
                    rebuilt_body = '\n'.join(kept)
                    out.append(f"{fence}diff\n{rebuilt_body}\n{fence}")
                # else: every section dropped -> drop the fence entirely.
        cursor = match.end()
    out.append(content[cursor:])
    result = ''.join(out)
    # A removed fence can leave a run of blank lines behind; collapse 3+ blank
    # lines to a single paragraph break so the surrounding prose stays tidy.
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


# ---------------------------------------------------------------------------
# Live-session UI-chrome stripping (export hygiene, defect MD-02).
#
# The chat UI renders an "auto-added context" affordance (MarkdownRenderer.tsx
# ~line 3554):
#
#   Auto-added N file(s) to context (a.py, b.py) — available for subsequent
#   queries. Remove via the A button in the Files panel.
#
# plus a transient "🔄 Checking context..." spinner. These are live-session
# affordances — they instruct the reader to click a button that does not exist
# in an exported document. Dropping them is NOT information loss: they carry no
# conversational content, and the UI itself treats them as ephemeral chrome
# rather than message text. The real answer beside the banner is preserved.
#
# The banner is emitted as a single logical line, so each pattern is anchored
# per-line (re.MULTILINE, NO DOTALL) and requires the banner's stable phrase
# sequence on one line. That keeps the match from spanning into — and eating —
# adjacent prose paragraphs. Any regex here is deliberately narrow; the
# no_ui_chrome check + its keep-marker guard the surrounding content.
# ---------------------------------------------------------------------------

# The auto-added-context banner: requires all four stable phrases, in order, on
# a single line. The file list and count vary, so they are matched loosely
# (`.*?`) BETWEEN the fixed anchors — never before "Auto-added" or after
# "Files panel", so the match cannot bleed into neighbouring text.
_MD_UI_CHROME_AUTOADD_RE = re.compile(
    r'^[ \t>*-]*'                       # optional leading list/quote markers
    r'Auto-added\b.*?'                  # anchor 1
    r'\bto context\b.*?'                # anchor 2
    r'available for subsequent queries\b.*?'  # anchor 3
    r'Remove via\b.*?'                  # anchor 4
    r'Files panel\.?'                   # anchor 5 (end of banner)
    r'[ \t]*$',
    re.MULTILINE,
)

# The transient context-check spinner. Distinctive emoji + exact phrase; matched
# as a whole line so it cannot eat prose that merely mentions "checking".
_MD_UI_CHROME_SPINNER_RE = re.compile(
    r'^[ \t>*-]*\U0001F504?\s*Checking context\.{2,3}[ \t]*$',
    re.MULTILINE,
)


def _strip_ui_chrome(content: str) -> str:
    """Remove live-session UI-chrome affordances from exported content (MD-02).

    Strips the auto-added-context banner and the checking-context spinner. Both
    are matched per-line against their verbatim phrasing, so real answer text on
    other lines is never touched. Blank lines left behind by a removed banner
    collapse to a single paragraph break.
    """
    if 'Auto-added' not in content and 'Checking context' not in content:
        return content  # fast path: nothing to strip
    result = _MD_UI_CHROME_AUTOADD_RE.sub('', content)
    result = _MD_UI_CHROME_SPINNER_RE.sub('', result)
    if result == content:
        return content
    # A removed banner leaves an empty line; collapse 3+ blanks to one break.
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


# ---------------------------------------------------------------------------
# Per-message code-fence balancing (export hygiene, defect MD-03).
#
# The markdown export joins raw message contents with "---" separators. Under
# CommonMark, an ODD number of net fence toggles in ONE message leaves a fence
# OPEN — which then swallows every following message AND the export footer into
# a single code block (Gist, GitHub, local viewers all behave this way). The
# chat UI never shows this because it renders each message in its own isolated
# container, so an unbalanced fence is confined to that message; the joined
# markdown document has no such isolation.
#
# The fix mirrors that per-message isolation losslessly: we detect an
# unterminated fence within a single message's content and append a matching
# closing fence at the message boundary. The author's code block still renders
# in full — it simply ends where the message ends (as it does in the UI)
# instead of consuming the rest of the document. No content is dropped.
#
# The fence-event semantics here are kept identical to
# tests/export_fidelity/checks._iter_fence_events / check_md_fence_integrity so
# the fix and its verifier agree on what "balanced" means.
# ---------------------------------------------------------------------------

_MD_FENCE_LINE_RE = re.compile(r'^[ \t]*(?P<ticks>`{3,})(?P<info>[^\n]*)$')


def _balance_code_fences(content: str) -> str:
    """Close any code fence left open within a single message's content (MD-03).

    Walks the content as CommonMark does: a fence opens with N backticks and an
    info string; it closes on the first later line of >= N backticks with an
    empty info string. If a fence is still open at the end of the content, a
    closing fence of the opener's tick-length is appended so the block cannot
    leak into the next message or the footer.
    """
    if '```' not in content:
        return content  # fast path: no fences at all
    open_ticks = None
    for line in content.split('\n'):
        m = _MD_FENCE_LINE_RE.match(line)
        if not m:
            continue
        ticks = len(m.group('ticks'))
        info = m.group('info').strip()
        if open_ticks is None:
            open_ticks = ticks  # opening a fence (with or without info)
        elif ticks >= open_ticks and not info:
            open_ticks = None  # a bare, long-enough line closes the fence
        # else: a longer or info-bearing fence line inside a block is content
    if open_ticks is None:
        return content  # already balanced
    # A fence was left open: append a matching closer at the message boundary.
    closer = '`' * open_ticks
    sep = '' if content.endswith('\n') else '\n'
    return f"{content}{sep}{closer}"


def _export_as_markdown(
    messages: List[Dict[str, Any]],
    target: str,
    version: str,
    model: str,
    provider: str,
    diagram_by_hash: Dict[str, Dict[str, Any]]
) -> str:
    """Export conversation as Markdown with embedded visualizations."""
    lines = []
    
    # Add header
    lines.append("# Ziya Conversation Export")
    lines.append("")
    lines.append(f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if diagram_by_hash:
        lines.append(f"**Visualizations:** {len(diagram_by_hash)} diagram(s) embedded")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Process each message
    for i, msg in enumerate(messages):
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        
        # Skip empty messages
        if not content or not content.strip():
            continue
        
        # Add message header
        if role == 'human':
            lines.append(f"## 👤 User")
        elif role == 'assistant':
            lines.append(f"## 🤖 AI Assistant")
        elif role == 'system':
            # Skip system messages in export
            continue
        else:
            lines.append(f"## {role.title()}")
        
        lines.append("")

        # Process content to clean up tool blocks before embedding diagrams
        content = _process_content_for_export(content)
        
        # Process content to handle and embed visualizations
        processed_content = _embed_diagrams_in_markdown(
            content, 
            diagram_by_hash
        )

        # Close any code fence left open within THIS message (MD-03) so it
        # cannot swallow the following messages or the footer once joined.
        # Done per-message, at the true message boundary, mirroring the UI's
        # per-message rendering isolation. Lossless: the block still renders,
        # it just ends where the message ends.
        processed_content = _balance_code_fences(processed_content)

        lines.append(processed_content)
        
        lines.append("")
        lines.append("---")
        lines.append("")
    
    # Add footer with metadata
    lines.append(_create_footer(target, version, model, provider, 'markdown'))
    
    return "\n".join(lines)

def _embed_diagrams_in_markdown(
    content: str,
    diagram_by_hash: Dict[str, Dict[str, Any]]
) -> str:
    """
    Embed captured diagrams in markdown content.
    """
    # Find visualization code blocks and replace with embedded versions
    viz_pattern = r'\`\`\`(' + _VIZ_TYPES_RE + r')\n(.*?)\`\`\`'
    
    def embed_diagram(match):
        viz_type = match.group(1)
        source_code = match.group(2)
        
        # Match to captured diagram by content fingerprint
        fp = _viz_fingerprint(source_code)
        diagram = diagram_by_hash.get(fp)
        if diagram and diagram.get('dataUri'):
            data_uri = diagram['dataUri']
            b64 = data_uri.split(',')[1] if ',' in data_uri else data_uri
            return f"![{viz_type} diagram](data:image/svg+xml;base64,{b64})\n"
        else:
            # No captured diagram available, keep original code block with note
            return f"""```{viz_type}
{source_code}
```

> *Visualization not captured. This is the source code - paste into a {viz_type} renderer to view.*

"""
    
    processed = re.sub(viz_pattern, embed_diagram, content, flags=re.DOTALL)
    
    return processed

def _export_as_html(
    messages: List[Dict[str, Any]],
    target: str,
    version: str,
    model: str,
    provider: str,
    diagram_by_hash: Dict[str, Dict[str, Any]]
) -> str:
    """Export conversation as standalone HTML with embedded styles and visualizations."""
    
    html_parts = []
    
    # HTML header with styles
    html_parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ziya Conversation Export</title>
    <style>
        /* This export is a LIGHT-themed transcript. Pin the document to the
           light color scheme so a downloaded .html opens light regardless of
           the reader's OS/browser theme. `color-scheme: light` also stops the
           browser from applying dark UA styling (scrollbars, form controls) on
           a dark-mode host. Deliberately no host-theme dark media override:
           such a query is a non-reactive host vector that turned the light
           transcript dark on a dark-mode machine (defect HTML-04). */
        :root { color-scheme: light; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background: #ffffff;
            color: #24292e;
            color-scheme: light;
        }
        .header {
            border-bottom: 2px solid #e1e4e8;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }
        .message {
            margin: 20px 0;
            padding: 15px;
            border-left: 4px solid #0969da;
            background: #f6f8fa;
            border-radius: 6px;
        }
        .message.user { border-left-color: #0969da; }
        .message.assistant { border-left-color: #1a7f37; }
        .message-header {
            font-weight: 600;
            font-size: 16px;
            margin-bottom: 10px;
            color: #0969da;
        }
        .message.assistant .message-header { color: #1a7f37; }
        pre {
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
        }
        code {
            background: #f6f8fa;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 13px;
        }
        .diff-view {
            border: 1px solid #d0d7de;
            border-radius: 6px;
            overflow: hidden;
            margin: 16px 0;
        }
        /* Per-line diff coloring (HTML-02). Each line is a block-level span so
           the add/remove background tiles the full row width. Colors are the
           GitHub-light palette the chat UI uses (insert green / delete red);
           only the BACKGROUND changes — text color stays uniform, so this is
           line highlighting, not syntax (Prism) tokenization. */
        pre.diff-block { padding: 0; }
        pre.diff-block code { background: transparent; padding: 0; }
        .diff-line {
            display: block;
            padding: 0 16px;
            white-space: pre-wrap;
        }
        .diff-line-insert { background: #e6ffec; }
        .diff-line-delete { background: #ffebe9; }
        /* NOTE: intentionally NO `color` override on any diff line — all diff
           lines share the uniform code text color. Only the BACKGROUND varies
           per line. Introducing a second text color here would make the
           fidelity harness's syntax_highlighting probe (which counts distinct
           `pre code span` text colors) spuriously pass, falsely reporting
           HTML-01 (missing Prism tokenization) as fixed. Diff add/remove is
           conveyed by background alone, which is what check_diff_coloring reads. */
        .diff-line-hunk { background: #ddf4ff; }
        .diff-line-context { background: transparent; }
        /* GFM tables (HTML-06). The regex fallback used to emit tables as
           literal pipe text; they now render as real grid elements. The wrap
           allows horizontal scroll for very wide tables rather than clipping
           columns off the page. */
        .table-wrap { overflow-x: auto; margin: 16px 0; }
        .table-wrap table {
            border-collapse: collapse;
            width: auto;
            font-size: 14px;
        }
        .table-wrap th, .table-wrap td {
            border: 1px solid #d0d7de;
            padding: 6px 13px;
        }
        .table-wrap th { background: #f6f8fa; font-weight: 600; }
        .table-wrap tr:nth-child(2n) td { background: #f6f8fa; }
        .visualization {
            border: 1px solid #d0d7de;
            border-radius: 6px;
            padding: 16px;
            margin: 16px 0;
            text-align: center;
            background: #f6f8fa;
        }
        .visualization img,
        .visualization svg {
            max-width: 100%;
            height: auto;
            display: block;
            margin: 0 auto;
        }
        .viz-caption {
            font-size: 14px;
            color: #57606a;
            margin-top: 12px;
            font-style: italic;
        }
        details {
            margin-top: 12px;
        }
        summary {
            cursor: pointer;
            padding: 8px;
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-radius: 4px;
            user-select: none;
        }
        summary:hover {
            background: #e1e4e8;
        }
        .footer {
            margin-top: 40px;
            padding-top: 20px;
            border-top: 2px solid #e1e4e8;
            font-size: 14px;
            color: #57606a;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎯 Ziya Conversation Export</h1>
        <p><strong>Exported:</strong> """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</p>
    </div>
""")
    
    # Process each message
    for msg in messages:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        
        # Skip empty or system messages
        if not content or not content.strip() or role == 'system':
            continue
        
        role_class = 'user' if role == 'human' else 'assistant'
        role_emoji = '👤' if role == 'human' else '🤖'
        role_name = 'User' if role == 'human' else 'AI Assistant'
        
        html_parts.append(f'''
    <div class="message {role_class}">
        <div class="message-header">{role_emoji} {role_name}</div>
        <div class="message-content">
''')
        
        # Process content (convert markdown to HTML, embed visualizations)
        processed_content = _embed_diagrams_in_html(
            content,
            diagram_by_hash
        )
        
        html_parts.append(processed_content)
        
        html_parts.append("""
        </div>
    </div>
""")
    
    # Add footer
    html_parts.append(_create_footer(target, version, model, provider, 'html'))
    
    # Close HTML
    html_parts.append("""
</body>
</html>
""")
    
    return "".join(html_parts)

def _embed_diagrams_in_html(
    content: str,
    diagram_by_hash: Dict[str, Dict[str, Any]]
) -> str:
    """
    Embed captured diagrams directly in HTML content.
    """
    # Convert basic markdown to HTML first
    html = _markdown_to_html_basic(content)
    
    # Find visualization code blocks and replace with embedded diagrams
    viz_pattern = r'<pre><code class="language-(' + _VIZ_TYPES_RE + r')">(.*?)</code></pre>'
    
    def embed_diagram(match):
        viz_type = match.group(1)
        source_code = match.group(2)
        
        # Match to captured diagram by content fingerprint
        fp = _viz_fingerprint(source_code)
        diagram = diagram_by_hash.get(fp)
        
        if diagram and diagram.get('dataUri'):
            data_uri = diagram['dataUri']
            width = diagram.get('width', 600)
            height = diagram.get('height', 400)
            
            # Always embed via <img src="data:image/svg+xml;base64,...">.
            # CWE-79 (PenPal #116): the SVG bytes come from the headless
            # renderer applied to model-authored diagram specs (indirect
            # prompt-injection reachable), so inlining the decoded SVG into
            # the DOM (<div>{svg}</div>) let a <script>/on* handler in it
            # execute the moment the exported HTML was opened. Loading the SVG
            # via <img> is script-inert by browser design, preserves vector
            # quality, and matches the PNG path below — no bypassable SVG
            # sanitizer needed. viz_type is a fixed-allowlist capture and the
            # data-URI body is pure base64, so both are safe in the tag.
            return f'<div class="visualization"><img src="{data_uri}" alt="{viz_type} diagram" width="{width}" height="{height}"/></div>'
        else:
            # No captured diagram, show source with warning
            return f'<pre><code class="language-{viz_type}">{source_code}</code></pre>'
    
    html = re.sub(viz_pattern, embed_diagram, html, flags=re.DOTALL)
    
    return html

def _escape_html_text(s: str) -> str:
    """Escape a plain-text segment for safe embedding in HTML."""
    return (
        s.replace('&', '&amp;')
         .replace('<', '&lt;')
         .replace('>', '&gt;')
         .replace('"', '&quot;')
    )


# Schemes that execute script in a browser context when used as a link href
# (javascript:, vbscript:, data: with an html/svg payload). Blocked outright
# rather than allowlisted, since legitimate exported links are http(s)/mailto.
_DANGEROUS_LINK_SCHEME_RE = re.compile(r'^\s*(javascript|vbscript|data):', re.IGNORECASE)


def _render_diff_code_block(code: str) -> str:
    """Render a unified diff as per-line, background-colored elements (HTML-02).

    The regex fallback previously emitted a ``diff`` fence as one uncolored
    ``<pre><code class="language-diff">`` blob — no per-line add/remove
    elements, no green/red backgrounds — so ``check_diff_coloring`` saw 0
    inserts / 0 deletes and the raster ``diff_delete_red`` signal fell below
    threshold. We instead split the diff into lines and wrap each in a
    block-level ``<span>`` carrying a ``diff-line-insert`` / ``diff-line-delete``
    class (matched by the probe's ``[class*="insert"]`` / ``[class*="delete"]``
    selectors) with the GitHub-light insert green / delete red background.

    CWE-79: each line is HTML-escaped BEFORE the class-carrying span is
    generated (same order/escaping the plain code path uses), so raw HTML in a
    diff line is neutralized. Only a background COLOR is added per line — the
    text color stays the uniform code color, so this does NOT introduce Prism
    token spans (HTML-01 remains a distinct, still-open defect). The spans are
    well-formed children of ``<pre><code>`` (no block element nested in a
    ``<p>``), so the HTML-05 structural-validity guarantee is preserved.
    """
    lines = code.split('\n')
    # Drop a single trailing empty line from the fence's closing newline so we
    # don't emit a spurious blank context row.
    if lines and lines[-1] == '':
        lines = lines[:-1]
    out_lines: List[str] = []
    for line in lines:
        escaped = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if line.startswith('+') and not line.startswith('+++'):
            cls = 'diff-line diff-line-insert'
        elif line.startswith('-') and not line.startswith('---'):
            cls = 'diff-line diff-line-delete'
        elif line.startswith('@@'):
            cls = 'diff-line diff-line-hunk'
        else:
            cls = 'diff-line diff-line-context'
        # A wholly-empty line still needs a rendered row so per-line backgrounds
        # tile continuously; a zero-width span collapses, so emit a space.
        out_lines.append(f'<span class="{cls}">{escaped or " "}</span>')
    body = '\n'.join(out_lines)
    return f'<pre class="diff-block"><code class="language-diff">{body}</code></pre>'


# A GitHub-Flavored-Markdown table: a header row, a delimiter row of dashes
# (with optional leading/trailing pipes and per-column ``:`` alignment), then
# one or more body rows. Matched as a contiguous run of ``|``-bearing lines.
_TABLE_DELIM_RE = re.compile(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$')


def _split_table_row(line: str) -> List[str]:
    """Split a markdown table row into cell strings.

    Handles optional leading/trailing pipes and escaped ``\\|`` inside a cell.
    """
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    # Split on unescaped pipes, then restore the escaped ones.
    cells = re.split(r'(?<!\\)\|', s)
    return [c.replace('\\|', '|').strip() for c in cells]


def _render_markdown_table(block_lines: List[str]) -> str:
    """Render a GFM markdown table as a real HTML ``<table>`` (HTML-06).

    The regex fallback had NO table support, so a markdown table was emitted as
    literal pipe-delimited text (``| Col | --- | cell |`` with ``<br>``s) — the
    grid layout was lost even though the cell text survived. This converts the
    header + delimiter + body rows into ``<table><thead>/<tbody>`` with proper
    ``<th>``/``<td>`` cells and per-column text-alignment from the delimiter's
    ``:`` markers.

    CWE-79: every cell's text is HTML-escaped (``_escape_html_text``) before it
    is placed inside a tag, so raw HTML in a cell is neutralized exactly as the
    prose path does. The returned ``<table>`` is a block-level element routed
    through the ``\x00CODEBLOCK`` sentinel by the caller, so ``_wrap_paragraph``
    does NOT nest it in a ``<p>`` (the HTML-05 structural-validity guarantee is
    preserved — a ``<table>`` inside a ``<p>`` would trigger the same
    unclosed-tag recovery a ``<pre>`` did).
    """
    header = _split_table_row(block_lines[0])
    aligns = _split_table_row(block_lines[1])
    ncol = len(header)

    def _align_style(spec: str) -> str:
        spec = spec.strip()
        left = spec.startswith(':')
        right = spec.endswith(':')
        if left and right:
            return ' style="text-align:center"'
        if right:
            return ' style="text-align:right"'
        if left:
            return ' style="text-align:left"'
        return ''

    col_styles = [_align_style(aligns[i]) if i < len(aligns) else '' for i in range(ncol)]

    def _cells(row: List[str], tag: str) -> str:
        out = []
        for i in range(ncol):
            val = row[i] if i < len(row) else ''
            out.append(f'<{tag}{col_styles[i]}>{_escape_html_text(val)}</{tag}>')
        return ''.join(out)

    thead = f'<thead><tr>{_cells(header, "th")}</tr></thead>'
    body_rows = []
    for line in block_lines[2:]:
        if not line.strip():
            continue
        body_rows.append(f'<tr>{_cells(_split_table_row(line), "td")}</tr>')
    tbody = f'<tbody>{"".join(body_rows)}</tbody>' if body_rows else ''
    return f'<div class="table-wrap"><table>{thead}{tbody}</table></div>'


def _highlight_code_block(lang: str, code: str) -> Optional[str]:
    """Syntax-highlight a fenced code block with Pygments (HTML-01).

    The regex fallback previously emitted every non-diff fence as a bare
    ``<pre><code class="language-{lang}">`` whose content was uniformly one
    color — ``check_syntax_highlighting`` measured 0 distinct token colors and
    FAILED. When Pygments is available and knows the language, we instead emit
    inline-styled ``<span style="color:…">`` token spans (keyword / string /
    comment / … hues) inside ``<pre><code>``, so the block carries multiple
    distinct computed text colors.

    Returns ``None`` (caller falls back to the plain escaped block) when:
      * Pygments is unavailable — HTML export must never hard-fail on a missing
        dependency (the architectural dual-mode contract), or
      * the language is unknown to Pygments, or is a visualization type
        (mermaid/graphviz/…) that a LATER pass renders from raw source — those
        must reach that pass as ``language-{type}`` with un-highlighted source.

    CWE-79: Pygments' ``HtmlFormatter`` HTML-escapes every code token (``<``,
    ``>``, ``&``, ``"``) before wrapping it in a span, so raw HTML inside a code
    block (e.g. ``<script>``) is neutralized exactly as the plain path's manual
    escaping did — verified by the XSS suite. ``noclasses=True`` inlines the
    colors so the standalone document stays self-contained (no external Prism
    CSS), preserving the self_containment guarantee.
    """
    if not _PYGMENTS_AVAILABLE:
        return None
    # Visualization languages are rendered downstream from their RAW source
    # (see _process_visualizations_for_html / the language-(graphviz|mermaid|…)
    # pass); highlighting them here would both corrupt that source and be
    # pointless, so leave them for the plain path.
    if lang in _VIZ_TYPES:
        return None
    try:
        lexer = _pygments_get_lexer(lang, stripnl=False, stripall=False)
    except _PygmentsClassNotFound:
        return None
    except Exception:  # pragma: no cover - defensive: never fail the export
        return None
    # Drop the single trailing newline the fence's closing ``\n``` leaves, so we
    # don't emit a spurious blank final line inside the block.
    body = code[:-1] if code.endswith('\n') else code
    try:
        formatter = _PygmentsHtmlFormatter(noclasses=True, nowrap=True)
        highlighted = _pygments_highlight(body, lexer, formatter)
    except Exception:  # pragma: no cover - defensive: never fail the export
        return None
    return f'<pre><code class="language-{lang}">{highlighted}</code></pre>'


def _markdown_to_html_basic(markdown: str) -> str:
    """Basic markdown to HTML conversion.

    CWE-79: this function embeds unsanitized, LLM/user-authored conversation
    text into an HTML document that is later opened in a browser (paste
    services, direct file open). Everything outside of fenced/inline code
    (escaped separately, below) is HTML-escaped BEFORE any markdown->tag
    conversion runs, so raw HTML (`<img onerror=...>`, `<script>`, etc.)
    appearing in prose is neutralized rather than passed through verbatim.
    Code/inline code are extracted to placeholders first so escaping the
    rest of the string can't double-escape their already-escaped content.
    """
    html = markdown
    code_blocks: List[str] = []
    
    # Extract fenced code blocks to placeholders (escaped once, here).
    def convert_code_block(match):
        lang = match.group(1) or 'text'
        code = match.group(2)
        # Diffs get per-line add/remove coloring (HTML-02); the helper does its
        # own per-line HTML-escaping, so branch BEFORE the blanket escape below.
        if lang == 'diff' or code.lstrip().startswith('diff --git'):
            code_blocks.append(_render_diff_code_block(code))
            return f'\x00CODEBLOCK{len(code_blocks) - 1}\x00'
        # Syntax-highlight with Pygments when possible (HTML-01); the helper
        # does its own CWE-79 escaping and returns None (fall through to the
        # plain escaped block below) for viz languages / unknown langs / when
        # Pygments is unavailable, so the export never hard-fails.
        highlighted = _highlight_code_block(lang, code)
        if highlighted is not None:
            code_blocks.append(highlighted)
            return f'\x00CODEBLOCK{len(code_blocks) - 1}\x00'
        code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        code_blocks.append(f'<pre><code class="language-{lang}">{code}</code></pre>')
        return f'\x00CODEBLOCK{len(code_blocks) - 1}\x00'
    
    html = re.sub(r'```(\w+)?\n(.*?)```', convert_code_block, html, flags=re.DOTALL)
    
    # Extract inline code to placeholders (escaped once, here).
    inline_code: List[str] = []

    def convert_inline_code(match):
        inline_code.append(f'<code>{_escape_html_text(match.group(1))}</code>')
        return f'\x00INLINECODE{len(inline_code) - 1}\x00'

    html = re.sub(r'`([^`]+)`', convert_inline_code, html)

    # Extract LaTeX math to placeholders and render it with KaTeX (HTML-03).
    # This runs AFTER code/inline-code extraction so a literal ``$`` inside a
    # code span is never mistaken for math, and BEFORE the prose-escape below
    # because KaTeX emits real (MathML) markup that must NOT be escaped. Each
    # expression becomes a ``\x00MATHBLOCK{n}\x00`` sentinel (which survives the
    # escape pass and is not ``<p>``-wrapped away). Display ``$$…$$`` is matched
    # before inline ``$…$``. On any render failure the original LaTeX is
    # restored as escaped text, so behaviour is never worse than before.
    math_blocks: List[str] = []
    math_items: List[Dict[str, Any]] = []
    math_fallbacks: List[str] = []

    def convert_display_math(match):
        tex = match.group(1)
        math_items.append({"tex": tex, "display": True})
        math_fallbacks.append(_escape_html_text(f'$${tex}$$'))
        math_blocks.append('')  # filled in after the batch render
        return f'\x00MATHBLOCK{len(math_blocks) - 1}\x00'

    def convert_inline_math(match):
        tex = match.group(1)
        math_items.append({"tex": tex, "display": False})
        math_fallbacks.append(_escape_html_text(f'${tex}$'))
        math_blocks.append('')
        return f'\x00MATHBLOCK{len(math_blocks) - 1}\x00'

    # Display first (``$$…$$`` may span lines); then inline single-line ``$…$``.
    html = re.sub(r'\$\$(.+?)\$\$', convert_display_math, html, flags=re.DOTALL)
    html = re.sub(r'\$([^$\n]+?)\$', convert_inline_math, html)

    if math_items:
        rendered_math = _render_math_batch(math_items)
        for i, mathml in enumerate(rendered_math):
            # Trust only real KaTeX output; otherwise keep the escaped LaTeX.
            math_blocks[i] = mathml if mathml else math_fallbacks[i]

    # Extract GFM tables to code-block placeholders and render them as real
    # <table> elements (HTML-06). This runs AFTER code/math extraction (so a
    # ``|`` inside a code span or math is never mistaken for a table cell) and
    # BEFORE the prose escape (the helper escapes each cell itself, and the
    # emitted <table> must reach the browser as real markup). A table is a
    # header line, a delimiter line of dashes, then >=1 body line, all
    # ``|``-bearing and contiguous. Routing through the CODEBLOCK sentinel keeps
    # _wrap_paragraph from nesting the block-level <table> inside a <p>
    # (preserving the HTML-05 structural-validity guarantee).
    def _extract_tables(text: str) -> str:
        lines = text.split('\n')
        out: List[str] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            # A candidate table needs a header row with a pipe, immediately
            # followed by a delimiter row of dashes.
            if ('|' in line and i + 1 < n and _TABLE_DELIM_RE.match(lines[i + 1])
                    and '\x00' not in line):
                block = [line, lines[i + 1]]
                j = i + 2
                while j < n and '|' in lines[j] and lines[j].strip() and '\x00' not in lines[j]:
                    block.append(lines[j])
                    j += 1
                code_blocks.append(_render_markdown_table(block))
                out.append(f'\x00CODEBLOCK{len(code_blocks) - 1}\x00')
                i = j
            else:
                out.append(line)
                i += 1
        return '\n'.join(out)

    html = _extract_tables(html)

    # Escape all remaining prose before generating any real HTML tags below —
    # this is the fix: previously the input reached <strong>/<a>/<h1>/<p>
    # generation completely unescaped, so raw HTML in prose (or a javascript:
    # link target) was emitted into the exported document verbatim.
    html = _escape_html_text(html)
    
    # Convert bold
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    
    # Convert italic
    html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
    
    # Convert links — reject javascript:/vbscript:/data: hrefs; the label and
    # href were already HTML-escaped above, so unescape just the href for the
    # scheme check (comparing against the escaped form would miss variants).
    def convert_link(match):
        label, href = match.group(1), match.group(2)
        raw_href = (
            href.replace('&amp;', '&').replace('&lt;', '<')
                .replace('&gt;', '>').replace('&quot;', '"')
        )
        if _DANGEROUS_LINK_SCHEME_RE.match(raw_href):
            return f'{label} ({href})'
        return f'<a href="{href}" rel="noopener noreferrer">{label}</a>'

    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', convert_link, html)
    
    # Convert headers
    html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    
    # Convert paragraphs
    paragraphs = html.split('\n\n')

    def _wrap_paragraph(p: str) -> str:
        stripped = p.strip()
        if not stripped:
            return p + '\n'
        # Do NOT wrap a chunk that already begins with a block-level HTML tag
        # (headers, etc.) OR that carries a fenced-code-block placeholder. The
        # placeholder is restored to a block-level <pre>...</pre> further down,
        # and a <pre> nested inside a <p> is invalid HTML: the browser closes
        # the <p> early and the literal </p> becomes a stray end tag, which
        # html5lib reports as unexpected-end-tag (unclosed-tag error recovery).
        # Wrapping such a chunk in <p> is what produced the malformed
        # <p><pre>...</pre></p> structure.
        if stripped.startswith('<') or '\x00CODEBLOCK' in p:
            return p + '\n'
        return f'<p>{p.replace(chr(10), "<br>")}</p>\n'

    html = ''.join(_wrap_paragraph(p) for p in paragraphs)
    
    # Restore code blocks / inline code / math now that escaping/conversion is
    # done. Math is restored to KaTeX MathML (a phrasing-level ``<span
    # class="katex">`` / ``<math>``), which is valid inside the ``<p>`` a
    # standalone display expression is wrapped in — no <pre>-in-<p> class of
    # structural error (HTML-05 unaffected).
    for i, block in enumerate(code_blocks):
        html = html.replace(f'\x00CODEBLOCK{i}\x00', block)
    for i, block in enumerate(inline_code):
        html = html.replace(f'\x00INLINECODE{i}\x00', block)
    for i, block in enumerate(math_blocks):
        html = html.replace(f'\x00MATHBLOCK{i}\x00', block)
    
    return html

def _embed_visualizations_in_html(html: str) -> str:
    """
    Embed visualizations directly in HTML.
    For SVGs and other diagrams, we embed them inline or as data URIs.
    """
    # Look for visualization code blocks and convert them to embedded SVGs
    # This is a placeholder - actual implementation would render the viz
    
    viz_pattern = r'<pre><code class="language-(graphviz|mermaid|vega-lite)">(.*?)</code></pre>'
    
    def embed_viz(match):
        viz_type = match.group(1)
        viz_code = match.group(2)
        
        # For now, keep as code block but add a note
        # In production, you'd want to render these server-side
        return f'''
<div class="visualization">
    <p><em>📊 {viz_type.title()} Visualization</em></p>
    <details>
        <summary>View {viz_type} Source</summary>
        <pre><code class="language-{viz_type}">{viz_code}</code></pre>
    </details>
</div>
'''
    
    html = re.sub(viz_pattern, embed_viz, html, flags=re.DOTALL)
    
    return html

def _create_footer(
    target: str,
    version: str,
    model: str,
    provider: str,
    format_type: str
) -> str:
    """Create footer with metadata and links."""
    # The URL lookup lives in get_export_urls(): it used to run inline here
    # with `for provider in config_providers:`, SHADOWING the `provider`
    # PARAMETER with a config-provider object.  The HTML footer then rendered
    # that object's repr, which the browser parsed as an unknown tag and
    # displayed as nothing — the "Provider: is empty" defect.
    ziya_url, _repo_url = get_export_urls()

    if format_type == 'html':
        return f'''
    <div class="footer">
        <p><strong>Generated by Ziya v{version}</strong></p>
        <p>Model: <code>{model}</code> | Provider: <code>{provider}</code></p>
        <p>Learn more: <a href="{ziya_url}">{ziya_url}</a></p>
        <p><em>This conversation was exported from Ziya — an AI client and orchestration harness for software engineering, system architecture, operations, and technical visualization.</em></p>
    </div>
'''
    else:  # markdown
        return f"""
---

## 📋 Export Metadata

**Generated by:** Ziya v{version}  
**Model:** `{model}`  
**Provider:** `{provider}`  
**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Learn more about Ziya:** [{ziya_url}]({ziya_url})

*This conversation was exported from Ziya — an AI client and orchestration harness for software engineering, system architecture, operations, and technical visualization. Ziya combines context-aware code intelligence with live system introspection, multi-model orchestration, and rich diagramming to support the full lifecycle from design through deployment.*
"""

def get_export_urls():
    """Resolve the ``(ziya_url, repo_url)`` pair used by export footers.

    Defaults to the public GitHub URLs; an active enterprise config provider
    may override them, so internal deployments show their internal link.
    Shared by the HTML/markdown footers here and the PDF per-page footer
    (app/services/pdf_exporter.py -> build_pdf_footer_template).
    """
    ziya_url = "https://github.com/ziya-ai/ziya"
    repo_url = "https://github.com/ziya-ai/ziya"
    try:
        from app.plugins import get_active_config_providers
        from app.utils.logging_utils import logger

        for config_provider in get_active_config_providers():
            try:
                provider_defaults = config_provider.get_defaults()
                if 'urls' in provider_defaults:
                    urls = provider_defaults['urls']
                    ziya_url = urls.get('ziya_url', ziya_url)
                    repo_url = urls.get('repo_url', repo_url)
                    logger.debug(
                        f"Using URLs from {config_provider.provider_id} "
                        f"config provider"
                    )
                    break
            except Exception as e:
                logger.debug(f"Error getting URLs from provider: {e}")
    except ImportError:
        pass  # Plugin system not available: keep the public defaults.
    except Exception:
        pass  # Never let URL lookup break an export.
    return ziya_url, repo_url


def _process_visualizations_for_markdown(content: str) -> str:
    """
    Process content to embed visualizations in markdown.
    
    Strategy:
    1. Keep original code blocks for reproducibility
    2. Add rendering hints for paste services
    """
    
    # Uses the module-level registry rather than a second literal list.  The
    # two had already diverged from each other and from the frontend.
    for viz_type in _VIZ_TYPES:
        pattern = f'```{viz_type}\\n(.*?)```'
        
        def add_viz_note(match):
            code = match.group(1)
            return f"""```{viz_type}
{code}
```

> 📊 **Visualization:** This is a {viz_type} diagram. To view:
> - GitHub Gist: Will render automatically if supported
> - Other viewers: Copy the code above to a {viz_type} renderer

"""
        
        content = re.sub(pattern, add_viz_note, content, flags=re.DOTALL)
    
    return content

def extract_svg_from_content(content: str) -> List[str]:
    """Extract SVG elements from content."""
    svg_pattern = r'<svg[^>]*>.*?</svg>'
    return re.findall(svg_pattern, content, flags=re.DOTALL)

def svg_to_data_uri(svg_content: str) -> str:
    """Convert SVG to data URI for embedding."""
    # Encode SVG as base64
    svg_bytes = svg_content.encode('utf-8')
    svg_base64 = base64.b64encode(svg_bytes).decode('utf-8')
    return f"data:image/svg+xml;base64,{svg_base64}"

def _process_content_for_html(content: str) -> str:
    """
    Process markdown content and convert to HTML with embedded visualizations.
    """
    # Convert markdown to HTML (basic implementation)
    html = content
    
    # Extract and embed SVGs
    svgs = extract_svg_from_content(content)
    for svg in svgs:
        # Keep SVG inline for HTML export
        html = html.replace(svg, f'<div class="visualization">{svg}</div>')
    
    # Convert code blocks with syntax highlighting hints
    def convert_code_block(match):
        lang = match.group(1) or 'text'
        code = match.group(2)
        
        # Check if this is a diff
        if lang == 'diff' or code.strip().startswith('diff --git'):
            escaped_code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            return f'<div class="diff-view"><pre><code class="language-diff">{escaped_code}</code></pre></div>'
        
        # Check if this is a visualization
        if lang in ['graphviz', 'mermaid', 'vega-lite', 'd3', 'joint']:
            escaped_code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            return f'''
<div class="visualization">
    <p><em>📊 {lang.title()} Visualization</em></p>
    <details>
        <summary>View Source Code</summary>
        <pre><code class="language-{lang}">{escaped_code}</code></pre>
    </details>
</div>
'''
        
        escaped_code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'<pre><code class="language-{lang}">{escaped_code}</code></pre>'
    
    html = re.sub(r'```(\w+)?\n(.*?)```', convert_code_block, html, flags=re.DOTALL)
    
    # Convert inline code
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    # Convert bold
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    
    # Convert italic  
    html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
    
    # Convert links
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    
    # Convert headers
    html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    
    # Convert line breaks to <br> for single newlines, <p> for double
    paragraphs = html.split('\n\n')
    html = ''.join(
        f'<p>{p.replace(chr(10), "<br>")}</p>\n' 
        if p.strip() and not p.strip().startswith('<') 
        else p + '\n' 
        for p in paragraphs
    )
    
    return html
