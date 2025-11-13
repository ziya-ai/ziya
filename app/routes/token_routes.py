"""
Token counting routes.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter(prefix="/api", tags=["tokens"])


class TokenCountRequest(BaseModel):
    model_config = {"extra": "allow"}
    text: str


class AccurateTokenCountRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_paths: List[str]


@router.post('/token-count')
async def token_count(request: TokenCountRequest) -> Dict[str, int]:
    """Get estimated token count for text."""
    # Lazy import to avoid circular dependency
    from app.server import count_tokens
    return await count_tokens(request)


@router.post('/accurate-token-count')
async def accurate_token_count(request: AccurateTokenCountRequest) -> Dict[str, Any]:
    """Get accurate token count for files."""
    # Lazy import to avoid circular dependency
    from app.server import get_accurate_token_counts
    return await get_accurate_token_counts(request)
