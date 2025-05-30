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

def extract_text_from_list_dict(content: str) -> Optional[str]:
    """Extract text from a string representation of a list of dicts."""
    try:
        # Try to match the text field in a list of dicts format
        pattern = r"'text':\s*['\"](.+?)['\"]"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            extracted = match.group(1)
            # Unescape the content
            extracted = extracted.replace("\\'", "'").replace('\\"', '"')
            return extracted
    except Exception as e:
        print(f"Error extracting text from list dict: {e}")
    return None

def extract_content_from_messages(content: str) -> Optional[str]:
    """Extract content from a string representation of a messages dict."""
    try:
        # Try to match the content field in a messages format
        pattern = r"content=['\"](.+?)['\"]"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            extracted = match.group(1)
            # Unescape the content
            extracted = extracted.replace("\\'", "'").replace('\\"', '"')
            
            # If the extracted content is itself a list dict, extract the text
            if "[{'type':" in extracted or "[{\\\'type\\\':" in extracted:
                inner_text = extract_text_from_list_dict(extracted)
                if inner_text:
                    return inner_text
            
            return extracted
    except Exception as e:
        print(f"Error extracting content from messages: {e}")
    return None

def extract_output_from_dict(content: str) -> Optional[str]:
    """Extract output from a string representation of an output dict."""
    try:
        # Try to match the output field in an output format
        pattern = r"'output':\s*['\"](.+?)['\"]"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            extracted = match.group(1)
            # Unescape the content
            extracted = extracted.replace("\\'", "'").replace('\\"', '"')
            
            # If the extracted content is itself a list dict, extract the text
            if "[{'type':" in extracted or "[{\\\'type\\\':" in extracted:
                inner_text = extract_text_from_list_dict(extracted)
                if inner_text:
                    return inner_text
            
            return extracted
    except Exception as e:
        print(f"Error extracting output from dict: {e}")
    return None

async def test_stream_chunks():
    """Test the streaming functionality with mock data."""
    print("Testing stream_chunks function with mock data...")
    
    # Initialize accumulated content
    accumulated_content = ""
    chunk_count = 0
    
    print("\nStreaming response chunks:")
    print("-" * 80)
    
    # Stream the response
    try:
        async for chunk in mock_astream({}):
            chunk_count += 1
            print(f"\nCHUNK #{chunk_count}")
            print("-" * 40)
            
            # Print raw chunk info for debugging
            print(f"Raw chunk type: {type(chunk)}")
            content = chunk
            
            # Print the raw content
            print(f"Raw content: {str(content)[:100]}...")
            
            # Try to parse the content if it's a string
            parsed_content = None
            if isinstance(content, str):
                # Case 1: String representation of a list of dicts with text field
                if "[{'type':" in content or "[{\\\'type\\\':" in content:
                    parsed_content = extract_text_from_list_dict(content)
                    if parsed_content:
                        print(f"Parsed as list dict: {parsed_content[:100]}...")
                
                # Case 2: String representation of dict with messages
                elif "{'messages':" in content:
                    parsed_content = extract_content_from_messages(content)
                    if parsed_content:
                        print(f"Parsed as messages dict: {parsed_content[:100]}...")
                
                # Case 3: String representation of dict with output
                elif "{'output':" in content:
                    parsed_content = extract_output_from_dict(content)
                    if parsed_content:
                        print(f"Parsed as output dict: {parsed_content[:100]}...")
            
            # Use parsed content if available, otherwise use the original content
            final_content = parsed_content if parsed_content else content
            if not isinstance(final_content, str):
                final_content = str(final_content)
            
            print(f"Final extracted content: {final_content[:100]}...")
            
            # Accumulate content
            if parsed_content:
                accumulated_content += parsed_content
            else:
                accumulated_content += "FAILED TO PARSE: " + content[:50] + "...\n"
    
    except Exception as e:
        print(f"Error during streaming: {str(e)}")
    
    print("\n" + "-" * 80)
    print("Final accumulated content:")
    print("-" * 80)
    print(accumulated_content[:500] + "..." if len(accumulated_content) > 500 else accumulated_content)
    print("-" * 80)

async def test_improved_extraction():
    """Test improved extraction methods."""
    print("Testing improved extraction methods...")
    
    # Initialize accumulated content
    accumulated_content = ""
    chunk_count = 0
    
    print("\nProcessing chunks:")
    print("-" * 80)
    
    # Stream the response
    try:
        async for chunk in mock_astream({}):
            chunk_count += 1
            print(f"\nCHUNK #{chunk_count}")
            print("-" * 40)
            
            content = None
            
            # Case 1: Messages dict with nested list dict
            if "{'messages':" in chunk and "content=" in chunk:
                try:
                    # Extract the content field
                    content_match = re.search(r"content=['\"](.+?)['\"]", chunk, re.DOTALL)
                    if content_match:
                        content_str = content_match.group(1)
                        # If content contains a list dict, extract the text
                        if "[{'type':" in content_str or "[{\\\'type\\\':" in content_str:
                            text_match = re.search(r"'text':\s*['\"](.+?)['\"]", content_str, re.DOTALL)
                            if text_match:
                                content = text_match.group(1).replace("\\'", "'").replace('\\"', '"')
                                print(f"Extracted from nested structure: {content[:100]}...")
                except Exception as e:
                    print(f"Error with messages extraction: {e}")
            
            # Case 2: Direct list dict
            elif "[{'type':" in chunk or "[{\\\'type\\\':" in chunk:
                try:
                    text_match = re.search(r"'text':\s*['\"](.+?)['\"]", chunk, re.DOTALL)
                    if text_match:
                        content = text_match.group(1).replace("\\'", "'").replace('\\"', '"')
                        print(f"Extracted from list dict: {content[:100]}...")
                except Exception as e:
                    print(f"Error with list dict extraction: {e}")
            
            # Case 3: Output dict with nested list dict
            elif "{'output':" in chunk:
                try:
                    # First try to find output with nested structure
                    output_match = re.search(r"'output':\s*['\"](.+?)['\"]", chunk, re.DOTALL)
                    if output_match:
                        output_str = output_match.group(1)
                        # If output contains a list dict, extract the text
                        if "[{'type':" in output_str or "[{\\\'type\\\':" in output_str:
                            text_match = re.search(r"'text':\s*['\"](.+?)['\"]", output_str, re.DOTALL)
                            if text_match:
                                content = text_match.group(1).replace("\\'", "'").replace('\\"', '"')
                                print(f"Extracted from nested output: {content[:100]}...")
                except Exception as e:
                    print(f"Error with output extraction: {e}")
            
            # Default case
            if content is None:
                content = f"FAILED TO EXTRACT: {chunk[:50]}..."
                print(f"Failed to extract content")
            
            # Accumulate content
            accumulated_content += content
    
    except Exception as e:
        print(f"Error during streaming: {str(e)}")
    
    print("\n" + "-" * 80)
    print("Final accumulated content:")
    print("-" * 80)
    print(accumulated_content[:500] + "..." if len(accumulated_content) > 500 else accumulated_content)
    print("-" * 80)

if __name__ == "__main__":
    asyncio.run(test_stream_chunks())
    print("\n\n" + "=" * 80 + "\n")
    asyncio.run(test_improved_extraction())
