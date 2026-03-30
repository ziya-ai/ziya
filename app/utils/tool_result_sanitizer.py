"""
Tool result sanitizer — strips bulk content from tool results before they
enter conversation context.

General-purpose transforms shipped with the core release:
- Base64 document blobs (PDFs → extracted text, other binary → placeholders)
- Hard size cap

Site-specific transforms (e.g. Quip sectionId stripping) are registered
via the ToolResultFilterProvider plugin interface.
"""

import base64
import os
import re
import zipfile
import tempfile
from typing import Optional

from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

# Base64 blobs: runs of 200+ base64 characters.
_BASE64_BLOB_RE = re.compile(r'[A-Za-z0-9+/]{200,}={0,3}')

# PDF magic bytes in base64: %PDF = JVBERi
_PDF_BASE64_PREFIX = 'JVBERi'

# OLE2 magic bytes in base64: \xd0\xcf\x11\xe0 → 0M8R4K (legacy .doc/.xls/.ppt)
_OLE2_BASE64_PREFIX = '0M8R4K'

# After all transforms, cap total result size.
MAX_CONTEXT_RESULT_CHARS = int(os.environ.get('TOOL_RESULT_MAX_CHARS', '100000'))


def sanitize_for_context(result_text: str, tool_name: str = '', args: dict | None = None) -> str:
    """Sanitize a tool result for inclusion in conversation context.

    Applies in order:
    1. Plugin filters (site-specific, e.g. Quip sectionId stripping)
    2. Base64 blob replacement (general-purpose)
    3. Hard size cap

    Args:
        result_text: Raw tool result text.
        tool_name: Normalized tool name.
        args: Tool arguments (passed to plugin filters for context).
    """
    if not result_text or len(result_text) < 200:
        return result_text

    original_len = len(result_text)
    text = result_text
    if args is None:
        args = {}

    # Phase 1: Plugin filters (site-specific)
    text = _apply_plugin_filters(text, tool_name, args)

    # Phase 2: General-purpose base64 blob replacement
    text = _replace_base64_blobs(text, tool_name)

    # Phase 3: Hard size cap
    text = _cap_size(text, tool_name)

    stripped = original_len - len(text)
    if stripped > 1000:
        logger.info(
            f"Sanitized tool result for '{tool_name}': "
            f"{original_len:,} → {len(text):,} chars ({stripped:,} removed)"
        )

    return text


def _apply_plugin_filters(text: str, tool_name: str, args: dict) -> str:
    """Run registered ToolResultFilterProvider plugins."""
    try:
        from app.plugins import get_tool_result_filter_providers
        for provider in get_tool_result_filter_providers():
            try:
                if provider.should_filter(tool_name):
                    text = provider.filter_result(tool_name, text, args)
            except Exception as e:
                logger.warning(
                    f"Tool result filter '{getattr(provider, 'provider_id', '?')}' "
                    f"failed for '{tool_name}': {e}"
                )
    except ImportError:
        pass  # Plugin system not available
    return text


def _replace_base64_blobs(text: str, tool_name: str) -> str:
    """Find base64 blobs and replace with extracted text or placeholders."""
    def _replace_blob(match: re.Match) -> str:
        blob = match.group(0)
        blob_bytes = len(blob)

        if blob.startswith(_PDF_BASE64_PREFIX):
            extracted = _extract_text_from_base64(blob, '.pdf')
            if extracted:
                return (
                    f'\n[Extracted text from PDF ({blob_bytes:,} bytes of '
                    f'base64 replaced)]\n{extracted}\n'
                )

        # OLE2 container (legacy .doc, .xls, .ppt)
        if blob.startswith(_OLE2_BASE64_PREFIX):
            extracted = _extract_text_from_base64(blob, '.xls')
            if extracted:
                return (
                    f'\n[Extracted text from document ({blob_bytes:,} bytes of '
                    f'base64 replaced)]\n{extracted}\n'
                )

        # Try decoding to detect ZIP-based Office formats (docx, xlsx, pptx)
        office_ext = _detect_office_format(blob)
        if office_ext:
            extracted = _extract_text_from_base64(blob, office_ext)
            if extracted:
                fmt = office_ext.lstrip('.').upper()
                return (
                    f'\n[Extracted text from {fmt} ({blob_bytes:,} bytes of '
                    f'base64 replaced)]\n{extracted}\n'
                )

        return f'[binary content removed: {blob_bytes:,} base64 chars]'

    return _BASE64_BLOB_RE.sub(_replace_blob, text)


def _detect_office_format(b64_data: str) -> Optional[str]:
    """Detect ZIP-based Office format by peeking inside the archive."""
    try:
        raw = base64.b64decode(b64_data[:64])  # Only need first few bytes
    except Exception:
        return None
    if raw[:4] != b'PK\x03\x04':
        return None
    # Full decode needed to inspect ZIP contents
    try:
        import io
        full = base64.b64decode(b64_data)
        with zipfile.ZipFile(io.BytesIO(full)) as zf:
            names = zf.namelist()
            if any(n.startswith('word/') for n in names):
                return '.docx'
            if any(n.startswith('xl/') for n in names):
                return '.xlsx'
            if any(n.startswith('ppt/') for n in names):
                return '.pptx'
    except Exception:
        pass
    return None


def _extract_text_from_base64(b64_data: str, suffix: str) -> Optional[str]:
    """Decode a base64 document and extract text using document_extractor."""
    tmp_path = None
    try:
        doc_bytes = base64.b64decode(b64_data)
    except Exception:
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(doc_bytes)
            tmp_path = tmp.name
        from app.utils.document_extractor import extract_document_text
        return extract_document_text(tmp_path)
    except Exception as e:
        logger.warning(f'Failed to extract text from base64 {suffix} blob: {e}')
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _cap_size(text: str, tool_name: str) -> str:
    """Hard cap on result size with a note about truncation."""
    if len(text) <= MAX_CONTEXT_RESULT_CHARS:
        return text
    logger.warning(
        f"Tool result for '{tool_name}' still {len(text):,} chars after "
        f"sanitization — truncating to {MAX_CONTEXT_RESULT_CHARS:,}"
    )
    return (
        text[:MAX_CONTEXT_RESULT_CHARS]
        + f'\n\n[result truncated — {len(text):,} total chars]'
    )
