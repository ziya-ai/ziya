"""
Skill storage implementation.
"""
from pathlib import Path
from typing import Optional, List
import uuid
import time

from .base import BaseStorage
from ..models.skill import Skill, SkillCreate, SkillUpdate
from ..services.token_service import TokenService
from ..services.color_service import generate_color
from ..data.built_in_skills import BUILT_IN_SKILLS

class SkillStorage(BaseStorage[Skill]):
    """Storage for skills within a project."""
    
    def __init__(self, project_dir: Path, token_service: TokenService):
        self.skills_dir = project_dir / "skills"
        self.token_service = token_service
        super().__init__(self.skills_dir)
        
        # Initialize built-in skills if this is a new project
        self._ensure_built_in_skills()
    
    def _skill_file(self, skill_id: str) -> Path:
        return self.skills_dir / f"{skill_id}.json"
    
    def _ensure_built_in_skills(self) -> None:
        """Ensure built-in skills exist for this project."""
        existing_skills = self.list()
        existing_names = {s.name for s in existing_skills if s.isBuiltIn}
        
        for built_in_data in BUILT_IN_SKILLS:
            if built_in_data['name'] not in existing_names:
                # Create built-in skill
                skill_id = f"builtin-{built_in_data['name'].lower().replace(' ', '-')}"
                now = int(time.time() * 1000)
                
                skill = Skill(
                    id=skill_id,
                    name=built_in_data['name'],
                    description=built_in_data['description'],
                    prompt=built_in_data['prompt'],
                    color=built_in_data['color'],
                    tokenCount=self.token_service.count_tokens(built_in_data['prompt']),
                    isBuiltIn=True,
                    createdAt=now,
                    lastUsedAt=now
                )
                
                self._write_json(self._skill_file(skill_id), skill.model_dump())
    
    def get(self, skill_id: str) -> Optional[Skill]:
        data = self._read_json(self._skill_file(skill_id))
        return Skill(**data) if data else None
    
    def list(self) -> List[Skill]:
        skills = []
        if not self.skills_dir.exists():
            return skills
        for skill_file in self.skills_dir.glob("*.json"):
            data = self._read_json(skill_file)
            if data:
                skills.append(Skill(**data))
        
        # Sort: built-ins first, then by last used
        return sorted(skills, key=lambda s: (not s.isBuiltIn, -s.lastUsedAt))
    
    def create(self, data: SkillCreate) -> Skill:
        skill_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        
        # Calculate token count for the prompt
        token_count = self.token_service.count_tokens(data.prompt)
        
        skill = Skill(
            id=skill_id,
            name=data.name,
            description=data.description,
            prompt=data.prompt,
            color=generate_color(data.name),
            tokenCount=token_count,
            isBuiltIn=False,
            createdAt=now,
            lastUsedAt=now
        )
        
        self._write_json(self._skill_file(skill_id), skill.model_dump())
        return skill
    
    def update(self, skill_id: str, data: SkillUpdate) -> Optional[Skill]:
        skill = self.get(skill_id)
        if not skill:
            return None
        
        # Cannot update built-in skills
        if skill.isBuiltIn:
            raise ValueError("Cannot update built-in skills")
        
        update_dict = data.model_dump(exclude_unset=True)
        
        # Recalculate tokens if prompt changed
        if 'prompt' in update_dict:
            update_dict['tokenCount'] = self.token_service.count_tokens(update_dict['prompt'])
        
        # Regenerate color if name changed
        if 'name' in update_dict:
            update_dict['color'] = generate_color(update_dict['name'])
        
        for key, value in update_dict.items():
            setattr(skill, key, value)
        
        skill.lastUsedAt = int(time.time() * 1000)
        self._write_json(self._skill_file(skill_id), skill.model_dump())
        return skill
    
    def delete(self, skill_id: str) -> bool:
        skill = self.get(skill_id)
        if not skill:
            return False
        
        # Cannot delete built-in skills
        if skill.isBuiltIn:
            raise ValueError("Cannot delete built-in skills")
        
        skill_file = self._skill_file(skill_id)
        if not skill_file.exists():
            return False
        skill_file.unlink()
        return True
    
    def touch(self, skill_id: str) -> None:
        """Update lastUsedAt timestamp."""
        skill = self.get(skill_id)
        if skill:
            skill.lastUsedAt = int(time.time() * 1000)
            self._write_json(self._skill_file(skill_id), skill.model_dump())
