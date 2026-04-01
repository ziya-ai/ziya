"""
Tests for document file token counting.

Guards against regressions where:
  1. External document files get 0 tokens because [external] paths aren't resolved
  2. Extracted document text gets double-adjusted by file-type multiplier
  3. Heuristic estimates are wildly wrong for structured document formats
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class TestExternalPathResolution(unittest.TestCase):
    """Token counting must resolve [external] paths correctly."""

    def test_resolve_external_path_strips_prefix(self):
        """[external]/abs/path should resolve to /abs/path."""
        from app.utils.file_utils import resolve_external_path
        result = resolve_external_path(
            '[external]/Users/dcohn/work/report.docx',
            '/some/project/root'
        )
        self.assertEqual(result, '/Users/dcohn/work/report.docx')

    def test_resolve_normal_path_joins_with_base(self):
        """Normal relative paths should join with base_dir."""
        from app.utils.file_utils import resolve_external_path
        result = resolve_external_path('src/main.py', '/project/root')
        self.assertEqual(result, '/project/root/src/main.py')


class TestAccurateTokenCountDocuments(unittest.TestCase):
    """get_accurate_token_count must extract text before counting tokens."""

    @patch('app.utils.document_extractor.is_document_file', return_value=True)
    @patch('app.utils.document_extractor.extract_document_text',
           return_value="Executive Summary\n\nShort document text.")
    @patch('app.utils.document_extractor.is_tool_backed_file', return_value=False)
    def test_docx_counts_extracted_text_not_raw_bytes(
        self, mock_tool, mock_extract, mock_is_doc
    ):
        """Token count for a docx should reflect extracted text, not ZIP size."""
        from app.utils.directory_util import get_accurate_token_count

        # The extracted text is ~8 tokens. A raw 500KB docx would be ~120K tokens.
        count = get_accurate_token_count('/tmp/fake.docx')

        mock_extract.assert_called_once()
        # Extracted text "Executive Summary\n\nShort document text." is ~8-10 tokens
        self.assertGreater(count, 0, "Should have some tokens")
        self.assertLess(count, 50, "Should not be counting raw ZIP bytes")

    @patch('app.utils.document_extractor.is_document_file', return_value=True)
    @patch('app.utils.document_extractor.extract_document_text', return_value=None)
    @patch('app.utils.document_extractor.is_tool_backed_file', return_value=False)
    def test_docx_extraction_failure_returns_zero(
        self, mock_tool, mock_extract, mock_is_doc
    ):
        """When extraction fails, return 0 rather than counting raw bytes."""
        from app.utils.directory_util import get_accurate_token_count

        count = get_accurate_token_count('/tmp/broken.docx')
        self.assertEqual(count, 0)


class TestEstimateTokensFastDocuments(unittest.TestCase):
    """Fast estimation for documents should use heuristic, not raw size / 4."""

    @patch('os.path.getsize', return_value=500_000)  # 500KB docx
    @patch('app.utils.directory_util.is_tool_backed_file', return_value=False)
    def test_docx_not_estimated_as_plain_text(self, mock_tool, mock_size):
        """A 500KB docx should NOT produce ~120K tokens (500000/4.1)."""
        from app.utils.directory_util import estimate_tokens_fast

        count = estimate_tokens_fast('/tmp/report.docx')

        # Raw text estimation would give ~121K tokens (500000/4.1)
        # Heuristic should give something much lower (~12.5K from /40)
        self.assertLess(count, 50_000,
                        f"500KB docx should not produce {count} tokens "
                        f"— likely counting raw ZIP bytes as text")

    @patch('os.path.getsize', return_value=1_000)  # 1KB plain text
    def test_plain_text_not_affected(self, mock_size):
        """Plain text files should use the normal chars_per_token estimate."""
        from app.utils.directory_util import estimate_tokens_fast

        count = estimate_tokens_fast('/tmp/readme.md')

        # 1000 bytes / ~4.1 chars per token ≈ 244 tokens
        self.assertGreater(count, 100)
        self.assertLess(count, 500)


if __name__ == '__main__':
    unittest.main()
