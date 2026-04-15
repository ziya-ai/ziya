"""
Token counting and cache statistics routes.

Extracted from server.py during Phase 3b refactoring.
"""
import os
import time
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List

from app.utils.logging_utils import logger

# Lazy import tiktoken
try:
    from app.utils.tiktoken_compat import tiktoken
except ImportError:
    tiktoken = None

router = APIRouter(tags=["tokens"])

class TokenCountRequest(BaseModel):
    model_config = {"extra": "allow"}
    text: str

class AccurateTokenCountRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_paths: List[str]

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

@router.post('/api/token-count')
async def count_tokens(request: TokenCountRequest) -> Dict[str, int]:
    try:
        # Use estimate_token_count which tries calibrator first, then tiktoken, then fallback
        # This gives us calibrated estimates when available, with graceful degradation
        from app.agents.agent import estimate_token_count
        
        token_count = estimate_token_count(text=request.text)
        
        # Log which method was actually used (calibrator logs this internally)
        method_used = "estimate_token_count"

        logger.debug(f"Counted {token_count} tokens using {method_used} method for text length {len(request.text)}")
        return {"token_count": token_count}
    except Exception as e:
        logger.error(f"Error counting tokens: {str(e)}", exc_info=True)
        # Return 0 in case of error to avoid breaking the frontend
        return {"token_count": 0}

@router.post('/api/accurate-token-count')
async def get_accurate_token_counts(request: AccurateTokenCountRequest) -> Dict[str, Any]:
    """Get accurate token counts for specific files."""
    try:
        from app.utils.directory_util import get_accurate_token_count

        # Check if we have pre-calculated accurate counts
        from app.utils.directory_util import _accurate_token_cache
        if _accurate_token_cache:
            logger.info(f"API request for accurate tokens: {len(request.file_paths)} files requested")
            results = {}
            for file_path in request.file_paths:
                if file_path in _accurate_token_cache:
                    results[file_path] = {
                        "accurate_count": _accurate_token_cache[file_path],
                        "timestamp": int(time.time())
                    }
            if results:
                cached_count = sum(1 for path in request.file_paths if path in _accurate_token_cache)
                calculated_count = len(results) - cached_count
                logger.info(f"Returning {len(results)} token counts: {cached_count} from cache (accurate), {calculated_count} calculated on-demand")
                return {"results": results, "debug_info": {"source": "precalculated_cache"}}

        from app.context import get_project_root
        user_codebase_dir = get_project_root()
        logger.debug(f"Accurate token count requested for {len(request.file_paths)} files")
        if not user_codebase_dir:
            raise ValueError("ZIYA_USER_CODEBASE_DIR not set")
        
        from app.utils.file_utils import resolve_external_path
        results = {}
        for file_path in request.file_paths:
            full_path = resolve_external_path(file_path, user_codebase_dir)
            if os.path.exists(full_path) and os.path.isfile(full_path):
                accurate_count = get_accurate_token_count(full_path)
                # Get the estimated count for comparison
                from app.utils.directory_util import estimate_tokens_fast
                estimated_count = estimate_tokens_fast(full_path)
                logger.debug(f"File: {file_path} - ACCURATE: {accurate_count} vs ESTIMATED: {estimated_count} (diff: {accurate_count - estimated_count})")
                results[file_path] = {
                    "accurate_count": accurate_count,
                    "timestamp": int(time.time())
                }
            else:
                results[file_path] = {"accurate_count": 0, "error": "File not found"}
                
        return {"results": results, "debug_info": {"files_processed": len(results)}}
    except Exception as e:
        logger.error(f"Error getting accurate token counts: {str(e)}")
        return {"error": str(e), "results": {}}

@router.get('/api/cache-stats')
async def get_cache_stats():
    """Get context caching statistics and effectiveness metrics."""
    try:
        from app.utils.context_cache import get_context_cache_manager
        cache_manager = get_context_cache_manager()
        
        stats = cache_manager.get_cache_stats()
        
        # Calculate effectiveness metrics
        total_operations = stats["hits"] + stats["misses"]
        hit_rate = (stats["hits"] / total_operations * 100) if total_operations > 0 else 0
        
        return {
            "cache_enabled": True,
            "statistics": {
                "cache_hits": stats["hits"],
                "cache_misses": stats["misses"],
                "context_splits": stats["splits"],
                "hit_rate_percent": round(hit_rate, 1),
                "active_cache_entries": stats["cache_entries"],
                "estimated_tokens_cached": stats["estimated_token_savings"]
            }
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {str(e)}")
        return {"cache_enabled": False, "error": str(e)}

@router.get('/api/cache-test')
async def test_cache_functionality():
    """Test if context caching is properly configured and working."""
    try:
        from app.utils.context_cache import get_context_cache_manager
        from app.agents.models import ModelManager
        
        # Check model configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL", ModelManager.DEFAULT_MODELS.get(endpoint))
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        cache_manager = get_context_cache_manager()
        
        # Create a large test content
        test_content = "Test file content. " * 1000  # ~20,000 chars
        
        return {
            "model_supports_caching": model_config.get("supports_context_caching", False),
            "current_model": model_name,
            "endpoint": endpoint,
            "test_content_size": len(test_content),
            "should_cache": cache_manager.should_cache_context(test_content, model_config),
            "min_cache_size": cache_manager.min_cache_size,
            "cache_manager_initialized": cache_manager is not None
        }
    except Exception as e:
        logger.error(f"Error testing cache functionality: {str(e)}")
        return {"error": str(e)}

