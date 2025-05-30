class StreamingMiddleware:
    """Middleware for handling streaming responses."""
    
    # Class-level variables for repetition detection
    _recent_lines = []
    _max_repetitions = 10
    
    def __init__(self, app):
        super().__init__(app)
    
    async def process_request(self, request, call_next):
        # For non-streaming requests, just pass through
        return await call_next(request)
    
    def _is_repetitive(self, content: str) -> bool:
        """Check if content contains repetitive lines that exceed threshold."""
        return any(content.count(line) > self._max_repetitions for line in set(content.split('\n')) if line.strip())
    
    async def safe_stream(self, original_iterator):
        """
        Safely process a stream of chunks.
        
        Args:
            original_iterator: The original stream iterator
            
        Yields:
            Processed chunks as SSE data
        """
        # Reset repetition detection state for this stream
        self._recent_lines = []
        accumulated_content = ""
        try:
            async for chunk in original_iterator:
                # Log chunk info for debugging
                
                thinking_mode_enabled = False
                
                chunk_content = ""
                # Process the chunk
                try:
                    # Handle AIMessageChunk objects
                    if hasattr(chunk, 'content'):
                        raw_content = chunk.content
                        
                        chunk_content = raw_content
                        
                        # Check if this might be thinking mode content
                        is_structured = isinstance(raw_content, dict)
                        
                        if is_structured:
                            # For structured content like thinking mode, preserve the structure
                            yield f"data: {raw_content}\n\n"
                            chunk_content = str(raw_content)
                        else:
                            # For simple string content
                            content = str(raw_content)
                            yield f"data: {content}\n\n"
                            
                    elif hasattr(chunk, 'message'):
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
                    if str_chunk: # Avoid empty data chunks
                        # Check for repetitive content
                        accumulated_content += str_chunk
                        
                        # Track lines for repetition detection
                        lines = str_chunk.split('\n')
                        for line in lines:
                            if line.strip():  # Only track non-empty lines
                                self._recent_lines.append(line)
                                # Keep only recent lines
                                if len(self._recent_lines) > 100:
                                    self._recent_lines.pop(0)
                        
                        # Check if any line repeats too many times
                        if any(self._recent_lines.count(line) > self._max_repetitions for line in set(self._recent_lines)):
                            print("Detected repetitive content in stream, interrupting")
                            # Send warning message
                            warning_msg = {
                                "warning": "repetitive_content",
                                "detail": "Response was interrupted because repetitive content was detected."
                            }
                            yield f"data: {warning_msg}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        else:
                            yield f"data: {str_chunk}\n\n"
                    
                except Exception as chunk_error:
                    print(f"Error processing chunk: {str(chunk_error)}")
        except Exception as e:
            print(f"Stream processing error: {str(e)}")
