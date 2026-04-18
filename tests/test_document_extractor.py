"""
Tests for the document extraction module and the /api/extract-document endpoint.

Covers:
- Library detection (_check_libraries)
- PDF text extraction via pdfplumber and pypdf
- PDF page image extraction for scanned documents
- Endpoint error handling (unsupported type, empty PDF, valid PDF)
- File size limits
"""

import os
import io
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Minimal valid PDF with extractable text.
#
# This is a hand-crafted PDF 1.0 document with a single page containing
# the text "Hello World".  It uses a Type1 Helvetica font so no font
# embedding is needed.  Both pdfplumber and pypdf can extract text from it.
# ---------------------------------------------------------------------------
MINIMAL_PDF_WITH_TEXT = (
    b"%PDF-1.0\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
    b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello World) Tj ET\n"
    b"endstream\nendobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000266 00000 n \n"
    b"0000000360 00000 n \n"
    b"trailer<</Size 6/Root 1 0 R>>\n"
    b"startxref\n430\n"
    b"%%EOF\n"
)

# A blank PDF — valid structure but no text content.
MINIMAL_BLANK_PDF = (
    b"%PDF-1.0\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"startxref\n193\n"
    b"%%EOF\n"
)


# ── Unit tests for the extractor module ────────────────────────────────────

class TestCheckLibraries:
    """Tests for _check_libraries and library detection."""

    def test_libraries_detected(self):
        """At least pdfplumber or pypdf should be detected in the test environment."""
        from app.utils.document_extractor import (
            _check_libraries, _AVAILABLE_LIBRARIES,
        )
        # Reset state to force re-check
        import app.utils.document_extractor as mod
        mod._LIBRARIES_CHECKED = False

        _check_libraries()
        available = {k for k, v in _AVAILABLE_LIBRARIES.items() if v}
        assert len(available) > 0, (
            f"No document libraries detected. Available: {_AVAILABLE_LIBRARIES}"
        )
        # pdfplumber and pypdf are installed per user's pip output
        assert 'pdfplumber' in available or 'pypdf' in available

    def test_idempotent(self):
        """Calling _check_libraries twice doesn't reset results."""
        from app.utils.document_extractor import (
            _check_libraries, _AVAILABLE_LIBRARIES,
        )
        import app.utils.document_extractor as mod
        mod._LIBRARIES_CHECKED = False

        _check_libraries()
        first_snapshot = dict(_AVAILABLE_LIBRARIES)
        _check_libraries()  # second call — should be no-op
        assert _AVAILABLE_LIBRARIES == first_snapshot


class TestExtractPdfText:
    """Tests for extract_pdf_text."""

    def test_extract_text_from_valid_pdf(self, tmp_path):
        """A minimal PDF with text should return non-empty string."""
        from app.utils.document_extractor import extract_pdf_text

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)

        result = extract_pdf_text(str(pdf_file))
        assert result is not None, "extract_pdf_text returned None for a PDF with text"
        assert "Hello" in result

    def test_blank_pdf_returns_none(self, tmp_path):
        """A blank PDF with no text content should return None."""
        from app.utils.document_extractor import extract_pdf_text

        pdf_file = tmp_path / "blank.pdf"
        pdf_file.write_bytes(MINIMAL_BLANK_PDF)

        result = extract_pdf_text(str(pdf_file))
        assert result is None

    def test_nonexistent_file_returns_none(self):
        """A nonexistent file should return None, not raise."""
        from app.utils.document_extractor import extract_pdf_text

        result = extract_pdf_text("/tmp/definitely_does_not_exist_12345.pdf")
        assert result is None


class TestExtractPdfPageImages:
    """Tests for extract_pdf_page_images (scanned PDF fallback)."""

    def test_renders_pages_from_valid_pdf(self, tmp_path):
        """Should produce at least one image from a valid PDF."""
        from app.utils.document_extractor import extract_pdf_page_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)

        images = extract_pdf_page_images(str(pdf_file))
        assert images is not None, "extract_pdf_page_images returned None"
        assert len(images) == 1  # one page

        img = images[0]
        assert img["mediaType"] == "image/jpeg"
        assert img["page"] == 1
        assert img["width"] > 0
        assert img["height"] > 0
        assert len(img["data"]) > 100  # non-trivial base64

        # Verify it's valid base64 JPEG
        import base64
        raw = base64.b64decode(img["data"])
        assert raw[:2] == b'\xff\xd8'  # JPEG magic bytes

    def test_renders_blank_pdf(self, tmp_path):
        """A blank PDF should still render a page image (white page)."""
        from app.utils.document_extractor import extract_pdf_page_images

        pdf_file = tmp_path / "blank.pdf"
        pdf_file.write_bytes(MINIMAL_BLANK_PDF)

        images = extract_pdf_page_images(str(pdf_file))
        assert images is not None
        assert len(images) == 1

    def test_nonexistent_returns_none(self):
        """A nonexistent file should return None."""
        from app.utils.document_extractor import extract_pdf_page_images

        result = extract_pdf_page_images("/tmp/not_a_real_file_12345.pdf")
        assert result is None

    def test_max_pages_limit(self, tmp_path):
        """Should respect the max_pages parameter."""
        from app.utils.document_extractor import extract_pdf_page_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)

        # Our test PDF has 1 page, but verify the parameter is accepted
        images = extract_pdf_page_images(str(pdf_file), max_pages=1)
        assert images is not None
        assert len(images) <= 1

    def test_image_dimensions_within_max_edge(self, tmp_path):
        """Rendered images should not exceed max_edge pixels."""
        from app.utils.document_extractor import extract_pdf_page_images

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)

        max_edge = 800
        images = extract_pdf_page_images(str(pdf_file), max_edge=max_edge)
        assert images is not None
        for img in images:
            assert img["width"] <= max_edge
            assert img["height"] <= max_edge


class TestExtractDocumentText:
    """Tests for the top-level extract_document_text dispatch function."""

    def test_dispatches_to_pdf(self, tmp_path):
        """extract_document_text should dispatch .pdf files to extract_pdf_text."""
        from app.utils.document_extractor import extract_document_text

        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)

        result = extract_document_text(str(pdf_file))
        assert result is not None
        assert "Hello" in result

    def test_nonexistent_returns_none(self):
        from app.utils.document_extractor import extract_document_text

        result = extract_document_text("/tmp/nope_not_here.pdf")
        assert result is None

    def test_unsupported_extension_returns_none(self, tmp_path):
        """An unsupported file extension should return None."""
        from app.utils.document_extractor import extract_document_text

        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("plain text")

        result = extract_document_text(str(txt_file))
        assert result is None

    def test_caching(self, tmp_path):
        """Second call for the same file should return cached result."""
        from app.utils.document_extractor import (
            extract_document_text, _DOCUMENT_CACHE,
        )

        pdf_file = tmp_path / "cached.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)
        path = str(pdf_file)

        # First call — populates cache
        result1 = extract_document_text(path)
        assert result1 is not None

        mtime = os.path.getmtime(path)
        cache_key = (path, mtime)
        assert cache_key in _DOCUMENT_CACHE

        # Second call — should hit cache
        result2 = extract_document_text(path)
        assert result2 == result1


class TestCheckLibrariesCalledBeforeExtraction:
    """Regression test: _extract_document_text_impl must call _check_libraries."""

    def test_impl_calls_check_libraries(self, tmp_path):
        """Ensure _extract_document_text_impl calls _check_libraries
        so that the upload endpoint works even if nothing else touched
        the module first."""
        import app.utils.document_extractor as mod

        # Reset the checked flag to simulate a fresh module load
        mod._LIBRARIES_CHECKED = False

        pdf_file = tmp_path / "fresh.pdf"
        pdf_file.write_bytes(MINIMAL_PDF_WITH_TEXT)

        result = mod._extract_document_text_impl(str(pdf_file))
        # _check_libraries should have been called, so libs should be detected
        assert mod._LIBRARIES_CHECKED is True
        # And extraction should succeed
        assert result is not None
        assert "Hello" in result


# ── Integration tests for the /api/extract-document endpoint ───────────────

@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app.

    We import late to avoid pulling in the entire server at module level,
    which would interfere with unit tests above.
    """
    from fastapi.testclient import TestClient
    from app.routes.misc_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestExtractDocumentEndpoint:
    """Tests for POST /api/extract-document."""

    def test_upload_valid_pdf(self, client):
        """Uploading a valid PDF with text should return extracted text."""
        resp = client.post(
            "/api/extract-document",
            files={"file": ("test.pdf", io.BytesIO(MINIMAL_PDF_WITH_TEXT), "application/pdf")},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "text" in data
        assert "Hello" in data["text"]
        assert data["filename"] == "test.pdf"
        assert data["chars"] > 0

    def test_upload_blank_pdf_returns_page_images(self, client):
        """Uploading a blank PDF should fall back to page image extraction."""
        resp = client.post(
            "/api/extract-document",
            files={"file": ("blank.pdf", io.BytesIO(MINIMAL_BLANK_PDF), "application/pdf")},
        )
        assert resp.status_code == 200, f"Expected 200 with images, got {resp.status_code}: {resp.text}"
        data = resp.json()
        # text should be None/null, images should be present
        assert data.get("text") is None
        assert "images" in data
        assert len(data["images"]) > 0
        img = data["images"][0]
        assert img["mediaType"] == "image/jpeg"
        assert img["page"] == 1
        assert len(img["data"]) > 100

    def test_upload_blank_pdf_no_images_when_unavailable(self, client):
        """When page image rendering fails, should still return 422."""
        with patch("app.routes.misc_routes.extract_pdf_page_images", return_value=None):
            # Need to ensure the patched import is used — patch at the call site
            pass

        # Use a more targeted approach: mock at the module level
        import app.utils.document_extractor as mod
        original_fn = mod.extract_pdf_page_images
        mod.extract_pdf_page_images = lambda *a, **kw: None
        try:
            resp = client.post(
                "/api/extract-document",
                files={"file": ("blank.pdf", io.BytesIO(MINIMAL_BLANK_PDF), "application/pdf")},
            )
            assert resp.status_code == 422
            data = resp.json()
            assert data["error"] == "no_text_extracted"
        finally:
            mod.extract_pdf_page_images = original_fn

    def test_upload_unsupported_type(self, client):
        """Uploading a .txt file should return 400."""
        resp = client.post(
            "/api/extract-document",
            files={"file": ("notes.txt", io.BytesIO(b"just text"), "text/plain")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_type"

    def test_upload_oversized_file(self, client):
        """Files over 50 MB should be rejected with 413."""
        big_content = b"x" * (51 * 1024 * 1024)
        resp = client.post(
            "/api/extract-document",
            files={"file": ("huge.pdf", io.BytesIO(big_content), "application/pdf")},
        )
        assert resp.status_code == 413
        assert "too_large" in resp.json()["error"]
