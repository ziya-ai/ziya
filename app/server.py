import os
import time
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


# import pydevd_pycharm
import uvicorn

from app.utils.code_util import use_git_to_apply_code_diff, correct_git_diff
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

@app.exception_handler(CredentialRetrievalError)
async def credential_exception_handler(request: Request, exc: CredentialRetrievalError):
    # Pass through the original error message which may contain helpful authentication instructions
    error_message = str(exc)
    return JSONResponse(
        status_code=401,
        content={"detail": f"AWS credential error: {error_message}"}
    )


@app.exception_handler(ClientError)
async def boto_client_exception_handler(request: Request, exc: ClientError):
    error_message = str(exc)
    if "ExpiredTokenException" in error_message or "InvalidIdentityTokenException" in error_message:
        return JSONResponse(
            status_code=401,
            content={"detail": "AWS credentials have expired. Please refresh your credentials."}
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
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})

app.mount("/static", StaticFiles(directory="../templates/static"), name="static")
templates = Jinja2Templates(directory="../templates")

# Add a route for the frontend
add_routes(app, agent_executor, disabled_endpoints=["playground"], path="/ziya")


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "diff_view_type": os.environ.get("ZIYA_DIFF_VIEW_TYPE", "unified")
    })


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse('../templates/favicon.ico')


# Cache for folder structure with timestamp
_folder_cache = {'timestamp': 0, 'data': None}

def get_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    should_ignore_fn = parse_gitignore_patterns(ignored_patterns)

    def count_tokens(file_path: str) -> int:
        try:
            with open(file_path, 'r') as file:
                content = file.read()
                return len(tiktoken.get_encoding("cl100k_base").encode(content))
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {str(e)}", exc_info=True)
            return 0

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

@app.post('/api/token-count')
async def count_tokens(request: TokenCountRequest) -> Dict[str, int]:
    try:
        # Use the existing model instance to count tokens
        token_count = model.get_num_tokens(request.text)
        logger.info(f"Counted {token_count} tokens for text length {len(request.text)}")
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
        corrected_diff = correct_git_diff(request.diff, file_path)
        logger.info(f"corrected diff content: \n{corrected_diff}")
        use_git_to_apply_code_diff(corrected_diff)
        return {'message': 'Changes applied successfully'}
    except Exception as e:
        logger.error(f"Error applying changes: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
