/**
 * Tests for upgradeNestedFences() — the fence-collision pass that lengthens an
 * outer backtick fence when its body contains a nested fence that would
 * otherwise prematurely close it.
 *
 * Case A (the real-world bug): a ```diff that patches a file containing its own
 * ```sql / ```json blocks. CommonMark closes the outer ```diff at the first
 * inner ```, chopping the diff. The pass must upgrade the outer fence to 4
 * backticks so the diff parses as one unit and the inner ``` pass through.
 *
 * Case B (regression guard): a non-nestable ```json block with a MISSING close
 * followed by a sibling ```python block must NOT be merged — the conservative
 * "stop at first lang-tagged opener" behavior is preserved for non-nestable
 * outer fences.
 */

import { upgradeNestedFences } from '../fenceScanner';

// Build fences from char codes so this source file does not itself contain
// triple-backtick runs (which would confuse tooling/rendering of the test).
const TICK3 = '`'.repeat(3);
const TICK4 = '`'.repeat(4);

describe('upgradeNestedFences — Case A: nested fences inside a diff', () => {
    it('upgrades a ```diff containing inner ```sql/```json to 4 backticks', () => {
        const input = [
            TICK3 + 'diff',
            'diff --git a/SKILL.md b/SKILL.md',
            '--- a/SKILL.md',
            '+++ b/SKILL.md',
            '@@ -1,3 +1,6 @@',
            ' Query examples:',
            // Inner fences appear at column 0 (lang opener) with a
            // space-indented close — exactly how the real malformed diff
            // bodies render. A "+"-prefixed line is NOT a valid CommonMark
            // fence close, so it would not collide and needs no upgrade.
            TICK3 + 'sql',
            'SELECT DISTINCT entityId FROM t',
            ' ' + TICK3,
            TICK3 + 'json',
            '{"resources": ["skill://x"]}',
            ' ' + TICK3,
            TICK3,
        ].join('\n');

        const out = upgradeNestedFences(input);
        const lines = out.split('\n');

        // Outer opener and close are widened to 4 backticks.
        expect(lines[0]).toBe(TICK4 + 'diff');
        expect(lines[lines.length - 1]).toBe(TICK4);
        // Inner fences are left at 3 backticks (pass through verbatim).
        expect(out).toContain('\n' + TICK3 + 'sql\n');
        expect(out).toContain('\n' + TICK3 + 'json\n');
        // No inner fence was widened.
        expect(out).not.toContain(TICK4 + 'sql');
        // The diff body survives intact (nothing chopped after the first ```sql).
        expect(out).toContain('{"resources": ["skill://x"]}');
    });

    it('does not upgrade a ```diff with NO nested fences', () => {
        const input = [
            TICK3 + 'diff',
            '--- a/foo.ts',
            '+++ b/foo.ts',
            '@@ -1 +1 @@',
            '-old',
            '+new',
            TICK3,
        ].join('\n');
        // No collision → returned byte-for-byte unchanged.
        expect(upgradeNestedFences(input)).toBe(input);
    });

    it('upgrades a ```markdown block that quotes a fenced example', () => {
        const input = [
            TICK3 + 'markdown',
            '# Title',
            TICK3 + 'bash',
            'echo hi',
            TICK3,
            'done',
            TICK3,
        ].join('\n');
        const out = upgradeNestedFences(input);
        const lines = out.split('\n');
        expect(lines[0]).toBe(TICK4 + 'markdown');
        expect(lines[lines.length - 1]).toBe(TICK4);
        // Inner ```bash and its close remain 3 backticks.
        expect(lines[2]).toBe(TICK3 + 'bash');
        expect(lines[4]).toBe(TICK3);
    });
});

describe('upgradeNestedFences — Case B: non-nestable, no false merge', () => {
    it('does NOT merge a ```json (missing close) with a sibling ```python', () => {
        const input = [
            TICK3 + 'json',
            '{"a": 1}',
            // intentionally no close for the json block
            TICK3 + 'python',
            'print(1)',
            TICK3,
        ].join('\n');
        // Non-nestable outer (json): scan stops at the ```python opener
        // (overshoot), no collision recorded → no upgrade, no merge.
        expect(upgradeNestedFences(input)).toBe(input);
    });

    it('does not upgrade a plain ```json that has no inner fence collision', () => {
        const input = [
            TICK3 + 'json',
            '{"a": 1, "b": [2, 3]}',
            TICK3,
        ].join('\n');
        expect(upgradeNestedFences(input)).toBe(input);
    });
});

describe('upgradeNestedFences — edge cases', () => {
    it('returns empty string unchanged', () => {
        expect(upgradeNestedFences('')).toBe('');
    });

    it('returns plain prose (no fences) unchanged', () => {
        const input = 'just some text\nwith two lines';
        expect(upgradeNestedFences(input)).toBe(input);
    });

    it('leaves a nestable fence with no outer close unchanged', () => {
        const input = [
            TICK3 + 'diff',
            '+line',
            '+' + TICK3 + 'sql',
            '+SELECT 1',
            '+' + TICK3,
            // no outer close
        ].join('\n');
        // No depth-0 column-0 close → closeIdx === -1 → no upgrade.
        expect(upgradeNestedFences(input)).toBe(input);
    });

    it('processes two independent nestable blocks in one document', () => {
        const block = (file: string) => [
            TICK3 + 'diff',
            TICK3 + 'sql',
            'SELECT 1 -- ' + file,
            ' ' + TICK3,
            TICK3,
        ];
        const input = [...block('a'), 'prose between', ...block('b')].join('\n');
        const out = upgradeNestedFences(input);
        // Both outer openers upgraded; inner ```sql left intact.
        expect(out.split('\n').filter(l => l === TICK4 + 'diff').length).toBe(2);
        expect(out.split('\n').filter(l => l === TICK3 + 'sql').length).toBe(2);
    });

    it('does not treat an indented (non-column-0) fence as an outer opener', () => {
        const input = [
            '  ' + TICK3 + 'diff',
            '  +' + TICK3 + 'sql',
            '  +' + TICK3,
            '  ' + TICK3,
        ].join('\n');
        // Indented opener is not column-0 → not upgraded.
        expect(upgradeNestedFences(input)).toBe(input);
    });
});
