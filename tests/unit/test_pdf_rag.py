"""
Unit tests for the PDF RAG pipeline (app.utils.pdf_rag and the
pdf_outline / pdf_read_pages / pdf_search MCP tools).

These tests patch the PDF extraction boundary (_extract_pages_text /
_extract_native_outline) so they verify the RAG indexing, search,
stubbing, and tool-dispatch logic without depending on a real PDF
extractor being able to recover text from synthetic test PDFs.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure the repository root is importable regardless of where pytest is run.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fake_pages(page_texts):
    """Build a fake _extract_pages_text return value."""
    return [
        {
            "page": i + 1,
            "text": text,
            "token_count": max(1, len(text) // 4),
            "has_images": False,
        }
        for i, text in enumerate(page_texts)
    ]


def _make_empty_pdf(path: Path, page_count: int = 3) -> None:
    """
    Drop an empty-but-valid PDF file at *path* so the "is this a PDF"
    / size / mtime-keyed caching paths work.  Content doesn't matter:
    extraction is mocked in the tests.
    """
    # Minimal valid PDF (acrobat will open it; pypdf will read page count
    # but that's fine — we mock the extractor).
    body = b"%PDF-1.4\n%stub for tests\n"
    # Pad so the file is non-trivially sized and distinct per page_count.
    body += b"% padding " * max(1, page_count) * 4
    body += b"\n%%EOF\n"
    path.write_bytes(body)


# --------------------------------------------------------------------------- #
# Core module tests
# --------------------------------------------------------------------------- #

class PdfRagCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ziya_pdf_rag_"))
        os.environ["ZIYA_USER_CODEBASE_DIR"] = str(self.tmp)
        os.environ["ZIYA_PDF_RAG_TOKEN_THRESHOLD"] = "10"
        # Reload so env takes effect.
        import app.utils.pdf_rag as pdf_rag
        importlib.reload(pdf_rag)
        self.pdf_rag = pdf_rag

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
        os.environ.pop("ZIYA_PDF_RAG_TOKEN_THRESHOLD", None)

    # --- BM25 -------------------------------------------------------------- #

    def test_bm25_ranks_matching_document_first(self):
        tokenise = self.pdf_rag._tokenise_for_bm25
        build = self.pdf_rag._build_bm25
        score = self.pdf_rag._bm25_score
        docs = [
            tokenise("the quick brown fox jumps over the lazy dog"),
            tokenise("completely unrelated payload about shipping manifests"),
            tokenise("another brown dog sleeping in the sun"),
        ]
        idx = build(docs)
        scores = score(idx, tokenise("quick fox"))
        # Doc 0 mentions both terms; docs 1 and 2 mention neither.
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[0], scores[2])

    def test_bm25_handles_unknown_query_terms(self):
        tokenise = self.pdf_rag._tokenise_for_bm25
        idx = self.pdf_rag._build_bm25([tokenise("hello world")])
        scores = self.pdf_rag._bm25_score(idx, tokenise("nonexistentterm"))
        self.assertEqual(scores, [0.0])

    def test_tokeniser_splits_hyphens_and_periods(self):
        """
        Hyphens and periods act as token separators so that a query for
        a bare sub-word matches hyphenated/dotted occurrences in the
        source text.  Without this, ``unique-needle`` on a page would be
        missed by a search for ``needle``.
        """
        tokenise = self.pdf_rag._tokenise_for_bm25
        self.assertIn("needle", tokenise("the unique-needle is here"))
        self.assertIn("unique", tokenise("the unique-needle is here"))
        # Figure/table id patterns: "Figure 3.2" → digits split on the period
        self.assertEqual(tokenise("Figure 3.2 caption"), ["figure", "3", "2", "caption"])
        # Underscores are preserved (identifier-like tokens stay whole)
        self.assertIn("my_token", tokenise("snake_case unchanged: my_token"))

    # --- Cache keying ------------------------------------------------------ #

    def test_cache_key_is_stable_for_same_file(self):
        pdf = self.tmp / "stable.pdf"
        _make_empty_pdf(pdf)
        k1, d1 = self.pdf_rag._cache_key_for(str(pdf))
        k2, d2 = self.pdf_rag._cache_key_for(str(pdf))
        self.assertEqual(k1, k2)
        self.assertEqual(d1, d2)

    def test_cache_key_changes_when_file_is_rewritten(self):
        import time
        pdf = self.tmp / "rewritten.pdf"
        _make_empty_pdf(pdf, page_count=1)
        k1, _ = self.pdf_rag._cache_key_for(str(pdf))
        time.sleep(1.1)  # mtime resolution
        _make_empty_pdf(pdf, page_count=10)  # different size
        k2, _ = self.pdf_rag._cache_key_for(str(pdf))
        self.assertNotEqual(k1, k2)

    def test_cache_key_identical_for_symlinked_and_direct_path(self):
        """
        On macOS /var/folders/... is a symlink to /private/var/folders/...,
        and at one point the tool path (which calls .resolve()) and the
        extractor path (which called os.path.abspath) produced different
        cache keys for the same file — causing the index to be rebuilt on
        every tool invocation.  _cache_key_for should now resolve symlinks
        so both forms land on the same cache.
        """
        pdf = self.tmp / "symcheck.pdf"
        _make_empty_pdf(pdf)
        # Build a symlink to a canonical location with a different string
        # path but pointing at the same underlying inode.
        link_dir = Path(tempfile.mkdtemp(prefix="ziya_pdf_link_"))
        try:
            link = link_dir / "via-symlink.pdf"
            os.symlink(str(pdf), str(link))
            k_direct, d_direct = self.pdf_rag._cache_key_for(str(pdf))
            k_link, d_link = self.pdf_rag._cache_key_for(str(link))
            self.assertEqual(k_direct, k_link,
                             "Symlinked and direct paths must produce the same cache key")
            self.assertEqual(d_direct, d_link)
        finally:
            shutil.rmtree(link_dir, ignore_errors=True)

    def test_project_relative_path_under_root(self):
        pdf = self.tmp / "sub" / "doc.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.assertEqual(
            self.pdf_rag._project_relative_path(str(pdf)),
            os.path.join("sub", "doc.pdf"),
        )

    def test_project_relative_path_outside_root_returned_verbatim(self):
        self.assertEqual(
            self.pdf_rag._project_relative_path("/elsewhere/foo.pdf"),
            "/elsewhere/foo.pdf",
        )

    # --- Index build & stub ------------------------------------------------ #

    def _build_with_mocked_extraction(self, pdf_path: Path, page_texts, outline=None):
        """Helper: build a PdfIndex with extraction mocked."""
        with mock.patch.object(self.pdf_rag, "_extract_pages_text",
                               return_value=_fake_pages(page_texts)), \
             mock.patch.object(self.pdf_rag, "_extract_native_outline",
                               return_value=(outline or [], {}, len(page_texts))), \
             mock.patch.object(self.pdf_rag, "_extract_image_captions",
                               return_value=[]):
            return self.pdf_rag.PdfIndex.build(str(pdf_path))

    def test_index_build_persists_files_and_page_text(self):
        pdf = self.tmp / "manual.pdf"
        _make_empty_pdf(pdf)
        idx = self._build_with_mocked_extraction(pdf, [
            "introduction to the widget system",
            "chapter one: installing widgets",
            "chapter two: configuring widgets",
            "chapter three: diagnosing widget failures",
            "appendix: glossary of widget terms",
        ])
        self.assertIsNotNone(idx)
        self.assertEqual(idx.page_count, 5)
        self.assertTrue((idx.cache_dir / "pages.jsonl").is_file())
        self.assertTrue((idx.cache_dir / "meta.json").is_file())

        # Reload from disk — meta should round-trip.
        reloaded = self.pdf_rag.PdfIndex.load(str(pdf))
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.page_count, 5)

    def test_stub_contains_metadata_and_tool_instructions(self):
        pdf = self.tmp / "spec.pdf"
        _make_empty_pdf(pdf)
        self._build_with_mocked_extraction(
            pdf, [f"page {i} content" for i in range(1, 8)]
        )
        stub = self.pdf_rag.get_pdf_stub(str(pdf))
        self.assertIsNotNone(stub)
        self.assertIn("Pages: 7", stub)
        self.assertIn("pdf_read_pages", stub)
        self.assertIn("pdf_search", stub)
        self.assertIn("pdf_outline", stub)

    def test_stub_uses_relative_path_when_under_project_root(self):
        pdf = self.tmp / "inside.pdf"
        _make_empty_pdf(pdf)
        self._build_with_mocked_extraction(pdf, ["x", "y", "z"])
        stub = self.pdf_rag.get_pdf_stub(str(pdf))
        self.assertIsNotNone(stub)
        # Last line shows the path argument — should be the relative form.
        self.assertIn("'inside.pdf'", stub)

    def test_read_pages_returns_requested_range_inclusive(self):
        pdf = self.tmp / "range.pdf"
        _make_empty_pdf(pdf)
        idx = self._build_with_mocked_extraction(
            pdf, [f"body of page {i}" for i in range(1, 11)]
        )
        result = idx.read_pages(3, 5)
        pages = [r["page"] for r in result]
        self.assertEqual(pages, [3, 4, 5])

    def test_search_returns_hits_for_unique_terms(self):
        pdf = self.tmp / "search.pdf"
        _make_empty_pdf(pdf)
        idx = self._build_with_mocked_extraction(pdf, [
            "alpha beta gamma",
            "delta epsilon zeta",
            "the unique-needle lives here",
            "eta theta iota",
        ])
        hits = idx.search("needle", top_k=3)
        self.assertTrue(hits, "expected at least one hit for the unique term")
        self.assertEqual(hits[0]["page"], 3)

    def test_search_empty_query_returns_empty_list(self):
        pdf = self.tmp / "empty-query.pdf"
        _make_empty_pdf(pdf)
        idx = self._build_with_mocked_extraction(pdf, ["some words"])
        self.assertEqual(idx.search("", top_k=5), [])

    def test_search_falls_back_to_bm25_when_embeddings_unavailable(self):
        """
        Without sentence-transformers installed, mode='embedding' should
        silently fall back to BM25 and still return hits.
        """
        pdf = self.tmp / "fallback.pdf"
        _make_empty_pdf(pdf)
        idx = self._build_with_mocked_extraction(pdf, [
            "the foo is here",
            "the bar is there",
            "completely off topic gardening tips",
        ])
        # Force the embedding path to report "unavailable".
        with mock.patch.object(idx, "_embedding_scores", return_value=None):
            hits = idx.search("foo", top_k=2, mode="embedding")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["page"], 1)

    # --- should_use_pdf_rag ------------------------------------------------ #

    def test_should_use_pdf_rag_when_index_exists(self):
        pdf = self.tmp / "already.pdf"
        _make_empty_pdf(pdf)
        # Build a real index on disk (with mocked extraction).
        self._build_with_mocked_extraction(pdf, ["x", "y", "z"])
        self.assertTrue(self.pdf_rag.should_use_pdf_rag(str(pdf)))

    def test_should_use_pdf_rag_rejects_nonexistent_and_non_pdf(self):
        self.assertFalse(self.pdf_rag.should_use_pdf_rag(""))
        self.assertFalse(self.pdf_rag.should_use_pdf_rag("/does/not/exist.pdf"))
        txt = self.tmp / "notpdf.txt"
        txt.write_text("hello")
        self.assertFalse(self.pdf_rag.should_use_pdf_rag(str(txt)))


# --------------------------------------------------------------------------- #
# MCP tool tests
# --------------------------------------------------------------------------- #

class PdfRagMcpToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ziya_pdf_tools_"))
        os.environ["ZIYA_USER_CODEBASE_DIR"] = str(self.tmp)
        os.environ["ZIYA_PDF_RAG_TOKEN_THRESHOLD"] = "10"
        import app.utils.pdf_rag as pdf_rag
        importlib.reload(pdf_rag)
        self.pdf_rag = pdf_rag
        from app.mcp.tools import pdf_tools
        importlib.reload(pdf_tools)
        self.pdf_tools = pdf_tools

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
        os.environ.pop("ZIYA_PDF_RAG_TOKEN_THRESHOLD", None)

    def _seed_pdf_with_index(self, name: str, page_texts, outline=None):
        """Create an empty PDF at ``tmp/name`` and seed its index on disk."""
        pdf = self.tmp / name
        _make_empty_pdf(pdf)
        with mock.patch.object(self.pdf_rag, "_extract_pages_text",
                               return_value=_fake_pages(page_texts)), \
             mock.patch.object(self.pdf_rag, "_extract_native_outline",
                               return_value=(outline or [], {}, len(page_texts))), \
             mock.patch.object(self.pdf_rag, "_extract_image_captions",
                               return_value=[]):
            self.pdf_rag.PdfIndex.build(str(pdf))
        return pdf

    def test_pdf_outline_accepts_relative_and_absolute_path(self):
        pdf = self._seed_pdf_with_index("rel.pdf", ["intro", "page two", "page three"])
        tool = self.pdf_tools.PdfOutlineTool()
        # Relative path
        out_rel = asyncio.run(tool.execute(path="rel.pdf", _workspace_path=str(self.tmp)))
        self.assertEqual(out_rel.get("page_count"), 3)
        # Absolute path
        out_abs = asyncio.run(tool.execute(path=str(pdf), _workspace_path=str(self.tmp)))
        self.assertEqual(out_abs.get("page_count"), 3)

    def test_pdf_read_pages_clamps_range_and_caps_max_pages(self):
        self._seed_pdf_with_index(
            "clamp.pdf", [f"line {i}" for i in range(1, 11)],
        )
        tool = self.pdf_tools.PdfReadPagesTool()
        # Request way beyond the end, with a max_pages cap of 5 — should
        # clamp to [3, 7], not error.
        out = asyncio.run(tool.execute(
            path="clamp.pdf",
            start_page=3,
            end_page=99,
            max_pages=5,
            _workspace_path=str(self.tmp),
        ))
        pages = [p["page"] for p in out.get("pages", [])]
        self.assertEqual(pages, [3, 4, 5, 6, 7])

    def test_pdf_search_rejects_empty_query(self):
        self._seed_pdf_with_index("q.pdf", ["hello"])
        tool = self.pdf_tools.PdfSearchTool()
        out = asyncio.run(tool.execute(
            path="q.pdf", query="  ", _workspace_path=str(self.tmp),
        ))
        self.assertTrue(out.get("error"))

    def test_pdf_search_finds_unique_term(self):
        self._seed_pdf_with_index(
            "find.pdf",
            ["first page", "the secret token is rutabaga", "third page"],
        )
        tool = self.pdf_tools.PdfSearchTool()
        out = asyncio.run(tool.execute(
            path="find.pdf", query="rutabaga", _workspace_path=str(self.tmp),
        ))
        hits = out.get("hits", [])
        self.assertTrue(hits, "expected at least one hit")
        self.assertEqual(hits[0]["page"], 2)

    def test_pdf_search_invalid_mode_falls_back_to_bm25(self):
        self._seed_pdf_with_index("mode.pdf", ["alpha", "beta gamma", "delta"])
        tool = self.pdf_tools.PdfSearchTool()
        out = asyncio.run(tool.execute(
            path="mode.pdf", query="beta", mode="nonsense",
            _workspace_path=str(self.tmp),
        ))
        # If the `mode` field hasn't been wired up yet this returns None,
        # which is a legitimate bug signal — the test should fail.
        self.assertEqual(out.get("mode"), "bm25")


if __name__ == "__main__":
    unittest.main()
