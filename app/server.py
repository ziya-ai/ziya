import os
import time
import json
from typing import Dict, Any, List, Tuple, Optional

import tiktoken
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langserve import add_routes
from app.agents.agent import model
from app.agents.agent import agent_executor
from fastapi.responses import FileResponse
from pydantic import BaseModel
from botocore.exceptions import ClientError, BotoCoreError, CredentialRetrievalError
from botocore.exceptions import EventStreamError
from starlette.responses import StreamingResponse

# import pydevd_pycharm
import uvicorn

from app.utils.code_util import use_git_to_apply_code_diff, correct_git_diff, PatchApplicationError
from app.utils.directory_util import get_ignored_patterns
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

app.mount("/static", StaticFiles(directory="../templates/static"), name="static")

# Only mount testcases directory if it exists
testcases_dir = "../tests/frontend/testcases"
if os.path.exists(testcases_dir):
    app.mount("/testcases", StaticFiles(directory=testcases_dir), name="testcases")
else:
    logger.info(f"Testcases directory '{testcases_dir}' does not exist - skipping mount")

templates = Jinja2Templates(directory="../templates")

# Add a route for the frontend
add_routes(app, agent_executor, disabled_endpoints=["playground"], path="/ziya")
# Override the stream endpoint with our error handling
@app.post("/ziya/stream")
async def stream_endpoint(body: dict):
    try:
        logger.info("Starting stream endpoint with body size: %d", len(str(body)))
        # Define the streaming response with proper error handling
        async def error_handled_stream():
            try:
                # Create the iterator inside the error handling context
                iterator = agent_executor.astream_log(body, {})
                async for chunk in iterator:
                    logger.info("Processing chunk: %s",
                              chunk if isinstance(chunk, dict) else chunk[:200] + "..." if len(chunk) > 200 else chunk)
                    if isinstance(chunk, dict) and "error" in chunk:
                        # Format error as SSE message
                        yield f"data: {json.dumps(chunk)}\n\n"
                        logger.info("Sent error message: %s", error_msg)
                        return
                    else:
                        try:
                            yield chunk
                            await response.flush()
                        except EventStreamError as e:
                            if "validationException" in str(e):
                                error_msg = {
                                    "error": "validation_error",
                                    "detail": "Selected content is too large for the model. Please reduce the number of files."
                                }
                                yield f"data: {json.dumps(error_msg)}\n\n"
                                await response.flush()
                                logger.info("Sent EventStreamError message: %s", error_msg)
                                return
            except EventStreamError as e:
                if "validationException" in str(e):
                    error_msg = {
                        "error": "validation_error",
                        "detail": "Selected content is too large for the model. Please reduce the number of files."
                    }
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    await response.flush()
                    return
                raise
        return StreamingResponse(error_handled_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    except Exception as e:
        logger.error(f"Error in stream endpoint: {str(e)}")
        error_msg = {"error": "stream_error", "detail": str(e)}
        logger.error(f"Sending error response: {error_msg}")
        logger.error(f"Sending error response: {error_msg}")
        return StreamingResponse(iter([f"data: {json.dumps(error_msg)}\n\n"]), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
        


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "diff_view_type": os.environ.get("ZIYA_DIFF_VIEW_TYPE", "unified")
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
def get_default_included_folders():
    return {'defaultIncludedFolders': []}

@app.get('/api/model-id')
def get_model_id():
    # Get the model ID from the configured Bedrock client
    return {'model_id': model.model_id.split(':')[0].split('/')[-1]}

class ApplyChangesRequest(BaseModel):
    diff: str
    filePath: str

class TokenCountRequest(BaseModel):
    text: str

def count_tokens_fallback(text: str) -> int:
    """Fallback methods for counting tokens when primary method fails."""
    try:
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

@app.post('/api/apply-changes')
async def apply_changes(request: ApplyChangesRequest):
    try:
        logger.info(f"Received request to apply changes to file: {request.filePath}")
        logger.info(f"Diff content: \n{request.diff}")

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

                raise HTTPException(status_code=status_code, detail={
                    'status': 'error',
                    'message': str(e),
                    'details': details
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
