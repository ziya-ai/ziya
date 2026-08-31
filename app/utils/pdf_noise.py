"""Targeted suppression of known-noise pdfminer warnings.

pdfminer.six logs a warning per FONT INSTANTIATION when a PDF's font
descriptor lacks a parseable ``FontBBox``::

    Could not get FontBBox from font descriptor because None cannot be
    parsed as 4 floats

Skia-produced PDFs — which includes every PDF Ziya itself exports via
headless Chromium's ``page.pdf()`` — routinely omit ``FontBBox`` from
their font descriptors.  pdfminer handles the condition gracefully
(``PDFFont._parse_bbox`` substitutes a zero rect and text extraction
proceeds correctly), so the warning is a non-actionable expected
condition, yet it fires once per font per page and floods the console
whenever such a PDF is read back (context extraction, the pdf tools,
RAG indexing).

This module installs a MESSAGE-TARGETED ``logging.Filter`` on the exact
logger the warning travels on (``pdfminer.pdffont``) rather than raising
that logger's level: other pdfminer warnings — genuinely malformed
files, encoding problems — remain visible.  This mirrors the narrow
pypdf quieting precedent in ``document_extractor._check_libraries`` but
is more surgical than a level change.

Idempotent: calling :func:`install_pdfminer_noise_filter` repeatedly
adds at most one filter.
"""
import logging

# Message prefixes that are expected, handled conditions inside pdfminer
# and carry no user action.  Matched with str.startswith against the
# fully-formatted message.
_NOISE_PREFIXES = (
    "Could not get FontBBox from font descriptor because ",
)

_PDFMINER_FONT_LOGGER = "pdfminer.pdffont"


class _PdfminerNoiseFilter(logging.Filter):
    """Drop known non-actionable pdfminer font warnings; pass all else."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        return not msg.startswith(_NOISE_PREFIXES)


def install_pdfminer_noise_filter() -> None:
    """Attach the noise filter to ``pdfminer.pdffont`` (idempotent).

    Attached to the EXACT logger that emits the message — a filter on a
    parent logger would not apply, because ``logging.Filter`` objects run
    only on the logger they are attached to, not on children.
    """
    target = logging.getLogger(_PDFMINER_FONT_LOGGER)
    if any(isinstance(f, _PdfminerNoiseFilter) for f in target.filters):
        return
    target.addFilter(_PdfminerNoiseFilter())
