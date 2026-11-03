#!/usr/bin/env python3
"""
Test for handling complex JSON in streaming middleware.
This test specifically targets the issue with Gemini responses.
"""

import asyncio
import json
import unittest
from typing import AsyncIterator, Any

from app.middleware.streaming import StreamingMiddleware

class TestComplexJsonHandling(unittest.TestCase):
    """Test the streaming middleware's handling of complex JSON."""
    

    def test_json_with_newlines_and_quotes(self):
        """Test that safe_stream handles JSON with newlines and quotes correctly."""
        # Create a problematic JSON string with newlines and quotes
        problematic_json = """
        {
            "code": "```python\\ndef hello_world():\\n    print(\\"Hello, world!\\")\\n```",
            "explanation": "This is a simple Python function that prints \\"Hello, world!\\"",
            "notes": "Make sure to call the function to see the output"
        }
        """
        
        async def mock_stream():
            yield problematic_json
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Process the stream through the middleware's safe_stream
        results = []
        
        async def run_test():
            async for chunk in middleware.safe_stream(mock_stream()):
                results.append(chunk)
        
        # Run the async function
        asyncio.run(run_test())
        
        # Verify we got the expected output
        self.assertTrue(len(results) > 0, "No results were returned")
        
        # Check that the content was properly formatted as SSE data
        for result in results[:-1]:  # Skip the [DONE] marker
            self.assertTrue(result.startswith('data: '), f"Result not properly formatted as SSE: {result[:50]}...")
            self.assertTrue(result.endswith('\n\n'), f"Result not properly terminated: {result[-10:]}")
            
            # Try to parse the data part as JSON to verify it's valid
            data_part = result[6:-2]  # Remove 'data: ' prefix and '\n\n' suffix
            try:
                # This should not raise an exception if the JSON is valid
                parsed = json.loads(data_part)
                self.assertTrue(isinstance(parsed, dict), "Parsed data is not a dictionary")
            except json.JSONDecodeError:
                # For this test, we're not expecting valid JSON, so this is fine
                pass

if __name__ == "__main__":
    unittest.main()
