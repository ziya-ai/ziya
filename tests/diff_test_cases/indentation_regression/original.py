class StreamingMiddleware:
    """Middleware for handling streaming responses."""
    
    def __init__(self, app):
        super().__init__(app)
    
    async def process_request(self, request, call_next):
        # For non-streaming requests, just pass through
        return await call_next(request)
    
    async def safe_stream(self, original_iterator):
        """
        Safely process a stream of chunks.
        
        Args:
            original_iterator: The original stream iterator
            
        Yields:
            Processed chunks as SSE data
        """
        try:
            async for chunk in original_iterator:
                # Log chunk info for debugging
                
                thinking_mode_enabled = False
                
                # Process the chunk
                try:
                    # Handle AIMessageChunk objects
                    if hasattr(chunk, 'content'):
                        raw_content = chunk.content
                        
                        # Check if this might be thinking mode content
                        is_structured = isinstance(raw_content, dict)
                        
                        if is_structured:
                            # For structured content like thinking mode, preserve the structure
                            yield f"data: {raw_content}\n\n"
                        else:
                            # For simple string content
                            content = str(raw_content)
                            yield f"data: {content}\n\n"
                            
                    elif hasattr(chunk, 'message'):
                        if hasattr(chunk.message, 'content'):
                            content = chunk.message.content
                            if content:
                                if isinstance(content, dict):
                                    # Preserve structured content
                                    yield f"data: {content}\n\n"
                                else:
                                    yield f"data: {content}\n\n"
                    
                    # Last resort: convert to string
                    str_chunk = str(chunk)
                    if str_chunk: # Avoid empty data chunks
                        yield f"data: {str_chunk}\n\n"
                    
                except Exception as chunk_error:
                    print(f"Error processing chunk: {str(chunk_error)}")
        except Exception as e:
            print(f"Stream processing error: {str(e)}")
