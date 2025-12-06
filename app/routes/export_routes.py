"""
Export configuration endpoints.

Allows plugins to extend available export targets.
"""

from fastapi import APIRouter
from typing import List, Dict, Any
from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/export", tags=["export"])

@router.get("/targets")
async def get_export_targets() -> Dict[str, Any]:
    """
    Get available export targets.
    
    Returns base targets (GitHub Gist) plus any plugin-provided targets.
    """
    # Base target always available
    targets = [
        {
            "id": "public",
            "name": "GitHub Gist",
            "url": "https://gist.github.com",
            "icon": "GithubOutlined",
            "description": "Public paste service with markdown support, syntax highlighting, and version control"
        }
    ]
    
    # Get additional targets from plugins
    try:
        from app.plugins import get_active_config_providers
        
        for provider in get_active_config_providers():
            config = provider.get_defaults()
            if 'export_targets' in config:
                targets.extend(config['export_targets'])
                logger.debug(f"Added export targets from {provider.provider_id}")
    except Exception as e:
        logger.debug(f"Could not load plugin export targets: {e}")
    
    return {"targets": targets}
