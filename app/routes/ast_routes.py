"""
API routes for AST capabilities.
"""

import os
import threading
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
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

from app.utils.context_enhancer import get_ast_indexing_status
from app.utils.context_enhancer import get_ast_indexing_status, reset_ast_indexing_status
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/ast", tags=["ast"])

class ResolutionChangeRequest(BaseModel):
    model_config = {"extra": "allow"}
    resolution: str

@router.get("/status")
async def get_status():
    """
    Get the current status of AST indexing.
    
    Returns:
        Dictionary with indexing status information
    """
    try:
        status = get_ast_indexing_status()
        
        # Add flag to indicate if AST is enabled/configured
        status['ast_enabled'] = os.environ.get("ZIYA_AST_RESOLUTION", "medium") != "disabled"
        
        # Add token count if AST is available
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