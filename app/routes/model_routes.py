"""
Model configuration and management routes.
"""
from fastapi import APIRouter, Request
from typing import Dict, Any
import logging

from app.config.models_config import get_available_models, get_model_capabilities

logger = logging.getLogger("ZIYA")
router = APIRouter()


@router.get('/api/available-models')
async def available_models():
    """Get list of available models."""
    return get_available_models()


@router.get('/api/current-model')
async def current_model(request: Request):
    """Get current model configuration."""
    model_manager = request.app.state.model_manager
    return {
        "model": model_manager.model_name,
        "endpoint": model_manager.endpoint
    }


@router.get('/api/model-id')
async def model_id(request: Request):
    """Get current model ID."""
    model_manager = request.app.state.model_manager
    return {"model_id": model_manager.model_id}


@router.post('/api/set-model')
async def set_model(request: Request):
    """Set the current model."""
    data = await request.json()
    model_manager = request.app.state.model_manager
    
    model_name = data.get('model')
    endpoint = data.get('endpoint')
    
    if not model_name or not endpoint:
        return {"success": False, "error": "Model and endpoint are required"}
    
    try:
        model_manager.set_model(model_name, endpoint)
        logger.info(f"Model changed to {model_name} on {endpoint}")
        return {
            "success": True,
            "model": model_name,
            "endpoint": endpoint,
            "model_id": model_manager.model_id
        }
    except Exception as e:
        logger.error(f"Error setting model: {e}")
        return {"success": False, "error": str(e)}


@router.get('/api/model-capabilities')
async def model_capabilities(request: Request):
    """Get capabilities of current model."""
    model_manager = request.app.state.model_manager
    capabilities = get_model_capabilities(model_manager.endpoint, model_manager.model_name)
    return capabilities


@router.post('/api/model-settings')
async def update_model_settings(request: Request):
    """Update model generation settings."""
    data = await request.json()
    model_manager = request.app.state.model_manager
    
    try:
        if 'temperature' in data:
            model_manager.temperature = float(data['temperature'])
        if 'top_p' in data:
            model_manager.top_p = float(data['top_p'])
        if 'top_k' in data:
            model_manager.top_k = int(data['top_k'])
        if 'max_tokens' in data:
            model_manager.max_tokens = int(data['max_tokens'])
            
        return {
            "success": True,
            "settings": {
                "temperature": model_manager.temperature,
                "top_p": model_manager.top_p,
                "top_k": model_manager.top_k,
                "max_tokens": model_manager.max_tokens
            }
        }
    except Exception as e:
        logger.error(f"Error updating model settings: {e}")
        return {"success": False, "error": str(e)}
