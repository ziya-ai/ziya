"""
Enhanced parse_output function to handle various response types.
"""

import json
import re
from typing import Any, Dict, Optional, Union
from langchain_core.agents import AgentFinish
from app.utils.logging_utils import logger
from app.utils.code_util import clean_backtick_sequences

def extract_text_from_json(content):
    """Extract text from various JSON formats."""
    if not isinstance(content, str):
        return content
        
    try:
        # Try to parse as JSON
        if content.startswith('[{') and ('"text"' in content or "'text'" in content):
            # Handle array of objects with text field
            json_content = content.replace("'", '"')
            parsed = json.loads(json_content)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and 'text' in parsed[0]:
                return parsed[0]['text']
        
        # Handle nested message format
        if content.startswith('{') and ('messages' in content or 'output' in content):
            # Replace single quotes with double quotes for JSON parsing
            content_fixed = content.replace("'", '"')
            try:
                parsed = json.loads(content_fixed)
                
                # Extract from messages
                if isinstance(parsed, dict) and 'messages' in parsed:
                    if isinstance(parsed['messages'], list) and parsed['messages']:
                        message = parsed['messages'][0]
                        if isinstance(message, dict) and 'content' in message:
                            inner_content = message['content']
                            # Try to parse inner content
                            return extract_text_from_json(inner_content)
                
                # Extract from output
                if isinstance(parsed, dict) and 'output' in parsed:
                    if isinstance(parsed['output'], dict) and 'output' in parsed['output']:
                        inner_content = parsed['output']['output']
                        # Try to parse inner content
                        return extract_text_from_json(inner_content)
            except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
                # Try regex approach if JSON parsing fails
                ai_message_match = re.search(r"AIMessage\(content=[\"'](\[\{.*?\}\])[\"']", content)
                if ai_message_match:
                    inner_content = ai_message_match.group(1)
                    # Replace single quotes with double quotes
                    inner_json = inner_content.replace("'", '"')
                    try:
                        parsed = json.loads(inner_json)
                        if isinstance(parsed, list) and parsed and 'text' in parsed[0]:
                            return parsed[0]['text']
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
        pass
        
    return content

def parse_output(message: Any) -> AgentFinish:
    """Parse and sanitize the output from the language model."""
    logger.info("parse_output called with type: %s", type(message))
    try:
        logger.info(f"Raw response from model: {message}")
        # Get the content based on the object type
        content = None
        
        # Handle string objects first
        if isinstance(message, str):
            content = message
        elif hasattr(message, 'text'):
            # Check if text is a method or an attribute
            if callable(message.text):
                content = message.text()
            else:
                content = message.text
        elif hasattr(message, 'content'):
            # Check if content is a method or an attribute
            if callable(message.content):
                content = message.content()
            else:
                content = message.content
        elif hasattr(message, 'message'):  # For ZiyaMessageChunk
            content = message.message
        else:
            content = str(message)

        # Ensure content is a string
        if not isinstance(content, str):
            logger.warning(f"Content is not a string, converting: {type(content)}")
            content = str(content)

        # Try to extract text from JSON formats
        cleaned_content = extract_text_from_json(content)
        
        logger.info(f"parse_output extracted content size: {len(content)} chars, cleaned size: {len(str(cleaned_content))} chars")
        
        # Return the parsed output
        return AgentFinish(
            return_values={"output": cleaned_content},
            log=content,
        )
    except Exception as e:
        logger.error(f"Error in parse_output initial processing: {str(e)}")
        # Provide a safe fallback
        return AgentFinish(return_values={"output": f"Error processing response: {str(e)}"}, 
                          log=f"Error processing response: {str(e)}")

    if not content:
        return AgentFinish(return_values={"output": ""}, log="")

    try:
        # Check if this is an error message
        error_data = json.loads(content)
        if error_data.get('error') == 'validation_error':
            logger.warning(f"Validation error detected: {error_data}")
            return AgentFinish(
                return_values={"output": f"Error: {error_data.get('detail', 'Unknown validation error')}"}, 
                log=content
            )
        elif error_data.get('error') == 'throttling_error':
            logger.warning(f"Throttling error detected: {error_data}")
            return AgentFinish(
                return_values={"output": f"Error: {error_data.get('detail', 'Rate limit exceeded')}"}, 
                log=content
            )
        elif error_data.get('error'):
            logger.warning(f"Error response detected: {error_data}")
            return AgentFinish(
                return_values={"output": f"Error: {error_data.get('detail', 'Unknown error')}"}, 
                log=content
            )
    except (json.JSONDecodeError, AttributeError):
        # Not a JSON error message, continue processing
        pass

    try:
        # If not a diff or error, clean and return the content
        text = clean_backtick_sequences(content)
        logger.info(f"parse_output extracted content size: {len(content)} chars, cleaned size: {len(text)} chars")
        return AgentFinish(return_values={"output": text}, log=text)
    except Exception as e:
        logger.error(f"Error in parse_output content processing: {str(e)}")
        # Provide a safe fallback
        return AgentFinish(return_values={"output": content}, log=content)
