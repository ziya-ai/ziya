"""
DeepSeek R1 wrapper for handling reasoning content properly
"""
from app.agents.bedrock_wrapper import ThrottleSafeBedrock


class DeepSeekWrapper(ThrottleSafeBedrock):
    """DeepSeek R1 wrapper that handles reasoning content as thinking type"""
    
    def process_streaming_chunk(self, chunk):
        """Process streaming chunk and handle reasoning content"""
        if hasattr(chunk, 'get') and 'delta' in chunk:
            delta = chunk['delta']
            
            # Handle reasoning content as thinking type
            if 'reasoningContent' in delta and delta['reasoningContent'].get('text'):
                reasoning_text = delta['reasoningContent']['text']
                return {'type': 'thinking', 'content': reasoning_text}
            
            # Handle regular text content
            elif 'text' in delta:
                return {'type': 'text', 'content': delta['text']}
        
        # Fall back to parent processing
        return super().process_streaming_chunk(chunk)
