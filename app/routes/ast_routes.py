"""
API routes for AST capabilities.
"""

import os
import threading

from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any

from pydantic import BaseModel
try:
    from app.utils.ast_parser.integration import (
        get_ast_token_count, 
        get_resolution_estimates,
        change_ast_resolution
    )
    from app.utils.context_enhancer import reset_ast_indexing_status
except ImportError:
    # Fallback if AST parser is not available
    get_ast_token_count = lambda: 0
    get_resolution_estimates = lambda: {}

from app.utils.context_enhancer import get_ast_indexing_status, reset_ast_indexing_status
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/ast", tags=["ast"])

class ResolutionChangeRequest(BaseModel):
    model_config = {"extra": "allow"}
    resolution: str

@router.get("/status")
async def get_status(request: Request):
    """
    Get the current status of AST indexing.
    """
    try:
        status = get_ast_indexing_status()
        
        # Resolve the project root from the request header so we can
        # check project-specific state rather than relying solely on
        # the global _ast_indexing_status dict (which may be stale
        # from a previous project's failed attempt).
        project_root = request.headers.get("X-Project-Root")
        if project_root:
            try:
                from app.utils.ast_parser.integration import (
                    _initialized_projects, _indexing_in_progress,
                    get_enhancer_for_project,
                )
                abs_root = os.path.abspath(project_root)

                if abs_root in _initialized_projects:
                    enhancer = get_enhancer_for_project(abs_root)
                    file_count = len(enhancer.ast_cache) if enhancer else 0
                    status.update({
                        'is_indexing': False,
                        'is_complete': True,
                        'indexed_files': file_count,
                        'total_files': file_count,
                        'error': None,
                    })
                elif abs_root in _indexing_in_progress:
                    status.update({
                        'is_indexing': True,
                        'is_complete': False,
                        'error': None,
                    })
            except (ImportError, AttributeError) as e:
                logger.debug(f"Could not check project-specific AST state: {e}")

        status['ast_enabled'] = os.environ.get("ZIYA_AST_RESOLUTION", "medium") != "disabled"
        
        # ast_in_prompt is True only when --ast flag was used, meaning AST
        # context is baked into the system prompt and costs input tokens.
        from app.config.app_config import env_bool
        status['ast_in_prompt'] = env_bool("ZIYA_ENABLE_AST")
        try:
            status['token_count'] = get_ast_token_count()
        except Exception as e:
            logger.warning(f"Could not get AST token count: {e}")
            status['token_count'] = 0
            
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting AST status: {str(e)}")

@router.get("/resolutions")
async def get_resolutions() -> Dict[str, Any]:
    """
    Get available AST resolution levels and their estimated token counts.
    
    Returns:
        Dictionary with resolution levels and their characteristics
    """
    try:
        estimates = get_resolution_estimates()
        return {
            "resolutions": estimates,
            "current_resolution": os.environ.get("ZIYA_AST_RESOLUTION", "medium")
        }
    except Exception as e:
        logger.error(f"Error getting AST resolutions: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting AST resolutions: {str(e)}")

@router.post("/change-resolution")
async def change_resolution(request: ResolutionChangeRequest) -> Dict[str, Any]:
    """
    Change the AST resolution and trigger re-indexing.
    
    Args:
        resolution: New resolution level ('minimal', 'medium', 'detailed', 'comprehensive')
        
    Returns:
        Status of the resolution change
    """
    try:
        
        # Validate resolution level
        valid_resolutions = ['disabled', 'minimal', 'medium', 'detailed', 'comprehensive']
        if request.resolution not in valid_resolutions:
            raise HTTPException(status_code=400, detail=f"Invalid resolution. Must be one of: {valid_resolutions}")
        
        # Reset indexing status to show progress
        reset_ast_indexing_status()
        
        # Start re-indexing in background thread
        def reindex_background():
            try:
                change_ast_resolution(request.resolution)
            except Exception as e:
                logger.error(f"Background AST re-indexing failed: {e}")
        
        thread = threading.Thread(target=reindex_background, daemon=True)
        thread.start()
        
        return {"status": "success", "message": f"AST resolution changed to {request.resolution}. Re-indexing in progress."}
    except Exception as e:
        logger.error(f"Error changing AST resolution: {e}")
        raise HTTPException(status_code=500, detail=f"Error changing AST resolution: {str(e)}")