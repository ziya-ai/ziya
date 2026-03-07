"""
Skill storage implementation.
"""
import logging
from pathlib import Path
from typing import Optional, List
import uuid
import time

from .base import BaseStorage
from ..models.skill import Skill, SkillCreate, SkillUpdate
from ..services.token_service import TokenService
from ..services.color_service import generate_color
from ..data.built_in_skills import BUILT_IN_SKILLS

logger = logging.getLogger(__name__)


class SkillStorage(BaseStorage[Skill]):
    """Storage for skills within a project."""
    
    def __init__(self, project_dir: Path, token_service: TokenService, workspace_path: str | None = None):
        self.skills_dir = project_dir / "skills"
        self.project_dir = project_dir
        self.workspace_path = workspace_path
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
                    source='builtin',
                    color=built_in_data['color'],
                    tokenCount=self.token_service.count_tokens(built_in_data['prompt']),
                    isBuiltIn=True,
                    createdAt=now,
                    lastUsedAt=now,
                    keywords=built_in_data.get('keywords'),
                )
                
                self._write_json(self._skill_file(skill_id), skill.model_dump())
    
    def get(self, skill_id: str) -> Optional[Skill]:
        data = self._read_json(self._skill_file(skill_id))
        if data:
            return Skill(**data)

        # Try project-discovered skills (with full body loaded)
        if self.workspace_path:
            try:
                from ..services.skill_discovery import discover_project_skills
                project_skills = discover_project_skills(
                    self.workspace_path,
                    self.token_service,
                    load_body=True,
                )
                for ps in project_skills:
                    if ps.id == skill_id:
                        return ps
            except Exception as e:
                logger.warning("Project skill discovery failed during get: %s", e)

        return None
    
    def list(self) -> List[Skill]:
        skills = []
        if self.skills_dir.exists():
            for skill_file in self.skills_dir.glob("*.json"):
                data = self._read_json(skill_file)
                if data:
                    skills.append(Skill(**data))

        # Discover agentskills-format skills from the project workspace
        if self.workspace_path:
            try:
                from ..services.skill_discovery import discover_project_skills
                project_skills = discover_project_skills(
                    self.workspace_path,
                    self.token_service,
                    load_body=False,  # Progressive disclosure: metadata only for list
                )
                stored_ids = {s.id for s in skills}
                for ps in project_skills:
                    if ps.id not in stored_ids:
                        skills.append(ps)
            except Exception as e:
                logger.warning("Project skill discovery failed: %s", e)

        return sorted(skills, key=lambda s: s.lastUsedAt, reverse=True)
    
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
            source='custom',
            createdAt=now,
            lastUsedAt=now,
            toolIds=data.toolIds,
            files=data.files,
            contextIds=data.contextIds,
            modelOverrides=data.modelOverrides,
            allowImplicitInvocation=data.allowImplicitInvocation if data.allowImplicitInvocation is not None else True,
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
        
        # Cannot update project-discovered skills (edit the SKILL.md file directly)
        if skill.source == 'project':
            raise ValueError("Cannot update project skills — edit the SKILL.md file directly")

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
        
        # Cannot delete project-discovered skills
        if skill.source == 'project':
            raise ValueError("Cannot delete project skills — remove the skill directory instead")

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
