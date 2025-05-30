class StreamingMiddleware:
    """Middleware for handling streaming responses."""
    
    def __init__(self, app):
        super().__init__(app)
    
    async def safe_stream(self, original_iterator):
        """
        Safely process a stream of chunks.
        """
        try:
            async for chunk in original_iterator:
                # Process the chunk
                try:
                    # Handle message chunks
                    if hasattr(chunk, 'message'):
                        if hasattr(chunk.message, 'content'):
                            content = chunk.message.content
                            if content:
                                chunk_content = content
                                if isinstance(content, dict):
                                    # Preserve structured content
                                    yield f"data: {content}\n\n"
                                else:
                                    yield f"data: {content}\n\n"
                    
                    # Last resort: convert to string
                    str_chunk = str(chunk)
                    chunk_content = str_chunk
                    if str_chunk:
                        yield f"data: {str_chunk}\n\n"
                    
                except Exception as chunk_error:
                    print(f"Error processing chunk: {str(chunk_error)}")
        except Exception as e:
            print(f"Stream processing error: {str(e)}")
