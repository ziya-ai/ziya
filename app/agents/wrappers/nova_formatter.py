"""
Formatter for Amazon Nova models.
Handles the specific message format required by Nova models.
"""

from typing import Dict, List, Any, Optional, Tuple
import json
from app.utils.logging_utils import logger
from langchain_core.messages import AIMessageChunk

class NovaFormatter:
    """
    Formatter class for Amazon Nova models.
    Handles the specific message format required by Nova models.
    """
    
    @staticmethod
    def _clean_nova_response(text: str) -> str:
        """
        Clean up Nova Pro responses by removing empty brackets at the beginning and end.
        
        Args:
            text: The text to clean
            
        Returns:
            str: The cleaned text
        """
        # Remove empty brackets at the beginning of the text
        if text.startswith("[]"):
            text = text[2:]
            
        # Remove empty brackets at the end of the text
        while text.endswith("[]"):
            text = text[:-2]

            
        # Handle the case where there are multiple empty brackets at the end
        if text.endswith("][]"):
            text = text[:-3] + "]"
            
        return text

    @staticmethod
    def format_system_prompt(system_prompt: str) -> List[Dict[str, str]]:
        """Format system prompt for Nova models."""
        return [{"text": system_prompt}] if system_prompt else []

    @staticmethod
    def clean_content_blocks(blocks: List[Any]) -> List[Any]:
        """Removes empty content blocks from Nova-style responses"""
        cleaned = []
        for block in blocks:
            if isinstance(block, dict) and 'text' in block:
                original_text = block['text']
                cleaned_text = original_text.strip("[]")
                if cleaned_text:
                    # Only keep blocks with non-empty text after cleaning
                    cleaned.append({'text': cleaned_text})
                else:
                    logger.debug("Removed empty content block from Nova response")
            elif isinstance(block, str):
                cleaned_text = block.strip("[]")
                if cleaned_text:
                    cleaned.append(cleaned_text)
            else:
                cleaned.append(block)
        return cleaned

    @staticmethod
    def clean_streaming_chunk(chunk: Any) -> Any:
        """
        Clean empty brackets from Nova streaming chunks while preserving structure.
        Only cleans if the chunk matches Nova's expected format.
        """
        # Only process Nova-style chunks
        if not NovaFormatter.is_nova_chunk(chunk):
            return chunk
            
        content = chunk.content
        if isinstance(content, list):
            # Direct array of content blocks
            cleaned = NovaFormatter._clean_content_blocks(content)
            if cleaned != content:
                logger.debug("Cleaned empty brackets from Nova content blocks")
                return AIMessageChunk(content=cleaned)
                
        elif isinstance(content, dict) and 'content' in content:
            # Nested content structure
            cleaned = NovaFormatter._clean_content_blocks(content['content'])
            if cleaned != content['content']:
                logger.debug("Cleaned empty brackets from nested Nova content")
                return AIMessageChunk(content={**content, 'content': cleaned})

        return chunk

    @staticmethod
    def is_nova_chunk(chunk: Any) -> bool:
        """Check if a chunk matches Nova's expected format."""
        if not isinstance(chunk, AIMessageChunk):
            return False
            
        content = chunk.content
        # Check for Nova's array of text blocks format
        if isinstance(content, list):
            return all(isinstance(b, dict) and 'text' in b for b in content)
            
        # Check for Nova's nested content format    
        if isinstance(content, dict) and 'content' in content:
            blocks = content['content']
            return isinstance(blocks, list) and all(isinstance(b, dict) and 'text' in b for b in blocks)
            
        return False

    @staticmethod
    def _clean_content_blocks(blocks: List[Any]) -> List[Any]:
        """
        Remove empty brackets from Nova content blocks.
        Preserves non-empty content and block structure.
        """
        cleaned = []
        for block in blocks:
            if not isinstance(block, dict) or 'text' not in block:
                cleaned.append(block)
                continue
                
            text = block['text']
            # Only clean if text consists solely of empty brackets
            if text.strip() in ('[]', '[][]'):
                continue
                
            cleaned.append(block)
            
        return cleaned

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
        # Only include temperature if it's in the model_kwargs (filtered by supported_parameters)
        if "temperature" in params and params.get("temperature") is not None:
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
        extracted_text = "" # Variable to hold the extracted text
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
                        # Clean up empty brackets from Nova Pro responses
                        text = NovaFormatter._clean_nova_response(text)
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
                    # Clean up empty brackets from Nova Pro responses
                    text = NovaFormatter._clean_nova_response(text)
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
                                # Clean up empty brackets from Nova Pro responses
                                text = NovaFormatter._clean_nova_response(text)
                                logger.info(f"Extracted text from content item {i} of length: {len(text)}")
                                logger.info(f"=== NOVA FORMATTER parse_response END ===")
                                return text

            # Handle the case where the response is an array of text chunks
            if isinstance(response, list):
                logger.info("Found list of content blocks")
                combined_text = ""
                for item in response:
                    if isinstance(item, dict) and 'text' in item:
                        combined_text += item['text']
                
                if combined_text:
                    # Clean up empty brackets from Nova Pro responses
                    combined_text = NovaFormatter._clean_nova_response(combined_text)
                    logger.info(f"Extracted combined text of length: {len(combined_text)}")
                    logger.info(f"=== NOVA FORMATTER parse_response END ===")
                    return combined_text

            # If we can't extract text in a structured way, return the raw response
            if not extracted_text:
                logger.warning(f"Could not parse Nova response structure, returning raw response string")
                extracted_text = str(response)
 
            # Clean up empty brackets from Nova Pro responses
            cleaned_text = NovaFormatter._clean_nova_response(extracted_text)
            logger.info(f"=== NOVA FORMATTER parse_response END ===")
            return cleaned_text
        except Exception as e:
            logger.error(f"Error parsing Nova response: {e}")
            logger.error(f"Response that caused error: {str(response)[:500]}...")
            logger.info(f"=== NOVA FORMATTER parse_response END with error ===")
            return f"Error parsing response: {str(e)}"
