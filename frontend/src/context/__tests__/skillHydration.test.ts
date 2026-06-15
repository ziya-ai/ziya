/**
 * Tests for ProjectContext skill-body hydration logic.
 *
 * Regression coverage for the bug where a file-backed (project/user)
 * skill activated via the lens injected "[Active Skill: <name>]\n" with an
 * EMPTY body.  Root cause: listSkills serves file-backed skills with
 * load_body=False (cheap catalog / progressive disclosure stage 1), so
 * skill.prompt is "" until hydrated via GET /skills/{id} (load_body=True).
 *
 * These are logic-only tests that re-implement the pure pieces of the fix
 * (activeSkillPrompts assembly, the needHydration filter, the merge-patch)
 * without React rendering — matching the convention in
 * hooks/__tests__/useSendPayload.test.ts.
 */

interface TestSkill {
    id: string;
    name: string;
    prompt: string;
    contextIds?: string[];
}

// ── Pure logic mirrors of ProjectContext ──────────────────────────────

// activeSkillPrompts useMemo body (the actual bug surface).
const buildActiveSkillPrompts = (
    activeSkillIds: string[],
    skills: TestSkill[],
    additionalPrompt: string | null,
): string => {
    const prompts: string[] = [];
    for (const skillId of activeSkillIds) {
        const skill = skills.find(s => s.id === skillId);
        if (skill) {
            prompts.push(`[Active Skill: ${skill.name}]\n${skill.prompt}`);
        }
    }
    if (additionalPrompt) prompts.push(additionalPrompt);
    return prompts.join('\n\n');
};

// The needHydration filter from the lens-restore effect.
const computeNeedHydration = (
    activeSkillIds: string[],
    skills: TestSkill[],
): string[] =>
    activeSkillIds.filter(id => {
        const s = skills.find(sk => sk.id === id);
        return !!s && !s.prompt;
    });

// The setSkills merge-patch applied after GET /skills/{id} resolves.
const mergeHydrated = (
    skills: TestSkill[],
    hydrated: Array<{ id: string; full: TestSkill }>,
): TestSkill[] =>
    skills.map(s => {
        const hit = hydrated.find(v => v.id === s.id);
        return hit ? { ...s, ...hit.full } : s;
    });

describe('ProjectContext skill hydration', () => {
    const ATLAS_BODY = '# Atlas Metrics Skill\n\nQuery Kuiper Atlas telemetry...';

    describe('activeSkillPrompts assembly (bug surface)', () => {
        it('produces an empty-body prompt when an active skill has no prompt (the bug)', () => {
            const skills: TestSkill[] = [
                { id: 'user-atlas-metrics-abc', name: 'atlas-metrics', prompt: '' },
            ];
            const result = buildActiveSkillPrompts(['user-atlas-metrics-abc'], skills, null);
            // This is exactly the broken output: header + newline + nothing.
            expect(result).toBe('[Active Skill: atlas-metrics]\n');
            expect(result).not.toContain('Query Kuiper Atlas');
        });

        it('produces the full prompt once the body is hydrated (the fix)', () => {
            const skills: TestSkill[] = [
                { id: 'user-atlas-metrics-abc', name: 'atlas-metrics', prompt: ATLAS_BODY },
            ];
            const result = buildActiveSkillPrompts(['user-atlas-metrics-abc'], skills, null);
            expect(result).toBe(`[Active Skill: atlas-metrics]\n${ATLAS_BODY}`);
            expect(result).toContain('Query Kuiper Atlas telemetry');
        });

        it('built-in skills (body always present) are unaffected', () => {
            const skills: TestSkill[] = [
                { id: 'builtin-tests', name: 'Tests for everything', prompt: 'Create and validate tests.' },
            ];
            const result = buildActiveSkillPrompts(['builtin-tests'], skills, null);
            expect(result).toBe('[Active Skill: Tests for everything]\nCreate and validate tests.');
        });

        it('joins multiple active skills and appends additionalPrompt', () => {
            const skills: TestSkill[] = [
                { id: 's1', name: 'one', prompt: 'A' },
                { id: 's2', name: 'two', prompt: 'B' },
            ];
            const result = buildActiveSkillPrompts(['s1', 's2'], skills, 'EXTRA');
            expect(result).toBe('[Active Skill: one]\nA\n\n[Active Skill: two]\nB\n\nEXTRA');
        });
    });

    describe('needHydration filter', () => {
        it('selects active skills with an empty body', () => {
            const skills: TestSkill[] = [
                { id: 'a', name: 'a', prompt: '' },        // active, empty → needs hydration
                { id: 'b', name: 'b', prompt: 'has body' },// active, full  → skip
                { id: 'c', name: 'c', prompt: '' },        // inactive      → skip
            ];
            expect(computeNeedHydration(['a', 'b'], skills)).toEqual(['a']);
        });

        it('returns empty when no active skill is given', () => {
            const skills: TestSkill[] = [{ id: 'a', name: 'a', prompt: '' }];
            expect(computeNeedHydration([], skills)).toEqual([]);
        });

        it('ignores active IDs that are not present in skills yet (still loading)', () => {
            const skills: TestSkill[] = [{ id: 'b', name: 'b', prompt: 'x' }];
            expect(computeNeedHydration(['missing'], skills)).toEqual([]);
        });
    });

    describe('merge-patch + no-loop guarantee', () => {
        it('patches the hydrated body into the matching skill only', () => {
            const skills: TestSkill[] = [
                { id: 'a', name: 'atlas-metrics', prompt: '' },
                { id: 'b', name: 'other', prompt: 'untouched' },
            ];
            const merged = mergeHydrated(skills, [
                { id: 'a', full: { id: 'a', name: 'atlas-metrics', prompt: ATLAS_BODY } },
            ]);
            expect(merged.find(s => s.id === 'a')!.prompt).toBe(ATLAS_BODY);
            expect(merged.find(s => s.id === 'b')!.prompt).toBe('untouched');
        });

        it('does not re-trigger hydration after a successful patch (no infinite loop)', () => {
            const skills: TestSkill[] = [
                { id: 'a', name: 'atlas-metrics', prompt: '' },
            ];
            // First pass: hydration needed.
            expect(computeNeedHydration(['a'], skills)).toEqual(['a']);
            // After merge, the effect re-runs (skills changed) — but the
            // now-hydrated skill must NOT be selected again.
            const merged = mergeHydrated(skills, [
                { id: 'a', full: { id: 'a', name: 'atlas-metrics', prompt: ATLAS_BODY } },
            ]);
            expect(computeNeedHydration(['a'], merged)).toEqual([]);
        });

        it('preserves contextIds and other fields when spreading the hydrated skill', () => {
            const skills: TestSkill[] = [
                { id: 'a', name: 'atlas-metrics', prompt: '', contextIds: ['ctx-1'] },
            ];
            const merged = mergeHydrated(skills, [
                { id: 'a', full: { id: 'a', name: 'atlas-metrics', prompt: ATLAS_BODY, contextIds: ['ctx-1', 'ctx-2'] } },
            ]);
            expect(merged[0].prompt).toBe(ATLAS_BODY);
            expect(merged[0].contextIds).toEqual(['ctx-1', 'ctx-2']);
        });
    });
});
