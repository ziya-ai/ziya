#!/usr/bin/env python3
import asyncio
import json
import sys
import os
import re
import ast
from typing import Any, Dict, List, Optional

# Add the project root to the path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.agents.agent import agent_executor

def extract_text_from_list_dict(content: str) -> Optional[str]:
    """Extract text from a string representation of a list of dicts."""
    try:
        # Try to match the text field in a list of dicts format
        pattern = r"'text': ['\"](.+?)['\"]"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Error extracting text from list dict: {e}")
    return None

def extract_content_from_messages(content: str) -> Optional[str]:
    """Extract content from a string representation of a messages dict."""
    try:
        # Try to match the content field in a messages format
        pattern = r"content='(.+?)'"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Error extracting content from messages: {e}")
    return None

def extract_output_from_dict(content: str) -> Optional[str]:
    """Extract output from a string representation of an output dict."""
    try:
        # Try to match the output field in an output format
        pattern = r"'output': '(.+?)'"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Error extracting output from dict: {e}")
    return None

def try_ast_eval(content: str) -> Any:
    """Try to safely evaluate a string as a Python literal."""
    try:
        return ast.literal_eval(content)
    except (SyntaxError, ValueError) as e:
        print(f"Error evaluating content as Python literal: {e}")
        return None

async def test_stream_chunks():
    """Test the streaming functionality with a simple query."""
    print("Testing stream_chunks function...")
    
    # Simple test query
    test_body = {
        "question": "What is Ziya?",
        "chat_history": [],
        "config": {"files": ["README.md"]}
    }
    
    # Initialize accumulated content
    accumulated_content = ""
    chunk_count = 0
    
    print("\nStreaming response chunks:")
    print("-" * 80)
    
    # Stream the response
    try:
        async for chunk in agent_executor.astream(test_body):
            chunk_count += 1
            print(f"\nCHUNK #{chunk_count}")
            print("-" * 40)
            
            # Print raw chunk info for debugging
            print(f"Raw chunk type: {type(chunk)}")
            print(f"Has content attr: {hasattr(chunk, 'content')}")
            print(f"Has message attr: {hasattr(chunk, 'message')}")
            if hasattr(chunk, 'message'):
                print(f"Message has content attr: {hasattr(chunk.message, 'content')}")
            
            # Try to extract content using different methods
            content = None
            extraction_method = "unknown"
            
            # Method 1: Direct content attribute
            if hasattr(chunk, 'content'):
                content = chunk.content
                if callable(content):
                    content = content()
                extraction_method = "direct content attribute"
            
            # Method 2: Message with content
            elif hasattr(chunk, 'message') and hasattr(chunk.message, 'content'):
                content = chunk.message.content
                if callable(content):
                    content = content()
                extraction_method = "message content attribute"
            
            # Method 3: Dictionary with output
            elif isinstance(chunk, dict) and 'output' in chunk:
                if isinstance(chunk['output'], str):
                    content = chunk['output']
                elif isinstance(chunk['output'], dict) and 'output' in chunk['output']:
                    content = chunk['output']['output']
                else:
                    content = str(chunk['output'])
                extraction_method = "dictionary with output"
            
            # Method 4: Dictionary with messages
            elif isinstance(chunk, dict) and 'messages' in chunk and chunk['messages']:
                message = chunk['messages'][0]
                if hasattr(message, 'content'):
                    content = message.content
                else:
                    content = str(message)
                extraction_method = "dictionary with messages"
            
            # Method 5: Convert to string
            else:
                content = str(chunk)
                extraction_method = "string conversion"
            
            # Print the raw content
            print(f"Raw content ({extraction_method}): {str(content)[:100]}...")
            
            # Try to parse the content if it's a string
            parsed_content = None
            if isinstance(content, str):
                # Case 1: String representation of a list of dicts with text field
                if content.startswith("[{'type':") or content.startswith("[{\\\'type\\':"):
                    parsed_content = extract_text_from_list_dict(content)
                    if parsed_content:
                        print(f"Parsed as list dict: {parsed_content[:100]}...")
                
                # Case 2: String representation of dict with messages
                elif content.startswith("{'messages':"):
                    parsed_content = extract_content_from_messages(content)
                    if parsed_content:
                        print(f"Parsed as messages dict: {parsed_content[:100]}...")
                
                # Case 3: String representation of dict with output
                elif content.startswith("{'output':"):
                    parsed_content = extract_output_from_dict(content)
                    if parsed_content:
                        print(f"Parsed as output dict: {parsed_content[:100]}...")
                
                # Case 4: Try ast.literal_eval as a last resort
                if not parsed_content:
                    evaluated = try_ast_eval(content)
                    if evaluated:
                        print(f"Evaluated with ast: {str(evaluated)[:100]}...")
                        if isinstance(evaluated, dict):
                            if 'messages' in evaluated and evaluated['messages']:
                                message = evaluated['messages'][0]
                                if hasattr(message, 'content'):
                                    parsed_content = message.content
                                    print(f"Extracted from evaluated messages: {parsed_content[:100]}...")
                            elif 'output' in evaluated:
                                if isinstance(evaluated['output'], str):
                                    parsed_content = evaluated['output']
                                    print(f"Extracted from evaluated output (str): {parsed_content[:100]}...")
                                elif isinstance(evaluated['output'], dict) and 'output' in evaluated['output']:
                                    parsed_content = evaluated['output']['output']
                                    print(f"Extracted from evaluated output (dict): {parsed_content[:100]}...")
                        elif isinstance(evaluated, list) and evaluated and isinstance(evaluated[0], dict):
                            if 'text' in evaluated[0]:
                                parsed_content = evaluated[0]['text']
                                print(f"Extracted from evaluated list: {parsed_content[:100]}...")
            
            # Use parsed content if available, otherwise use the original content
            final_content = parsed_content if parsed_content else content
            if not isinstance(final_content, str):
                final_content = str(final_content)
            
            print(f"Final extracted content: {final_content[:100]}...")
            
            # Accumulate content
            accumulated_content += final_content
    
    except Exception as e:
        print(f"Error during streaming: {str(e)}")
    
    print("\n" + "-" * 80)
    print("Final accumulated content:")
    print("-" * 80)
    print(accumulated_content[:500] + "..." if len(accumulated_content) > 500 else accumulated_content)
    print("-" * 80)

if __name__ == "__main__":
    asyncio.run(test_stream_chunks())
