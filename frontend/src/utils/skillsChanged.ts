/**
 * Fired when the set of skills in a project changed (created, edited or
 * deleted through the Bench).  ProjectContext reloads its skill list on it:
 * the chip bar and the Skills tab resolve bench items to skills BY NAME from
 * that list, so a skill created in the Bench and placed on hand would have
 * no chip until a reload otherwise.  Distinct from BENCH_CHANGED_EVENT, which
 * is about placements of skills the list already knows.
 */
export const SKILLS_CHANGED_EVENT = 'ziya:skills-changed';

export interface SkillsChangedDetail { projectId: string; }

export function announceSkillsChanged(projectId: string): void {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent<SkillsChangedDetail>(SKILLS_CHANGED_EVENT, { detail: { projectId } }));
}
