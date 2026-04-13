"""
Diff application, file validation, export, and context management routes.

Extracted from server.py during Phase 3b refactoring.
"""
import os
import json
import time
import uuid
import asyncio
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from app.utils.logging_utils import logger
from app.utils.code_util import extract_target_file_from_diff, split_combined_diff
from app.utils.code_util import PatchApplicationError
from app.utils.diff_utils import apply_diff_pipeline
from app.utils.diff_utils.pipeline.reverse_pipeline import apply_reverse_diff_pipeline
from app.services.folder_service import is_path_explicitly_allowed
from app.utils.conversation_exporter import export_conversation_for_paste

router = APIRouter(tags=["diff"])

class ApplyChangesRequest(BaseModel):
    diff: str
    filePath: str = Field(..., description="Path to the file being modified")
    requestId: Optional[str] = Field(None, description="Unique ID to track this specific diff application")
    projectRoot: Optional[str] = Field(None, description="Root directory for the project (client-specific)")
    elementId: Optional[str] = None
    buttonInstanceId: Optional[str] = None

    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "example": {
                "diff": "diff --git a/file.txt b/file.txt\n...",
                "filePath": "file.txt"
            }
        },
        "str_max_length": 1000000  # Allow larger diffs
    }


@router.post('/api/apply-changes')
async def apply_changes(request: Request):
    try:
        # Parse body manually to debug
        body = await request.json()
        logger.info(f"Raw apply-changes body: {body}")
        
        # Validate manually
        try:
            validated = ApplyChangesRequest(**body)
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return JSONResponse(status_code=422, content={"detail": str(e)})
        
        logger.info(f"TRACE_ID: Received apply-changes request with ID: {validated.requestId}")
        # Validate diff size
        if len(validated.diff) < 100:  # Arbitrary minimum for a valid git diff
            logger.warning(f"Suspiciously small diff received: {len(validated.diff)} bytes")
            logger.warning(f"Diff content: {validated.diff}")

        logger.info(f"Received request to apply changes to file: {validated.filePath}")
        logger.info(f"Raw request diff length: {len(validated.diff)} bytes")
        logger.info(f"First 100 chars of raw diff for request {validated.requestId}:")
        
        # Always use the client-provided request ID if available
        if validated.requestId:
            request_id = validated.requestId
            logger.info(f"Using client-provided request ID: {request_id}")
        else:
            # Only generate a server-side ID if absolutely necessary
            request_id = str(uuid.uuid4())
            logger.warning(f"Using server-side generated request ID: {request_id}")

        logger.info(validated.diff[:100])
        logger.info(f"Full diff content: \n{validated.diff}")

        # Use client-provided projectRoot if available, otherwise fall back to environment variable
        if validated.projectRoot:
            user_codebase_dir = os.path.abspath(validated.projectRoot)
            logger.info(f"Using client-provided project root: {user_codebase_dir}")
        else:
            env_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
            if not env_codebase_dir:
                raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set and no projectRoot provided")
            user_codebase_dir = os.path.abspath(env_codebase_dir)
            logger.info(f"Using environment variable project root: {user_codebase_dir}")
        
        if not os.path.isdir(user_codebase_dir):
            raise ValueError(f"Project root directory does not exist: {user_codebase_dir}")
        
        # Prioritize extracting the file path from the diff content itself
        extracted_path = extract_target_file_from_diff(validated.diff)

        if extracted_path:
            file_path = os.path.join(user_codebase_dir, extracted_path)
            logger.info(f"Extracted target file from diff: {extracted_path}")
        elif validated.filePath:
            # Fallback to using the provided filePath if extraction fails
            file_path = os.path.join(user_codebase_dir, validated.filePath)
            logger.info(f"Using provided file path: {validated.filePath}")

            # Resolve the absolute path and verify it is within an allowed root
            resolved_path = os.path.abspath(file_path)
            if not is_path_explicitly_allowed(resolved_path, user_codebase_dir):
                logger.error(f"Attempt to access file outside codebase directory: {resolved_path}")
                raise ValueError("Invalid file path specified")
        else:
            raise ValueError("Could not determine target file path from diff or request")

        # Extract individual diffs if multiple are present
        individual_diffs = split_combined_diff(validated.diff)
        if len(individual_diffs) > 1:
            logger.info(f"Received combined diff with {len(individual_diffs)} files")
            # Find the diff for our target file
            logger.debug("Individual diffs:")
            logger.debug('\n'.join(individual_diffs))
            target_diff = None
            for diff in individual_diffs:
                target_file = extract_target_file_from_diff(diff)
                if target_file and os.path.normpath(target_file) == os.path.normpath(extracted_path or validated.filePath):
                    target_diff = diff
                    break

            if not target_diff:
                raise HTTPException(
                    status_code=400,
                    detail={
                        'status': 'error',
                        'type': 'file_not_found',
                        'message': f'No diff found for requested file {validated.filePath} in combined diff'
                    }
                )
        else:
            logger.info("Single diff found")
            target_diff = individual_diffs[0]

        # Run in thread pool to avoid blocking the event loop and allow parallel processing
        result = await run_in_threadpool(apply_diff_pipeline, validated.diff, file_path, request_id)
        
        # Check the result status and return appropriate response
        status_code = 200 # Default to OK
        if result.get('status') == 'error':
            # Determine appropriate error code
            error_message = result.get('message', '').lower()
            if "file does not exist" in error_message:
                status_code = 404 # Not Found
            elif "malformed" in error_message or "failed to apply" in error_message:
                status_code = 422 # Unprocessable Entity
            else:
                status_code = 500 # Internal Server Error
        elif result.get('status') == 'partial':
            status_code = 207 # Multi-Status
 
        return JSONResponse(content=result, status_code=status_code)

    except Exception as e:
        error_msg = str(e)
        if isinstance(e, PatchApplicationError):
            details = e.details
            logger.error(f"Patch application failed:")
            status = details.get('status', 'error')
            if status == 'success':
                return JSONResponse(status_code=200, content={
                    'status': 'success',
                    'message': 'Changes applied successfully',
                    'request_id' : request_id,
                    'details': details
                })
            elif status == 'partial':
                return JSONResponse(status_code=207, content={
                    'status': 'partial',
                    'message': str(e),
                    'request_id' : request_id,
                    'details': details
                })
            elif status == 'error':
                error_type = details.get('type', 'unknown')
                if error_type == 'no_hunks':
                    status_code = 400  # Bad Request
                elif error_type == 'invalid_count':
                    status_code = 500  # Internal Server Error
                elif error_type == 'missing_file':
                    status_code = 404 # Not Found
                else:
                    status_code = 422  # Unprocessable Entity

                # Format error response based on whether we have multiple failures
                error_content = {
                    'status': 'error',
                    'message': str(e),
                    'request_id': request_id
                }
                if 'failures' in details:
                    error_content['failures'] = details['failures']
                else:
                    error_content['details'] = details

                raise HTTPException(status_code=status_code, detail={
                    'status': 'error',
                    'request_id': request_id,
                    **error_content
                })
        logger.error(f"Error applying changes: {error_msg}")
        if isinstance(e, FileNotFoundError):
             status_code = 404
        elif isinstance(e, ValueError): # e.g., invalid path
             status_code = 400 # Bad Request
        else:
            status_code = 500 # Default Internal Server Error
        raise HTTPException(
            # Determine status code based on exception type if possible
            status_code = status_code,
            detail={
                'status': 'error',
                'request_id': request_id,
                'message': f"Unexpected error: {error_msg}"
            }
        )


@router.post('/api/unapply-changes')
async def unapply_changes(request: Request):
    """Reverse/unapply a previously applied diff."""
    try:
        body = await request.json()
        diff = body.get('diff', '')
        file_path_from_request = body.get('filePath', '')
        project_root_from_request = body.get('projectRoot', '')
        request_id = body.get('requestId', str(uuid.uuid4()))
        
        logger.info(f"Received unapply-changes request with ID: {request_id}")
        
        # Use client-provided projectRoot if available, otherwise fall back to environment variable
        if project_root_from_request:
            user_codebase_dir = os.path.abspath(project_root_from_request)
            logger.info(f"Using client-provided project root for unapply: {user_codebase_dir}")
        else:
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
            if not user_codebase_dir:
                raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set and no projectRoot provided")
            user_codebase_dir = os.path.abspath(user_codebase_dir)
            logger.info(f"Using environment variable project root for unapply: {user_codebase_dir}")
        
        # Extract file path from diff or use provided path
        extracted_path = extract_target_file_from_diff(diff)
        if extracted_path:
            file_path = os.path.join(user_codebase_dir, extracted_path)
        elif file_path_from_request:
            file_path = os.path.join(user_codebase_dir, file_path_from_request)
        else:
            raise ValueError("Could not determine target file path")
        
        # Validate path is within codebase
        resolved_path = os.path.abspath(file_path)
        if not resolved_path.startswith(os.path.abspath(user_codebase_dir)):
            raise ValueError("Invalid file path specified")
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File does not exist: {file_path}")
        
        # Apply the reverse diff
        result = await run_in_threadpool(apply_reverse_diff_pipeline, diff, file_path)
        
        if result.get('status') == 'success':
            return JSONResponse(content={
                'status': 'success',
                'message': 'Changes successfully reversed',
                'request_id': request_id,
                'stage': result.get('stage')
            }, status_code=200)
        else:
            return JSONResponse(content={
                'status': 'error',
                'message': result.get('error', 'Failed to reverse changes'),
                'request_id': request_id
            }, status_code=422)
            
    except FileNotFoundError as e:
        return JSONResponse(content={
            'status': 'error',
            'message': str(e)
        }, status_code=404)
    except ValueError as e:
        return JSONResponse(content={
            'status': 'error', 
            'message': str(e)
        }, status_code=400)
    except Exception as e:
        logger.error(f"Error unapplying changes: {e}")
        return JSONResponse(content={
            'status': 'error',
            'message': f"Unexpected error: {str(e)}"
        }, status_code=500)


@router.post('/api/files/validate')
async def validate_files(request: Request):
    """Validate which files from a list actually exist on disk."""
    try:
        body = await request.json()
        files = body.get('files', [])
        project_root = body.get('projectRoot')
        
        # Use provided project root if available, otherwise fall back to env var
        if project_root:
            user_codebase_dir = os.path.abspath(project_root)
            logger.debug(f"🔍 VALIDATE: Using provided project root: {user_codebase_dir}")
        else:
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        
        if not user_codebase_dir or not os.path.isdir(user_codebase_dir):
            return JSONResponse(status_code=500, content={"error": "ZIYA_USER_CODEBASE_DIR not set"})
        
        existing_files = []
        for file_path in files:
            full_path = os.path.join(user_codebase_dir, file_path)
            if os.path.exists(full_path) and os.path.isfile(full_path):
                existing_files.append(file_path)
        
        return {"existingFiles": existing_files}
    except Exception as e:
        logger.error(f"Error validating files: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post('/api/check-files-in-context')
async def check_files_in_context(request: Request):
    """Check which files from a list are currently available in the selected context."""
    try:
        body = await request.json()
        file_paths = body.get('filePaths', [])
        current_files = body.get('currentFiles', [])
        
        if not file_paths:
            return {"missingFiles": [], "availableFiles": []}
        
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        if not user_codebase_dir:
            return JSONResponse(status_code=500, content={"error": "ZIYA_USER_CODEBASE_DIR not set"})
        
        logger.info(f"🔄 CONTEXT_CHECK: Checking {len(file_paths)} files against {len(current_files)} current context files")
        logger.info(f"🔄 CONTEXT_CHECK: Files to check: {file_paths}")
        logger.info(f"🔄 CONTEXT_CHECK: Current context: {current_files[:10]}...")
        
        missing_files = []
        available_files = []
        
        for file_path in file_paths:
            # Clean up the file path (remove a/ or b/ prefixes from git diffs)
            clean_path = file_path.strip()
            if clean_path.startswith('a/') or clean_path.startswith('b/'):
                clean_path = clean_path[2:]
            
            # Check if the file is in the current selected context
            is_in_context = False
            
            # Direct match
            if clean_path in current_files:
                is_in_context = True
            # Check if any selected folder contains this file
            elif any(clean_path.startswith(f + '/') or f.endswith('/') and clean_path.startswith(f) 
                    for f in current_files):
                is_in_context = True
            
            logger.info(f"🔄 CONTEXT_CHECK: File '{clean_path}' in context: {is_in_context}")
            
            if is_in_context:
                available_files.append(clean_path)
            else:
                # File is not in current context - check if it exists on disk (can be added)
                full_path = os.path.join(user_codebase_dir, clean_path)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    missing_files.append(clean_path)  # Exists but not in context
                else:
                    missing_files.append(clean_path)  # Doesn't exist at all
        
        logger.info(f"🔄 CONTEXT_CHECK: Result - Available: {available_files}, Missing: {missing_files}")
        return {
            "missingFiles": missing_files,
            "availableFiles": available_files
        }
        
    except Exception as e:
        logger.error(f"Error checking files in context: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post('/api/export-conversation')
async def export_conversation(request: Request):
    """Export a conversation in a format suitable for paste services."""
    try:
        body = await request.json()
        conversation_id = body.get('conversation_id')
        format_type = body.get('format', 'markdown')  # 'markdown' or 'html'
        target = body.get('target', 'public')  # 'public' or 'internal'
        captured_diagrams = body.get('captured_diagrams', [])
        
        if not conversation_id:
            return JSONResponse(
                status_code=400, 
                content={"error": "conversation_id is required"}
            )
        
        # Get conversation messages
        # In a real implementation, you'd fetch from the conversation store
        messages = body.get('messages', [])
        
        # Get current model info
        from app.agents.models import ModelManager
        model_alias = ModelManager.get_model_alias()
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
        # Get version
        from app.utils.version_util import get_current_version
        version = get_current_version()
        
        logger.info(f"Exporting conversation with {len(captured_diagrams)} captured diagrams")
        
        # Export the conversation
        exported = export_conversation_for_paste(
            messages=messages,
            format_type=format_type,
            target=target,
            captured_diagrams=captured_diagrams,
            version=version,
            model=model_alias,
            provider=endpoint
        )
        
        return JSONResponse(content=exported)
        
    except Exception as e:
        logger.error(f"Error exporting conversation: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post('/api/restart-stream-with-context')
async def restart_stream_with_context(request: Request):
    """Restart stream with enhanced context including additional files."""
    try:
        body = await request.json()
        conversation_id = body.get('conversation_id')
        added_files = body.get('added_files', [])
        current_files = body.get('current_files', [])
        
        if not conversation_id:
            return JSONResponse(status_code=400, content={"error": "conversation_id is required"})
        
        logger.info(f"🔄 CONTEXT_ENHANCEMENT: Restarting stream for {conversation_id} with {len(added_files)} additional files")
        
        # First, cleanly abort the current stream if it exists
        from app.server import active_streams, cleanup_stream, _keepalive_wrapper, stream_chunks

        if conversation_id in active_streams:
            logger.info(f"🔄 CONTEXT_ENHANCEMENT: Aborting existing stream for {conversation_id}")
            await cleanup_stream(conversation_id)
            # Give it a moment to clean up
            await asyncio.sleep(0.1)
        
        # Combine current files with newly added files
        all_files = list(set(current_files + added_files))
        logger.info(f"🔄 CONTEXT_ENHANCEMENT: Using combined files: current={len(current_files)}, added={len(added_files)}, total={len(all_files)}")
        
        # Build enhanced context body
        enhanced_body = {
            'question': "The referenced files have been added to your context.",
            'conversation_id': conversation_id,
            'config': {
                'files': all_files,  # Use all files including newly added ones
                'conversation_id': conversation_id
            },
            '_context_enhancement': True,  # Flag to indicate this is a context enhancement
            '_added_files': added_files
        }
        
        logger.info(f"🔄 CONTEXT_ENHANCEMENT: Starting enhanced stream with {len(added_files)} files")
        
        # Stream the enhanced response
        return StreamingResponse(
            _keepalive_wrapper(stream_chunks(enhanced_body)),
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
        logger.error(f"Error restarting stream with enhanced context: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

