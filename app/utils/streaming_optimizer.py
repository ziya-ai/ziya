"""
Streaming Response Optimizer
Fixes chunking issues identified in streaming analysis
"""

import re
import time
from typing import Generator, Optional

class StreamingContentOptimizer:
    """Optimizes content streaming to prevent mid-word splits"""
    
    def __init__(self, min_chunk_size: int = 15, max_buffer_size: int = 500):
        self.buffer = ""
        self.min_chunk_size = min_chunk_size
        self.max_buffer_size = max_buffer_size
        self.word_boundary = re.compile(r'(\s+)')
        self.in_code_block = False
        
    def add_content(self, content: str) -> Generator[str, None, None]:
        """Add content and yield optimized chunks"""
        self.buffer += content
        
        # Update code block state
        self._update_code_block_state(content)
        
        # NEVER flush in the middle of a code block
        if self.in_code_block:
            # Only flush if buffer is extremely large (safety valve)
            if len(self.buffer) > 5000:
                yield from self._flush_complete_words()
            return
        
        # Force flush if buffer gets too large
        if len(self.buffer) > self.max_buffer_size:
            yield from self._flush_complete_words()
            
        # Check if we have enough content to send
        elif len(self.buffer) >= self.min_chunk_size:
            yield from self._flush_complete_words()
    
    def _update_code_block_state(self, content: str) -> None:
        """Track if we're inside a code block"""
        # Count all ``` markers in the entire buffer, not just new content
        # This ensures we have accurate state even if chunks arrive fragmented
        marker_count = self.buffer.count('```')
        self.in_code_block = (marker_count % 2) == 1
    
    def _flush_complete_words(self) -> Generator[str, None, None]:
        """Flush complete words from buffer"""
        if not self.buffer.strip():
            return
            
        # Split on word boundaries but keep delimiters
        parts = self.word_boundary.split(self.buffer)
        
        if len(parts) > 2:  # We have at least one complete word
            # Send all but the last part (which might be incomplete)
            complete_parts = parts[:-1]
            chunk_to_send = ''.join(complete_parts)
            
            if chunk_to_send.strip():
                yield chunk_to_send
                self.buffer = parts[-1]  # Keep the last part
        
    def flush_remaining(self) -> Optional[str]:
        """Flush any remaining content"""
        if self.buffer.strip():
            content = self.buffer
            self.buffer = ""
            return content
        return None

    def is_in_code_block(self, accumulated_text: str) -> tuple[bool, str]:
        """
        Check if the accumulated text ends in the middle of a code block.
        
        Returns:
            (is_in_block, block_type): True if in block, with block type
        """
        lines = accumulated_text.split('\n')
        open_blocks = []
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('```'):
                if stripped == '```':
                    # Closing block
                    if open_blocks:
                        open_blocks.pop()
                else:
                    # Opening block
                    block_type = stripped[3:].strip() or 'text'
                    open_blocks.append(block_type)
        
        if open_blocks:
            return True, open_blocks[-1]  # Return the most recent open block type
        return False, None
    
    def count_code_block_markers(self, text: str) -> int:
        """Count the number of ``` markers in text."""
        return text.count('```')
    
    def has_incomplete_code_block(self, text: str) -> bool:
        """Check if text has an odd number of code block markers (incomplete)."""
        return self.count_code_block_markers(text) % 2 == 1
def optimize_streaming_chunk(content: str, optimizer: StreamingContentOptimizer) -> Generator[dict, None, None]:
    """
    Optimize a streaming content chunk to prevent mid-word splits
    """
    chunks = list(optimizer.add_content(content))
    
    for chunk in chunks:
        yield {
            'type': 'text',
            'content': chunk,
            'timestamp': f"{int(time.time() * 1000)}ms"
        }
