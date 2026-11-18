/**
 * The client and the server must agree on which diff fences are one diff.
 *
 * chainHeaderlessContinuationDiffs() merges a headerless continuation fence
 * into the headed diff before it; the pair gets one Apply button and is sent
 * as one diff.  The server's apply ledger keys diffs by content and merges
 * fences by its own copy of the rule (_chains_to_previous in
 * app/utils/apply_ledger.py).  When the two drift, applies stop matching
 * their key and quietly fall back to matching by file.
 *
 * Same fixture table as tests/test_diff_continuation_parity.py.
 */

// Same shims as chainHeaderlessContinuationDiffs.test.ts: ``marked`` and
// ``uuid`` are ESM-only and the CRA jest transform won't process them.
jest.mock('marked', () => {
  const marked = (s: string) => s;
  Object.assign(marked, {
    parse: (s: string) => s,
    setOptions: () => {},
    use: () => {},
    walkTokens: () => {},
    parseInline: (s: string) => s,
  });
  return { marked, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import * as fs from 'fs';
import * as path from 'path';
import { chainHeaderlessContinuationDiffs } from '../MarkdownRenderer';

interface Case { name: string; blocks: string[]; into: Array<'head' | number | null> }

const FIXTURE_PATH = path.resolve(__dirname, '../../../../tests/fixtures/diff_continuation_cases.json');
const cases: Case[] = JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8')).cases;

/** The lines the server hashes (diff_key in apply_ledger.py): added and
 *  removed lines, without file headers or trailing whitespace. */
const changedLines = (text: string): string[] =>
    text.split('\n')
        .filter(l => (l[0] === '+' || l[0] === '-') && !l.startsWith('--- ') && !l.startsWith('+++ '))
        .map(l => l.replace(/\s+$/, ''));

describe('diff continuation parity with the server', () => {
    it('the fixture exercises both outcomes', () => {
        const flat = cases.reduce<Array<'head' | number | null>>((acc, c) => acc.concat(c.into), []);
        expect(cases.length).toBeGreaterThanOrEqual(10);
        expect(flat.some(t => typeof t === 'number')).toBe(true);
        expect(flat.some(t => t === null)).toBe(true);
    });

    it.each(cases.map(c => [c.name, c] as const))('%s', (_name, c) => {
        const out = chainHeaderlessContinuationDiffs(c.blocks);
        expect(out.length).toBe(c.blocks.length);
        c.blocks.forEach((block, i) => {
            if (typeof c.into[i] === 'number') {
                expect(out[i]).toBe('');            // absorbed into its head
                return;
            }
            const merged = c.blocks.filter((_b, j) => c.into[j] === i);
            if (merged.length === 0) {
                expect(out[i]).toBe(block);         // left exactly as written
                return;
            }
            const want = [block, ...merged]
                .reduce<string[]>((acc, b) => acc.concat(changedLines(b)), []);
            expect(changedLines(out[i])).toEqual(want);
        });
    });
});
