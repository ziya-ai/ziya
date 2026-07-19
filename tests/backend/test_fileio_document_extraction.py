"""
Tests for document extraction in the file_read MCP tool.

Guards against regression where document files (.docx, .pdf, etc.)
are read as raw binary text instead of being routed through the
document extractor.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class TestFileReadDocumentExtraction(unittest.IsolatedAsyncioTestCase):
    """file_read MCP tool must route document files through extractors."""

    async def _execute_file_read(self, path_str, project_root="/tmp/test_project", **kwargs):
        """Helper to execute the FileReadTool with mocked filesystem."""
        from app.mcp.tools.fileio import FileReadTool
        tool = FileReadTool()
        return await tool.execute(
            path=path_str,
            _project_root=project_root,
            **kwargs
        )

    @patch('app.mcp.tools.fileio._resolve_and_validate')
    @patch('app.utils.document_extractor.is_document_file', return_value=True)
    @patch('app.utils.document_extractor.extract_document_text',
           return_value="Executive Summary\n\nThis is extracted text from a DOCX file.")
    async def test_docx_routed_through_extractor(self, mock_extract, mock_is_doc, mock_resolve):
        """A .docx file should be extracted, not read as raw bytes."""
        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.is_file.return_value = True
        fake_path.__str__ = lambda self: "/tmp/test_project/report.docx"
        mock_resolve.return_value = fake_path

        result = await self._execute_file_read("report.docx")

        self.assertFalse(result.get("error", False), f"Unexpected error: {result}")
        self.assertIn("Executive Summary", result["content"])
        self.assertIn("extracted from document", result["metadata"])
        mock_extract.assert_called_once_with("/tmp/test_project/report.docx")
        # raw read_text should NOT have been called
        fake_path.read_text.assert_not_called()

    @patch('app.mcp.tools.fileio._resolve_and_validate')
    @patch('app.utils.document_extractor.is_document_file', return_value=True)
    @patch('app.utils.document_extractor.extract_document_text', return_value=None)
    async def test_docx_extraction_failure_falls_back_to_raw(self, mock_extract, mock_is_doc, mock_resolve):
        """When extraction fails, fall back to raw text read (graceful degradation)."""
        # FileReadTool now performs the raw-read fallback via a bounded
        # open(resolved, "rb") rather than resolved.read_text(), so point the
        # validator at a REAL file whose bytes the open() path actually reads.
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", suffix="corrupt.docx", delete=False)
        tmp.write("PK\x03\x04 garbled binary")
        tmp.close()
        mock_resolve.return_value = Path(tmp.name)

        result = await self._execute_file_read("corrupt.docx")

        # Should fall back to raw read without error
        self.assertFalse(result.get("error", False), f"Unexpected error: {result}")
        mock_extract.assert_called_once()
        os.unlink(tmp.name)

    @patch('app.mcp.tools.fileio._resolve_and_validate')
    @patch('app.utils.document_extractor.is_document_file', return_value=False)
    async def test_plain_text_file_not_affected(self, mock_is_doc, mock_resolve):
        """Plain text files should be read normally, not routed through extractor."""
        # The raw read now goes through a bounded open(resolved, "rb"), so a
        # real file is required for the read to return content.
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", suffix="readme.md", delete=False)
        tmp.write("# Hello World\n\nSome content.")
        tmp.close()
        mock_resolve.return_value = Path(tmp.name)

        result = await self._execute_file_read("readme.md")

        self.assertFalse(result.get("error", False))
        self.assertIn("Hello World", result["content"])
        os.unlink(tmp.name)

    @patch('app.mcp.tools.fileio._resolve_and_validate')
    @patch('app.utils.document_extractor.is_document_file', return_value=True)
    @patch('app.utils.document_extractor.extract_document_text',
           return_value="Line 1\nLine 2\nLine 3\nLine 4\nLine 5")
    async def test_docx_offset_and_limit_applied(self, mock_extract, mock_is_doc, mock_resolve):
        """Offset and max_lines should work on extracted document text."""
        fake_path = MagicMock(spec=Path)
        fake_path.exists.return_value = True
        fake_path.is_file.return_value = True
        fake_path.__str__ = lambda self: "/tmp/test_project/doc.docx"
        mock_resolve.return_value = fake_path

        result = await self._execute_file_read("doc.docx", offset=2, max_lines=2)

        self.assertFalse(result.get("error", False))
        self.assertIn("Line 2", result["content"])
        self.assertIn("Line 3", result["content"])
        self.assertNotIn("Line 1", result["content"])
        self.assertNotIn("Line 4", result["content"])


if __name__ == '__main__':
    unittest.main()
