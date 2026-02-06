"""
Token calculation API endpoints.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional

from ..storage.projects import ProjectStorage
from ..storage.contexts import ContextStorage
from ..storage.skills import SkillStorage
from ..services.token_service import TokenService
from ..utils.paths import get_ziya_home, get_project_dir

router = APIRouter(prefix="/api/v1/projects/{project_id}/tokens", tags=["tokens"])

class TokenCalculationRequest(BaseModel):
    files: Optional[List[str]] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None

class TokenCalculationResponse(BaseModel):
    totalTokens: int
    fileTokens: Dict[str, int]
    skillTokens: Dict[str, int]
    additionalPromptTokens: int
    overlappingFiles: List[str]
    deduplicatedTokens: int

@router.post("/calculate", response_model=TokenCalculationResponse)
async def calculate_tokens(project_id: str, request: TokenCalculationRequest):
    """Calculate token count for files, contexts, and skills."""
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    token_service = TokenService()
    context_storage = ContextStorage(get_project_dir(project_id), token_service)
    skill_storage = SkillStorage(get_project_dir(project_id), token_service)
    
    # Collect all files
    all_files: List[str] = list(request.files or [])
    file_sources: Dict[str, List[str]] = {}
    
    # Track which files come from which source
    for f in all_files:
        file_sources.setdefault(f, []).append("direct")
    
    # Add files from contexts
    if request.contextIds:
        for ctx_id in request.contextIds:
            ctx = context_storage.get(ctx_id)
            if ctx:
                for f in ctx.files:
                    if f not in all_files:
                        all_files.append(f)
                    file_sources.setdefault(f, []).append(ctx.name)
    
    # Find overlapping files
    overlapping = [f for f, sources in file_sources.items() if len(sources) > 1]
    
    # Calculate file tokens (deduplicated automatically)
    unique_files = list(set(all_files))
    file_tokens = token_service.count_tokens_per_file(project.path, unique_files)
    file_total = sum(file_tokens.values())
    
    # Calculate skill tokens
    skill_tokens: Dict[str, int] = {}
    skill_total = 0
    if request.skillIds:
        for skill_id in request.skillIds:
            skill = skill_storage.get(skill_id)
            if skill:
                skill_tokens[skill.name] = skill.tokenCount
                skill_total += skill.tokenCount
    
    # Calculate additional prompt tokens
    additional_prompt_tokens = 0
    if request.additionalPrompt:
        additional_prompt_tokens = token_service.count_tokens(request.additionalPrompt)
    
    total = file_total + skill_total + additional_prompt_tokens
    
    return TokenCalculationResponse(
        totalTokens=total,
        fileTokens=file_tokens,
        skillTokens=skill_tokens,
        additionalPromptTokens=additional_prompt_tokens,
        overlappingFiles=overlapping,
        deduplicatedTokens=total
    )
