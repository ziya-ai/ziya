"""
Utility functions for handling Nova model responses.
"""

from typing import Any
from langchain_core.messages import AIMessageChunk

def clean_nova_chunk(chunk: Any) -> str:
    """Removes empty brackets from Nova streaming chunks."""
    if isinstance(chunk, AIMessageChunk):
        content = chunk.content
        if isinstance(content, list) or isinstance(content, dict):
            # Handle list of content blocks
            return "".join([block['text'] for block in content if 'text' in block and block['text'].strip()])
        elif isinstance(content, str):
            # Handle nested content structure
            return "".join([block['text'] for block in content['content'] if 'text' in block and block['text'].strip()])
    elif isinstance(chunk, str):
        # Handle raw string chunks
        return chunk.strip("[]")
    return str(chunk)

def is_nova_chunk(chunk: Any) -> bool:
    """Detects Nova model chunks based on content structure."""
    if isinstance(chunk, AIMessageChunk):
        content = chunk.content
        if isinstance(content, list):
            return all(isinstance(block, dict) and 'text' in block for block in content)
        if isinstance(content, dict) and 'content' in content:
            blocks = content.get('content', [])
            return isinstance(blocks, list) and all(isinstance(block, dict) and 'text' in block 
                                                  for block in blocks)
    return False
