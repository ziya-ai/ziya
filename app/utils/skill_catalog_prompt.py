"""
Generate the compact skill catalog that goes into the system prompt.

The model sees this catalog on every request (~200 tokens) and can call
``get_skill_details(skill_id)`` to load full instructions for any skill
it deems relevant.  This follows the SkillMesh pattern: cheap catalog in
context, expensive details loaded on-demand.
"""

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

    if not skills:
        return ""

    # Build compact catalog — one line per skill
    rows = []
    for skill in skills:
        sid = skill.get("id", "")
        desc = skill.get("catalog_description") or skill.get("description", "")
        rows.append(f"  • {sid} — {desc}")

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
