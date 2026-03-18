"""
Tests for Nova model error handling.

Updated: NovaWrapper moved from app.agents.nova_wrapper to
app.agents.wrappers.nova_wrapper. ZiyaMessage removed, only
ZiyaMessageChunk and ZiyaString remain.
"""

import unittest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app.agents.wrappers.nova_wrapper import NovaWrapper
from app.agents.custom_message import ZiyaString, ZiyaMessageChunk


class TestNovaWrapperInit(unittest.TestCase):
    """Test NovaWrapper initialization."""

    @patch('boto3.client')
    def test_nova_wrapper_creation(self, mock_boto3):
        """NovaWrapper should be instantiable with a model_id."""
        mock_boto3.return_value = MagicMock()
        wrapper = NovaWrapper(model_id="us.amazon.nova-lite-v1:0")
        self.assertIsNotNone(wrapper)

    @patch('boto3.client')
    def test_nova_wrapper_with_kwargs(self, mock_boto3):
        """NovaWrapper should accept temperature and top_p."""
        mock_boto3.return_value = MagicMock()
        wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9,
        )
        self.assertIsNotNone(wrapper)


class TestZiyaMessageChunkCompat(unittest.TestCase):
    """Test ZiyaMessageChunk works correctly for Nova responses."""

    def test_chunk_creation(self):
        """ZiyaMessageChunk should accept content and id."""
        chunk = ZiyaMessageChunk(content="test response", id="test-123")
        self.assertEqual(chunk.content, "test response")

    def test_chunk_message_attribute(self):
        """ZiyaMessageChunk should expose content via .message."""
        chunk = ZiyaMessageChunk(content="hello", id="id-1")
        self.assertEqual(chunk.message, "hello")

    def test_ziya_string(self):
        """ZiyaString should preserve string behavior."""
        s = ZiyaString("test content")
        self.assertEqual(str(s), "test content")
        self.assertIn("test", s)


if __name__ == '__main__':
    unittest.main()
