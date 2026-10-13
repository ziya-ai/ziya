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

        # Fuzzy fallback: the shared matcher (app.utils.skill_match), so
        # "Code Review" / "code-review" / "code_review" are one skill here
        # exactly as they are for a card's scope.skills; plus keywords.
        if not skill:
            from app.utils.skill_match import skill_key, skill_matches
            want_key = skill_key(skill_id)
            for candidate in get_model_discoverable_skills():
                ckw = [skill_key(k) for k in candidate.get("keywords", [])]
                if skill_matches(skill_id, candidate) or want_key in ckw:
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
                from app.utils.skill_match import skill_key, skill_matches

                # The CALLER's project, not the server's startup env: a
                # task-card body (project_root set on its context) or a
                # second project's chat window must find ITS .ziya/skills,
                # not whichever repo the server happened to be launched in.
                workspace = (
                    kwargs.get("_workspace_path")
                    or _request_project_root()
                    or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
                    or None
                )
                want_key = skill_key(skill_id)
                for ds in discover_all_skills(
                    workspace, TokenService(), load_body=True,
                ):
                    ds_kw = [skill_key(k) for k in (ds.keywords or [])]
                    if skill_matches(skill_id, ds) or want_key in ds_kw:
                        logger.info(f"🎓 SKILL_ACTIVATED ({ds.source}): {ds.name}")
                        # agentskills.io: give the skill's directory and list
                        # its bundled files so relative references in the
                        # body ("scripts/x.py") resolve.
                        from app.utils.skill_resources import skill_activation_body
                        return {
                            "content": f"[Skill: {ds.name}]\n\n"
                                       f"{skill_activation_body(ds)}"
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
