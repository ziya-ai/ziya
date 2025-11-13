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

class FileValidationResponse(BaseModel):
    model_config = {"extra": "allow"}
    existingFiles: List[str]
    missingFiles: List[str]

@router.post("/api/files/validate", response_model=FileValidationResponse)
async def validate_files(request: FileValidationRequest):
    """Validate which files exist and return lists of existing/missing files."""
    
    existing_files = []
    missing_files = []
    
    for file_path in request.files:
        if os.path.exists(file_path):
            existing_files.append(file_path)
        else:
            missing_files.append(file_path)
    
    return FileValidationResponse(
        existingFiles=existing_files,
        missingFiles=missing_files
    )
