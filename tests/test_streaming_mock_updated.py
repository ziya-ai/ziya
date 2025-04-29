#!/usr/bin/env python3
import asyncio
import json
import sys
import os
import re
from typing import Any, Dict, List, Optional, AsyncGenerator

# Mock response chunks that simulate what we're seeing in the logs
MOCK_CHUNKS = [
    "{'messages': [AIMessage(content='[{\\'type\\': \\'text\\', \\'text\\': \"It seems like you\\'ve provided a document outlining issues and recommendations for improving the Ziya diff apply code, particularly focusing on the force difflib mode.\"}]', additional_kwargs={}, response_metadata={})]}",
    "[{'type': 'text', 'text': 'It seems like you\\'ve provided a document outlining issues and recommendations for improving the difflib implementation in the Ziya project.', 'index': 0}]",
    "{'output': {'output': '[{\\'type\\': \\'text\\', \\'text\\': \"Based on the analysis provided, here are the key issues and recommendations for the Ziya diff apply code:\\n\\n1. **Current Implementation Structure**:\\n   - There are multiple difflib-related implementations\\n   - Main files: `app/utils/diff_utils/application/difflib_apply.py` and `app/utils/diff_utils/pipeline/pipeline_manager.py`\\n   - Several unused/experimental files\\n   - Special case handlers in `app/utils/difflib_fixes.py`\\n\\n2. **Key Issues in Force Difflib Mode**:\\n   - Invisible Unicode Characters handling\\n   - Already Applied Detection problems\\n   - Escape Sequence Handling issues\\n   - Line Calculation problems\\n   - Network Diagram Plugin failures\\n\\n3. **Recommendations**:\\n   - Clean up unused code\\n   - Improve Unicode character handling\\n   - Fix already applied detection\\n   - Enhance escape sequence handling\\n   - Fix line calculation issues\\n   - Improve logging and debugging\\n   - Consolidate special case handlers\\n\\nThese improvements should help the force difflib mode handle more test cases successfully, especially those involving text corruption or misplacement.\"}]', additional_kwargs={}, response_metadata={})]}"
]

async def mock_astream(body: Dict) -> AsyncGenerator[str, None]:
    """Mock the agent_executor.astream method."""
    for chunk in MOCK_CHUNKS:
        yield chunk
        await asyncio.sleep(0.5)  # Simulate some delay between chunks

def extract_nested_text(content: str) -> Optional[str]:
    """Extract text from nested structures."""
    # Try to extract from messages dict with nested list dict
    if "{'messages':" in content and "content=" in content:
        try:
            # Extract the content field
            content_match = re.search(r"content=['\"](.+?)['\"]", content, re.DOTALL)
            if content_match:
                content_str = content_match.group(1)
                # If content contains a list dict, extract the text
                if "[{'type':" in content_str or "[{\\\'type\\\':" in content_str:
                    text_match = re.search(r"'text':\s*['\"](.+?)['\"]", content_str, re.DOTALL)
                    if text_match:
                        return text_match.group(1).replace("\\'", "'").replace('\\"', '"')
        except Exception:
            pass
    
    # Try to extract from output dict with nested list dict
    elif "{'output':" in content:
        try:
            # First try to find output with nested structure
            output_match = re.search(r"'output':\s*['\"](.+?)['\"]", content, re.DOTALL)
            if output_match:
                output_str = output_match.group(1)
                # If output contains a list dict, extract the text
                if "[{'type':" in output_str or "[{\\\'type\\\':" in output_str:
                    text_match = re.search(r"'text':\s*['\"](.+?)['\"]", output_str, re.DOTALL)
                    if text_match:
                        return text_match.group(1).replace("\\'", "'").replace('\\"', '"')
        except Exception:
            pass
    
    # Try to extract directly from list dict
    elif "[{'type':" in content or "[{\\\'type\\\':" in content:
        try:
            text_match = re.search(r"'text':\s*['\"](.+?)['\"]", content, re.DOTALL)
            if text_match:
                return text_match.group(1).replace("\\'", "'").replace('\\"', '"')
        except Exception:
            pass
    
    return None

async def test_updated_stream_chunks():
    """Test the updated streaming functionality with mock data."""
    print("Testing updated stream_chunks function...")
    
    # Initialize accumulated content
    accumulated_content = ""
    min_chunk_size = 50  # Minimum characters to send as a chunk
    chunk_count = 0
    sent_count = 0
    
    print("\nProcessing chunks:")
    print("-" * 80)
    
    # Stream the response
    try:
        async for chunk in mock_astream({}):
            chunk_count += 1
            print(f"\nCHUNK #{chunk_count}")
            print("-" * 40)
            print(f"Raw chunk: {chunk[:100]}...")
            
            # Extract content from the chunk
            content = extract_nested_text(chunk)
            
            if content:
                print(f"Extracted content: {content[:100]}...")
            else:
                content = str(chunk)
                print(f"Using original content: {content[:100]}...")
            
            # Accumulate content
            if content:
                accumulated_content += content
            
            # Only send if we have accumulated enough content
            if len(accumulated_content) >= min_chunk_size:
                sent_count += 1
                print(f"\nSENDING CHUNK #{sent_count}")
                print(f"Content length: {len(accumulated_content)}")
                print(f"Content: {accumulated_content[:100]}...")
                
                # In the real server, this would be:
                # response_json = json.dumps({"text": accumulated_content})
                # yield f"data: {response_json}\n\n"
                
                # Reset accumulated content
                accumulated_content = ""
        
        # Send any remaining accumulated content
        if accumulated_content:
            sent_count += 1
            print(f"\nSENDING FINAL CHUNK #{sent_count}")
            print(f"Content length: {len(accumulated_content)}")
            print(f"Content: {accumulated_content[:100]}...")
    
    except Exception as e:
        print(f"Error during streaming: {str(e)}")
    
    print("\n" + "-" * 80)
    print("Final accumulated content:")
    print("-" * 80)
    print(accumulated_content[:500] + "..." if len(accumulated_content) > 500 else accumulated_content)
    print("-" * 80)

if __name__ == "__main__":
    asyncio.run(test_updated_stream_chunks())
