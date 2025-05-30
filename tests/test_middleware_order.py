"""
Test that middleware order is correct.
"""

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.middleware import StreamingMiddleware, RequestSizeMiddleware, ErrorHandlingMiddleware

class MiddlewareExecutionOrderTest(unittest.TestCase):
    """Test the actual execution order of middleware."""
    
    def test_execution_order(self):
        """Test the actual execution order of middleware."""
        execution_order = []
        
        class FirstMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                execution_order.append("First-Request")
                response = await call_next(request)
                execution_order.append("First-Response")
                return response
        
        class SecondMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                execution_order.append("Second-Request")
                response = await call_next(request)
                execution_order.append("Second-Response")
                return response
        
        class ThirdMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                execution_order.append("Third-Request")
                response = await call_next(request)
                execution_order.append("Third-Response")
                return response
        
        app = FastAPI()
        
        @app.get("/test")
        def test_endpoint():
            execution_order.append("Endpoint")
            return {"message": "Test"}
        
        # Add middleware in order
        app.add_middleware(FirstMiddleware)
        app.add_middleware(SecondMiddleware)
        app.add_middleware(ThirdMiddleware)
        
        # Test the execution order
        client = TestClient(app)
        response = client.get("/test")
        
        # The expected order is:
        # 1. Third-Request (last added, first executed)
        # 2. Second-Request
        # 3. First-Request
        # 4. Endpoint
        # 5. First-Response (first added, last executed)
        # 6. Second-Response
        # 7. Third-Response
        self.assertEqual(execution_order, [
            "Third-Request",
            "Second-Request",
            "First-Request",
            "Endpoint",
            "First-Response",
            "Second-Response",
            "Third-Response"
        ])

    def test_streaming_middleware_execution(self):
        """Test that streaming middleware executes in the correct order."""
        execution_order = []
        
        class MockStreamingMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                execution_order.append("Streaming-Request")
                response = await call_next(request)
                execution_order.append("Streaming-Response")
                return response
        
        class MockRequestSizeMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                execution_order.append("RequestSize-Request")
                response = await call_next(request)
                execution_order.append("RequestSize-Response")
                return response
        
        class MockErrorHandlingMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                execution_order.append("ErrorHandling-Request")
                response = await call_next(request)
                execution_order.append("ErrorHandling-Response")
                return response
        
        app = FastAPI()
        
        @app.get("/stream")
        def stream_endpoint():
            execution_order.append("Endpoint")
            return {"message": "Test"}
        
        # Add middleware in the correct order
        app.add_middleware(MockStreamingMiddleware)
        app.add_middleware(MockRequestSizeMiddleware)
        app.add_middleware(MockErrorHandlingMiddleware)
        
        # Test the execution order
        client = TestClient(app)
        response = client.get("/stream", headers={"Accept": "text/event-stream"})
        
        # The expected order is:
        # 1. ErrorHandling-Request
        # 2. RequestSize-Request
        # 3. Streaming-Request
        # 4. Endpoint
        # 5. Streaming-Response
        # 6. RequestSize-Response
        # 7. ErrorHandling-Response
        self.assertEqual(execution_order, [
            "ErrorHandling-Request",
            "RequestSize-Request",
            "Streaming-Request",
            "Endpoint",
            "Streaming-Response",
            "RequestSize-Response",
            "ErrorHandling-Response"
        ])

if __name__ == "__main__":
    unittest.main()
