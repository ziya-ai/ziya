"""
Document IR — the authored-document intermediate representation.

A "document" is NOT a conversation transcript: it is an authored work product
(report, memo, spec) that the model creates and revises as a plain markdown
file with YAML front-matter, stored under ``<project>/.ziya/documents/``.
The file is the model's editing surface (revised with ordinary diffs /
file_write, never regenerated wholesale) and the single input to every export
adapter.  Phase 1 target: high-fidelity PDF via the shared ``/print`` route
(see ``app/services/pdf_exporter.export_document_pdf``).

PORTABILITY CONTRACT.  The IR is a legitimate markdown file that degrades
gracefully outside Ziya:

  * YAML front-matter — GitHub renders it as a small table; other viewers
    ignore or show it harmlessly.
  * ``<!-- ziya:pagebreak -->`` — an HTML comment, invisible everywhere;
    inside Ziya's document renderer it forces a page break.
  * Math/diagram fences keep their SEMANTIC SOURCE (LaTeX, mermaid, chemfig…)
    so each export adapter decides per-construct between a native mapping and
    an image fallback.  Nothing is pre-rasterized in the IR.

Front-matter schema (all keys optional):

    ---
    ziya-doc: 1            # IR version marker (accepted, currently ignored)
    title: "Queue Depth Analysis"
    subtitle: "Capacity headroom, Q3"   # optional second line of the title block
    author: "dcohn"        # overrides the model/provider default in PDF /Author
    date: 2026-09-19       # optional; ISO date or free text; default: today
    layout: report         # report (title block) | titlepage (title block on
                           # its own page) | plain (no chrome at all)
    numbering:             # optional; true/false sets all three
      sections: false      # "1", "1.1", "1.1.1" prefixes on h1-h3
      figures: true        # "Figure N" labels under diagrams/images
      tables: true         # "Table N" labels above tables
    page:
      size: A4             # only A4 in phase 1
      margin: 18mm         # one value for all sides, or {top,bottom,left,right}
    ---

Caption lines (recognised by the PDF decoration pass, plain paragraphs in any
other viewer): a paragraph beginning ``Figure:`` directly BELOW a diagram or
image becomes its caption; a paragraph beginning ``Table:`` directly ABOVE (or
below) a table becomes that table's caption.  See
``app/utils/document_print_decor.py``.

YAML parsing is defensive: PyYAML is effectively always present (a langchain
transitive dependency) but is NOT a declared direct dependency, so a minimal
``key: value`` fallback parser keeps document export working without it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

# Front-matter block: the file must START with a `---` line; the block ends at
# the next `---` line.  DOTALL is scoped to the block body only.
_FRONT_MATTER_RE = re.compile(r'\A---[ \t]*\n(.*?)\n---[ \t]*\n?', re.DOTALL)

# Page-break directive: an HTML comment on its own line (invisible in any
# renderer that doesn't know it).  Whitespace-tolerant.
_PAGEBREAK_RE = re.compile(r'^[ \t]*<!--\s*ziya:pagebreak\s*-->[ \t]*$', re.MULTILINE)

# CSS length accepted for page margins (matches what page.pdf() accepts).
_MARGIN_VALUE_RE = re.compile(r'^\d+(\.\d+)?(mm|cm|in|px)$')

_VALID_LAYOUTS = ('plain', 'report', 'titlepage')

_NUMBERING_KEYS = ('sections', 'figures', 'tables')

_MARGIN_SIDES = ('top', 'bottom', 'left', 'right')


def _parse_yaml_or_fallback(text: str) -> Dict[str, Any]:
    """Parse a front-matter body with PyYAML, else a minimal line parser.

    The fallback understands flat ``key: value`` lines (quoted strings,
    booleans, ints) — enough for every phase-1 key except a nested ``page``
    mapping, which it skips.  Never raises; a malformed block yields ``{}``.
    """
    try:
        import yaml
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        pass
    except Exception:
        logger.warning("Document front-matter is not valid YAML; ignoring block")
        return {}
    # Minimal fallback: flat key: value only.
    out: Dict[str, Any] = {}
    for line in text.split('\n'):
        m = re.match(r'^([A-Za-z][\w-]*)\s*:\s*(.*)$', line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if not raw:
            continue  # nested mapping header — fallback skips nesting
        if raw.startswith(('"', "'")) and raw.endswith(raw[0]) and len(raw) >= 2:
            out[key] = raw[1:-1]
        elif raw.lower() in ('true', 'false'):
            out[key] = raw.lower() == 'true'
        else:
            try:
                out[key] = int(raw)
            except ValueError:
                out[key] = raw
    return out


def parse_document(text: str) -> Tuple[Dict[str, Any], str]:
    """Split an IR file into ``(meta, body)``.

    ``meta`` is the NORMALIZED front-matter (see :func:`normalize_meta`);
    ``body`` is the markdown after the front-matter block (or the whole text
    when no block is present).  Never raises on malformed front-matter.
    """
    m = _FRONT_MATTER_RE.match(text or '')
    if not m:
        return normalize_meta({}), text or ''
    raw_meta = _parse_yaml_or_fallback(m.group(1))
    return normalize_meta(raw_meta), text[m.end():]


def normalize_meta(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize raw front-matter into the fixed phase-1 schema.

    Unknown keys are dropped (they never cross the wire to the renderer);
    invalid values fall back to defaults rather than raising, so a hand-edited
    file cannot break its own export.
    """
    meta: Dict[str, Any] = {
        'title': None,
        'subtitle': None,
        'author': None,
        'date': None,
        'layout': 'plain',
        'numbering': None,
        'page': {},
    }
    if not isinstance(raw, dict):
        return meta

    title = raw.get('title')
    if isinstance(title, str) and title.strip():
        meta['title'] = title.strip()
    subtitle = raw.get('subtitle')
    if isinstance(subtitle, str) and subtitle.strip():
        meta['subtitle'] = subtitle.strip()
    author = raw.get('author')
    if isinstance(author, str) and author.strip():
        meta['author'] = author.strip()

    # PyYAML turns an unquoted `2026-09-19` into a date object; keep it as
    # the ISO string so the meta stays JSON-serialisable.  Free text passes
    # through verbatim ("Q3 FY26", "Draft").  Formatting for display happens
    # in document_print_decor.format_document_date.
    date = raw.get('date')
    if hasattr(date, 'isoformat'):
        meta['date'] = date.isoformat()[:10]
    elif isinstance(date, (str, int)) and str(date).strip():
        meta['date'] = str(date).strip()

    layout = raw.get('layout')
    if isinstance(layout, str) and layout.strip().lower() in _VALID_LAYOUTS:
        meta['layout'] = layout.strip().lower()

    # numbering: true/false (all knobs) or {sections, figures, tables}.
    # None means "renderer defaults" (figures and tables on, sections off).
    numbering = raw.get('numbering')
    if isinstance(numbering, bool):
        meta['numbering'] = {k: numbering for k in _NUMBERING_KEYS}
    elif isinstance(numbering, dict):
        norm_num = {k: numbering[k] for k in _NUMBERING_KEYS
                    if isinstance(numbering.get(k), bool)}
        if norm_num:
            meta['numbering'] = norm_num

    page = raw.get('page')
    if isinstance(page, dict):
        norm_page: Dict[str, Any] = {}
        size = page.get('size')
        if isinstance(size, str) and size.strip().upper() == 'A4':
            norm_page['size'] = 'A4'
        margin = page.get('margin')
        norm_margin = _normalize_margin(margin)
        if norm_margin:
            norm_page['margin'] = norm_margin
        meta['page'] = norm_page

    return meta


def _normalize_margin(margin: Any) -> Optional[Dict[str, str]]:
    """Normalize a margin spec into the dict ``page.pdf()`` accepts.

    Accepts a single CSS length (applied to all four sides) or a mapping of
    ``top/bottom/left/right`` lengths.  Invalid values are dropped side-by-side
    rather than rejecting the whole spec.
    """
    if isinstance(margin, str):
        v = margin.strip()
        if _MARGIN_VALUE_RE.match(v):
            return {side: v for side in _MARGIN_SIDES}
        return None
    if isinstance(margin, dict):
        out = {}
        for side in _MARGIN_SIDES:
            v = margin.get(side)
            if isinstance(v, str) and _MARGIN_VALUE_RE.match(v.strip()):
                out[side] = v.strip()
        return out or None
    return None


def split_sections(body: str) -> List[str]:
    """Split a document body at ``<!-- ziya:pagebreak -->`` directives.

    Each returned section renders on its own page (the renderer applies
    ``break-before: page`` from the second section on).  Empty sections
    produced by adjacent/leading/trailing directives are dropped; a body with
    no directive returns a single section.  Always returns at least one
    element so the renderer never receives an empty list.
    """
    parts = _PAGEBREAK_RE.split(body or '')
    sections = [p.strip('\n') for p in parts]
    sections = [s for s in sections if s.strip()]
    return sections or ['']


# ---------------------------------------------------------------------------
# Document store — <project>/.ziya/documents/
# ---------------------------------------------------------------------------

def documents_dir(create: bool = True) -> Path:
    """The project-local document store: ``<codebase>/.ziya/documents``.

    Documents live in the PROJECT'S ``.ziya`` (model-writable, versionable
    alongside the code they describe), not the global ``~/.ziya`` home.
    """
    from app.config.env_registry import ziya_env
    root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
    d = Path(root) / '.ziya' / 'documents'
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_document_path(name: str) -> Path:
    """Resolve a document name to a path INSIDE the store, or raise ValueError.

    ``name`` is a store-relative filename (subdirectories allowed for
    organisation).  Absolute paths and any traversal that escapes the store
    are rejected — this is the API-facing guard for the export endpoint.
    """
    if not name or name.startswith(('/', '\\')) or (len(name) > 1 and name[1] == ':'):
        raise ValueError(f"Document name must be store-relative: {name!r}")
    base = documents_dir(create=False).resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise ValueError(f"Document name escapes the document store: {name!r}")
    return candidate


def load_document(name: str) -> Dict[str, Any]:
    """Load and parse a stored document by store-relative name.

    Returns ``{'meta', 'body', 'path'}``.  Raises ``FileNotFoundError`` for a
    missing document and ``ValueError`` for a name outside the store.
    """
    path = resolve_document_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"Document not found: {name}")
    text = path.read_text(encoding='utf-8')
    meta, body = parse_document(text)
    return {'meta': meta, 'body': body, 'path': str(path)}
