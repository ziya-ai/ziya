"""
Tests for the post-instruction system.
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add the parent directory to the path so we can import the app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.post_instructions import PostInstructionManager, post_instruction

class TestPostInstructions(unittest.TestCase):
    """Test the post-instruction system."""
    
    def setUp(self):
        """Set up the test environment."""
        # Clear any existing post-instructions
        PostInstructionManager._post_instructions = {
            "model": {},
            "family": {},
            "endpoint": {},
            "global": {}
        }
        PostInstructionManager._config = {
            "model": {},
            "family": {},
            "endpoint": {},
            "global": {}
        }
    
    def test_register_post_instruction(self):
        """Test registering a post-instruction."""
        # Define a simple post-instruction function
        def test_post_instruction(query, context):
            return query + " TEST"
        
        # Register the post-instruction
        PostInstructionManager.register_post_instruction(
            post_instruction_fn=test_post_instruction,
            name="test_post_instruction",
            instruction_type="global",
            config={"enabled": True}
        )
        
        # Check that the post-instruction was registered
        self.assertIn("test_post_instruction", PostInstructionManager._post_instructions["global"])
        self.assertEqual(
            PostInstructionManager._post_instructions["global"]["test_post_instruction"],
            test_post_instruction
        )
        
        # Check that the configuration was registered
        self.assertIn("test_post_instruction", PostInstructionManager._config["global"])
        self.assertEqual(
            PostInstructionManager._config["global"]["test_post_instruction"],
            {"enabled": True}
        )
    
    def test_apply_post_instructions(self):
        """Test applying post-instructions to a query."""
        # Define and register post-instructions
        @post_instruction(name="global_test", instruction_type="global")
        def global_test(query, context):
            return query + " GLOBAL"
        
        @post_instruction(name="endpoint_test", instruction_type="endpoint", target="test_endpoint")
        def endpoint_test(query, context):
            return query + " ENDPOINT"
        
        @post_instruction(name="family_test", instruction_type="family", target="test_family")
        def family_test(query, context):
            return query + " FAMILY"
        
        @post_instruction(name="model_test", instruction_type="model", target="test_model")
        def model_test(query, context):
            return query + " MODEL"
        
        # Test applying global post-instruction only
        query = "Hello"
        modified_query = PostInstructionManager.apply_post_instructions(query)
        self.assertEqual(modified_query, "Hello GLOBAL")
        
        # Test applying endpoint post-instruction
        modified_query = PostInstructionManager.apply_post_instructions(
            query, endpoint="test_endpoint"
        )
        self.assertEqual(modified_query, "Hello GLOBAL ENDPOINT")
        
        # Test applying family post-instruction
        modified_query = PostInstructionManager.apply_post_instructions(
            query, model_family="test_family"
        )
        self.assertEqual(modified_query, "Hello GLOBAL FAMILY")
        
        # Test applying model post-instruction
        modified_query = PostInstructionManager.apply_post_instructions(
            query, model_name="test_model"
        )
        self.assertEqual(modified_query, "Hello GLOBAL MODEL")
        
        # Test applying all post-instructions
        modified_query = PostInstructionManager.apply_post_instructions(
            query,
            model_name="test_model",
            model_family="test_family",
            endpoint="test_endpoint"
        )
        self.assertEqual(modified_query, "Hello GLOBAL ENDPOINT FAMILY MODEL")
    
    def test_disabled_post_instruction(self):
        """Test that disabled post-instructions are not applied."""
        # Define and register a disabled post-instruction
        @post_instruction(
            name="disabled_test",
            instruction_type="global",
            config={"enabled": False}
        )
        def disabled_test(query, context):
            return query + " DISABLED"
        
        # Test that the disabled post-instruction is not applied
        query = "Hello"
        modified_query = PostInstructionManager.apply_post_instructions(query)
        self.assertEqual(modified_query, "Hello")
    
    def test_gemini_post_instruction(self):
        """Test the Gemini post-instruction."""
        # Import the Gemini post-instruction
        from app.extensions.post_instructions import gemini_post_instructions
        
        # Test applying the Gemini post-instruction
        query = "How do I implement a binary search tree?"
        modified_query = PostInstructionManager.apply_post_instructions(
            query,
            model_family="gemini"
        )
        
        # Check that the post-instruction was applied
        self.assertIn("remember to use diff formatting", modified_query.lower())
        self.assertIn("do not make up approximations", modified_query.lower())

if __name__ == "__main__":
    unittest.main()
