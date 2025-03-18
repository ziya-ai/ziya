import os
import time
import json
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
from app.agents.agent import update_conversation_state, update_and_return
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError 
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.agents.models import ModelManager
from botocore.exceptions import ClientError, BotoCoreError, CredentialRetrievalError
from botocore.exceptions import EventStreamError
import botocore.errorfactory
from starlette.responses import StreamingResponse

# import pydevd_pycharm
from google.api_core.exceptions import ResourceExhausted
import uvicorn

from app.utils.code_util import use_git_to_apply_code_diff, correct_git_diff
from app.utils.code_util import PatchApplicationError, split_combined_diff
from app.utils.directory_util import get_ignored_patterns
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns

# Server configuration defaults
DEFAULT_PORT = 6969
# For model configurations, see app/agents/model.py

class SetModelRequest(BaseModel):
    model_id: str

app = FastAPI(
    title="Ziya API",
    description="Code assistant API",
    docs_url="/docs"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request size middleware
from app.middleware import RequestSizeMiddleware

app.add_middleware(RequestSizeMiddleware)


@app.exception_handler(EventStreamError)
async def eventstream_exception_handler(request: Request, exc: EventStreamError):
    error_message = str(exc)
    if "validationException" in error_message:
        return JSONResponse(
            status_code=413,  # Request Entity Too Large
            headers={"Content-Type": "application/json"},
            content={
                "error": "validation_error",
                "detail": "Selected content is too large for the model. Please reduce the number of files.",
                "original_error": error_message
            }
        )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 413:  # Entity Too Large
        return JSONResponse(
            status_code=413,
            headers={"Content-Type": "application/json"},
            content={
                "error": "validation_error",
                "detail": exc.detail
            }
        )
    raise exc

@app.exception_handler(CredentialRetrievalError)
async def credential_exception_handler(request: Request, exc: CredentialRetrievalError):
    # Pass through the original error message which may contain helpful authentication instructions
    error_message = str(exc)
    return JSONResponse(
        status_code=401,
        content={"detail": f"AWS credential error: {error_message}"},
        headers={"WWW-Authenticate": "Bearer"}
    )

@app.exception_handler(ClientError)
async def boto_client_exception_handler(request: Request, exc: ClientError):
    error_message = str(exc)
    if "ExpiredTokenException" in error_message or "InvalidIdentityTokenException" in error_message:
        return JSONResponse(
            status_code=401,
            content={"detail": "AWS credentials have expired. Please refresh your credentials."},
            headers={"WWW-Authenticate": "Bearer"}
        )
    elif "ValidationException" in error_message:
        logger.error(f"Bedrock validation error: {error_message}")
        return JSONResponse(
            status_code=400,
            content={"error": "validation_error",
                    "detail": "Invalid request format for Bedrock service. Please check your input format.",
                    "message": error_message})
    elif "ServiceUnavailableException" in error_message:
        return JSONResponse(
            status_code=503,
            content={"detail": "AWS Bedrock service is temporarily unavailable. This usually happens when the service is experiencing high load. Please wait a moment and try again."}
        )
    return JSONResponse(
        status_code=500,
        content={"detail": f"AWS Service Error: {str(exc)}"}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    error_message = str(exc)
    status_code = 500
    error_type = "unknown_error"
    
    # Check for empty text parameter error from Gemini
    if "Unable to submit request because it has an empty text parameter" in error_message:
        logger.error("Caught empty text parameter error from Gemini")
        return JSONResponse(
            status_code=400,
            content={
                "error": "validation_error",
                "detail": "Empty message content detected. Please provide a question."
            }
        )
    
    # Check for Google API quota exceeded error
    if "Resource has been exhausted" in error_message and "check quota" in error_message:
        return JSONResponse(
            status_code=429,  # Too Many Requests
            content={
                "error": "quota_exceeded",
                "detail": "API quota has been exceeded. Please try again in a few minutes."
            })
    
    # Check for Gemini token limit error
    if isinstance(exc, ChatGoogleGenerativeAIError) and "token count" in error_message:
        return JSONResponse(
            status_code=413,
            content={
                "error": "validation_error",
                "detail": "Selected content is too large for the model. Please reduce the number of files."
            }
        )
    
    # Check for Google API quota exceeded error
    if "Resource has been exhausted" in error_message and "check quota" in error_message:
        return JSONResponse(
            status_code=429,  # Too Many Requests
            content={
                "error": "quota_exceeded",
                "detail": "API quota has been exceeded. Please try again in a few minutes."
            })

    try:
        # Check if this is a streaming error
        if isinstance(exc, EventStreamError):
            if "validationException" in error_message:
                status_code = 413
                error_type = "validation_error"
                error_message = "Selected content is too large for the model. Please reduce the number of files."
        elif isinstance(exc, ExceptionGroup):
            # Handle nested exceptions
            for e in exc.exceptions:
                if isinstance(e, EventStreamError) and "validationException" in str(e):
                    status_code = 413
                    error_type = "validation_error"
                    error_message = "Selected content is too large for the model. Please reduce the number of files."
                    break
        logger.error(f"Exception handler: type={error_type}, status={status_code}, message={error_message}")

        return JSONResponse(
            status_code=status_code,
            content={"error": error_type, "detail": error_message}
        )
    except Exception as e:
        logger.error(f"Error in exception handler: {str(e)}", exc_info=True)
        raise

# Get the absolute path to the project root directory
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Define paths relative to project root
static_dir = os.path.join(project_root, "templates", "static")
testcases_dir = os.path.join(project_root, "tests", "frontend", "testcases")
templates_dir = os.path.join(project_root, "templates")

# Create directories if they don't exist
os.makedirs(static_dir, exist_ok=True)
os.makedirs(testcases_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

# Mount static files and templates
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Only mount testcases directory if it exists
testcases_dir = "../tests/frontend/testcases"
if os.path.exists(testcases_dir):
    app.mount("/testcases", StaticFiles(directory=testcases_dir), name="testcases")
else:
    logger.debug(f"Testcases directory '{testcases_dir}' does not exist - skipping mount")

templates = Jinja2Templates(directory=templates_dir)

# Add a route for the frontend
add_routes(app, agent_executor, disabled_endpoints=["playground"], path="/ziya")
# Override the stream endpoint with our error handling
@app.post("/ziya/stream")
async def stream_endpoint(body: dict):

    # Debug logging
    logger.info("Stream endpoint request body:")
    logger.info(f"Question: '{body.get('question', 'EMPTY')}'")
    logger.info(f"Chat history length: {len(body.get('chat_history', []))}")
    logger.info(f"Files count: {len(body.get('config', {}).get('files', []))}")
    logger.info(f"Question type: {type(body.get('question', None))}")

    # Log the first few files
    if 'config' in body and 'files' in body['config']:
        logger.info(f"First few files: {body['config']['files'][:5]}")

    # Check if the question is empty or missing
    if not body.get("question") or not body.get("question").strip():
        logger.warning("Empty question detected, returning error response")
        error_response = json.dumps({
            "error": "validation_error",
            "detail": "Please provide a question to continue."
        })

        # Return a properly formatted SSE response with the error
        async def error_stream():
            # Send the error message
            yield f"data: {error_response}\n\n"
            # Wait a moment to ensure the client receives it
            await asyncio.sleep(0.1)
            # Send an end message
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"}
        )
    try:
        # Check for empty question
        if not body.get("question") or not body.get("question").strip():
            logger.warning("Empty question detected in stream request")
            # Return a friendly error message
            return StreamingResponse(
                iter([f'data: {json.dumps({"error": "validation_error", "detail": "Please enter a question"})}' + '\n\n']),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"}
            )
            
        # Check for empty messages in chat history
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
            logger.debug(f"Cleaned chat history: {json.dumps(cleaned_history)}")
            
        logger.info("Starting stream endpoint with body size: %d", len(str(body)))
        # Define the streaming response with proper error handling
        async def error_handled_stream():
            response = None
            try:
                # Convert to ChatPromptValue before streaming
                if isinstance(body, dict) and "messages" in body:
                    from langchain_core.prompt_values import ChatPromptValue
                    from langchain_core.messages import HumanMessage
                    body["messages"] = [HumanMessage(content=msg) for msg in body["messages"]]
                    body = ChatPromptValue(messages=body["messages"])
                # Create the iterator inside the error handling context
                iterator = agent_executor.astream_log(body)
                async for chunk in iterator:
                    if isinstance(chunk, dict) and "error" in chunk:
                        # Format error as SSE message
                        yield f"data: {json.dumps(chunk)}\n\n"
                        
                    elif isinstance(chunk, Generation) and hasattr(chunk, 'text') and "quota_exceeded" in chunk.text:
                        yield f"data: {chunk.text}\n\n"
                        update_and_return(body)
                        return
                        
                    else:
                        try:
                            # Parse and clean the chunk before sending
                            parsed_chunk = parse_output(chunk)
                            if parsed_chunk and parsed_chunk.return_values:
                                cleaned_output = parsed_chunk.return_values.get("output", "")
                                if cleaned_output:
                                    yield f"data: {cleaned_output}\n\n"
                                    continue
                            
                            # Fall back to original chunk if parsing fails
                            chunk_content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                            yield f"data: {chunk.content if hasattr(chunk, 'content') else str(chunk)}\n\n"

                            await response.flush()
                        except EventStreamError as e:
                            if "validationException" in str(e):
                                error_msg = {
                                    "error": "validation_error",
                                    "detail": "Selected content is too large for the model. Please reduce the number of files."
                                }
                                yield f"data: {json.dumps(error_msg)}\n\n"
                                update_and_return(body)
                                await response.flush()
                                logger.info("Sent EventStreamError message: %s", error_msg)
                                return
                        except ChatGoogleGenerativeAIError as e:
                            if "token count" in str(e):
                                error_msg = {
                                    "error": "validation_error",
                                    "detail": "Selected content is too large for the model. Please reduce the number of files."
                                }
                                yield f"data: {json.dumps(error_msg)}\n\n"
                                update_and_return(body)
                                await response.flush()
                                logger.info("Sent token limit error message: %s", error_msg)
                                return
            except ResourceExhausted as e:
                error_msg = {
                    "error": "quota_exceeded",
                    "detail": "API quota has been exceeded. Please try again in a few minutes."
                }
                yield f"data: {json.dumps(error_msg)}\n\n"
                update_and_return(body)
                logger.error(f"Caught ResourceExhausted error: {str(e)}")
                return
            except EventStreamError as e:
                if "validationException" in str(e):
                    error_msg = {
                        "error": "validation_error",
                        "detail": "Selected content is too large for the model. Please reduce the number of files."
                    }
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    update_and_return(body)
                    await response.flush()
                    return
                raise
            finally:
                update_and_return(body)
        return StreamingResponse(error_handled_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    except Exception as e:
        logger.error(f"Error in stream endpoint: {str(e)}")
        error_msg = {"error": "stream_error", "detail": str(e)}
        logger.error(f"Sending error response: {error_msg}")
        update_and_return(body)
        return StreamingResponse(iter([f"data: {json.dumps(error_msg)}\n\n"]), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        


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
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)

    def count_tokens(file_path: str) -> int:
        try:
            # Skip binary files by extension
            binary_extensions = {
                '.pyc', '.pyo', '.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg',
                '.core', '.bin', '.exe', '.dll', '.so', '.dylib', '.class',
                '.pyd', '.woff', '.woff2', '.ttf', '.eot'
            }

            if any(file_path.endswith(ext) for ext in binary_extensions):
                logger.debug(f"Skipping binary file by extension: {file_path}")
                return 0

            # Try to detect if file is binary by reading first few bytes
            with open(file_path, 'rb') as file:
                content_bytes = file.read(1024)
                if b'\x00' in content_bytes:  # Binary file detection
                    return 0

            # If not binary, read as text
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
                return len(tiktoken.get_encoding("cl100k_base").encode(content))
        except (UnicodeDecodeError, IOError) as e:
            logger.debug(f"Skipping binary or unreadable file {file_path}: {str(e)}")
            return 0 # Skip files that can't be read as text

    def get_structure(current_dir: str, current_depth: int):
        if current_depth > max_depth:
            return None

        current_structure = {}
        for entry in os.listdir(current_dir):
            if entry.startswith('.'):  # Skip hidden files/folders
                continue
            entry_path = os.path.join(current_dir, entry)
            if os.path.islink(entry_path):  # Skip symbolic links
                continue
            if os.path.isdir(entry_path):
                if not should_ignore_fn(entry_path):
                    sub_structure = get_structure(entry_path, current_depth + 1)
                    if sub_structure is not None:
                        token_count = sum(sub_structure[key]['token_count'] for key in sub_structure)
                        current_structure[entry] = {'token_count': token_count, 'children': sub_structure}
            else:
                if not should_ignore_fn(entry_path):
                    token_count = count_tokens(entry_path)
                    current_structure[entry] = {'token_count': token_count}

        return current_structure

    folder_structure = get_structure(directory, 1)
    return folder_structure

def get_cached_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    current_time = time.time()
    cache_age = current_time - _folder_cache['timestamp']

    # Refresh cache if older than 10 seconds
    if _folder_cache['data'] is None or cache_age > 10:
        _folder_cache['data'] = get_folder_structure(directory, ignored_patterns, max_depth)
        _folder_cache['timestamp'] = current_time
        logger.info("Refreshed folder structure cache")

    return _folder_cache['data']

@app.get("/api/folders")
async def get_folders():
    # pydevd_pycharm.settrace('localhost', port=59939, stdoutToServer=True, stderrToServer=True)
    user_codebase_dir = os.environ["ZIYA_USER_CODEBASE_DIR"]
    max_depth = int(os.environ.get("ZIYA_MAX_DEPTH"))
    ignored_patterns: List[Tuple[str, str]] = get_ignored_patterns(user_codebase_dir)
    return get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth)

@app.get('/api/default-included-folders')

@app.get('/api/current-model')
def get_current_model():
    """Get detailed information about the currently active model."""
    logger.info(
        "Current model info request - Environment state: %s",
        {   'model_id': ModelManager.get_model_id(model),
            'endpoint': os.environ.get("ZIYA_ENDPOINT", "bedrock")
        })

    # Get actual model settings
    model_kwargs = {}
    # Get model settings through ModelManager
    model_kwargs = ModelManager.get_model_settings(model)

    logger.info("Current model configuration:")
    logger.info(f"  Model ID: {ModelManager.get_model_id(model)}")
    logger.info(f"  Temperature: {model_kwargs.get('temperature', 'Not set')} (env: {os.environ.get('ZIYA_TEMPERATURE', 'Not set')})")
    logger.info(f"  Top K: {model_kwargs.get('top_k', 'Not set')} (env: {os.environ.get('ZIYA_TOP_K', 'Not set')})")
    logger.info(f"  Max tokens: {model_kwargs.get('max_tokens', 'Not set')} (env: {os.environ.get('ZIYA_MAX_OUTPUT_TOKENS', 'Not set')})")
    logger.info(f"  Thinking mode: {os.environ.get('ZIYA_THINKING_MODE', 'Not set')}")
        
    return {
        'model_id': ModelManager.get_model_id(model),
        'endpoint': os.environ.get("ZIYA_ENDPOINT", "bedrock"),
        'settings': {
            'temperature': model_kwargs.get('temperature', 
                float(os.environ.get("ZIYA_TEMPERATURE", 0.3))),
            'max_output_tokens': model_kwargs.get('max_tokens',
                int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))),
            'top_k': model_kwargs.get('top_k',
                int(os.environ.get("ZIYA_TOP_K", 15))),
            'thinking_mode': os.environ.get("ZIYA_THINKING_MODE") == "1"
        }
    }

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
        for alias, config in ModelManager.MODEL_CONFIGS[endpoint].items():
            if config['model_id'] == model_id or alias == model_id:
                found_alias = alias
                break
        
        if not found_alias:
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
            new_model = ModelManager.initialize_model(force_reinit=True)
            
            # Verify the model was actually changed by checking the model ID and updating global references
            expected_model_id = ModelManager.MODEL_CONFIGS[endpoint][found_alias]['model_id']
            actual_model_id = ModelManager.get_model_id(new_model)
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
            agent = create_agent_chain(new_model)
            agent_executor = create_agent_executor(agent)

            logger.info("Created new agent chain and executor")
            # Reinitialize langserve routes with new agent_executor
            initialize_langserve(app, agent_executor)
            
            # Return success response
            return {
                "status": "success",
                "model": found_alias,
                "changed": True,
                "message": "Model and routes successfully updated"
            }
            
        except ValueError as e:
            logger.error(f"Model initialization error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to initialize model {found_alias}: {str(e)}")
            # Restore previous state
            logger.error("Exception details:", exc_info=True)
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
        raise HTTPException(status_code=500, detail=str(e))

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
            'max_input_tokens': 'max_input_tokens',
            # Add more mappings as needed
        }
        
        for setting_name, param_name in param_mapping.items():
            value = getattr(settings, setting_name, None)
            if value is not None:
                model_kwargs[param_name] = value
            
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=DEFAULT_PORT)
