"""
Skill storage implementation.
"""
import logging
from pathlib import Path
from typing import Optional, List
import uuid
import time

from .base import BaseStorage, contained_path
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
        return contained_path(self.skills_dir, f"{skill_id}.json")
    
    def _ensure_built_in_skills(self) -> None:
        """Ensure built-in skills exist and stay in sync for this project.

        Creates missing built-in skills and updates existing ones with any
        new fields (visibility, source, keywords, id) that were added after
        the skill was first persisted.

        Adoption of same-named custom skills: when a skill that users
        previously created by hand is later PROMOTED to a built-in (as
        Continuous Documentation and Tests for Everything were), the old
        hand-made copy must be retired or the list shows two
        indistinguishable cards under one name — and only the built-in one
        is seedable by a template, so the visible duplicate is also the one
        that does nothing.  The superseded custom file is deleted and the
        canonical built-in record written in its place.

        Matching is case- and whitespace-insensitive, and runs whether or
        not the canonical built-in already exists.  Requiring an exact name
        match made the whole adoption path unreachable for the very record
        it was written for ("Tests for everything" vs "Tests for
        Everything"), and gating it on the built-in being ABSENT meant that
        once the built-in had been created the duplicate was permanent —
        which is the state every existing project is in.
        File-discovered skills (``source`` project/user) are NOT adopted —
        they are owned by a file on disk that this code must not delete, and
        a user who wrote a SKILL.md deliberately outranks a shipped default.

        Built-in records whose name has LEFT ``BUILT_IN_SKILLS`` are also
        removed here.  The on-disk id is derived from the name, so renaming
        a shipped skill (as Task Decomposition was, to "Task Decomposition,
        Delegation & Swarm") writes a new file and orphans the old one,
        which then lists forever as a built-in that no longer exists.
        """
        existing_skills = self.list()

        def _name_key(name: str) -> str:
            return (name or '').strip().lower()

        existing_by_name = {
            _name_key(s.name): s for s in existing_skills if s.isBuiltIn
        }
        adoptable_by_name = {
            _name_key(s.name): s for s in existing_skills
            if not s.isBuiltIn and s.source not in ('project', 'user')
        }
        canonical_names = {_name_key(b['name']) for b in BUILT_IN_SKILLS}

        # Retire built-in records whose name is no longer shipped.
        for stale in existing_skills:
            if stale.isBuiltIn and _name_key(stale.name) not in canonical_names:
                orphan_file = self._skill_file(stale.id)
                if orphan_file.exists():
                    orphan_file.unlink()
                    logger.info(
                        "Removed orphaned built-in skill %r (%s)",
                        stale.name, stale.id,
                    )

        for built_in_data in BUILT_IN_SKILLS:
            canonical_id = f"builtin-{built_in_data['name'].lower().replace(' ', '-')}"
            name_key = _name_key(built_in_data['name'])
            existing = existing_by_name.get(name_key)

            superseded = adoptable_by_name.get(name_key)
            if superseded is not None and superseded.id != canonical_id:
                old_file = self._skill_file(superseded.id)
                if old_file.exists():
                    old_file.unlink()
                    logger.info(
                        "Adopted custom skill %r (%s) into built-in %s",
                        superseded.name, superseded.id, canonical_id,
                    )

            if existing is None:
                # Brand-new built-in skill
                now = int(time.time() * 1000)
                skill = Skill(
                    id=canonical_id,
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
                    visibility=built_in_data.get('visibility'),
                )
                self._write_json(self._skill_file(canonical_id), skill.model_dump())
            else:
                # Existing built-in skill — sync fields that may have been
                # added since it was first persisted (visibility, source,
                # keywords, catalog_description, id normalization).
                dirty = False
                if existing.visibility != built_in_data.get('visibility'):
                    existing.visibility = built_in_data.get('visibility')
                    dirty = True
                if existing.source != 'builtin':
                    existing.source = 'builtin'
                    dirty = True
                if existing.keywords != built_in_data.get('keywords'):
                    existing.keywords = built_in_data.get('keywords')
                    dirty = True
                if existing.description != built_in_data['description']:
                    existing.description = built_in_data['description']
                    dirty = True
                if existing.prompt != built_in_data['prompt']:
                    existing.prompt = built_in_data['prompt']
                    # Recompute alongside the prompt.  ``tokenCount`` is
                    # rendered to the user ("N tokens" in SkillsSection), so
                    # a stale value silently misreports what enabling the
                    # skill costs — and the error grows with every edit to a
                    # shipped prompt, in the direction that matters (a
                    # rewritten skill reads as its original, much smaller
                    # size).  Both the create path above and ``update``
                    # below already do this; only this sync path did not.
                    existing.tokenCount = self.token_service.count_tokens(
                        built_in_data['prompt']
                    )
                    dirty = True
                if dirty:
                    self._write_json(self._skill_file(existing.id), existing.model_dump())
    
    def get(self, skill_id: str) -> Optional[Skill]:
        data = self._read_json(self._skill_file(skill_id))
        if data:
            return Skill(**data)

        # Try file-discovered skills across all well-known roots (project +
        # user-global), with cross-root precedence resolved.  Full body loaded.
        try:
            from ..services.skill_discovery import discover_all_skills
            for s in discover_all_skills(
                self.workspace_path, self.token_service, load_body=True,
            ):
                if s.id == skill_id:
                    return s
        except Exception as e:
            logger.warning("Skill discovery failed during get: %s", e)

        return None
    
    def list(self) -> List[Skill]:
        skills = []
        if self.skills_dir.exists():
            for skill_file in self.skills_dir.glob("*.json"):
                data = self._read_json(skill_file)
                if data:
                    skills.append(Skill(**data))

        # Discover agentskills-format skills across all well-known roots
        # (project + user-global) with cross-root precedence resolved.
        # Stored JSON skills win: only add discovered names/ids not present.
        try:
            from ..services.skill_discovery import discover_all_skills
            stored_ids = {s.id for s in skills}
            stored_names = {s.name for s in skills}
            for ds in discover_all_skills(
                self.workspace_path, self.token_service, load_body=False,
            ):
                if ds.id not in stored_ids and ds.name not in stored_names:
                    skills.append(ds)
        except Exception as e:
            logger.warning("Skill discovery failed: %s", e)

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
        
        # Cannot update file-backed skills (project or user) — edit the
        # SKILL.md file on disk directly; it's re-read on next discovery.
        if skill.source in ('project', 'user'):
            raise ValueError("Cannot update file-backed skills — edit the SKILL.md file directly")

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
        if skill.source in ('project', 'user'):
            raise ValueError("Cannot delete file-backed skills — remove the skill directory instead")

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
