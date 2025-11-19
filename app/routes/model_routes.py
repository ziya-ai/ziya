"""
Model configuration and management routes.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Any, Optional
import logging
import os

from app.agents.models import ModelManager
from app.config import models_config as config

logger = logging.getLogger("ZIYA")
router = APIRouter(prefix="/api", tags=["models"])


class ModelSettingsRequest(BaseModel):
    model_config = {"extra": "allow"}
    model_config = {"extra": "allow"}
    temperature: Optional[float] = None
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    model: Optional[str] = None
    thinking_level: Optional[str] = None


@router.get('/available-models')
async def available_models():
    """Get list of available models."""
    # Lazy import to avoid circular dependency
    from app.server import get_available_models
    return get_available_models()


@router.get('/current-model')
async def current_model():
    """Get detailed information about the currently active model."""
    # Lazy import to avoid circular dependency
    from app.server import get_current_model
    return get_current_model()


@router.get('/model-id')
async def model_id():
    """Get the model ID in a simplified format for the frontend."""
    return {'model_id': ModelManager.get_model_alias()}


@router.post('/set-model')
async def set_model(request: Request):
    """Set the current model - forwards to server.py implementation."""
    from app.server import set_model as server_set_model
    return await server_set_model(request)


@router.get('/model-capabilities')
async def model_capabilities(model: Optional[str] = None):
    """Get capabilities of specified or current model."""
    # Lazy import to avoid circular dependency
    from app.server import get_model_capabilities
    model_name = model or ModelManager.get_model_alias()
    return get_model_capabilities(model_name)


@router.post('/model-settings')
async def update_model_settings(settings: ModelSettingsRequest):
    """Update model generation settings."""
    # Lazy import to avoid circular dependency
    from app.server import update_model_settings as server_update_settings
    return await server_update_settings(settings)
