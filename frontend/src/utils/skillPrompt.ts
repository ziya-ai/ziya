import type { Skill } from '../types/skill';

/**
 * The "[Active Skill]" block for one client-activated skill.
 *
 * A file-backed skill's bundled-resource manifest (agentskills.io: skill
 * directory + <skill_resources>) follows the body so relative references
 * such as "scripts/x.py" resolve.  The server renders the manifest
 * (app/utils/skill_resources.py) and the server-side activation paths use
 * the same layout: body, blank line, manifest.  An empty body means it has
 * not been hydrated yet, so no manifest is attached.
 */
export function formatActiveSkillPrompt(skill: Skill): string {
  const body = skill.prompt || '';
  const text = body && skill.resourceManifest
    ? `${body.trimEnd()}\n\n${skill.resourceManifest}`
    : body;
  return `[Active Skill: ${skill.name}]\n${text}`;
}
