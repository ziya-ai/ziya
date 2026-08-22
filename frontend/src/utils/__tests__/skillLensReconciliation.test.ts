/**
 * Tests for pruning stale ids out of the project lens.
 *
 * Observed failure (defect 5a): the localStorage lens for the ziya project
 * held two ids —
 *
 *   {"contextIds":[],"skillIds":["8db1784f-…","6e12608d-…"]}
 *
 * — and NEITHER resolved to a skill on the server.  Both were uuid-named
 * custom records: one deleted long ago, one deleted by the built-in adoption
 * pass (SkillStorage._ensure_built_in_skills).  Nothing reconciles the lens
 * against the loaded skill list, so a dead id survives every reload forever.
 *
 * The user-visible result is silent and total: SkillsSection renders cards
 * from `skills` (the server list), so a dead id matches no card and EVERY
 * card reads "off"; activeSkillPrompts guards on `if (skill)`, so nothing is
 * injected.  The skill looks switched off and re-toggling appears to be the
 * only recourse.
 *
 * `deleteSkillFn` prunes the lens only in the tab that performs the delete.
 * A deletion from another tab, another browser, or the backend (adoption)
 * leaves a permanent ghost.
 *
 * The pure predicate is imported from the production module rather than
 * re-implemented, so these tests fail if the shipped logic is wrong.  The
 * wiring group asserts against real ProjectContext source, because the
 * GUARDS are the dangerous part: pruning while skills are still loading, or
 * against another project's list, silently destroys a good lens.  A mirror
 * test of the predicate alone would pass with every guard missing.
 */

import * as fs from 'fs';
import * as path from 'path';

import { staleSkillIds } from '../skillActivation';

// ── Fixtures ─────────────────────────────────────────────────────────

/** The two dead ids actually observed in the ziya project's lens. */
const GHOST_ADOPTED = '6e12608d-9f85-456c-ae89-bdad6b04ed44';
const GHOST_OLD = '8db1784f-7903-4dfb-9375-316bd87021b1';

const LOADED = [
    { id: 'builtin-tests-for-everything', visibility: 'user_selectable' },
    { id: 'builtin-continuous-documentation', visibility: 'user_selectable' },
    { id: 'builtin-packet-diagrams', visibility: 'model_discoverable' },
];

describe('staleSkillIds', () => {
    it('reports the exact ghost pair observed in the live lens', () => {
        expect(staleSkillIds([GHOST_OLD, GHOST_ADOPTED], LOADED))
            .toEqual([GHOST_OLD, GHOST_ADOPTED]);
    });

    it('reports nothing when every lens id resolves', () => {
        expect(staleSkillIds(
            ['builtin-tests-for-everything', 'builtin-packet-diagrams'],
            LOADED,
        )).toEqual([]);
    });

    it('separates only the dead ids out of a mixed lens', () => {
        expect(staleSkillIds(
            ['builtin-tests-for-everything', GHOST_ADOPTED],
            LOADED,
        )).toEqual([GHOST_ADOPTED]);
    });

    it('does NOT treat a suppression marker as stale', () => {
        // A loaded model_discoverable skill in the lens means "off".  That
        // is meaningful state, not a ghost — pruning it would silently
        // re-enable a skill the user switched off.
        expect(staleSkillIds(['builtin-packet-diagrams'], LOADED)).toEqual([]);
    });

    it('reports nothing for an empty lens', () => {
        expect(staleSkillIds([], LOADED)).toEqual([]);
    });

    it('reports every id when skills have not loaded yet', () => {
        // Callers MUST NOT act on this — hence the guards asserted below.
        // Stated here so the predicate's behaviour is unambiguous.
        expect(staleSkillIds([GHOST_OLD], [])).toEqual([GHOST_OLD]);
    });

    it('does not mutate its inputs', () => {
        const lens = [GHOST_OLD, 'builtin-tests-for-everything'];
        const skills = [...LOADED];
        staleSkillIds(lens, skills);
        expect(lens).toEqual([GHOST_OLD, 'builtin-tests-for-everything']);
        expect(skills).toEqual(LOADED);
    });
});

// ── Wiring: assert against the real provider source ──────────────────

const SOURCE = fs.readFileSync(
    path.join(__dirname, '..', '..', 'context', 'ProjectContext.tsx'),
    'utf8',
);

/** The prune effect body, isolated so assertions cannot match elsewhere. */
function pruneEffectBody(): string {
    const anchor = SOURCE.indexOf('staleSkillIds(activeSkillIds, skills)');
    expect(anchor).toBeGreaterThan(-1);
    const start = SOURCE.lastIndexOf('useEffect(() => {', anchor);
    expect(start).toBeGreaterThan(-1);
    return SOURCE.slice(start, SOURCE.indexOf('}, [', anchor) + 200);
}

describe('ProjectContext lens reconciliation wiring', () => {
    it('imports the shared predicate', () => {
        expect(SOURCE).toMatch(
            /import \{[^}]*staleSkillIds[^}]*\} from '\.\.\/utils\/skillActivation'/s,
        );
    });

    it('records which project the loaded skills belong to', () => {
        // Without this the prune can run while `skills` still holds the
        // PREVIOUS project's list and `activeSkillIds` already holds the new
        // project's lens — pruning a good lens to nothing on every switch.
        expect(SOURCE).toContain('setSkillsProjectId(currentProject.id)');
        expect(SOURCE).toContain('const [skillsProjectId, setSkillsProjectId]');
    });

    it('clears the marker when there is no project', () => {
        expect(SOURCE).toContain('setSkillsProjectId(null)');
    });

    it('refuses to prune while skills are still loading', () => {
        // `skills` is [] mid-load; without this guard the first render after
        // a reload wipes the entire restored lens.
        expect(pruneEffectBody()).toContain('isLoadingSkills');
    });

    it('refuses to prune against another project\'s skill list', () => {
        expect(pruneEffectBody()).toContain('skillsProjectId !== currentProject.id');
    });

    it('refuses to prune when the skill list is empty', () => {
        // Belt-and-braces for a failed listSkills, which leaves skills=[]
        // with isLoadingSkills already back to false.
        expect(pruneEffectBody()).toContain('skills.length === 0');
    });

    it('exits early when there is nothing stale', () => {
        // Guards against an infinite setState loop: the effect depends on
        // activeSkillIds and also writes it.
        expect(pruneEffectBody()).toMatch(/stale\.length === 0/);
    });

    it('persists the pruned lens through the wrapped setter', () => {
        // setActiveSkillIds (not the raw _setActiveSkillIds) is what writes
        // localStorage; using the raw setter would prune in memory only and
        // resurrect the ghosts on the next reload.
        expect(pruneEffectBody()).toContain('setActiveSkillIds(');
        expect(pruneEffectBody()).not.toContain('_setActiveSkillIds(');
    });

    it('declares its inputs as dependencies', () => {
        const body = pruneEffectBody();
        for (const dep of ['skillsProjectId', 'isLoadingSkills', 'skills', 'activeSkillIds']) {
            expect(body).toContain(dep);
        }
    });

    it('reports what it dropped', () => {
        // A silent prune is indistinguishable from the bug it fixes.
        expect(pruneEffectBody()).toMatch(/console\.(warn|info|log)/);
    });
});
