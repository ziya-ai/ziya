"""
Formatter for Amazon Nova models.
Handles the specific message format required by Nova models.
"""

from typing import Dict, List, Any, Optional, Tuple
import json
from app.utils.logging_utils import logger

class NovaFormatter:
    """
    Formatter class for Amazon Nova models.
    Handles the specific message format required by Nova models.
    """

    @staticmethod
    def format_system_prompt(system_prompt: str) -> List[Dict[str, str]]:
        """Format system prompt for Nova models."""
        return [{"text": system_prompt}] if system_prompt else []

    @staticmethod
    def format_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format message history for Nova models."""
        formatted_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            # Ensure role is either 'user' or 'assistant' for Nova API
            if role not in ["user", "assistant"]:
                role = "user"  # Default to user for any other role

            # Format the content as Nova expects
            if isinstance(content, str):
                # Ensure content is not empty for Nova API
                if not content.strip():
                    content = "Hello"  # Use a default non-empty message
                formatted_content = [{"text": content}]
            else:
                # Handle already formatted content (for multimodal)
                formatted_content = content
                # Ensure there's at least one content block with non-empty text
                has_valid_text = False
                for block in formatted_content:
                    if "text" in block and block["text"].strip():
                        has_valid_text = True
                        break
                if not has_valid_text and formatted_content:
                    formatted_content[0]["text"] = "Hello"  # Add text to first block if all empty

            formatted_messages.append({
                "role": role,
                "content": formatted_content
            })
            
        # Debug log the formatted messages
        for i, msg in enumerate(formatted_messages):
            logger.info(f"Formatted message {i}: role={msg['role']}, content blocks={len(msg['content'])}")
            for j, block in enumerate(msg['content']):
                if 'text' in block:
                    text_preview = block['text'][:50] + "..." if len(block['text']) > 50 else block['text']
                    logger.info(f"  Block {j} text: {text_preview}")
                else:
                    logger.info(f"  Block {j} has no text field, keys: {list(block.keys())}")
                    
        return formatted_messages

    @staticmethod
    def format_inference_params(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Format inference parameters for Nova models.
        
        Returns:
            Tuple containing:
            - inference_config: Parameters for the main inferenceConfig
            - additional_model_fields: Parameters for additionalModelRequestFields
        """
        inference_config = {}

        # Map standard parameters to Nova format
        if "max_tokens" in params:
            inference_config["maxTokens"] = params["max_tokens"]
        if "temperature" in params:
            inference_config["temperature"] = params["temperature"]
        if "top_p" in params:
            inference_config["topP"] = params["top_p"]
        if "stop_sequences" in params:
            inference_config["stopSequences"] = params["stop_sequences"]

        # Handle topK separately for Converse API
        additional_model_fields = {}
        if "top_k" in params:
            additional_model_fields["inferenceConfig"] = {"topK": params["top_k"]}

        return inference_config, additional_model_fields

    @staticmethod
    def parse_response(response: Dict[str, Any]) -> str:
        """Parse Nova response to extract text content."""
        try:
            logger.info(f"=== NOVA FORMATTER parse_response START ===")
            logger.info(f"Response type: {type(response)}")
            logger.info(f"Response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
            
            # Log the first part of the response for debugging
            response_preview = str(response)[:500] + "..." if len(str(response)) > 500 else str(response)
            logger.info(f"Response preview: {response_preview}")

            # Handle Converse API response format
            if "output" in response and "message" in response["output"]:
                logger.info("Found output.message structure")
                message = response["output"]["message"]
                logger.info(f"Message keys: {list(message.keys())}")
                
                if "content" in message and len(message["content"]) > 0:
                    logger.info(f"Found content array with {len(message['content'])} items")
                    content_block = message["content"][0]
                    logger.info(f"Content block keys: {list(content_block.keys())}")
                    
                    if "text" in content_block:
                        text = content_block["text"]
                        logger.info(f"Extracted text of length: {len(text)}")
                        logger.info(f"=== NOVA FORMATTER parse_response END ===")
                        return text

            # Handle streaming response format
            if "contentBlockDelta" in response:
                logger.info("Found contentBlockDelta structure")
                delta = response.get("contentBlockDelta", {})
                logger.info(f"Delta keys: {list(delta.keys())}")
                
                if "delta" in delta and "text" in delta["delta"]:
                    text = delta["delta"]["text"]
                    logger.info(f"Extracted text from delta of length: {len(text)}")
                    logger.info(f"=== NOVA FORMATTER parse_response END ===")
                    return text

            # Handle InvokeModel response format
            if "output" in response:
                logger.info("Found output structure")
                output = response["output"]
                
                if isinstance(output, dict) and "message" in output:
                    logger.info("Found output.message structure")
                    message = output["message"]
                    logger.info(f"Message keys: {list(message.keys())}")
                    
                    if "content" in message and len(message["content"]) > 0:
                        logger.info(f"Found content array with {len(message['content'])} items")
                        
                        for i, content_item in enumerate(message["content"]):
                            logger.info(f"Content item {i} keys: {list(content_item.keys())}")
                            
                            if "text" in content_item:
                                text = content_item["text"]
                                logger.info(f"Extracted text from content item {i} of length: {len(text)}")
                                logger.info(f"=== NOVA FORMATTER parse_response END ===")
                                return text

            # If we can't extract text in a structured way, return the raw response
            logger.warning(f"Could not parse Nova response structure, returning raw response")
            logger.info(f"=== NOVA FORMATTER parse_response END with fallback ===")
            return str(response)
        except Exception as e:
            logger.error(f"Error parsing Nova response: {e}")
            logger.error(f"Response that caused error: {str(response)[:500]}...")
            logger.info(f"=== NOVA FORMATTER parse_response END with error ===")
            return f"Error parsing response: {str(e)}"
