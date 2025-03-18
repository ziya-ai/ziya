from starlette.middleware.base import BaseHTTPMiddleware

class RequestSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            request.scope["max_request_size"] = 10 * 1024 * 1024  # 10MB
        return await call_next(request)
