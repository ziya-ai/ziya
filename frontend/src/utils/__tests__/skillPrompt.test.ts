/**
 * formatActiveSkillPrompt — the client-assembled "[Active Skill]" block.
 *
 * Until the bench is authoritative for a project (bench_prompt.merge), the
 * prompt the server uses is the one ProjectContext builds here.  A
 * file-backed skill's bundled-resource manifest (agentskills.io: skill
 * directory + <skill_resources>) arrives pre-rendered on the record as
 * ``resourceManifest`` (GET /skills/{id}); it must reach the prompt, or a
 * SKILL.md that says "run scripts/x.py" has no anchor on this path.
 */
import { formatActiveSkillPrompt } from '../skillPrompt';
import type { Skill } from '../../types/skill';

const base: Skill = {
    id: 'project-csv-helper-abc', name: 'csv-helper', description: 'd',
    prompt: 'Run `scripts/summarise.py`.', color: '#000', tokenCount: 1,
    isBuiltIn: false, createdAt: 0, lastUsedAt: 0,
};

const MANIFEST = [
    'Skill directory: /p/.skills/csv-helper',
    'Relative paths in this skill are relative to the skill directory.',
    '',
    '<skill_resources>',
    '  <file>scripts/summarise.py</file>',
    '</skill_resources>',
].join('\n');

describe('formatActiveSkillPrompt', () => {
    it('keeps the existing header and body for a skill with no manifest', () => {
        expect(formatActiveSkillPrompt(base))
            .toBe('[Active Skill: csv-helper]\nRun `scripts/summarise.py`.');
    });

    it('appends the manifest after the body', () => {
        expect(formatActiveSkillPrompt({ ...base, resourceManifest: MANIFEST }))
            .toBe(`[Active Skill: csv-helper]\nRun \`scripts/summarise.py\`.\n\n${MANIFEST}`);
    });

    it('does not attach a manifest to an unhydrated (empty) body', () => {
        // Matches the server: "" means the body has not loaded yet.
        expect(formatActiveSkillPrompt({ ...base, prompt: '', resourceManifest: MANIFEST }))
            .toBe('[Active Skill: csv-helper]\n');
    });
});
