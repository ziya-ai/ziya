"""
Per-PDF RAG index.

Large PDFs (reference manuals, specs, textbooks) are frequently too big
to load into a single model context as one extracted string.  This module
builds a lightweight per-page index on disk so large PDFs can be:

  * represented in context as a *stub* (metadata + outline/ToC + first and
    last pages + instructions for how to pull more via MCP tools), and
  * queried on demand by the agent through ``pdf_read_pages`` and
    ``pdf_search`` MCP tools without re-extracting.

The index is:
  * project-scoped  (lives under ``{project_root}/.ziya/pdf_index/``)
  * content-addressed by (abspath, mtime, size) so stale indices are
    automatically invalidated
  * built lazily on first access and persisted across restarts
  * self-contained — BM25 is implemented inline, no new heavy deps

Small PDFs bypass this entirely: callers check
``should_use_pdf_rag(path)`` first and fall back to the existing
``extract_pdf_text`` when the document fits comfortably in context.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger
from app.config.env_registry import ziya_env

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# A PDF whose extracted text exceeds this many tokens is considered "large"
# and will be represented in context as a stub + indexed for on-demand access.
# Can be overridden via the ZIYA_PDF_RAG_TOKEN_THRESHOLD environment variable.
DEFAULT_TOKEN_THRESHOLD = 25_000

# Number of pages to include verbatim at the head and tail of a stub.
STUB_HEAD_PAGES = 2
STUB_TAIL_PAGES = 2

# Cache directory name under the project root.
CACHE_DIR_NAME = ".ziya/pdf_index"

# BM25 tuning parameters (standard defaults).
_BM25_K1 = 1.5
_BM25_B = 0.75

# A per-process lock protecting index builds so concurrent callers don't
# duplicate work on the same PDF.
_BUILD_LOCKS: Dict[str, threading.Lock] = {}
_BUILD_LOCKS_MUTEX = threading.Lock()


def _get_token_threshold() -> int:
    raw = ziya_env("ZIYA_PDF_RAG_TOKEN_THRESHOLD")
    if raw:
        try:
            return max(1000, int(raw))
        except ValueError:
            logger.warning(f"Invalid ZIYA_PDF_RAG_TOKEN_THRESHOLD={raw!r}, using default")
    return DEFAULT_TOKEN_THRESHOLD


# --------------------------------------------------------------------------- #
# Tokenisation helpers
# --------------------------------------------------------------------------- #

# Word tokeniser for BM25.  Accepts alphanumerics plus underscore.
# Hyphens and periods act as separators so that `unique-needle` and
# `Figure 3.2` produce `[unique, needle]` and `[figure, 3, 2]` — users
# searching for "needle" or "3.2" find the right pages.
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]*")


def _count_tokens(text: str) -> int:
    """Fast tiktoken-based token count.  Falls back to a char-based estimate."""
    if not text:
        return 0
    try:
        from app.utils.tiktoken_compat import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, KeyError, UnicodeEncodeError):
        # Conservative fallback: 4 chars / token
        return max(1, len(text) // 4)


def _tokenise_for_bm25(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


# --------------------------------------------------------------------------- #
# Project-root discovery
# --------------------------------------------------------------------------- #

def _get_project_root() -> str:
    """
    Resolve the active project root without introducing an MCP runtime
    dependency at import time.
    """
    try:
        from app.context import get_project_root as _ctx_root
        root = _ctx_root()
        if root and os.path.isdir(root):
            return root
    except (ImportError, RuntimeError):
        pass  # Context module unavailable — use env fallback
    return ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()


def _project_relative_path(abspath: str) -> str:
    """
    Return *abspath* expressed relative to the current project root if it lives
    under the project, otherwise return the absolute path unchanged.  Used so
    stubs emit the form of path the model is most likely to pass back through
    the MCP tools.
    """
    try:
        root = str(Path(_get_project_root()).resolve())
        ap = str(Path(abspath).resolve())
        if ap == root or ap.startswith(root + os.sep):
            return os.path.relpath(ap, root)
    except (OSError, ValueError):
        pass  # Path resolution failed — return absolute path
    return abspath


def _cache_key_for(path: str) -> Tuple[str, Path]:
    """
    Compute the content-addressed cache directory for a PDF.

    The key incorporates the absolute path, mtime and size so any edit
    invalidates the index automatically.  The path is fully resolved
    (symlinks followed) so callers that pass /var/... and /private/var/...
    on macOS — or ./foo.pdf and /abs/foo.pdf — land on the same cache.
    """
    try:
        abspath = str(Path(path).resolve())
    except (OSError, ValueError):
        abspath = os.path.abspath(path)
    try:
        st = os.stat(abspath)
        signature = f"{abspath}|{int(st.st_mtime)}|{st.st_size}"
    except OSError:
        signature = abspath
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]
    cache_root = Path(_get_project_root()) / CACHE_DIR_NAME
    return digest, cache_root / digest


# --------------------------------------------------------------------------- #
# Outline / bookmark extraction
# --------------------------------------------------------------------------- #

def _extract_native_outline(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any], int]:
    """
    Extract the bookmark tree, document metadata and page count using pypdf.

    Returns (outline, metadata, page_count).  Outline is a nested list of
    ``{title, page, level, children}`` dicts.  Empty list if the PDF has no
    bookmarks.
    """
    outline: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    page_count = 0
    try:
        import pypdf
    except ImportError:
        return outline, metadata, page_count

    try:
        reader = pypdf.PdfReader(path)
        page_count = len(reader.pages)

        # Document info (title, author, subject, ...).
        try:
            info = reader.metadata or {}
            for k, v in info.items():
                if v is None:
                    continue
                key = str(k).lstrip("/")
                try:
                    metadata[key] = str(v)
                except (TypeError, ValueError):
                    pass  # Non-stringifiable metadata value
        except (AttributeError, TypeError):
            pass  # Metadata not accessible

        def _walk(items, level: int) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for it in items:
                if isinstance(it, list):
                    # Nested children — attach to previous entry if any.
                    if out:
                        out[-1]["children"] = _walk(it, level + 1)
                    continue
                try:
                    title = getattr(it, "title", None) or str(it)
                    page_idx = reader.get_destination_page_number(it)
                    page_num = (page_idx or 0) + 1
                except Exception:
                    continue
                out.append({
                    "title": title.strip() if isinstance(title, str) else str(title),
                    "page": page_num,
                    "level": level,
                    "children": [],
                })
            return out

        try:
            raw_outline = reader.outline
            if raw_outline:
                outline = _walk(raw_outline, 0)
        except Exception as e:
            logger.debug(f"Native outline extraction failed for {path}: {e}")
    except Exception as e:
        logger.warning(f"pypdf could not open {path} for outline: {e}")

    return outline, metadata, page_count


# Heuristic patterns for detecting List-of-Figures / List-of-Tables lines
# when a PDF has no embedded bookmarks.  These are best-effort.
_FIGURE_LINE_RE = re.compile(r"^\s*(?:Figure|Fig\.?)\s+([\d\.\-]+)\s*[:\.\-]\s*(.{3,160})$", re.IGNORECASE | re.MULTILINE)
_TABLE_LINE_RE = re.compile(r"^\s*Table\s+([\d\.\-]+)\s*[:\.\-]\s*(.{3,160})$", re.IGNORECASE | re.MULTILINE)


def _extract_heuristic_figures_tables(pages_text: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scan every page for ``Figure N: ...`` / ``Table N: ...`` captions."""
    figures: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    for i, text in enumerate(pages_text):
        if not text:
            continue
        for m in _FIGURE_LINE_RE.finditer(text):
            figures.append({"id": m.group(1).strip(), "caption": m.group(2).strip(), "page": i + 1})
        for m in _TABLE_LINE_RE.finditer(text):
            tables.append({"id": m.group(1).strip(), "caption": m.group(2).strip(), "page": i + 1})
    return figures, tables


# --------------------------------------------------------------------------- #
# Page extraction
# --------------------------------------------------------------------------- #

def _extract_page_ranges(path: str, ranges: List[Tuple[int, int]]) -> List[Dict[str, Any]]:
    """Extract text for specific 1-based inclusive page ranges only.

    Uses pdfplumber's lazy page indexing so we only pay for the pages we
    actually read — critical for very large PDFs where full extraction
    would take minutes.  Falls back to pypdf if pdfplumber is unavailable.
    """
    wanted: set[int] = set()
    for start, end in ranges:
        if start < 1:
            start = 1
        if end < start:
            end = start
        wanted.update(range(start, end + 1))
    if not wanted:
        return []

    pages: List[Dict[str, Any]] = []
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None  # type: ignore

    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                total = len(pdf.pages)
                for page_no in sorted(p for p in wanted if 1 <= p <= total):
                    try:
                        pp = pdf.pages[page_no - 1]
                        text = pp.extract_text() or ""
                        has_images = bool(getattr(pp, "images", []))
                    except Exception:
                        text = ""
                        has_images = False
                    pages.append({
                        "page": page_no, "text": text,
                        "token_count": _count_tokens(text),
                        "has_images": has_images,
                    })
            return pages
        except Exception as e:
            logger.warning(f"pdfplumber range extraction failed for {path}: {e}")

    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        total = len(reader.pages)
        for page_no in sorted(p for p in wanted if 1 <= p <= total):
            try:
                text = reader.pages[page_no - 1].extract_text() or ""
            except Exception:
                text = ""
            pages.append({
                "page": page_no, "text": text,
                "token_count": _count_tokens(text), "has_images": False,
            })
        return pages
    except Exception as e:
        logger.error(f"pypdf range extraction failed for {path}: {e}")
        return []


def _extract_pages_text(path: str, page_count_hint: int = 0) -> List[Dict[str, Any]]:
    """
    Extract text per page.  Returns a list of
    ``{page, text, token_count, has_images}`` dicts (1-based page numbers).
    """
    pages: List[Dict[str, Any]] = []

    # pdfplumber gives richer per-page text and image metadata.
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None  # type: ignore

    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages):
                    try:
                        text = page.extract_text() or ""
                    except Exception:
                        text = ""
                    try:
                        has_images = bool(getattr(page, "images", []))
                    except Exception:
                        has_images = False
                    pages.append({
                        "page": i + 1,
                        "text": text,
                        "token_count": _count_tokens(text),
                        "has_images": has_images,
                    })
            return pages
        except Exception as e:
            logger.warning(f"pdfplumber page extraction failed for {path}: {e}")

    # Fallback to pypdf (no image info).
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append({
                "page": i + 1,
                "text": text,
                "token_count": _count_tokens(text),
                "has_images": False,
            })
        return pages
    except Exception as e:
        logger.error(f"pypdf page extraction failed for {path}: {e}")
        return []


def _extract_image_captions(path: str, pages_text: List[str]) -> List[Dict[str, Any]]:
    """
    For each image embedded in the PDF, pair it with the nearest caption text
    (looking for ``Figure N:`` / ``Fig N.`` anywhere on the same page).

    Returns a list of ``{page, index, bbox, caption}`` dicts.  Used by the
    ``pdf_search`` tool so the agent can look images up by title.
    """
    out: List[Dict[str, Any]] = []
    try:
        import pdfplumber
    except ImportError:
        return out
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                try:
                    images = list(getattr(page, "images", []) or [])
                except Exception:
                    images = []
                if not images:
                    continue
                page_text = pages_text[i] if i < len(pages_text) else ""
                captions: List[str] = []
                for m in _FIGURE_LINE_RE.finditer(page_text):
                    captions.append(f"Figure {m.group(1).strip()}: {m.group(2).strip()}")
                for m in _TABLE_LINE_RE.finditer(page_text):
                    captions.append(f"Table {m.group(1).strip()}: {m.group(2).strip()}")
                for idx, img in enumerate(images):
                    caption = captions[idx] if idx < len(captions) else (captions[0] if captions else "")
                    try:
                        bbox = [float(img.get("x0", 0)), float(img.get("top", 0)),
                                float(img.get("x1", 0)), float(img.get("bottom", 0))]
                    except Exception:
                        bbox = []
                    out.append({
                        "page": i + 1,
                        "index": idx,
                        "bbox": bbox,
                        "caption": caption,
                    })
    except Exception as e:
        logger.debug(f"Image caption extraction failed for {path}: {e}")
    return out


# --------------------------------------------------------------------------- #
# Inline BM25
# --------------------------------------------------------------------------- #

def _build_bm25(documents: List[List[str]]) -> Dict[str, Any]:
    """
    Build a compact BM25 index over already-tokenised documents.

    Stored as a plain JSON-serialisable dict so it survives the round-trip
    to disk without pickle.
    """
    n_docs = len(documents)
    doc_lengths = [len(doc) for doc in documents]
    avgdl = sum(doc_lengths) / n_docs if n_docs else 0.0
    df: Dict[str, int] = {}
    tf: List[Dict[str, int]] = []
    for doc in documents:
        seen = set()
        freq: Dict[str, int] = {}
        for term in doc:
            freq[term] = freq.get(term, 0) + 1
            seen.add(term)
        for term in seen:
            df[term] = df.get(term, 0) + 1
        tf.append(freq)
    idf = {term: math.log(1 + (n_docs - f + 0.5) / (f + 0.5)) for term, f in df.items()}
    return {
        "n_docs": n_docs,
        "avgdl": avgdl,
        "doc_lengths": doc_lengths,
        "idf": idf,
        "tf": tf,
    }


def _bm25_score(index: Dict[str, Any], query_tokens: List[str]) -> List[float]:
    tf = index["tf"]
    idf = index["idf"]
    doc_lengths = index["doc_lengths"]
    avgdl = index["avgdl"] or 1.0
    scores = [0.0] * index["n_docs"]
    for term in query_tokens:
        term_idf = idf.get(term)
        if term_idf is None:
            continue
        for i, freq_map in enumerate(tf):
            f = freq_map.get(term, 0)
            if f == 0:
                continue
            dl = doc_lengths[i] or 1
            denom = f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl)
            scores[i] += term_idf * (f * (_BM25_K1 + 1)) / denom
    return scores


# --------------------------------------------------------------------------- #
# PdfIndex
# --------------------------------------------------------------------------- #

@dataclass
class PdfIndex:
    """Handle to an on-disk PDF index."""
    path: str
    cache_dir: Path
    meta: Dict[str, Any]

    # --- builders / loaders ------------------------------------------------ #

    @classmethod
    def load(cls, path: str) -> Optional["PdfIndex"]:
        _, cache_dir = _cache_key_for(path)
        meta_path = cache_dir / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return cls(path=path, cache_dir=cache_dir, meta=meta)

    @classmethod
    def build(cls, path: str) -> Optional["PdfIndex"]:
        if not os.path.isfile(path):
            logger.error(f"PDF not found for indexing: {path}")
            return None

        key, cache_dir = _cache_key_for(path)

        # Serialise concurrent builds for the same key.
        with _BUILD_LOCKS_MUTEX:
            lock = _BUILD_LOCKS.setdefault(key, threading.Lock())
        with lock:
            # Another thread may have finished while we were waiting.
            existing = cls.load(path)
            if existing is not None:
                return existing

            cache_dir.mkdir(parents=True, exist_ok=True)

            outline, doc_meta, page_count_hint = _extract_native_outline(path)
            pages = _extract_pages_text(path, page_count_hint=page_count_hint)
            if not pages:
                logger.error(f"Could not extract any pages from {path}")
                return None

            pages_text = [p["text"] for p in pages]
            figures, tables = _extract_heuristic_figures_tables(pages_text)
            image_captions = _extract_image_captions(path, pages_text)

            total_tokens = sum(p["token_count"] for p in pages)

            # Persist pages.jsonl
            with (cache_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
                for p in pages:
                    fh.write(json.dumps(p, ensure_ascii=False) + "\n")

            # Persist images.jsonl
            with (cache_dir / "images.jsonl").open("w", encoding="utf-8") as fh:
                for img in image_captions:
                    fh.write(json.dumps(img, ensure_ascii=False) + "\n")

            meta = {
                "path": os.path.abspath(path),
                "page_count": len(pages),
                "total_tokens": total_tokens,
                "outline": outline,
                "metadata": doc_meta,
                "figures": figures,
                "tables": tables,
                "has_native_outline": bool(outline),
                "image_count": len(image_captions),
                "version": 1,
            }
            (cache_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

            logger.info(
                f"Built PDF index for {path}: {meta['page_count']} pages, "
                f"{total_tokens} tokens, outline={'yes' if outline else 'no'}, "
                f"images={meta['image_count']}"
            )
            return cls(path=path, cache_dir=cache_dir, meta=meta)

    @classmethod
    def build_light(cls, path: str) -> Optional["PdfIndex"]:
        """Build a minimal index: outline + head/tail pages only.

        Cheap even for 5000-page PDFs — skips whole-document text extraction,
        figure/table scanning, and image-caption extraction.  Sufficient for
        the in-context stub and for pdf_outline.  pdf_read_pages extracts
        additional pages on demand.  pdf_search promotes this to a full
        index via ensure_full().
        """
        if not os.path.isfile(path):
            logger.error(f"PDF not found for light indexing: {path}")
            return None
        key, cache_dir = _cache_key_for(path)
        with _BUILD_LOCKS_MUTEX:
            lock = _BUILD_LOCKS.setdefault(key, threading.Lock())
        with lock:
            existing = cls.load(path)
            if existing is not None:
                return existing
            cache_dir.mkdir(parents=True, exist_ok=True)

            outline, doc_meta, page_count_hint = _extract_native_outline(path)
            if page_count_hint <= 0:
                # Fall back to full extraction if we can't even get a page count.
                return cls.build(path)

            head = (1, min(STUB_HEAD_PAGES, page_count_hint))
            tail_start = max(page_count_hint - STUB_TAIL_PAGES + 1, STUB_HEAD_PAGES + 1)
            ranges = [head]
            if tail_start <= page_count_hint:
                ranges.append((tail_start, page_count_hint))
            pages = _extract_page_ranges(path, ranges)

            with (cache_dir / "pages.jsonl").open("w", encoding="utf-8") as fh:
                for p in pages:
                    fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            # Empty images file — populated on ensure_full().
            (cache_dir / "images.jsonl").write_text("", encoding="utf-8")

            meta = {
                "path": os.path.abspath(path),
                "page_count": page_count_hint,
                "total_tokens": 0,  # unknown until full extraction
                "outline": outline,
                "metadata": doc_meta,
                "figures": [], "tables": [],
                "has_native_outline": bool(outline),
                "image_count": 0,
                "light": True,
                "version": 1,
            }
            (cache_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(
                f"Built LIGHT PDF index for {path}: {page_count_hint} pages "
                f"({len(pages)} extracted), outline={'yes' if outline else 'no'} "
                f"— full index deferred until pdf_search is called")
            return cls(path=path, cache_dir=cache_dir, meta=meta)

    @classmethod
    def get_or_build(cls, path: str, full: bool = True) -> Optional["PdfIndex"]:
        """Load an existing index, or build one.

        full=True (default): build a complete index (all pages + figures +
        image captions + BM25-ready).  Use this when search is imminent.

        full=False: build a light index (outline + head/tail pages only).
        Dramatically faster for very large PDFs.  Suitable for stubs and
        pdf_outline.  Promoted to full on pdf_search.
        """
        existing = cls.load(path)
        if existing is not None:
            return existing
        return cls.build(path) if full else cls.build_light(path)

    # --- accessors --------------------------------------------------------- #

    @property
    def page_count(self) -> int:
        return int(self.meta.get("page_count", 0))

    @property
    def total_tokens(self) -> int:
        return int(self.meta.get("total_tokens", 0))

    @property
    def is_light(self) -> bool:
        return bool(self.meta.get("light", False))

    def ensure_full(self) -> "PdfIndex":
        """Promote a light index to a full one if it isn't already.

        Runs the expensive full-document extraction, figure/table detection,
        and image-caption scanning, then rewrites pages.jsonl, images.jsonl,
        and meta.json.  Called by pdf_search which genuinely needs every
        page tokenised.
        """
        if not self.is_light:
            return self
        key, _ = _cache_key_for(self.path)
        with _BUILD_LOCKS_MUTEX:
            lock = _BUILD_LOCKS.setdefault(key, threading.Lock())
        with lock:
            # Re-check after acquiring lock — another thread may have promoted.
            reloaded = PdfIndex.load(self.path)
            if reloaded is not None and not reloaded.is_light:
                self.meta = reloaded.meta
                return self
            logger.info(f"Promoting light PDF index to full for {self.path}")
            # Clear old (light) pages file so full build overwrites cleanly.
            try:
                (self.cache_dir / "pages.jsonl").unlink(missing_ok=True)
                (self.cache_dir / "images.jsonl").unlink(missing_ok=True)
                (self.cache_dir / "meta.json").unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"Could not clear light index files: {e}")
            rebuilt = PdfIndex.build(self.path)
            if rebuilt is not None:
                self.meta = rebuilt.meta
            return self

    def read_pages(self, start_page: int, end_page: int) -> List[Dict[str, Any]]:
        """Return page records for [start_page, end_page] inclusive (1-based)."""
        if start_page < 1:
            start_page = 1
        if end_page < start_page:
            end_page = start_page
        out: List[Dict[str, Any]] = []
        pages_file = self.cache_dir / "pages.jsonl"
        if not pages_file.is_file():
            return out
        with pages_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                p = rec.get("page", 0)
                if start_page <= p <= end_page:
                    out.append(rec)
                elif p > end_page:
                    break
        # Light index only stores head+tail — extract missing pages on demand.
        have_pages = {rec.get("page") for rec in out}
        missing = [p for p in range(start_page, end_page + 1) if p not in have_pages]
        if missing and self.is_light:
            logger.debug(f"Extracting {len(missing)} page(s) on demand from light index: {self.path}")
            # Coalesce into contiguous ranges for efficient extraction.
            extra = _extract_page_ranges(self.path, [(min(missing), max(missing))])
            extra = [p for p in extra if p.get("page") in set(missing)]
            out = sorted(out + extra, key=lambda r: r.get("page", 0))
        return out

    def _load_pages_tokenised(self) -> List[List[str]]:
        """Load and tokenise every page for BM25.  Cached on disk."""
        tokenised: List[List[str]] = []
        pages_file = self.cache_dir / "pages.jsonl"
        if not pages_file.is_file():
            return tokenised
        with pages_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                tokenised.append(_tokenise_for_bm25(rec.get("text", "")))
        return tokenised

    def _load_or_build_bm25(self) -> Dict[str, Any]:
        bm25_path = self.cache_dir / "bm25.json"
        if bm25_path.is_file():
            try:
                return json.loads(bm25_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        documents = self._load_pages_tokenised()
        index = _build_bm25(documents)
        try:
            bm25_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to persist BM25 index for {self.path}: {e}")
        return index

    def search(self, query: str, top_k: int = 5, mode: str = "bm25") -> List[Dict[str, Any]]:
        """BM25 search over page text.  Returns the top_k hits with snippets."""
        query_tokens = _tokenise_for_bm25(query)
        if not query_tokens:
            return []
        # BM25 needs every page tokenised — promote from light if necessary.
        self.ensure_full()
        index = self._load_or_build_bm25()
        if index.get("n_docs", 0) == 0:
            return []
        if mode == "embedding":
            emb_scores = self._embedding_scores(query)
            scores = emb_scores if emb_scores is not None else _bm25_score(index, query_tokens)
        else:
            scores = _bm25_score(index, query_tokens)
        ranked = sorted(
            [(i, s) for i, s in enumerate(scores) if s > 0],
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]
        if not ranked:
            return []
        results: List[Dict[str, Any]] = []
        # Also scan image captions for title matches.
        caption_hits = self._search_image_captions(query)
        page_to_record: Dict[int, Dict[str, Any]] = {}
        pages_file = self.cache_dir / "pages.jsonl"
        with pages_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    page_to_record[int(rec.get("page", 0))] = rec
                except Exception:
                    continue
        for doc_idx, score in ranked:
            page_no = doc_idx + 1
            rec = page_to_record.get(page_no, {})
            text = rec.get("text", "")
            snippet = _build_snippet(text, query_tokens)
            results.append({
                "page": page_no,
                "score": round(float(score), 4),
                "snippet": snippet,
                "has_images": bool(rec.get("has_images", False)),
            })
        # Merge caption hits (dedup by page).
        seen_pages = {r["page"] for r in results}
        for hit in caption_hits:
            if hit["page"] in seen_pages:
                continue
            results.append(hit)
        return results[:top_k + len(caption_hits)]

    def _embedding_scores(self, query: str) -> Optional[List[float]]:
        """
        Optional embedding-based relevance scoring.  Uses
        ``sentence-transformers`` when available.  Embeddings for the
        document pages are cached on disk (``embeddings.npy``) after the
        first build.  Returns None if the optional dependency is missing
        so the caller can fall back to BM25.
        """
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.debug(
                "sentence-transformers not installed — pdf_search embedding mode "
                "unavailable, falling back to BM25.  "
                "Install with: pip install sentence-transformers"
            )
            return None

        model_name = ziya_env("ZIYA_PDF_EMBEDDING_MODEL")
        emb_path = self.cache_dir / "embeddings.npy"
        try:
            if emb_path.is_file():
                doc_emb = np.load(emb_path)
            else:
                pages_file = self.cache_dir / "pages.jsonl"
                texts: List[str] = []
                with pages_file.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            texts.append(json.loads(line).get("text", "") or " ")
                        except Exception:
                            texts.append(" ")
                if not texts:
                    return None
                model = SentenceTransformer(model_name)
                logger.info(
                    f"Building embedding index for {self.path} "
                    f"({len(texts)} pages, model={model_name})"
                )
                doc_emb = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
                try:
                    np.save(emb_path, doc_emb)
                except Exception as e:
                    logger.warning(f"Failed to persist embeddings for {self.path}: {e}")
            model = SentenceTransformer(model_name)
            q_emb = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]
            # Cosine similarity since both sides are normalized.
            sims = doc_emb @ q_emb
            return [float(s) for s in sims.tolist()]
        except Exception as e:
            logger.warning(
                f"Embedding search failed for {self.path}: {e} — falling back to BM25"
            )
            return None

    def _search_image_captions(self, query: str) -> List[Dict[str, Any]]:
        images_file = self.cache_dir / "images.jsonl"
        if not images_file.is_file():
            return []
        q_lower = query.lower()
        out: List[Dict[str, Any]] = []
        with images_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                caption = (rec.get("caption") or "").lower()
                if caption and q_lower in caption:
                    out.append({
                        "page": rec.get("page"),
                        "score": 1.0,
                        "snippet": f"[image match] {rec.get('caption')}",
                        "has_images": True,
                        "image_index": rec.get("index"),
                    })
        return out

    # --- stub builder ------------------------------------------------------ #

    def build_stub(self) -> str:
        """
        Build the in-context stub for a large PDF.  Contains metadata, the
        outline tree (or heuristic figures/tables when absent), first and
        last pages verbatim, and clear instructions telling the model how
        to fetch additional content via MCP tools.
        """
        lines: List[str] = []
        m = self.meta
        lines.append(f"[Large PDF — on-demand access via MCP tools]")
        lines.append(f"File: {os.path.basename(m.get('path', self.path))}")
        lines.append(f"Pages: {self.page_count}  |  Total tokens: {self.total_tokens:,}")
        doc_meta = m.get("metadata") or {}
        for key in ("Title", "Author", "Subject", "Keywords"):
            if doc_meta.get(key):
                lines.append(f"{key}: {doc_meta[key]}")
        lines.append("")

        outline = m.get("outline") or []
        if outline:
            lines.append("## Outline (native bookmarks)")
            lines.extend(_format_outline(outline))
        else:
            figs = m.get("figures") or []
            tbls = m.get("tables") or []
            if figs or tbls:
                lines.append("## Structure (heuristic — PDF has no bookmarks)")
                if figs:
                    lines.append("### Figures")
                    for f in figs[:200]:
                        lines.append(f"- Figure {f['id']} (p.{f['page']}): {f['caption']}")
                if tbls:
                    lines.append("### Tables")
                    for t in tbls[:200]:
                        lines.append(f"- Table {t['id']} (p.{t['page']}): {t['caption']}")
            else:
                lines.append("_(No bookmarks or captions detected — use `pdf_search` to locate content.)_")
        lines.append("")

        # First and last pages verbatim
        head = self.read_pages(1, min(STUB_HEAD_PAGES, self.page_count))
        tail_start = max(self.page_count - STUB_TAIL_PAGES + 1, STUB_HEAD_PAGES + 1)
        tail = self.read_pages(tail_start, self.page_count) if tail_start <= self.page_count else []
        if head:
            lines.append(f"## Pages 1–{head[-1]['page']} (excerpt)")
            for p in head:
                lines.append(f"--- page {p['page']} ---")
                lines.append(p.get("text", "").strip())
            lines.append("")
        if tail:
            lines.append(f"## Pages {tail[0]['page']}–{tail[-1]['page']} (excerpt)")
            for p in tail:
                lines.append(f"--- page {p['page']} ---")
                lines.append(p.get("text", "").strip())
            lines.append("")

        lines.append("## How to access more of this document")
        lines.append(
            "This PDF is too large to include in full.  Use these MCP tools "
            "to pull specific content on demand:"
        )
        lines.append(
            "  * `pdf_read_pages(path, start_page, end_page)` — read a page range verbatim. "
            "Set `include_images=true` to also get rendered page images."
        )
        lines.append(
            "  * `pdf_search(path, query, top_k=5)` — BM25 search over pages; "
            "also matches figure/table captions so you can look up images by title."
        )
        lines.append(
            "  * `pdf_outline(path)` — re-fetch the full bookmark tree / figures / tables."
        )
        display_path = _project_relative_path(m.get("path", self.path))
        lines.append(f"The `path` argument is: {display_path!r}")
        return "\n".join(lines)


def _format_outline(outline: List[Dict[str, Any]], indent: int = 0) -> List[str]:
    out: List[str] = []
    for entry in outline:
        pad = "  " * indent
        out.append(f"{pad}- {entry.get('title','?')} (p.{entry.get('page','?')})")
        children = entry.get("children") or []
        if children:
            out.extend(_format_outline(children, indent + 1))
    return out


def _build_snippet(text: str, query_tokens: List[str], window: int = 240) -> str:
    if not text:
        return ""
    lower = text.lower()
    for qt in query_tokens:
        pos = lower.find(qt)
        if pos >= 0:
            start = max(0, pos - window // 2)
            end = min(len(text), pos + window // 2)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
            return prefix + text[start:end].replace("\n", " ") + suffix
    return text[:window].replace("\n", " ") + ("…" if len(text) > window else "")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def should_use_pdf_rag(path: str, fast_token_estimate: Optional[int] = None) -> bool:
    """
    Decide whether a PDF is large enough to warrant the RAG path.

    Order of decision:
      1. If an index already exists on disk, use it.
      2. If *fast_token_estimate* is provided and exceeds the threshold, use it.
      3. Otherwise do a cheap page-count check: anything over 60 pages is
         considered large (avoids extracting text twice just to decide).
    """
    if not path or not os.path.isfile(path) or not path.lower().endswith(".pdf"):
        return False
    _, cache_dir = _cache_key_for(path)
    if (cache_dir / "meta.json").is_file():
        return True
    threshold = _get_token_threshold()
    if fast_token_estimate is not None and fast_token_estimate >= threshold:
        return True
    # Cheap page-count heuristic.
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        page_count = len(reader.pages)
        if page_count >= 60:
            return True
    except Exception:
        return False
    return False


def get_pdf_stub(path: str) -> Optional[str]:
    """
    Convenience: build (or reuse) the index for *path* and return its stub.
    Returns None on failure so callers can fall back to the atomic extractor.
    Uses the light-build path — for very large PDFs this avoids a full
    multi-minute extraction.  pdf_search triggers full build on demand.
    """
    idx = PdfIndex.get_or_build(path, full=False)
    if idx is None:
        return None
    return idx.build_stub()


__all__ = [
    "PdfIndex",
    "should_use_pdf_rag",
    "get_pdf_stub",
    "DEFAULT_TOKEN_THRESHOLD",
]
