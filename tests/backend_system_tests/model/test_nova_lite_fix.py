"""
Tests for NovaWrapper message formatting.

Updated: NovaWrapper moved from app.agents.nova_wrapper_fix to
app.agents.wrappers.nova_wrapper. Single module now, no separate
"fix" variant.
"""

import unittest
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app.agents.wrappers.nova_wrapper import NovaWrapper
from app.agents.custom_message import ZiyaString


class TestNovaWrapperMessageFormat(unittest.TestCase):
    """Test that NovaWrapper handles message formatting correctly."""

    @patch('boto3.client')
    def test_nova_lite_creation(self, mock_boto3):
        """Should create NovaWrapper for nova-lite model."""
        mock_boto3.return_value = MagicMock()
        wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9,
        )
        self.assertIsNotNone(wrapper)

    @patch('boto3.client')
    def test_nova_pro_creation(self, mock_boto3):
        """Should create NovaWrapper for nova-pro model."""
        mock_boto3.return_value = MagicMock()
        wrapper = NovaWrapper(
            model_id="us.amazon.nova-pro-v1:0",
            temperature=0.5,
        )
        self.assertIsNotNone(wrapper)


class TestZiyaStringCompat(unittest.TestCase):
    """Test ZiyaString compatibility."""

    def test_string_behavior(self):
        s = ZiyaString("response text")
        self.assertEqual(len(s), len("response text"))
        self.assertTrue(s.startswith("response"))

    def test_empty_string(self):
        s = ZiyaString("")
        self.assertEqual(str(s), "")
        self.assertEqual(len(s), 0)


if __name__ == '__main__':
    unittest.main()
