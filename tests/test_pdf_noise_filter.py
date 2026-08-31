"""Tests for the targeted pdfminer FontBBox-noise filter
(``app/utils/pdf_noise.py``).

The defect: reading any Skia/Chromium-produced PDF (including Ziya's own
exports) floods the console with one pdfminer warning per font::

    Could not get FontBBox from font descriptor because None cannot be
    parsed as 4 floats

pdfminer handles the missing FontBBox gracefully (zero-rect substitute),
so the message is pure noise.  The filter must drop EXACTLY that message
class on EXACTLY the emitting logger, leaving every other pdfminer
warning visible.
"""
import logging

import pytest

from app.utils.pdf_noise import (
    _PDFMINER_FONT_LOGGER,
    _PdfminerNoiseFilter,
    install_pdfminer_noise_filter,
)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def font_logger():
    """The real pdfminer font logger, with filter/handler state restored."""
    lg = logging.getLogger(_PDFMINER_FONT_LOGGER)
    prior_filters = list(lg.filters)
    prior_level = lg.level
    yield lg
    lg.filters = prior_filters
    lg.setLevel(prior_level)


def _log_both(lg: logging.Logger, cap: _Capture):
    lg.addHandler(cap)
    try:
        lg.warning(
            "Could not get FontBBox from font descriptor because "
            "None cannot be parsed as 4 floats"
        )
        lg.warning("genuinely useful pdfminer warning about a broken file")
    finally:
        lg.removeHandler(cap)


class TestFilterBehaviour:
    def test_noise_dropped_other_warnings_pass(self, font_logger):
        install_pdfminer_noise_filter()
        cap = _Capture()
        _log_both(font_logger, cap)
        messages = [r.getMessage() for r in cap.records]
        # The FontBBox flood is gone...
        assert not any("Could not get FontBBox" in m for m in messages)
        # ...while a non-noise warning still passes (negative control that
        # the filter is surgical, not a blanket level change).
        assert any("genuinely useful" in m for m in messages)

    def test_fails_without_filter(self, font_logger):
        """Positive control: absent the filter, the noise DOES reach handlers.

        This is the assertion that certifies the defect exists — if pdfminer
        or logging config ever suppresses the message on its own, the filter
        (and this suite) can be retired.
        """
        # No install here; fixture guarantees a clean filter list.
        font_logger.filters = [
            f for f in font_logger.filters
            if not isinstance(f, _PdfminerNoiseFilter)
        ]
        cap = _Capture()
        _log_both(font_logger, cap)
        messages = [r.getMessage() for r in cap.records]
        assert any("Could not get FontBBox" in m for m in messages)

    def test_idempotent_install(self, font_logger):
        install_pdfminer_noise_filter()
        install_pdfminer_noise_filter()
        n = sum(isinstance(f, _PdfminerNoiseFilter) for f in font_logger.filters)
        assert n == 1

    def test_filter_variant_reprs_also_dropped(self, font_logger):
        """The %r payload varies (None, missing keys, short arrays) —
        prefix matching must catch all variants."""
        install_pdfminer_noise_filter()
        cap = _Capture()
        font_logger.addHandler(cap)
        try:
            font_logger.warning(
                "Could not get FontBBox from font descriptor because "
                "[0, 0] cannot be parsed as 4 floats"
            )
        finally:
            font_logger.removeHandler(cap)
        assert cap.records == []


class TestLiveSeam:
    """Drive the REAL pdfminer parse path, not a hand-logged message."""

    def test_real_parse_bbox_warning_is_filtered(self, font_logger):
        pdfminer = pytest.importorskip("pdfminer")
        from pdfminer.pdffont import PDFFont

        install_pdfminer_noise_filter()
        cap = _Capture()
        font_logger.addHandler(cap)
        try:
            # A descriptor with no FontBBox — exactly what Skia emits.
            bbox = PDFFont._parse_bbox({})
        finally:
            font_logger.removeHandler(cap)
        # pdfminer's graceful handling is the premise the suppression
        # rests on: assert it, so a pdfminer that STOPS handling the
        # condition gracefully fails here rather than silently.
        assert bbox == (0.0, 0.0, 0.0, 0.0)
        assert not any(
            "Could not get FontBBox" in r.getMessage() for r in cap.records
        )


class TestConsumersInstall:
    """The seam: the filter helps nothing unless the pdfplumber consumers
    actually install it."""

    def test_pdf_rag_import_installs(self, font_logger):
        font_logger.filters = [
            f for f in font_logger.filters
            if not isinstance(f, _PdfminerNoiseFilter)
        ]
        import importlib
        import app.utils.pdf_rag
        importlib.reload(app.utils.pdf_rag)
        assert any(
            isinstance(f, _PdfminerNoiseFilter) for f in font_logger.filters
        )

    def test_document_extractor_library_check_installs(self, font_logger):
        pytest.importorskip("pdfplumber")
        font_logger.filters = [
            f for f in font_logger.filters
            if not isinstance(f, _PdfminerNoiseFilter)
        ]
        import app.utils.document_extractor as de
        # Force the (cached) library check to run again.
        de._LIBRARIES_CHECKED = False
        de._check_libraries()
        assert any(
            isinstance(f, _PdfminerNoiseFilter) for f in font_logger.filters
        )
