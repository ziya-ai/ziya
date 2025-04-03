import os
import re
import time
import json
import asyncio
import traceback
from typing import Dict, Any, List, Tuple, Optional, Union

import tiktoken
from fastapi import FastAPI, Request, HTTPException, APIRouter, routing
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langserve import add_routes
from app.agents.agent import model, RetryingChatBedrock, initialize_langserve
from app.agents.agent import agent, agent_executor, create_agent_chain, create_agent_executor
from app.agents.agent import update_conversation_state, update_and_return, parse_output
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError 
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# Import configuration
import app.config as config
from app.agents.models import ModelManager
from botocore.exceptions import ClientError, BotoCoreError, CredentialRetrievalError
from botocore.exceptions import EventStreamError
import botocore.errorfactory
from starlette.responses import StreamingResponse
from langchain_core.outputs import Generation

# import pydevd_pycharm
from google.api_core.exceptions import ResourceExhausted
import uvicorn

from app.utils.code_util import use_git_to_apply_code_diff, correct_git_diff
from app.utils.code_util import PatchApplicationError, split_combined_diff, extract_target_file_from_diff
from app.utils.directory_util import get_ignored_patterns
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns
from app.utils.error_handlers import (
    create_json_response, create_sse_error_response, 
    is_streaming_request, ValidationError, handle_request_exception,
    handle_streaming_error
)
from app.utils.custom_exceptions import ThrottlingException, ExpiredTokenException
from app.middleware import RequestSizeMiddleware, ErrorHandlingMiddleware

# Use configuration from config module
# For model configurations, see app/config.py

class SetModelRequest(BaseModel):
    model_id: str

class PatchRequest(BaseModel):
    diff: str
    file_path: Optional[str] = None
    
class FolderRequest(BaseModel):
    directory: str
    max_depth: int = 3
    
class FileRequest(BaseModel):
    file_path: str
    
class FileContentRequest(BaseModel):
    file_path: str
    content: str

# Create the FastAPI app
app = FastAPI(
    title="Ziya API",
    description="API for Ziya, a code assistant powered by LLMs",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request size middleware
app.add_middleware(
    RequestSizeMiddleware,
    default_max_size_mb=20  # 20MB
)

# Add error handling middleware
app.add_middleware(ErrorHandlingMiddleware)

# Import and include AST routes
from app.routes.ast_routes import router as ast_router
app.include_router(ast_router)

# Get the directory of the current file
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

# Set up templates directory
templates_dir = os.path.join(parent_dir, "templates")

# Mount templates/static if it exists (for frontend assets)
templates_static_dir = os.path.join(templates_dir, "static")
if os.path.exists(templates_static_dir) and os.path.isdir(templates_static_dir):
    app.mount("/static", StaticFiles(directory=templates_static_dir), name="static")
    logger.info(f"Mounted templates/static directory at /static")
else:
    logger.warning(f"Templates static directory '{templates_static_dir}' does not exist - frontend assets may not load correctly")


templates = Jinja2Templates(directory=templates_dir)

# Add a route for the frontend
add_routes(app, agent_executor, disabled_endpoints=["playground", "stream_log"], path="/ziya")

# Add custom stream_log endpoint for compatibility
@app.post("/ziya/stream_log")
async def stream_log_endpoint(request: Request, body: dict):
    """Stream log endpoint with proper diff parameter handling."""
    try:
        # Debug logging
        logger.info("Stream log endpoint request body:")
        
        # Extract and store diff parameter if present
        diff_content = None
        if 'diff' in body:
            diff_content = body['diff']
            # Create a copy of the body without the diff parameter
            body_copy = {k: v for k, v in body.items() if k != 'diff'}
        else:
            body_copy = body
            
        # Extract input from body if present
        if 'input' in body_copy:
            input_data = body_copy['input']
            
            # Get the question from input_data
            question = input_data.get('question', 'EMPTY')
            logger.info(f"Question from input: '{question}'")
            
            # Handle chat_history
            chat_history = input_data.get('chat_history', [])
            if not isinstance(chat_history, list):
                logger.warning(f"Chat history is not a list: {type(chat_history)}")
                chat_history = []
            
            # Log chat history details for debugging
            logger.info(f"Chat history length: {len(chat_history)}")
            for i, msg in enumerate(chat_history):
                if isinstance(msg, dict):
                    logger.info(f"Input chat history item {i}: type={msg.get('type', 'unknown')}")
                else:
                    logger.info(f"Input chat history item {i}: type={type(msg)}")
            
            input_data['chat_history'] = chat_history
            
            # Handle config and files
            config = input_data.get('config', {})
            files = []
            if isinstance(config, dict):
                files = config.get("files", [])
            elif isinstance(config, list):
                logger.warning("Config is a list, assuming it's the files list")
                files = config
            
            if not isinstance(files, list):
                logger.warning(f"Files is not a list: {type(files)}")
                files = []
                
            # Count string files for summary logging
            string_file_count = sum(1 for f in files if isinstance(f, str))
            if string_file_count > 0:
                logger.info(f"Files count: {len(files)} ({string_file_count} are strings)")
            else:
                logger.info(f"Files count: {len(files)}")
            # Don't log individual file details here - too verbose
            
            # Update input_data with normalized values
            input_data['chat_history'] = chat_history
            input_data['config'] = {'files': files} if isinstance(config, list) else config
            
            # Ensure we use the current question from input_data
            input_data['question'] = question
            body_copy = input_data
        
        # Use direct streaming with StreamingResponse
        return StreamingResponse(
            stream_chunks(body_copy),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Type": "text/event-stream"
            }
        )
    except Exception as e:
        logger.error(f"Error in stream_log_endpoint: {str(e)}")
        # Return error as streaming response
        error_json = json.dumps({"error": str(e)})
        return StreamingResponse(
            (f"data: {error_json}\n\ndata: {json.dumps({'done': True})}\n\n" for _ in range(1)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Type": "text/event-stream"
            }
        )

async def stream_chunks(body):
    """Stream chunks from the agent executor."""
    # Send heartbeat to keep connection alive (don't send processing message as it appears in the UI)
    yield f"data: {json.dumps({'heartbeat': True})}\n\n"
    
    try:
        # Log the entire body structure for debugging
        logger.info(f"[INSTRUMENTATION] stream_chunks body structure: {type(body)}")
        if isinstance(body, dict):
            # Only log key names, not values (which could be large)
            logger.info(f"[INSTRUMENTATION] stream_chunks body keys: {', '.join(body.keys())}")
        
        # Get the question from the request body
        question = body.get("question", "")
        logger.info(f"[INSTRUMENTATION] stream_chunks question: '{question[:50]}...' (truncated)")
        conversation_id = body.get("conversation_id", "default")
        chat_history = body.get("chat_history", [])
        
        # Get the files from the config if available
        config = body.get("config", {})
        files = []
        
        # Handle different possible formats of config and files
        if isinstance(config, dict):
            files = config.get("files", [])
        elif isinstance(config, list):
            # If config is a list, assume it's the files list
            logger.warning("[INSTRUMENTATION] stream_chunks config is a list, assuming it's the files list")
            files = config
        
        # Ensure files is a list
        if not isinstance(files, list):
            logger.warning(f"[INSTRUMENTATION] stream_chunks files is not a list: {type(files)}")
            files = []
        
        # Log the request details
        logger.info(f"[INSTRUMENTATION] stream_chunks processing request: conversation_id={conversation_id}, question={question[:50]}...")
        logger.info(f"[INSTRUMENTATION] stream_chunks chat history length: {len(chat_history)}")
        logger.info(f"[INSTRUMENTATION] stream_chunks files count: {len(files)}")
        
        # Get the model instance using the proper method
        from app.agents.agent import model
        logger.info("[INSTRUMENTATION] stream_chunks getting model instance")
        model_instance = model.get_model()
        logger.info(f"[INSTRUMENTATION] stream_chunks got model instance: {type(model_instance)}")
        
        # Prepare the messages for the model
        messages = []
        
        # Add system message with file context if available
        if files:
            from langchain_core.messages import SystemMessage
            logger.info(f"[INSTRUMENTATION] stream_chunks adding system message with {len(files)} files")
            logger.info("=== System Message Content Debug ===")
            file_context = "Here are the files in the codebase:\n\n"
            
            # Count string files to avoid excessive logging
            string_file_count = 0
            
            for i, file in enumerate(files):
                # Check if file is a dictionary with path and content
                if isinstance(file, dict):
                    file_path = file.get("path", "")
                    logger.info(f"Processing file: {file_path}")
                    file_content = file.get("content", "")
                    if file_path and file_content:
                        logger.info(f"Adding content for {file_path}, size: {len(content)}")
                        logger.info(f"Content preview:\n{content[:200]}...")
                        file_context += f"File: {file_path}\n```\n{file_content}\n```\n\n"
                # Handle case where file might be a string or other format
                elif isinstance(file, str):
                    logger.info(f"String only file: {file}")
                    try:
                        full_path = os.path.join(os.environ.get("ZIYA_USER_CODEBASE_DIR", ""), file)
                        if os.path.exists(full_path):
                             with open(full_path, 'r', encoding='utf-8') as f:
                               file_content = f.read()
                             logger.info(f"Adding content for {file}, size: {len(file_content)}")
                             logger.info(f"Content preview:\n{file_content[:200]}...")
                             file_context += f"File: {file}\n```\n{file_content}\n```\n\n"
                             string_file_count += 1
                        else:
                             logger.warning(f"File not found: {full_path}")
                    except Exception as e:
                         logger.error(f"Error reading file {file}: {str(e)}")
            
            # Log summary of string files instead of individual warnings
            if string_file_count > 0:
                logger.warning(f"[INSTRUMENTATION] stream_chunks found {string_file_count} file entries as strings instead of dicts")
            
            # Add system message with file context
            messages.append(SystemMessage(content=file_context))
            logger.info(f"[INSTRUMENTATION] stream_chunks added system message with {len(files)} files")
        
        # Add chat history if available
        if chat_history:
            from langchain_core.messages import HumanMessage, AIMessage
            
            # Log the chat history for debugging
            logger.info(f"[INSTRUMENTATION] stream_chunks processing chat history with {len(chat_history)} messages")
            # Only log details for first few messages
            for i, msg in enumerate(chat_history):
                if i >= 5:  # Only log first 5 messages in detail
                    break
                    
                if isinstance(msg, dict):
                    logger.info(f"[INSTRUMENTATION] stream_chunks chat history item {i}: type={msg.get('type', 'unknown')}")
                elif isinstance(msg, list):
                    logger.info(f"[INSTRUMENTATION] stream_chunks chat history item {i}: type=list, length={len(msg)}")
                    if len(msg) > 0:
                        logger.info(f"[INSTRUMENTATION] stream_chunks first element: {msg[0][:50]}...")  # Limit length of logged content
                else:
                    logger.info(f"[INSTRUMENTATION] stream_chunks chat history item {i}: type={type(msg)}")
            
            for msg in chat_history:
                # Check if msg is a dictionary with 'type' and 'content' keys
                if isinstance(msg, dict) and 'type' in msg and 'content' in msg:
                    msg_type = msg["type"]
                    msg_content = msg["content"]
                    # Only log first few characters of content
                    logger.info(f"[INSTRUMENTATION] stream_chunks adding message of type {msg_type} with content: {msg_content[:30]}...")
                    
                    if msg_type == "human":
                        messages.append(HumanMessage(content=msg_content))
                    elif msg_type == "ai":
                        messages.append(AIMessage(content=msg_content))
                # Handle case where msg is a list (common format from frontend)
                elif isinstance(msg, list):
                    # Format is typically [question, answer]
                    if len(msg) >= 2:
                        question = msg[0]
                        answer = msg[1]
                        logger.info(f"[INSTRUMENTATION] stream_chunks adding list-format message pair - Q: {question[:30]}... A: {answer[:30]}...")
                        
                        # Add as separate human and AI messages
                        messages.append(HumanMessage(content=question))
                        messages.append(AIMessage(content=answer))
                # Handle case where msg is a tuple (format from cleaned chat history)
                elif isinstance(msg, tuple) and len(msg) == 2:
                    # Format is typically (human_message, ai_message)
                    human_msg, ai_msg = msg
                    logger.info(f"[INSTRUMENTATION] stream_chunks adding tuple-format message pair - Human: {human_msg[:30]}... AI: {ai_msg[:30]}...")
                    
                    # Add as separate human and AI messages
                    messages.append(HumanMessage(content=human_msg))
                    messages.append(AIMessage(content=ai_msg))
                # Handle other formats
                else:
                    logger.warning(f"[INSTRUMENTATION] stream_chunks unexpected message format in chat history: {type(msg)}")
                    # Try other formats as a fallback
                    try:
                        if hasattr(msg, 'role') and hasattr(msg, 'content'):
                            role = getattr(msg, 'role')
                            content = getattr(msg, 'content')
                            if role == "human" or role == "user":
                                messages.append(HumanMessage(content=content))
                            elif role == "ai" or role == "assistant":
                                messages.append(AIMessage(content=content))
                    except Exception as e:
                        logger.error(f"[INSTRUMENTATION] stream_chunks failed to process chat history message: {e}")
                        # Continue with other messages
        # Add the current question
        from langchain_core.messages import HumanMessage
        logger.info(f"[INSTRUMENTATION] stream_chunks adding current question: {question[:50]}...")
        
        # Double-check that we're using the most recent question
        if not question:
            logger.error("[INSTRUMENTATION] stream_chunks CRITICAL ERROR: Question is empty!")
            logger.info(f"[INSTRUMENTATION] stream_chunks body keys: {body.keys() if isinstance(body, dict) else type(body)}")
            if isinstance(body, dict) and 'input' in body and isinstance(body['input'], dict):
                input_question = body['input'].get('question', '')
                if input_question:
                    logger.info(f"[INSTRUMENTATION] stream_chunks found question in body['input']: {input_question[:50]}...")
                    question = input_question
        
        messages.append(HumanMessage(content=question))
        
        # Log the final message list for debugging
        logger.info(f"[INSTRUMENTATION] stream_chunks final message list contains {len(messages)} messages:")
        # Log details for the first few and lest few messages
        log_limit = 5
        for i, msg in enumerate(messages):
            if i < log_limit or i >= len(messages) - log_limit:
                try:
                    # Increase preview length and replace newlines for better logging
                    content_preview = str(msg.content)[:150].replace('\n', '\\n') + ('...' if len(str(msg.content)) > 150 else '')
                    logger.info(f"[INSTRUMENTATION] stream_chunks message {i}: type={msg.__class__.__name__}, content_preview='{content_preview}'")
                except Exception as e:
                    logger.error(f"[INSTRUMENTATION] stream_chunks error logging message {i}: {e}")
                    logger.info(f"[INSTRUMENTATION] stream_chunks message {i}: type={type(msg)}")
            elif i == log_limit:
                 logger.info(f"[INSTRUMENTATION] stream_chunks ... ({len(messages) - 2 * log_limit} messages omitted) ...")
                
        # Stream directly from the model
        logger.info("[INSTRUMENTATION] stream_chunks calling model.astream()")
        chunk_count = 0
        full_response = ""

        # Flag to track if we've sent the done marker
        done_marker_sent = False
        
        # Set up the model with the stop sequence
        # This ensures the model will properly stop at the sentinel
        model_with_stop = model_instance.bind(stop=["</tool_input>"])
        

        
        try:
            async for chunk in model_with_stop.astream(messages):
                
                # Check if the chunk contains a structured error message first
                is_error_chunk = False
                error_data = None
                if hasattr(chunk, 'content'):
                    content_str = str(chunk.content) if callable(chunk.content) else str(chunk.content)
                    if content_str.startswith('{') and '"error":' in content_str:
                        try:
                            error_data = json.loads(content_str)
                            if "error" in error_data and "detail" in error_data:
                                is_error_chunk = True
                        except json.JSONDecodeError:
                            pass # Not a valid JSON error structure

                if is_error_chunk and error_data:
                    logger.warning(f"[INSTRUMENTATION] stream_chunks detected structured error chunk: {error_data}")
                    # Ensure we yield the error data correctly formatted for SSE
                    error_sse_data = f"data: {json.dumps(error_data)}\n\n"
                    logger.info(f"[INSTRUMENTATION] Yielding SSE Error: {error_sse_data.strip()}")
                    yield error_sse_data # Send formatted error
                    yield "data: [DONE]\n\n" # Send DONE marker immediately after error
                    done_marker_sent = True # Mark as sent
                    break # Terminate the stream after sending error and DONE
                # Skip processing if we've already sent the done marker
                if done_marker_sent:
                    # Check if chunk is already an SSE message
                    if isinstance(chunk, str) and chunk.startswith('data: {"error"'):
                        yield chunk
                        continue
                    logger.info("[INSTRUMENTATION] stream_chunks skipping chunk after done marker sent")
                    continue
                    
                chunk_count += 1 # Only increment for non-error chunks
                
                # Extract content from the chunk
                if hasattr(chunk, 'content'):
                    logger.info(f"[INSTRUMENTATION] stream_chunks chunk {chunk_count} has content attribute")
                    content = chunk.content
                    
                    # Handle callable content
                    if callable(content):
                        logger.info(f"[INSTRUMENTATION] stream_chunks chunk {chunk_count} content is callable")
                        content = content()
                    
                    # Check for stop sentinel in the content
                    content_str = str(content) if content else ""
                    if "</tool_input>" in content_str:
                        logger.info(f"[INSTRUMENTATION] stream_chunks detected stop sentinel in content")
                        # Send the done marker and stop processing more chunks
                        if not done_marker_sent:
                            logger.info("[INSTRUMENTATION] stream_chunks sending done marker after stop sentinel detected")
                            yield f"data: {json.dumps({'done': True})}\n\n"
                            done_marker_sent = True
                            break  # Exit the loop but not the function
                    
                    # Check if this chunk has generation_info with stop_reason
                    if hasattr(chunk, 'generation_info') and chunk.generation_info:
                        gen_info = chunk.generation_info
                        if 'finish_reason' in gen_info or 'stop_reason' in gen_info:
                            stop_reason = gen_info.get('finish_reason') or gen_info.get('stop_reason')
                            if stop_reason:
                                logger.info(f"[INSTRUMENTATION] stream_chunks detected stop_reason: {stop_reason}")
                                # Send the done marker and stop processing more chunks
                                if not done_marker_sent:
                                    logger.info("[INSTRUMENTATION] stream_chunks sending done marker after stop reason detected")
                                    yield f"data: {json.dumps({'done': True})}\n\n"
                                    done_marker_sent = True
                                    break  # Exit the loop but not the function
                
                    # Handle Nova model format (list of content blocks)
                    if isinstance(content, list) and len(content) > 0:
                        logger.info(f"[INSTRUMENTATION] stream_chunks chunk {chunk_count} content is list of length {len(content)}")
                        for content_block in content:
                            if isinstance(content_block, dict) and 'text' in content_block:
                                text = content_block.get('text', '')
                                # Clean up the text - remove any [] markers
                                if text == "[]":
                                    logger.info(f"[INSTRUMENTATION] stream_chunks skipping empty brackets")
                                    continue  # Skip empty brackets
                            
                                # Remove [] from the beginning and end of the text if present
                                # text = text.strip("[]")
                                # commented out as this seems to be too aggressively clipping square brackets
                                
                                if text:  # Only send non-empty text
                                    # Accumulate the full response for later use
                                    full_response += text
                                    
                                    # Use the format that the frontend expects for streamed output
                                    ops = [
                                        {
                                            "op": "add",
                                            "path": "/streamed_output_str/-",
                                            "value": text
                                        }
                                    ]
                                    
                                    # Only log every 10th chunk or first/last chunks
                                    if chunk_count <= 1 or chunk_count % 10 == 0:
                                        logger.info(f"[INSTRUMENTATION] stream_chunks sending Nova chunk #{chunk_count}: text={text[:20]}...")
                                    yield f"data: {json.dumps({'ops': ops})}\n\n"
                    else:
                        # Handle standard text content
                        logger.info(f"[INSTRUMENTATION] stream_chunks chunk {chunk_count} content is standard text")
                        text = str(content)
                        # Clean up the text - remove any [] markers
                        if text == "[]":
                            logger.info(f"[INSTRUMENTATION] stream_chunks skipping empty brackets")
                            continue  # Skip empty brackets
                        
                        # Remove [] from the beginning and end of the text if present
                        #text = text.strip("[]")
                        # commented out as this seems to be too aggressively clipping square brackets
                        
                        if text:
                            # Accumulate the full response for later use
                            full_response += text
                            
                            # Use the format that the frontend expects for streamed output
                            ops = [
                                {
                                    "op": "add",
                                    "path": "/streamed_output_str/-",
                                    "value": text
                                }
                            ]
                            
                            # Only log every 10th chunk or first/last chunks
                            if chunk_count <= 1 or chunk_count % 10 == 0:
                                logger.info(f"[INSTRUMENTATION] stream_chunks sending chunk #{chunk_count}: text={text[:20]}...")
                            yield f"data: {json.dumps({'ops': ops})}\n\n"
            else:
                logger.info(f"[INSTRUMENTATION] stream_chunks skipping chunk #{chunk_count} without content: {type(chunk)}")
                
            # Check for stop sentinel in the full response so far
            if "</tool_input>" in full_response and not done_marker_sent:
                logger.info(f"[INSTRUMENTATION] stream_chunks detected stop sentinel in full response")
                # Send the done marker and stop processing more chunks
                logger.info("[INSTRUMENTATION] stream_chunks sending done marker after stop sentinel detected")
                yield f"data: {json.dumps({'done': True})}\n\n"
                done_marker_sent = True
                # We need to break out of the async for loop
                return  # Exit the entire generator function
                    
            # End of the async for loop iteration
            
            # After successful streaming, log the complete response
            if full_response:
                logger.info("=== FULL SERVER RESPONSE ===")
                logger.info(f"Response length: {len(full_response)}")
                logger.info(f"Response content:\n{full_response}")
                logger.info("=== END SERVER RESPONSE ===")
            
            # If the loop finished normally (no error break) and we sent content, send DONE
            if not done_marker_sent and chunk_count > 0 and not is_error_chunk:
                logger.info(f"[INSTRUMENTATION] stream_chunks total chunks sent: {chunk_count}")
                logger.info("[INSTRUMENTATION] stream_chunks sending done marker after all chunks")
                yield f"data: {json.dumps({'done': True})}\n\n"

        except ChatGoogleGenerativeAIError as e:
            error_msg = {
                "error": "server_error",
                "detail": str(e),
                "status_code": 500
            }
            yield f"data: {json.dumps(error_msg)}\n\n"
            # Send DONE only if not already sent
            if not done_marker_sent:
                yield "data: [DONE]\n\n"
            return
                
        except Exception as e:
            # Handle any exceptions during streaming
            logger.error(f"[INSTRUMENTATION] stream_chunks exception during streaming loop: {str(e)}")
            logger.error(f"[INSTRUMENTATION] stream_chunks exception type: {type(e).__name__}")
            logger.error(f"[INSTRUMENTATION] stream_chunks exception traceback: {traceback.format_exc()}")
            if not done_marker_sent:
                # Let the middleware handle formatting this unexpected error and sending DONE
                # logger.info("[INSTRUMENTATION] stream_chunks sending done marker after exception")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

                # yield f"data: {json.dumps({'done': True})}\n\n" # Middleware will send DONE
                # done_marker_sent = True
            raise # Re-raise for middleware to catch
        # Update conversation state after streaming is complete
        try:
            # Note: update_conversation_state only takes 2 args (conversation_id and file_paths)
            # We're not updating any files, so pass an empty list
            logger.info(f"[INSTRUMENTATION] stream_chunks updating conversation state for {conversation_id}")
            update_conversation_state(conversation_id, [])
        except Exception as e:
            logger.error(f"[INSTRUMENTATION] stream_chunks error updating conversation state: {e}")
            
    except Exception as e:
        logger.error(f"[INSTRUMENTATION] stream_chunks exception during streaming: {str(e)}")
        logger.error(f"[INSTRUMENTATION] stream_chunks exception type: {type(e).__name__}")
        logger.error(f"[INSTRUMENTATION] stream_chunks exception traceback: {traceback.format_exc()}")
        raise # re-raise for middleware to catch        
        # Update conversation state after streaming is complete
        try:
            # Note: update_conversation_state only takes 2 args (conversation_id and file_paths)
            # We're not updating any files, so pass an empty list
            logger.info(f"[INSTRUMENTATION] stream_chunks updating conversation state for {conversation_id}")
            update_conversation_state(conversation_id, [])
        except Exception as e:
            logger.error(f"[INSTRUMENTATION] stream_chunks error updating conversation state: {e}")
            
    except Exception as e:
        logger.error(f"[INSTRUMENTATION] stream_chunks exception during streaming: {str(e)}")
        logger.error(f"[INSTRUMENTATION] stream_chunks exception type: {type(e).__name__}")
        logger.error(f"[INSTRUMENTATION] stream_chunks exception traceback: {traceback.format_exc()}")
        # Let the middleware handle formatting this unexpected error and sending DONE
        # yield f"data: {json.dumps({'error': str(e)})}\n\n"
        # yield f"data: {json.dumps({'done': True})}\n\n"

# Override the stream endpoint with our error handling
@app.post("/ziya/stream")
async def stream_endpoint(request: Request, body: dict):
    """Stream endpoint with centralized error handling."""
    try:
        # Debug logging
        logger.info("[INSTRUMENTATION] /ziya/stream received request")
        logger.info(f"[INSTRUMENTATION] /ziya/stream question: '{body.get('question', 'EMPTY')[:50]}...' (truncated)")
        logger.info(f"[INSTRUMENTATION] /ziya/stream chat_history length: {len(body.get('chat_history', []))}")
        logger.info(f"[INSTRUMENTATION] /ziya/stream files count: {len(body.get('config', {}).get('files', []))}")
        
        # Log body structure
        logger.info(f"[INSTRUMENTATION] /ziya/stream body keys: {body.keys() if isinstance(body, dict) else type(body)}")
        
        # Log chat history structure if present
        chat_history = body.get('chat_history', [])
        if chat_history and len(chat_history) > 0:
            logger.info(f"[INSTRUMENTATION] /ziya/stream first history item type: {type(chat_history[0])}")
            if isinstance(chat_history[0], list) and len(chat_history[0]) >= 2:
                logger.info(f"[INSTRUMENTATION] /ziya/stream first history format: ['{chat_history[0][0][:20]}...', '{chat_history[0][1][:20]}...'] (truncated)")
            elif isinstance(chat_history[0], dict):
                logger.info(f"[INSTRUMENTATION] /ziya/stream first history keys: {chat_history[0].keys()}")

        # Check if the question is empty or missing
        if not body.get("question") or not body.get("question").strip():
            logger.warning("[INSTRUMENTATION] /ziya/stream empty question detected")
            raise ValidationError("Please provide a question to continue.")
            
        # Clean chat history if present
        if "chat_history" in body:
            logger.info(f"[INSTRUMENTATION] /ziya/stream cleaning chat history of length {len(chat_history)}")
            cleaned_history = []
            for pair in body["chat_history"]:
                try:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        logger.warning(f"[INSTRUMENTATION] /ziya/stream invalid chat history pair format: {type(pair)}")
                        continue
                        
                    human, ai = pair
                    if not isinstance(human, str) or not isinstance(ai, str):
                        logger.warning(f"[INSTRUMENTATION] /ziya/stream non-string message in pair: human={type(human)}, ai={type(ai)}")
                        continue
                        
                    if human.strip() and ai.strip():
                        cleaned_history.append((human.strip(), ai.strip()))
                        logger.info(f"[INSTRUMENTATION] /ziya/stream added valid pair: ['{human[:20]}...', '{ai[:20]}...'] (truncated)")
                    else:
                        logger.warning(f"[INSTRUMENTATION] /ziya/stream empty message in pair")
                except Exception as e:
                    logger.error(f"[INSTRUMENTATION] /ziya/stream error processing chat history pair: {str(e)}")
            
            logger.info(f"[INSTRUMENTATION] /ziya/stream cleaned chat history from {len(body['chat_history'])} to {len(cleaned_history)} pairs")
            body["chat_history"] = cleaned_history
            
        logger.info("[INSTRUMENTATION] /ziya/stream starting stream endpoint with body size: %d", len(str(body)))
        
        # Convert to ChatPromptValue if needed
        if isinstance(body, dict) and "messages" in body:
            from langchain_core.prompt_values import ChatPromptValue
            from langchain_core.messages import HumanMessage
            logger.info(f"[INSTRUMENTATION] /ziya/stream converting {len(body['messages'])} messages to ChatPromptValue")
            body["messages"] = [HumanMessage(content=msg) for msg in body["messages"]]
            body = ChatPromptValue(messages=body["messages"])
            logger.info(f"[INSTRUMENTATION] /ziya/stream converted body to {type(body)}")
        
        # Return the streaming response
        logger.info("[INSTRUMENTATION] /ziya/stream calling stream_chunks()")
        return StreamingResponse(
            stream_chunks(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type"
            }
        )
    except Exception as e:
        # Handle any exceptions using the centralized error handler
        logger.error(f"Exception in stream_endpoint: {str(e)}")
        return handle_request_exception(request, e)

async def stream_agent_response(body, request):
    """Stream the agent's response with centralized error handling."""
    try:
        first_chunk = True
        # Stream the response
        async for chunk in agent_executor.astream_log(body):
            # Process the chunk
            try:
                # Parse and clean the chunk before sending
                parsed_chunk = parse_output(chunk)
                if parsed_chunk and parsed_chunk.return_values:
                    cleaned_output = parsed_chunk.return_values.get("output", "")
                    if cleaned_output:
                        first_chunk = False
                        # Use proper JSON format for SSE
                        yield f"data: {json.dumps({'text': cleaned_output})}\n\n"
                        continue
                
                # Fall back to original chunk if parsing fails
                chunk_content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                first_chunk = False
                # Use proper JSON format for SSE
                yield f"data: {json.dumps({'text': chunk_content})}\n\n"
            except Exception as e:
                logger.error(f"Error processing chunk: {e}")
                continue
        
        # Send the [DONE] marker
        yield f"data: {json.dumps({'done': True})}\n\n"
        
    except Exception as e:
        # Use the centralized error handler for streaming errors
        logger.error(f"Exception during streaming: {str(e)}")
        
        # Don't try to handle the error here, let the middleware handle it
        # Just re-raise the exception so the middleware can catch it
        raise

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "diff_view_type": os.environ.get("ZIYA_DIFF_VIEW_TYPE", "unified"),
        "api_poth": "/ziya"
    })


@app.get("/debug")
async def debug(request: Request):
   return templates.TemplateResponse("index.html", {"request": request})

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse('../templates/favicon.ico')


# Cache for folder structure with timestamp
_folder_cache = {'timestamp': 0, 'data': None}

def get_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    """
    Get the folder structure of a directory with token counts.
    
    Args:
        directory: The directory to get the structure of
        ignored_patterns: Patterns to ignore
        max_depth: Maximum depth to traverse
        
    Returns:
        Dict with folder structure including token counts
    """
    from app.utils.file_utils import is_binary_file
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)
    encoding = tiktoken.get_encoding("cl100k_base")
    
    # Ensure max_depth is at least 15 if not specified
    if max_depth <= 0:
        max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
    
    logger.debug(f"Getting folder structure for {directory} with max depth {max_depth}")
    
    def count_tokens(file_path: str) -> int:
        """Count tokens in a file using tiktoken."""
        try:
            # Skip binary files
            if is_binary_file(file_path):
                return 0
                
            # Skip large files (>1MB)
            if os.path.getsize(file_path) > 1024 * 1024:
                return 0
                
            # Read file and count tokens
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                return len(encoding.encode(content))
        except Exception as e:
            logger.debug(f"Error counting tokens in {file_path}: {e}")
            return 0
    
    def process_dir(path: str, depth: int) -> Dict[str, Any]:
        """Process a directory recursively."""
        if depth > max_depth:
            return {'token_count': 0}
            
        result = {'token_count': 0, 'children': {}}
        total_tokens = 0
        
        try:
            entries = os.listdir(path)
        except PermissionError:
            logger.debug(f"Permission denied for {path}")
            return {'token_count': 0}
            
        for entry in entries:
            if entry.startswith('.'):  # Skip hidden files
                continue
                
            entry_path = os.path.join(path, entry)
            
            if os.path.islink(entry_path):  # Skip symlinks
                continue
                
            if should_ignore_fn(entry_path):  # Skip ignored files
                continue
                
            if os.path.isdir(entry_path):
                if depth < max_depth:
                    sub_result = process_dir(entry_path, depth + 1)
                    if sub_result['token_count'] > 0 or sub_result.get('children'):
                        result['children'][entry] = sub_result
                        total_tokens += sub_result['token_count']
            elif os.path.isfile(entry_path):
                tokens = count_tokens(entry_path)
                if tokens > 0:
                    result['children'][entry] = {'token_count': tokens}
                    total_tokens += tokens
        
        result['token_count'] = total_tokens
        return result
    
    # Process the root directory
    root_result = process_dir(directory, 1)
    
    # Return just the children of the root to match expected format
    return root_result.get('children', {})

@app.post("/folder")
async def get_folder(request: FolderRequest):
    """Get the folder structure of a directory."""
    try:
        # Get the ignored patterns
        ignored_patterns = get_ignored_patterns(request.directory)
        
        # Use the max_depth from the request, but ensure it's at least 15 if not specified
        max_depth = request.max_depth if request.max_depth > 0 else int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        logger.info(f"Using max depth for folder structure: {max_depth}")
        
        # Check if we have a cached result that's less than 5 seconds old
        current_time = time.time()
        if _folder_cache['timestamp'] > current_time - 5:
            return _folder_cache['data']
            
        # Get the folder structure
        result = get_folder_structure(request.directory, ignored_patterns, max_depth)
        
        # Cache the result
        _folder_cache['timestamp'] = current_time
        _folder_cache['data'] = result
        
        return result
    except Exception as e:
        logger.error(f"Error in get_folder: {e}")
        return {"error": str(e)}

@app.post("/file")
async def get_file(request: FileRequest):
    """Get the content of a file."""
    try:
        with open(request.file_path, 'r') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        logger.error(f"Error in get_file: {e}")
        return {"error": str(e)}

@app.post("/save")
async def save_file(request: FileContentRequest):
    """Save content to a file."""
    try:
        with open(request.file_path, 'w') as f:
            f.write(request.content)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error in save_file: {e}")
        return {"error": str(e)}

@app.post("/apply_patch")
async def apply_patch(request: PatchRequest):
    """Apply a git diff to a file."""
    try:
        # If file_path is not provided, try to extract it from the diff
        target_file = request.file_path
        if not target_file:
            target_file = extract_target_file_from_diff(request.diff)
            
        if not target_file:
            return {"error": "Could not determine target file from diff"}
            
        # Apply the patch
        try:
            result = use_git_to_apply_code_diff(request.diff, target_file)

            # Check if result contains error information
            if isinstance(result, dict) and result.get('status') == 'error':
                return JSONResponse(
                    status_code=422,
                    content={
                        "status": "error",
                        "type": result.get("type", "patch_error"),
                        "message": result.get("message", "Failed to apply patch"),
                        "details": result.get("details", {})
                    }
                )

            # Check for partial success with failed hunks
            if isinstance(result, dict) and result.get('hunk_statuses'):
                failed_hunks = [
                    hunk_num for hunk_num, status in result['hunk_statuses'].items()
                    if status.get('status') == 'failed'
                ]
                if failed_hunks:
                    return JSONResponse(
                        status_code=207,
                        content={
                            "status": "partial",
                            "message": "Some hunks failed to apply",
                            "details": {
                                "failed": failed_hunks,
                                "hunk_statuses": result['hunk_statuses']
                            }
                        }
                    )

            # All hunks succeeded
            return JSONResponse(
                content={
                    "status": "success",
                    "message": "Changes applied successfully",
                    "details": result
                }
            )
        except Exception as e:
            logger.error("Error in apply_path: {e}")

    except PatchApplicationError as e:
        logger.error(f"Error applying patch: {e}")
        return {"error": str(e), "type": "patch_error"}
    except Exception as e:
        logger.error(f"Error in apply_patch: {e}")
        return {"error": str(e)}

@app.get('/api/available-models')
def get_available_models():
    """Get list of available models for the current endpoint."""
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")

    try:
        models = []
        for name, config in ModelManager.MODEL_CONFIGS[endpoint].items():
            models.append({
                "id": config["model_id"],
                "name": name,
                "alias": name,
                "display_name": f"{name} ({config['model_id']})"
            })
        return models
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the Ziya server")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT, help="Port to run the server on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to run the server on")
    parser.add_argument("--model", type=str, default=None, help="Model to use")
    parser.add_argument("--profile", type=str, default=None, help="AWS profile to use")
    parser.add_argument("--region", type=str, default=None, help="AWS region to use")
    
    args = parser.parse_args()
    
    # Set the AWS profile if provided
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile
        
    # Set the AWS region if provided
    if args.region:
        os.environ["AWS_REGION"] = args.region
        
    # Initialize the model if provided
    if args.model:
        try:
            ModelManager.initialize_model(args.model)
        except Exception as e:
            logger.error(f"Error initializing model: {e}")
            
    # Run the server
    uvicorn.run(app, host=args.host, port=args.port)

@app.get('/api/default-included-folders')
async def get_default_included_folders():
    """Get the default included folders."""
    return []

@app.post('/api/chat')
async def chat_endpoint(request: Request):
    """Handle chat requests from the frontend.
    
    This is a thin wrapper around the /ziya/stream endpoint that formats
    the request data appropriately and forwards it to the stream endpoint.
    """
    try:
        body = await request.json()
        
        # Extract data from the request
        messages = body.get('messages', [])
        question = body.get('question', '')
        files = body.get('files', [])

        logger.info("=== File Processing Debug ===")
        logger.info(f"Files received: {files}")
        if files:
            for file_path in files:
                try:
                    full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
                    if os.path.exists(full_path):
                        with open(full_path, 'r') as f:
                            content = f.read()
                            logger.info(f"File {file_path} exists, size: {len(content)} bytes")
                            logger.info(f"Preview: {content[:200]}...")
                    else:
                        logger.warning(f"File not found: {full_path}")
                except Exception as e:
                    logger.error(f"Error reading file {file_path}: {e}")
        
        logger.info(f"[INSTRUMENTATION] /api/chat received request with {len(messages)} messages and {len(files)} files")
        logger.info(f"[INSTRUMENTATION] /api/chat question: '{question[:50]}...' (truncated)")
        
        # Log message structure for debugging
        if messages and len(messages) > 0:
            logger.info(f"[INSTRUMENTATION] First message structure: {type(messages[0])}")
            if isinstance(messages[0], list) and len(messages[0]) >= 2:
                logger.info(f"[INSTRUMENTATION] First message format: ['{messages[0][0][:20]}...', '{messages[0][1][:20]}...'] (truncated)")
            elif isinstance(messages[0], dict):
                logger.info(f"[INSTRUMENTATION] First message keys: {messages[0].keys()}")
        
        # Format the data for the stream endpoint
        formatted_body = {
            'question': question,
            'chat_history': messages,
            'config': {
                'files': files
            }
        }
        
        logger.info(f"[INSTRUMENTATION] /api/chat formatted body structure: {formatted_body.keys()}")
        logger.info(f"[INSTRUMENTATION] /api/chat forwarding to /ziya/stream")
        
        # Forward the request to the /ziya/stream endpoint
        # This ensures all validation and normalization logic is applied
        stream_request = Request(scope=request.scope)
        stream_request._body = json.dumps(formatted_body).encode()
        
        # Call the stream endpoint directly
        return await stream_endpoint(stream_request, formatted_body)
    except Exception as e:
        logger.error(f"Error in chat_endpoint: {str(e)}")
        # Return error as streaming response
        error_json = json.dumps({"error": str(e)})
        return StreamingResponse(
            (f"data: {error_json}\n\ndata: {json.dumps({'done': True})}\n\n" for _ in range(1)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Type": "text/event-stream"
            }
        )

@app.get('/api/current-model')
def get_current_model():
    """Get detailed information about the currently active model."""
    try:
        logger.info("Current model info request received")
        
        # Get model ID and endpoint
        model_id = ModelManager.get_model_id(model)
        endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        
        # Get model settings through ModelManager
        model_settings = ModelManager.get_model_settings(model)
        
        logger.info("Current model configuration:")
        logger.info(f"  Model ID: {model_id}")
        logger.info(f"  Endpoint: {endpoint}")
        logger.info(f"  Settings: {model_settings}")
        
        # Return complete model information
        return {
            'model_id': model_id,
            'endpoint': endpoint,
            'settings': model_settings
        }
    except Exception as e:
        logger.error(f"Error getting current model: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get current model: {str(e)}")

@app.get('/api/model-id')
def get_model_id():
    if os.environ.get("ZIYA_ENDPOINT") == "google":
        model_name = os.environ.get("ZIYA_MODEL", "gemini-pro")
        return {'model_id': model_name}
    elif os.environ.get("ZIYA_MODEL"):
        return {'model_id': os.environ.get("ZIYA_MODEL")}
    else:
        # Bedrock
        return {'model_id': ModelManager.get_model_id(model).split(':')[0].split('/')[-1]}


def get_cached_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    current_time = time.time()
    cache_age = current_time - _folder_cache['timestamp']

    # Refresh cache if older than 10 seconds
    if _folder_cache['data'] is None or cache_age > 10:
        _folder_cache['data'] = get_folder_structure(directory, ignored_patterns, max_depth)
        _folder_cache['timestamp'] = current_time
        logger.info("Refreshed folder structure cache")

    return _folder_cache['data']

@app.get('/api/folders')
async def api_get_folders():
    """Get the folder structure for API compatibility."""
    try:
        user_codebase_dir = os.environ["ZIYA_USER_CODEBASE_DIR"]
        max_depth = int(os.environ.get("ZIYA_MAX_DEPTH"))
        ignored_patterns: List[Tuple[str, str]] = get_ignored_patterns(user_codebase_dir)
        return get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth)
    except Exception as e:
        logger.error(f"Error in api_get_folders: {e}")
        return {"error": str(e)}

@app.post('/api/set-model')
async def set_model(request: SetModelRequest):
    """Set the active model for the current endpoint."""
    try:
        model_id = request.model_id
        logger.info(f"Received model change request: {model_id}")

        if not model_id:
            logger.error("Empty model ID provided")
            raise HTTPException(status_code=400, detail="Model ID is required")

        # Get current endpoint
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        current_model = os.environ.get("ZIYA_MODEL")

        logger.info(f"Current state - Endpoint: {endpoint}, Model: {current_model}")

        # If we received a full model ID, try to find its alias
        found_alias = None
        for alias, model_config_item in ModelManager.MODEL_CONFIGS[endpoint].items():
            if model_config_item['model_id'] == model_id or alias == model_id:
                found_alias = alias
                break

        if not found_alias:
            logger.error(f"Invalid model identifier: {model_id}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model identifier: {model_id}. Valid models are: "
                       f"{', '.join(ModelManager.MODEL_CONFIGS[endpoint].keys())}"
            )

        # If model hasn't actually changed, return early
        if found_alias == current_model:
            logger.info(f"Model {found_alias} is already active, no change needed")
            return {"status": "success", "model": found_alias, "changed": False}

        # Update environment variable
        logger.info(f"Setting model to: {found_alias}")

        # Reinitialize all model related state
        old_state = {
            'model_id': os.environ.get("ZIYA_MODEL"),
            'model': ModelManager._state.get('model'),
            'current_model_id': ModelManager._state.get('current_model_id')
        }
        logger.info(f"Saved old state: {old_state}")

        try:
            logger.info(f"Reinitializing model with alias: {found_alias}")
            ModelManager._reset_state()
            logger.info(f"State after reset: {ModelManager._state}")

            # Set the new model in environment
            os.environ["ZIYA_MODEL"] = found_alias
            logger.info(f"Set ZIYA_MODEL environment variable to: {found_alias}")

            # Reinitialize with agent
            try:
                new_model = ModelManager.initialize_model(force_reinit=True)
                logger.info(f"Model initialization successful: {type(new_model)}")
            except Exception as model_init_error:
                logger.error(f"Model initialization failed: {str(model_init_error)}", exc_info=True)
                raise model_init_error

            # Verify the model was actually changed by checking the model ID and updating global references
            expected_model_id = ModelManager.MODEL_CONFIGS[endpoint][found_alias]['model_id']
            actual_model_id = ModelManager.get_model_id(new_model)
            logger.info(f"Model ID verification - Expected: {expected_model_id}, Actual: {actual_model_id}")
            
            if actual_model_id != expected_model_id:
                logger.error(f"Model initialization failed - expected ID: {expected_model_id}, got: {actual_model_id}")
                # Restore previous state
                os.environ["ZIYA_MODEL"] = old_state['model_id'] if old_state['model_id'] else ModelManager.DEFAULT_MODELS["bedrock"]
                ModelManager._state.update(old_state)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to change model - expected {expected_model_id}, got {actual_model_id}"
                )
            logger.info(f"Successfully changed model to {found_alias} ({actual_model_id})")
            # update the global model reference
            global model
            model = new_model

            global agent
            global agent_executor

            # Recreate agent chain and executor with new model
            try:
                agent = create_agent_chain(new_model)
                agent_executor = create_agent_executor(agent)
                logger.info("Created new agent chain and executor")
            except Exception as agent_error:
                logger.error(f"Failed to create agent: {str(agent_error)}", exc_info=True)
                raise agent_error

            # Reinitialize langserve routes with new agent_executor
            try:
                initialize_langserve(app, agent_executor)
                logger.info("Reinitialized langserve routes")
            except Exception as langserve_error:
                logger.error(f"Failed to initialize langserve: {str(langserve_error)}", exc_info=True)
                raise langserve_error

            # Return success response
            return {
                "status": "success",
                "model": found_alias,
                "changed": True,
                "message": "Model and routes successfully updated"
            }

        except ValueError as e:
            logger.error(f"Model initialization error: {str(e)}", exc_info=True)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to initialize model {found_alias}: {str(e)}", exc_info=True)
            # Restore previous state
            logger.info(f"Restoring previous state: {old_state}")

            os.environ["ZIYA_MODEL"] = old_state['model_id'] if old_state['model_id'] else ModelManager.DEFAULT_MODELS["bedrock"]
            if old_state['model']:
                ModelManager._state.update(old_state)
            else:
                logger.warning("No previous model state to restore")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize model {found_alias}: {str(e)}"
            )

    except Exception as e:
        logger.error(f"Error in set_model: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to change model: {str(e)}")

@app.get('/api/model-capabilities')
def get_model_capabilities(model: str = None):
    """Get the capabilities of the current model."""
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    # If model parameter is provided, get capabilities for that model
    # Otherwise use current model
    model_alias = None

    if model:
        # Convert model ID to alias if needed
        if model.startswith('us.anthropic.'):
            for alias, config in ModelManager.MODEL_CONFIGS[endpoint].items():
                if config['model_id'] == model:
                    model_alias = alias
                    break
            if not model_alias:
                return {"error": f"Unknown model ID: {model}"}
        else:
            model_alias = model
    else:
        model_alias = os.environ.get("ZIYA_MODEL")

    try:
        model_config = ModelManager.get_model_config(endpoint, model_alias)
        capabilities = {
            "supports_thinking": model_config.get("supports_thinking", False),
            "max_output_tokens": model_config.get("max_output_tokens", 4096),
            "max_input_tokens": model_config.get("token_limit", 4096),
            "token_limit": model_config.get("token_limit", 4096),
            "temperature_range": {"min": 0, "max": 1, "default": model_config.get("temperature", 0.3)},
            "top_k_range": {"min": 0, "max": 500, "default": model_config.get("top_k", 15)} if endpoint == "bedrock" else None
        }
        return capabilities
    except Exception as e:
        logger.error(f"Error getting model capabilities: {str(e)}")
        return {"error": str(e)}

class ApplyChangesRequest(BaseModel):
    diff: str
    filePath: str = Field(..., description="Path to the file being modified")

    class Config:
        json_schema_extra = {
            "example": {
                "diff": "diff --git a/file.txt b/file.txt\n...",
                "filePath": "file.txt"
            }
        }
        max_str_length = 1000000  # Allow larger diffs

class ModelSettingsRequest(BaseModel):
    temperature: float = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=15, ge=0, le=500)
    max_output_tokens: int = Field(default=4096, ge=1)
    thinking_mode: bool = Field(default=False)
    max_input_tokens: Optional[int] = Field(default=None, ge=1)


class TokenCountRequest(BaseModel):
    text: str

def count_tokens_fallback(text: str) -> int:
    """Fallback methods for counting tokens when primary method fails."""
    try:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        # First try using tiktoken directly with cl100k_base (used by Claude)
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"Tiktoken fallback failed: {str(e)}")
        try:
            # Simple approximation based on whitespace-split words
            # Multiply by 1.3 as tokens are typically fewer than words
            return int(len(text.split()) * 1.3)
        except Exception as e:
            logger.error(f"All token counting methods failed: {str(e)}")
            # Return character count divided by 4 as very rough approximation
            return int(len(text) / 4)

@app.post('/api/token-count')
async def count_tokens(request: TokenCountRequest) -> Dict[str, int]:
    try:
        token_count = 0
        method_used = "unknown"

        try:
            # Try primary method first
            token_count = model.get_num_tokens(request.text)
            method_used = "primary"
        except AttributeError:
            # If primary method fails, use fallback
            logger.warning("Primary token counting method unavailable, using fallback")
            token_count = count_tokens_fallback(request.text)
            method_used = "fallback"
        except Exception as e:
            logger.error(f"Unexpected error in primary token counting: {str(e)}")
            token_count = count_tokens_fallback(request.text)
            method_used = "fallback"

        logger.info(f"Counted {token_count} tokens using {method_used} method for text length {len(request.text)}")
        return {"token_count": token_count}
    except Exception as e:
        logger.error(f"Error counting tokens: {str(e)}", exc_info=True)
        # Return 0 in case of error to avoid breaking the frontend
        return {"token_count": 0}

@app.post('/api/model-settings')
async def update_model_settings(settings: ModelSettingsRequest):
    global model
    try:
        # Log the requested settings
        logger.info(f"Requested model settings update: {settings.dict()}")

        # Get current model configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)

        # Store all settings in environment variables with ZIYA_ prefix
        for key, value in settings.dict().items():
            if value is not None:  # Only set if value is provided
                env_key = f"ZIYA_{key.upper()}"
                os.environ[env_key] = str(value)
                logger.info(f"  Set {env_key}={value}")

        # Create a kwargs dictionary with all settings
        model_kwargs = {}
        # Map settings to model parameter names
        param_mapping = {
            'temperature': 'temperature',
            'top_k': 'top_k',
            'max_output_tokens': 'max_tokens',
            # Only include max_input_tokens if the model supports it
            # This will be filtered by filter_model_kwargs if not supported
        }

        for setting_name, param_name in param_mapping.items():
            value = getattr(settings, setting_name, None)
            if value is not None:
                model_kwargs[param_name] = value
                
        # Only add max_input_tokens if the model supports it
        if model_config.get('supports_max_input_tokens', False):
            max_input_tokens = getattr(settings, 'max_input_tokens', None)
            if max_input_tokens is not None:
                model_kwargs['max_input_tokens'] = max_input_tokens

        # Filter kwargs to only include supported parameters
        filtered_kwargs = ModelManager.filter_model_kwargs(model_kwargs, model_config)
        logger.info(f"Filtered model kwargs: {filtered_kwargs}")

        # Update the model's kwargs directly
        if hasattr(model, 'model'):
            # For wrapped models (e.g., RetryingChatBedrock)
            if hasattr(model.model, 'model_kwargs'):
                # Replace the entire model_kwargs dict
                model.model.model_kwargs = filtered_kwargs
        elif hasattr(model, 'model_kwargs'):
            # For direct model instances
            model.model_kwargs = filtered_kwargs

        # Force model reinitialization to apply new settings
        model = ModelManager.initialize_model(force_reinit=True)

        # Get the model's current settings for verification
        current_kwargs = {}
        if hasattr(model, 'model') and hasattr(model.model, 'model_kwargs'):
            current_kwargs = model.model.model_kwargs
        elif hasattr(model, 'model_kwargs'):
            current_kwargs = model.model_kwargs

        logger.info("Current model settings after update:")
        for key, value in current_kwargs.items():
            logger.info(f"  {key}: {value}")

        return {
            'status': 'success',
            'message': 'Model settings updated',
            'settings': current_kwargs
        }
    except Exception as e:
        logger.error(f"Error updating model settings: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error updating model settings: {str(e)}"
        )

@app.post('/api/apply-changes')
async def apply_changes(request: ApplyChangesRequest):
    try:
        # Validate diff size
        if len(request.diff) < 100:  # Arbitrary minimum for a valid git diff
            logger.warning(f"Suspiciously small diff received: {len(request.diff)} bytes")
            logger.warning(f"Diff content: {request.diff}")

        logger.info(f"Received request to apply changes to file: {request.filePath}")
        logger.info(f"Raw request diff length: {len(request.diff)} bytes")
        logger.info("First 100 chars of raw diff:")
        logger.info(request.diff[:100])
        logger.info(f"Full diff content: \n{request.diff}")
        
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        
        # Prioritize extracting the file path from the diff content itself
        extracted_path = extract_target_file_from_diff(request.diff)

        if extracted_path:
            file_path = os.path.join(user_codebase_dir, extracted_path)
            logger.info(f"Extracted target file from diff: {extracted_path}")
        elif request.filePath:
            # Fallback to using the provided filePath if extraction fails
            file_path = os.path.join(user_codebase_dir, request.filePath)
            logger.info(f"Using provided file path: {request.filePath}")
        else:
            raise ValueError("Could not determine target file path from diff or request")

        # Extract individual diffs if multiple are present
        individual_diffs = split_combined_diff(request.diff)
        if len(individual_diffs) > 1:
            logger.info(f"Received combined diff with {len(individual_diffs)} files")
            # Find the diff for our target file
            logger.debug("Individual diffs:")
            logger.debug('\n'.join(individual_diffs))
            target_diff = None
            for diff in individual_diffs:
                target_file = extract_target_file_from_diff(diff)
                if target_file and os.path.normpath(target_file) == os.path.normpath(extracted_path or request.filePath):
                    target_diff = diff
                    break

            if not target_diff:
                raise HTTPException(
                    status_code=400,
                    detail={
                        'status': 'error',
                        'type': 'file_not_found',
                        'message': f'No diff found for requested file {request.filePath} in combined diff'
                    }
                )
        else:
            logger.info("Single diff found")
            target_diff = individual_diffs[0]

        request.diff = target_diff
        use_git_to_apply_code_diff(request.diff, file_path)
        return {'status': 'success', 'message': 'Changes applied successfully'}
    except Exception as e:
        error_msg = str(e)
        if isinstance(e, PatchApplicationError):
            details = e.details
            logger.error(f"Patch application failed:")
            logger.error(f"  Patch command error: {details.get('patch_error', 'N/A')}")
            logger.error(f"  Git apply error: {details.get('git_error', 'N/A')}")
            logger.error(f"  Analysis: {json.dumps(details.get('analysis', {}), indent=2)}")

            status = details.get('status', 'error')
            if status == 'success':
                return JSONResponse(status_code=200, content={
                    'status': 'success',
                    'message': 'Changes applied successfully',
                    'details': details
                })
            elif status == 'partial':
                return JSONResponse(status_code=207, content={
                    'status': 'partial',
                    'message': str(e),
                    'details': details
                })
            elif status == 'error':
                error_type = details.get('type', 'unknown')
                if error_type == 'no_hunks':
                    status_code = 400  # Bad Request
                elif error_type == 'invalid_count':
                    status_code = 500  # Internal Server Error
                else:
                    status_code = 422  # Unprocessable Entity

                # Format error response based on whether we have multiple failures
                error_content = {
                    'status': 'error',
                    'message': str(e)
                }
                if 'failures' in details:
                    error_content['failures'] = details['failures']
                else:
                    error_content['details'] = details

                raise HTTPException(status_code=status_code, detail={
                    'status': 'error',
                    **error_content
                })
        logger.error(f"Error applying changes: {error_msg}")
        raise HTTPException(
            status_code=500,
            detail={
                'status': 'error',
                'message': f"Unexpected error: {str(e)}"
            }
        )


