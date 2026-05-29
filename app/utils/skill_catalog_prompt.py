"""
Generate the compact skill catalog that goes into the system prompt.

The model sees this catalog on every request (~200 tokens) and can call
``get_skill_details(skill_id)`` to load full instructions for any skill
it deems relevant.  This follows the SkillMesh pattern: cheap catalog in
context, expensive details loaded on-demand.
"""

import os
from app.utils.logging_utils import logger


def get_skill_catalog_section() -> str:
    """
    Build the skill catalog block for the system prompt.

    Returns an empty string if the skills builtin category is disabled
    or there are no model-discoverable skills.
    """
    # Check if skills builtin category is enabled
    try:
        from app.mcp.builtin_tools import is_builtin_category_enabled
        if not is_builtin_category_enabled("skills"):
            return ""
    except Exception:
        return ""

    try:
        from app.data.built_in_skills import get_model_discoverable_skills
        skills = get_model_discoverable_skills()
    except Exception as e:
        logger.debug(f"Could not load skill catalog: {e}")
        return ""

    # Build compact catalog — one line per skill
    rows = []
    for skill in skills:
        sid = skill.get("id", "")
        desc = skill.get("catalog_description") or skill.get("description", "")
        rows.append(f"  • {sid} — {desc}")

    # Also include project-discovered SKILL.md files marked
    # visibility: model_discoverable (agentskills.io progressive disclosure
    # stage 1 — frontmatter only, no body).
    try:
        workspace = os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        if workspace:
            from app.services.skill_discovery import discover_project_skills
            from app.services.token_service import TokenService
            project_skills = discover_project_skills(
                workspace, TokenService(), load_body=False,
            )
            for ps in project_skills:
                if ps.visibility != "model_discoverable":
                    continue
                # Use skill name as the catalog ID (matches what the model
                # will pass to get_skill_details).
                rows.append(f"  • {ps.name} — {ps.description}")
    except Exception as e:
        logger.debug(f"Project skill catalog merge failed: {e}")

    if not rows:
        return ""

    catalog = "\n".join(rows)

    return f"""

## Specialized Skills (on-demand)

You have access to specialized skills with detailed instructions for
specific task types.  When a user's request matches one of these skills,
call the `get_skill_details` tool with the skill ID to load its full
instructions before responding.

{catalog}

Do NOT guess at skill formats — always load the skill first.
Only activate a skill when it is clearly relevant to the request.
"""
