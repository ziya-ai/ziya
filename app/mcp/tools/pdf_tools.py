"""
MCP tools for on-demand access to large PDF reference documents.

These tools complement the automatic PDF RAG stubbing done by
``app.utils.pdf_rag``.  When a large PDF is included as context, the
model sees a stub (metadata + outline + first/last pages).  These
tools let it pull specific pages, search page text, or fetch rendered
page images on demand without re-extracting the whole document.

All three tools are read-only and path-validated against the project
root, matching the conventions of ``FileReadTool``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger
from app.mcp.tools.fileio import _resolve_and_validate, _get_project_root, _get_safe_write_paths


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _load_index(path_arg: str, kwargs: Dict[str, Any]):
    """
    Resolve *path_arg* and return a PdfIndex, or an error dict.

    Accepts both project-relative paths and absolute paths.  Relative
    paths are resolved against the project root with the standard
    traversal-safe validator.  Absolute paths are accepted when they
    resolve to an existing PDF — this allows the model to reference
    large reference PDFs that may live outside the project tree
    (e.g. /Volumes/Ref/spec.pdf) and were previously tagged via the
    upload endpoint.
    """
    from app.utils.pdf_rag import PdfIndex
    project_root = _get_project_root(kwargs)
    path_str = (path_arg or "").strip().strip("'\"")
    if not path_str:
        return None, {"error": True, "message": "path must not be empty"}

    if os.path.isabs(path_str):
        resolved = Path(path_str).resolve()
        # Absolute path is accepted as long as the file actually exists.
        if not resolved.exists():
            return None, {"error": True, "message": f"File not found: {path_arg}"}
    else:
        try:
            resolved = _resolve_and_validate(
                path_str, project_root,
                allowed_absolute_prefixes=_get_safe_write_paths(),
            )
        except ValueError as e:
            return None, {"error": True, "message": str(e)}

    if not resolved.is_file():
        return None, {"error": True, "message": f"Not a file: {path_arg}"}
    if resolved.suffix.lower() != ".pdf":
        return None, {"error": True, "message": f"Not a PDF: {path_arg}"}
    idx = PdfIndex.get_or_build(str(resolved))
    if idx is None:
        return None, {"error": True, "message": f"Could not index PDF: {path_arg}"}
    return idx, None


# --------------------------------------------------------------------------- #
# Tool: pdf_outline
# --------------------------------------------------------------------------- #

class PdfOutlineInput(BaseModel):
    path: str = Field(
        ...,
        description="Path to the PDF (project-relative or absolute).",
    )


class PdfOutlineTool(BaseMCPTool):
    """Return the bookmark tree / outline for a PDF."""
    name: str = "pdf_outline"
    description: str = (
        "Return the full outline (bookmarks/table of contents), "
        "document metadata, figure list, table list, and page count "
        "for a PDF file in the project.  Use this to orient yourself "
        "before calling pdf_read_pages or pdf_search."
    )
    InputSchema = PdfOutlineInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        path_arg = kwargs.get("path", "")
        idx, err = _load_index(path_arg, kwargs)
        if err:
            return err
        m = idx.meta
        return {
            "path": path_arg,
            "page_count": idx.page_count,
            "total_tokens": idx.total_tokens,
            "metadata": m.get("metadata") or {},
            "outline": m.get("outline") or [],
            "figures": m.get("figures") or [],
            "tables": m.get("tables") or [],
            "has_native_outline": bool(m.get("has_native_outline")),
            "image_count": int(m.get("image_count", 0)),
        }


# --------------------------------------------------------------------------- #
# Tool: pdf_read_pages
# --------------------------------------------------------------------------- #

class PdfReadPagesInput(BaseModel):
    path: str = Field(
        ...,
        description="Path to the PDF (project-relative or absolute).",
    )
    start_page: int = Field(..., description="First page to read (1-based, inclusive).")
    end_page: Optional[int] = Field(
        None,
        description="Last page to read (1-based, inclusive).  Defaults to start_page.",
    )
    include_images: bool = Field(
        False,
        description=(
            "If true, also render the requested pages as JPEG images (base64-encoded). "
            "Useful for scanned PDFs or pages whose meaning depends on diagrams."
        ),
    )
    max_pages: int = Field(
        20,
        description="Safety cap on the number of pages returned at once (default 20).",
    )


class PdfReadPagesTool(BaseMCPTool):
    """Read a specific page range from a PDF."""
    name: str = "pdf_read_pages"
    description: str = (
        "Read a specific page range from a PDF file verbatim.  "
        "Returns per-page text and, optionally, rendered page images "
        "(base64 JPEG) for scanned or image-heavy pages."
    )
    InputSchema = PdfReadPagesInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        path_arg = kwargs.get("path", "")
        start_page = int(kwargs.get("start_page") or 1)
        end_page_raw = kwargs.get("end_page")
        end_page = int(end_page_raw) if end_page_raw is not None else start_page
        include_images = bool(kwargs.get("include_images", False))
        max_pages = max(1, min(int(kwargs.get("max_pages", 20) or 20), 100))

        idx, err = _load_index(path_arg, kwargs)
        if err:
            return err

        # Clamp and enforce the page cap.
        if end_page < start_page:
            end_page = start_page
        if end_page - start_page + 1 > max_pages:
            end_page = start_page + max_pages - 1

        pages = idx.read_pages(start_page, end_page)
        if not pages:
            return {
                "path": path_arg,
                "page_count": idx.page_count,
                "pages": [],
                "warning": f"No pages in range [{start_page}, {end_page}] "
                           f"(document has {idx.page_count} pages).",
            }

        images: List[Dict[str, Any]] = []
        if include_images:
            try:
                from app.utils.document_extractor import extract_pdf_page_images
                all_images = extract_pdf_page_images(
                    idx.meta.get("path", path_arg),
                    max_pages=end_page,
                )
                if all_images:
                    images = [img for img in all_images if start_page <= img["page"] <= end_page]
            except Exception as e:
                logger.warning(f"Page image rendering failed for {path_arg}: {e}")

        return {
            "path": path_arg,
            "page_count": idx.page_count,
            "start_page": start_page,
            "end_page": end_page,
            "pages": pages,
            "images": images,
        }


# --------------------------------------------------------------------------- #
# Tool: pdf_search
# --------------------------------------------------------------------------- #

class PdfSearchInput(BaseModel):
    path: str = Field(
        ...,
        description="Path to the PDF (project-relative or absolute).",
    )
    query: str = Field(..., description="Search query — words or phrase.")
    top_k: int = Field(5, description="Maximum number of page hits to return (default 5).")
    mode: str = Field(
        "bm25",
        description=(
            "Scoring mode: 'bm25' (default — keyword match, no extra deps) or "
            "'embedding' (semantic similarity via sentence-transformers if installed; "
            "silently falls back to BM25 if unavailable)."
        ),
    )


class PdfSearchTool(BaseMCPTool):
    """BM25 search over a PDF's pages (also matches figure/table captions)."""
    name: str = "pdf_search"
    description: str = (
        "BM25 keyword search over all pages of a PDF.  Returns the "
        "highest-scoring pages with snippets.  Also searches figure and "
        "table captions, so you can look up images or tables by title "
        "(e.g. query 'Figure 3.2' or 'sequence diagram')."
    )
    InputSchema = PdfSearchInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        path_arg = kwargs.get("path", "")
        query = (kwargs.get("query") or "").strip()
        top_k = max(1, min(int(kwargs.get("top_k", 5) or 5), 25))
        mode = str(kwargs.get("mode", "bm25") or "bm25").lower()
        if mode not in ("bm25", "embedding"):
            mode = "bm25"
        if not query:
            return {"error": True, "message": "query must not be empty"}
        idx, err = _load_index(path_arg, kwargs)
        if err:
            return err
        hits = idx.search(query, top_k=top_k, mode=mode)
        return {
            "path": path_arg,
            "query": query,
            "page_count": idx.page_count,
            "hits": hits,
            "mode": mode,
        }


__all__ = ["PdfOutlineTool", "PdfReadPagesTool", "PdfSearchTool"]
