"""
API routes for prompt cache management.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from app.utils.prompt_cache import get_prompt_cache
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/cache", tags=["cache"])


class CacheInvalidateRequest(BaseModel):
    conversation_id: Optional[str] = None
    file_paths: Optional[List[str]] = None


@router.get("/stats")
async def get_cache_stats() -> Dict[str, Any]:
    """
    Get prompt cache statistics.
    
    Returns:
        Dictionary with cache statistics
    """
    try:
        cache = get_prompt_cache()
        stats = cache.get_cache_stats()
        
        # Add human-readable information
        stats['cache_size_mb'] = round(stats['cache_file_size'] / (1024 * 1024), 2)
        
        if stats['oldest_entry'] > 0:
            import datetime
            stats['oldest_entry_date'] = datetime.datetime.fromtimestamp(
                stats['oldest_entry']
            ).isoformat()
        
        if stats['newest_entry'] > 0:
            import datetime
            stats['newest_entry_date'] = datetime.datetime.fromtimestamp(
                stats['newest_entry']
            ).isoformat()
        
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting cache stats: {str(e)}")


@router.post("/invalidate")
async def invalidate_cache(request: CacheInvalidateRequest) -> Dict[str, Any]:
    """
    Invalidate cache entries.
    
    Args:
        request: Invalidation request specifying conversation_id or file_paths
        
    Returns:
        Success status
    """
    try:
        cache = get_prompt_cache()
        
        if request.conversation_id:
            cache.invalidate_conversation(request.conversation_id)
            return {
                "success": True,
                "message": f"Invalidated cache for conversation {request.conversation_id}"
            }
        elif request.file_paths:
            cache.invalidate_files(request.file_paths)
            return {
                "success": True,
                "message": f"Invalidated cache for {len(request.file_paths)} files"
            }
        else:
            raise HTTPException(status_code=400, detail="Must specify either conversation_id or file_paths")
            
    except Exception as e:
        logger.error(f"Error invalidating cache: {e}")
        raise HTTPException(status_code=500, detail=f"Error invalidating cache: {str(e)}")


@router.delete("/clear")
async def clear_cache() -> Dict[str, Any]:
    """
    Clear all cache entries.
    
    Returns:
        Success status
    """
    try:
        cache = get_prompt_cache()
        cache._cache.clear()
        cache._save_cache()
        
        return {
            "success": True,
            "message": "All cache entries cleared"
        }
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=f"Error clearing cache: {str(e)}")
