"""
API routes for AST capabilities.
"""

from fastapi import APIRouter, HTTPException

from app.utils.context_enhancer import get_ast_indexing_status

router = APIRouter(prefix="/api/ast", tags=["ast"])

@router.get("/status")
async def get_status():
    """
    Get the current status of AST indexing.
    
    Returns:
        Dictionary with indexing status information
    """
    try:
        return get_ast_indexing_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting AST status: {str(e)}")
