/**
 * Skill-activation polarity.
 *
 * ``activeSkillIds`` (ProjectContext / localStorage lens) carries TWO
 * OPPOSITE meanings depending on the skill's ``visibility``:
 *
 *   - ``user_selectable``    — membership means the user switched it ON.
 *     Nothing else advertises these skills, so the lens is the only way
 *     they reach the model.
 *   - ``model_discoverable`` — membership means the user switched it OFF.
 *     These are on-demand by default: the catalogue in the system prompt
 *     advertises them and the model loads the body itself via
 *     ``get_skill_details``, so the only state the lens can add is
 *     SUPPRESSION.  See SkillsSection.getLevel / setLevel.
 *
 * Extracted from ProjectContext rather than inlined so the predicate can be
 * tested against the code that actually runs, instead of against a
 * re-implementation that would pass whether or not the consumers were
 * fixed.
 */

/** Minimal structural shape needed to decide activation. */
export interface ActivatableSkill {
  id: string;
  visibility?: string | null;
}

/** ``Skill.visibility`` value whose lens membership means "off". */
export const MODEL_DISCOVERABLE = 'model_discoverable';

/**
 * True when this skill's presence in the lens means SUPPRESSED, not active.
 *
 * An absent/unloaded skill is not a marker: visibility is unknown, and the
 * caller drops unknown ids anyway.
 */
export function isSuppressionMarker(
  skill: ActivatableSkill | undefined | null,
): boolean {
  return !!skill && skill.visibility === MODEL_DISCOVERABLE;
}

/**
 * The subset of ``activeSkillIds`` that genuinely means "inject this".
 *
 * Drops suppression markers, and drops ids with no loaded skill record —
 * a lens id outlives its skill (a deleted skill leaves a stale id in
 * localStorage), and injecting a prompt for a skill whose visibility cannot
 * be read would be guessing in the harmful direction.
 *
 * Order is preserved; inputs are not mutated.
 */
export function effectiveActiveSkillIds<T extends ActivatableSkill>(
  activeSkillIds: readonly string[],
  skills: readonly T[],
): string[] {
  return activeSkillIds.filter(id => {
    const skill = skills.find(s => s.id === id);
    return !!skill && !isSuppressionMarker(skill);
  });
}

/**
 * Lens ids with no corresponding loaded skill — dead references.
 *
 * A lens id outlives the skill it names: ``deleteSkillFn`` prunes only in
 * the tab that performed the delete, so a deletion from another tab, another
 * browser, or the backend (``SkillStorage._ensure_built_in_skills`` adopting
 * a promoted custom skill) leaves the id behind forever.  The failure is
 * silent: SkillsSection renders cards from the server list, so a dead id
 * matches no card and every card reads "off".
 *
 * A LOADED ``model_discoverable`` skill is NOT stale — its membership is a
 * deliberate suppression marker (see ``isSuppressionMarker``), and dropping
 * it would re-enable a skill the user switched off.
 *
 * Callers MUST NOT act on the result unless the skill list is known to be
 * fully loaded AND to belong to the current project: every id looks stale
 * against an empty or foreign list.
 *
 * Order is preserved; inputs are not mutated.
 */
export function staleSkillIds(
  activeSkillIds: readonly string[],
  skills: readonly { id: string }[],
): string[] {
  const live = new Set(skills.map(s => s.id));
  return activeSkillIds.filter(id => !live.has(id));
}