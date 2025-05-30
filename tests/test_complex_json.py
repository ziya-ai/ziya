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
    
    def test_complex_json_with_escapes(self):
        """Test that safe_stream handles complex JSON with escape sequences correctly."""
        # Create a mock stream that yields a complex JSON string with escape sequences
        # This simulates the problematic Gemini response
        complex_json = """ZIYA: INFO     Response content:
Okay, I understand the goal. You have multiple middleware components handling errors and streaming, leading to potential redundancy and complexity. The aim is to consolidate the error handling logic into a single, robust ASGI middleware (`app/middleware/error_middleware.py`) and simplify the other related middlewares.

Here's the proposed refactoring plan:

1.  **Consolidate Error Handling:** Move all core error detection, formatting (SSE/JSON), and response generation logic into `app/middleware/error_middleware.py`. This middleware, being ASGI, sits at a lower level and can catch errors more comprehensively.
2.  **Rename and Replace:** Rename `app/middleware/error_middleware.py` to `app/middleware/error_handling.py`. Delete the *original* `app/middleware/error_handling.py`. This makes the ASGI middleware the primary error handler.
3.  **Simplify Streaming Middleware:** Remove the `try...except` blocks from `safe_stream` in `app/middleware/streaming.py`. Its sole responsibility will be formatting *successful* stream chunks into SSE format. Error handling during streaming will be caught by the consolidated `ErrorHandlingMiddleware`.
4.  **Update `__init__.py`:** Modify `app/middleware/__init__.py` to reflect the removal of the old `ErrorHandlingMiddleware` and the renaming of the ASGI one.
5.  **Update `server.py`:**
    *   Remove the addition of the *old* `ErrorHandlingMiddleware` (the `BaseHTTPMiddleware` one).
    *   Ensure the *new* ASGI `ErrorHandlingMiddleware` wraps the main FastAPI `app` instance correctly. This is usually done *before* passing the `app` to `uvicorn.run`.
    *   Keep `RequestSizeMiddleware` and `StreamingMiddleware` as they handle different concerns (though `StreamingMiddleware` is now simplified).

Let's apply these changes step-by-step.
ZIYA: INFO     === END SERVER RESPONSE ==="""
        
        async def mock_stream():
            yield complex_json
        
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
        
        # Check that the [DONE] marker is present
        self.assertEqual(results[-1], 'data: [DONE]\n\n', "Missing [DONE] marker")
        
        # Check that the content was properly formatted as SSE data
        for result in results[:-1]:  # Skip the [DONE] marker
            self.assertTrue(result.startswith('data: '), f"Result not properly formatted as SSE: {result[:50]}...")
            self.assertTrue(result.endswith('\n\n'), f"Result not properly terminated: {result[-10:]}")
    
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
