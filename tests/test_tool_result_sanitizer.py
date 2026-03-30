"""Tests for app.utils.tool_result_sanitizer."""

import base64
import os
from unittest.mock import patch, MagicMock

import pytest

from app.utils.tool_result_sanitizer import (
    sanitize_for_context,
    _replace_base64_blobs,
    _extract_text_from_base64,
    _detect_office_format,
    _apply_plugin_filters,
    _cap_size,
    MAX_CONTEXT_RESULT_CHARS,
)


class TestDetectOfficeFormat:
    """Tests for ZIP-based Office format detection."""

    def test_non_base64_returns_none(self):
        assert _detect_office_format('not-valid!!!') is None

    def test_non_zip_returns_none(self):
        b64 = base64.b64encode(b'just random bytes here' * 20).decode()
        assert _detect_office_format(b64) is None

    def test_detects_docx(self):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('word/document.xml', '<doc/>')
            zf.writestr('[Content_Types].xml', '<types/>')
        b64 = base64.b64encode(buf.getvalue()).decode()
        assert _detect_office_format(b64) == '.docx'

    def test_detects_xlsx(self):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('xl/workbook.xml', '<wb/>')
            zf.writestr('[Content_Types].xml', '<types/>')
        b64 = base64.b64encode(buf.getvalue()).decode()
        assert _detect_office_format(b64) == '.xlsx'

    def test_detects_pptx(self):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('ppt/presentation.xml', '<pres/>')
            zf.writestr('[Content_Types].xml', '<types/>')
        b64 = base64.b64encode(buf.getvalue()).decode()
        assert _detect_office_format(b64) == '.pptx'

    def test_ambiguous_zip_returns_none(self):
        """A ZIP without word/, xl/, or ppt/ is not an Office doc."""
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('random/file.txt', 'hello')
        b64 = base64.b64encode(buf.getvalue()).decode()
        assert _detect_office_format(b64) is None


class TestReplaceBase64Blobs:
    """Tests for base64 blob detection and replacement."""

    def test_replaces_long_base64_with_placeholder(self):
        blob = 'A' * 300
        text = f'before {blob} after'
        result = _replace_base64_blobs(text, 'test_tool')
        assert '[binary content removed: 300 base64 chars]' in result
        assert 'A' * 300 not in result

    def test_preserves_short_base64(self):
        """Runs under 200 chars are left alone (could be a hash or ID)."""
        text = 'token: ' + 'A' * 100
        assert _replace_base64_blobs(text, 'test') == text

    @patch('app.utils.tool_result_sanitizer._extract_text_from_base64')
    def test_extracts_pdf_text(self, mock_extract):
        mock_extract.return_value = 'Extracted PDF content here'
        blob = 'JVBERi' + 'A' * 300
        text = f'data: {blob} end'
        result = _replace_base64_blobs(text, 'quip')
        assert 'Extracted PDF content here' in result
        assert 'base64 replaced' in result
        assert blob not in result

    @patch('app.utils.tool_result_sanitizer._extract_text_from_base64')
    def test_extracts_ole2_document(self, mock_extract):
        """OLE2 magic (legacy .xls/.doc) triggers extraction."""
        mock_extract.return_value = 'Spreadsheet data'
        # 0M8R4K is base64 for \xd0\xcf\x11\xe0 (OLE2 magic)
        blob = '0M8R4K' + 'A' * 300
        text = f'file: {blob} done'
        result = _replace_base64_blobs(text, 'some_tool')
        assert 'Spreadsheet data' in result
        assert blob not in result

    @patch('app.utils.tool_result_sanitizer._extract_text_from_base64')
    def test_pdf_extraction_failure_falls_back_to_placeholder(self, mock_extract):
        mock_extract.return_value = None
        blob = 'JVBERi' + 'A' * 300
        text = f'data: {blob} end'
        result = _replace_base64_blobs(text, 'quip')
        assert '[binary content removed:' in result


class TestExtractTextFromBase64:
    """Tests for the document extraction helper."""

    def test_invalid_base64_returns_none(self):
        assert _extract_text_from_base64('not-valid-base64!!!', '.pdf') is None

    @patch('app.utils.document_extractor.extract_document_text')
    def test_pdf_routes_to_extractor(self, mock_extract):
        mock_extract.return_value = 'Page 1 text'
        pdf_bytes = b'%PDF-1.4 fake pdf content'
        b64 = base64.b64encode(pdf_bytes).decode()
        result = _extract_text_from_base64(b64, '.pdf')
        assert result == 'Page 1 text'
        mock_extract.assert_called_once()
        call_path = mock_extract.call_args[0][0]
        assert call_path.endswith('.pdf')

    @patch('app.utils.document_extractor.extract_document_text')
    def test_docx_routes_to_extractor(self, mock_extract):
        mock_extract.return_value = 'Document paragraph'
        b64 = base64.b64encode(b'fake docx content').decode()
        result = _extract_text_from_base64(b64, '.docx')
        assert result == 'Document paragraph'
        call_path = mock_extract.call_args[0][0]
        assert call_path.endswith('.docx')

    @patch('app.utils.document_extractor.extract_document_text')
    def test_xlsx_routes_to_extractor(self, mock_extract):
        mock_extract.return_value = 'Sheet: Data\ncol1 col2'
        b64 = base64.b64encode(b'fake xlsx content').decode()
        result = _extract_text_from_base64(b64, '.xlsx')
        assert result == 'Sheet: Data\ncol1 col2'
        call_path = mock_extract.call_args[0][0]
        assert call_path.endswith('.xlsx')

    @patch('app.utils.document_extractor.extract_document_text')
    def test_cleans_up_temp_file_on_error(self, mock_extract):
        mock_extract.side_effect = Exception('extraction failed')
        pdf_bytes = b'%PDF-1.4 fake'
        b64 = base64.b64encode(pdf_bytes).decode()
        result = _extract_text_from_base64(b64, '.pdf')
        assert result is None


class TestCapSize:
    """Tests for the hard size cap."""

    def test_under_limit_passthrough(self):
        text = 'short text'
        assert _cap_size(text, 'test') == text

    def test_over_limit_truncated(self):
        text = 'x' * (MAX_CONTEXT_RESULT_CHARS + 1000)
        result = _cap_size(text, 'test')
        assert len(result) < len(text)
        assert '[result truncated' in result


class TestPluginFilters:
    """Tests for the plugin filter pipeline."""

    def test_no_providers_passthrough(self):
        """With no filter providers registered, text passes through."""
        with patch('app.plugins.get_tool_result_filter_providers', return_value=[]):
            result = _apply_plugin_filters('hello world', 'test', {})
            assert result == 'hello world'

    def test_provider_filter_applied(self):
        """A registered provider's filter_result is called."""
        mock_provider = MagicMock()
        mock_provider.should_filter.return_value = True
        mock_provider.filter_result.return_value = 'filtered!'

        with patch('app.plugins.get_tool_result_filter_providers', return_value=[mock_provider]):
            result = _apply_plugin_filters('raw text', 'QuipEditor', {'documentId': 'abc'})
            assert result == 'filtered!'
            mock_provider.filter_result.assert_called_once_with('QuipEditor', 'raw text', {'documentId': 'abc'})

    def test_provider_skipped_when_should_filter_false(self):
        """Provider is skipped when should_filter returns False."""
        mock_provider = MagicMock()
        mock_provider.should_filter.return_value = False

        with patch('app.plugins.get_tool_result_filter_providers', return_value=[mock_provider]):
            result = _apply_plugin_filters('untouched', 'run_shell_command', {})
            assert result == 'untouched'
            mock_provider.filter_result.assert_not_called()

    def test_chained_providers(self):
        """Multiple providers are applied in sequence."""
        provider_a = MagicMock()
        provider_a.should_filter.return_value = True
        provider_a.filter_result.return_value = 'step_a'

        provider_b = MagicMock()
        provider_b.should_filter.return_value = True
        provider_b.filter_result.return_value = 'step_b'

        with patch('app.plugins.get_tool_result_filter_providers', return_value=[provider_a, provider_b]):
            result = _apply_plugin_filters('raw', 'tool', {})
            assert result == 'step_b'
            provider_a.filter_result.assert_called_once_with('tool', 'raw', {})
            provider_b.filter_result.assert_called_once_with('tool', 'step_a', {})

    def test_provider_exception_does_not_break_chain(self):
        """A failing provider is skipped; subsequent providers still run."""
        bad_provider = MagicMock()
        bad_provider.should_filter.return_value = True
        bad_provider.filter_result.side_effect = RuntimeError('boom')
        bad_provider.provider_id = 'bad'

        good_provider = MagicMock()
        good_provider.should_filter.return_value = True
        good_provider.filter_result.return_value = 'cleaned'

        with patch('app.plugins.get_tool_result_filter_providers', return_value=[bad_provider, good_provider]):
            result = _apply_plugin_filters('raw', 'tool', {})
            assert result == 'cleaned'


class TestSanitizeForContext:
    """Integration tests for the main entry point."""

    def test_short_text_passthrough(self):
        assert sanitize_for_context('hello') == 'hello'

    def test_empty_passthrough(self):
        assert sanitize_for_context('') == ''

    def test_normal_tool_result_untouched(self):
        """Regular tool results with no bloat pass through cleanly."""
        text = '$ ls -la\ntotal 42\ndrwxr-xr-x  5 user  staff  160 Jan  1 12:00 .\n' * 10
        result = sanitize_for_context(text, 'run_shell_command')
        assert result == text
