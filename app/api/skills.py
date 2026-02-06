"""
Skill API endpoints.
"""
from fastapi import APIRouter, HTTPException
from typing import List

from ..models.skill import Skill, SkillCreate, SkillUpdate
from ..storage.projects import ProjectStorage
from ..storage.skills import SkillStorage
from ..storage.chats import ChatStorage
from ..services.token_service import TokenService
from ..utils.paths import get_ziya_home, get_project_dir

router = APIRouter(prefix="/api/v1/projects/{project_id}/skills", tags=["skills"])

def get_skill_storage(project_id: str) -> SkillStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    token_service = TokenService()
    storage = SkillStorage(get_project_dir(project_id), token_service)
    return storage

@router.get("", response_model=List[Skill])
async def list_skills(project_id: str):
    """List all skills for a project (includes built-ins)."""
    storage = get_skill_storage(project_id)
    return storage.list()

@router.post("", response_model=Skill)
async def create_skill(project_id: str, data: SkillCreate):
    """Create a new custom skill."""
    storage = get_skill_storage(project_id)
    return storage.create(data)

@router.get("/{skill_id}", response_model=Skill)
async def get_skill(project_id: str, skill_id: str):
    """Get a specific skill."""
    storage = get_skill_storage(project_id)
    skill = storage.get(skill_id)
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    storage.touch(skill_id)
    return skill

@router.put("/{skill_id}", response_model=Skill)
async def update_skill(project_id: str, skill_id: str, data: SkillUpdate):
    """Update a skill (only custom skills, not built-ins)."""
    storage = get_skill_storage(project_id)
    
    try:
        skill = storage.update(skill_id, data)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return skill
    except ValueError as e:
        # Built-in skills cannot be updated
        raise HTTPException(status_code=403, detail=str(e))

@router.delete("/{skill_id}")
async def delete_skill(project_id: str, skill_id: str):
    """Delete a skill (only custom skills, not built-ins)."""
    storage = get_skill_storage(project_id)
    
    try:
        if not storage.delete(skill_id):
            raise HTTPException(status_code=404, detail="Skill not found")
    except ValueError as e:
        # Built-in skills cannot be deleted
        raise HTTPException(status_code=403, detail=str(e))
    
    # Remove from chats that reference this skill
    chat_storage = ChatStorage(get_project_dir(project_id))
    chat_storage.remove_skill_from_all_chats(skill_id)
    
    return {"deleted": True}
