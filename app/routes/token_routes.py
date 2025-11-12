"""
Token counting routes.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import List, Dict, Any
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter()


class TokenCountRequest(BaseModel):
    messages: List[Dict[str, Any]]


@router.post('/api/token-count')
async def token_count(request: Request, body: TokenCountRequest):
    """Get estimated token count for messages."""
    model_manager = request.app.state.model_manager
    
    try:
        count = model_manager.count_tokens(body.messages)
        return {"token_count": count}
    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        return {"error": str(e)}


@router.post('/api/accurate-token-count')
async def accurate_token_count(request: Request):
    """Get accurate token count for files."""
    from app.utils.token_counter import get_accurate_token_counts
    
    data = await request.json()
    files = data.get('files', [])
    model_manager = request.app.state.model_manager
    
    try:
        counts = get_accurate_token_counts(files, model_manager)
        return counts
    except Exception as e:
        logger.error(f"Error getting accurate token counts: {e}")
        return {"error": str(e)}
