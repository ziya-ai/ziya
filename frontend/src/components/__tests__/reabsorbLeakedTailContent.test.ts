/**
 * reabsorbLeakedTailContent — the "fence fix pass 2 (tail)" of
 * MarkdownRenderer's lexedTokens pipeline.
 *
 * Regression for the 2026-09-18 conversation-switch freeze: the inline regex
 * `(?:[^\n]{0,80}\n?){1,5}$` made the newline optional, so a long final line
 * was tiled into ≤80-char chunks in a combinatorial number of ways whenever
 * the tail could not reach `$` within five chunks. A real 40 KB message that
 * ended "…270 chars\n\n…135 chars" spent 39.5 s in this one pass (profiled at
 * 96% of a 79 s recording); a 200-char line costs 16 s. The 1000-char tail
 * window added earlier bounded the string length, not this term.
 *
 * The timing assertions FAIL against the old regex (16-40 s vs a 250 ms
 * budget) and pass against the mandatory-newline form in <1 ms. The output
 * assertions pin the behaviour that must not change while fixing it.
 */
import { reabsorbLeakedTailContent } from '../fenceScanner';

const timed = (fn: () => string): [string, number] => {
    const t = performance.now();
    const out = fn();
    return [out, performance.now() - t];
};

describe('reabsorbLeakedTailContent', () => {
    it('pulls a short orphan line back inside the last fence', () => {
        const md = 'Intro\n\n```js\nfoo();\n```\n});\n';
        expect(reabsorbLeakedTailContent(md)).toBe('Intro\n\n```js\nfoo();\n});\n```');
    });

    it('pulls several short orphan lines back', () => {
        const md = '```py\nx = 1\n```\ny = 2\nz = 3\n';
        expect(reabsorbLeakedTailContent(md)).toBe('```py\nx = 1\ny = 2\nz = 3\n```');
    });

    it('accepts a single leaked line up to the 120-char guard', () => {
        const line = 'a'.repeat(100);
        const md = '```\ncode\n```\n' + line + '\n';
        expect(reabsorbLeakedTailContent(md)).toBe('```\ncode\n' + line + '\n```');
    });

    it('leaves prose separated by a blank line alone', () => {
        const md = 'Text\n\n```js\nfoo();\n```\n\nThat is the fix.\n';
        expect(reabsorbLeakedTailContent(md)).toBe(md);
    });

    it('leaves a >120-char trailing paragraph alone', () => {
        const md = '```\ncode\n```\n' + 'b'.repeat(121);
        expect(reabsorbLeakedTailContent(md)).toBe(md);
    });

    it('does not match the ``` inside a 4-backtick fence close', () => {
        const md = 'x\n````tool:mcp_x|h|text\nout\n````\nafter\n';
        expect(reabsorbLeakedTailContent(md)).toBe(md);
    });

    it('does not span a following code block', () => {
        const md = '```\na\n```\n```js\nb\n```\n';
        expect(reabsorbLeakedTailContent(md)).toBe(md);
    });

    it('is not exponential on a long unmatched final line (the 2026-09-18 freeze shape)', () => {
        // Real message 53's tail: fence, 270-char line, blank, 135-char line.
        // The old regex needs 7 chunks to reach `$`, has only 5, and tries
        // every tiling of the 270-char line before failing: 39.5 s measured.
        const md = 'prose\n```\n' + 'a'.repeat(270) + '\n\n' + 'b'.repeat(135);
        const [out, ms] = timed(() => reabsorbLeakedTailContent(md));
        expect(out).toBe(md);
        expect(ms).toBeLessThan(250);
    });

    it('is not exponential on a 200-char final line (16 s on the old regex)', () => {
        const md = 'x\n```\n' + 'a'.repeat(200) + '\n\n' + 'b'.repeat(135);
        const [out, ms] = timed(() => reabsorbLeakedTailContent(md));
        expect(out).toBe(md);
        expect(ms).toBeLessThan(250);
    });

    it('only inspects the trailing window, so message size is irrelevant', () => {
        const md = 'z'.repeat(300_000) + '\n```\ncode\n```\n});\n';
        const [out, ms] = timed(() => reabsorbLeakedTailContent(md));
        expect(out.endsWith('```\ncode\n});\n```')).toBe(true);
        expect(ms).toBeLessThan(250);
    });
});
