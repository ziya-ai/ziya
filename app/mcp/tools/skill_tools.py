"""
Skill discovery MCP tool.

Provides ``get_skill_details`` so the model can load full instructions
for any model-discoverable skill on-demand.  The model sees a compact
skill catalog in the system prompt (~200 tokens) and calls this tool
only when a skill is relevant to the current task.

This follows the SkillMesh / Claude Skills pattern:
  catalog (cheap)  →  model decides  →  load full prompt (on-demand)
"""

import os
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class GetSkillDetailsInput(BaseModel):
    """Input schema for get_skill_details."""
    skill_name: str = Field(
        ...,
        description=(
            "The skill ID to load (e.g. 'task_decomposition', 'packet_diagrams'). "
            "See the skill catalog in your instructions for available IDs."
        ),
    )


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

class GetSkillDetailsTool(BaseMCPTool):
    """Load full instructions for a model-discoverable skill."""

    name: str = "get_skill_details"
    description: str = (
        "Load detailed instructions and format specifications for a "
        "specialized skill.  Call this when you decide a skill from the "
        "catalog is relevant to the user's request.  Returns the full "
        "skill prompt that you should follow for this response."
    )
    InputSchema = GetSkillDetailsInput

    # Skill results are internal guidance, not user-facing output
    is_internal: bool = True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        skill_id = (kwargs.get("skill_name") or "").strip().lower()
        # Strip common prefixes the model might add
        for prefix in ("skill:", "skills/", "builtin-"):
            if skill_id.startswith(prefix):
                skill_id = skill_id[len(prefix):]

        if not skill_id:
            return {"error": True, "message": "skill_name is required"}

        from app.data.built_in_skills import get_skill_by_id, get_model_discoverable_skills

        skill = get_skill_by_id(skill_id)

        # Fuzzy fallback: try matching by name or keywords
        if not skill:
            for candidate in get_model_discoverable_skills():
                cid = candidate.get("id", "")
                cname = candidate.get("name", "").lower().replace(" ", "_")
                ckw = [k.lower() for k in candidate.get("keywords", [])]
                if skill_id in (cid, cname) or skill_id in ckw:
                    skill = candidate
                    break

        # Fall back to file-discovered SKILL.md files across all well-known
        # roots (project + user-global), with cross-root precedence resolved.
        # The match below is keyed on an explicit name/id/keyword, so
        # visibility is intentionally
        # NOT gated here: when the model deliberately asks for a skill by name
        # it should get the body even if the skill is user_selectable (i.e.
        # normally toggled in the UI rather than auto-listed in the catalog).
        # Loads the full body on demand (agentskills.io progressive disclosure).
        if not skill:
            try:
                from app.services.skill_discovery import discover_all_skills
                from app.services.token_service import TokenService

                workspace = os.environ.get("ZIYA_USER_CODEBASE_DIR", "") or None
                for ds in discover_all_skills(
                    workspace, TokenService(), load_body=True,
                ):
                    ds_kw = [k.lower() for k in (ds.keywords or [])]
                    if skill_id in (ds.name, ds.id) or skill_id in ds_kw:
                        logger.info(f"🎓 SKILL_ACTIVATED ({ds.source}): {ds.name}")
                        return {
                            "content": f"[Skill: {ds.name}]\n\n{ds.prompt}"
                        }
            except Exception as e:
                logger.debug(f"Skill lookup failed: {e}")

        if not skill:
            available = [s["id"] for s in get_model_discoverable_skills()]
            return {
                "error": True,
                "message": (
                    f"Skill '{skill_id}' not found. "
                    f"Available skills: {', '.join(available)}"
                ),
            }

        logger.info(f"🎓 SKILL_ACTIVATED: {skill['id']} — {skill['name']}")

        return {
            "content": (
                f"[Skill: {skill['name']}]\n\n"
                f"{skill['prompt']}"
            ),
        }
