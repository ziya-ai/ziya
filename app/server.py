import os
import time
import json
import asyncio
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
add_routes(app, agent_executor, disabled_endpoints=["playground"], path="/ziya")
# Override the stream endpoint with our error handling
@app.post("/ziya/stream")
async def stream_endpoint(request: Request, body: dict):
    """Stream endpoint with centralized error handling."""
    try:
        # Debug logging
        logger.info("Stream endpoint request body:")
        logger.info(f"Question: '{body.get('question', 'EMPTY')}'")
        logger.info(f"Chat history length: {len(body.get('chat_history', []))}")
        logger.info(f"Files count: {len(body.get('config', {}).get('files', []))}")

        # Check if the question is empty or missing
        if not body.get("question") or not body.get("question").strip():
            logger.warning("Empty question detected")
            raise ValidationError("Please provide a question to continue.")
            
        # Clean chat history if present
        if "chat_history" in body:
            cleaned_history = []
            for pair in body["chat_history"]:
                try:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        logger.warning(f"Invalid chat history pair format: {pair}")
                        continue
                        
                    human, ai = pair
                    if not isinstance(human, str) or not isinstance(ai, str):
                        logger.warning(f"Non-string message in pair: human={type(human)}, ai={type(ai)}")
                        continue
                        
                    if human.strip() and ai.strip():
                        cleaned_history.append((human.strip(), ai.strip()))
                    else:
                        logger.warning(f"Empty message in pair: {pair}")
                except Exception as e:
                    logger.error(f"Error processing chat history pair: {str(e)}")
            
            logger.debug(f"Cleaned chat history from {len(body['chat_history'])} to {len(cleaned_history)} pairs")
            body["chat_history"] = cleaned_history
            
        logger.info("Starting stream endpoint with body size: %d", len(str(body)))
        
        # Convert to ChatPromptValue if needed
        if isinstance(body, dict) and "messages" in body:
            from langchain_core.prompt_values import ChatPromptValue
            from langchain_core.messages import HumanMessage
            body["messages"] = [HumanMessage(content=msg) for msg in body["messages"]]
            body = ChatPromptValue(messages=body["messages"])
        
        # Return the streaming response
        return StreamingResponse(
            stream_agent_response(body, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"}
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
                        yield f"data: {cleaned_output}\n\n"
                        continue
                
                # Fall back to original chunk if parsing fails
                chunk_content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                first_chunk = False
                yield f"data: {chunk_content}\n\n"
            except Exception as e:
                logger.error(f"Error processing chunk: {e}")
                continue
        
        # Send the [DONE] marker
        yield "data: [DONE]\n\n"
        
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
        result = use_git_to_apply_code_diff(request.diff, target_file)
        return {"success": True, "result": result}
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
                if target_file and os.path.normpath(target_file) == os.path.normpath(request.filePath):
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
        logger.info(f"Using diff for {request.filePath}")

        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        if not user_codebase_dir:
            raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set")

        file_path = os.path.join(user_codebase_dir, request.filePath)
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


