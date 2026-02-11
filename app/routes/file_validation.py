"""
File validation API endpoint.
"""

import os
from typing import List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class FileValidationRequest(BaseModel):
    model_config = {"extra": "allow"}
    files: List[str]
    projectRoot: Optional[str] = None

class FileValidationResponse(BaseModel):
    model_config = {"extra": "allow"}
    existingFiles: List[str]
    missingFiles: List[str]

@router.post("/api/files/validate", response_model=FileValidationResponse)
async def validate_files(request: FileValidationRequest):
    """Validate which files exist and return lists of existing/missing files."""
    
    existing_files = []
    missing_files = []
    
    # Use provided project root if available, otherwise fall back to environment
    if request.projectRoot:
        base_dir = request.projectRoot
        logger.info(f"🔍 VALIDATE: Using provided project root: {base_dir}")
    else:
        base_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        logger.info(f"🔍 VALIDATE: Using environment project root: {base_dir}")
    
    base_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    
    # Get external directories if any
    include_dirs = os.environ.get("ZIYA_INCLUDE_DIRS", "")
    external_dirs = [d.strip() for d in include_dirs.split(',') if d.strip()] if include_dirs else []
    
    for file_path in request.files:
        file_exists = False
        
        # Try absolute path first
        if os.path.isabs(file_path):
            file_exists = os.path.exists(file_path)
        else:
            # Try relative to main codebase directory
            full_path = os.path.join(base_dir, file_path)
            if os.path.exists(full_path):
                file_exists = True
            else:
                # Try relative to each external directory
                for ext_dir in external_dirs:
                    ext_full_path = os.path.join(ext_dir, file_path)
                    if os.path.exists(ext_full_path):
                        file_exists = True
                        break
        
        if file_exists:
            existing_files.append(file_path)
        else:
            missing_files.append(file_path)
    
    return FileValidationResponse(
        existingFiles=existing_files,
        missingFiles=missing_files
    )
