/**
 * The client and the server must agree on which diff a later diff replaces.
 *
 * findSupersededDiffParts() decides which diffs the UI fades as "Superseded
 * by a later revision below".  The diff-apply hold counts the diffs it waits
 * for on the server, by its own copy of the rule (_superseded_indices in
 * app/utils/apply_ledger.py).  When the two disagree the chip says "Waiting
 * for 2 of 2 diffs" under a response that offers one.
 *
 * Same fixture table as tests/test_diff_supersede_parity.py.
 */
import * as fs from 'fs';
import * as path from 'path';
import { findSupersededDiffParts } from '../diffUtils';

interface Case { name: string; blocks: string[]; superseded: number[][] }

const FIXTURE_PATH = path.resolve(__dirname, '../../../../tests/fixtures/diff_supersede_cases.json');
const cases: Case[] = JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8')).cases;

const byPair = (a: number[], b: number[]) => a[0] - b[0] || a[1] - b[1];

describe('diff supersede parity with the server', () => {
    it('the fixture exercises both outcomes', () => {
        expect(cases.length).toBeGreaterThanOrEqual(10);
        expect(cases.some(c => c.superseded.length > 0)).toBe(true);
        expect(cases.some(c => c.superseded.length === 0)).toBe(true);
    });

    it.each(cases.map(c => [c.name, c] as const))('%s', (_name, c) => {
        const got: number[][] = [];
        findSupersededDiffParts(c.blocks).forEach((files, block) => {
            files.forEach(file => got.push([block, file]));
        });
        expect(got.sort(byPair)).toEqual([...c.superseded].sort(byPair));
    });
});
