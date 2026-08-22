/**
 * Guards the pass ORDER in MarkdownRenderer's preprocessing pipeline: the
 * glued-opener repair must leave fence parity correct for the passes that
 * run after it (splitJsonSpecTrailingContent, repairAtomicFenceRuns,
 * stripBareProseFences), otherwise those passes act on inverted state and
 * strip the stray bare fence instead of restoring the block.
 *
 * The pipeline is reproduced here rather than imported because it lives
 * inline in MarkdownRenderer's useMemo. If that order changes, update this.
 */
import {
    applyOutsideFences,
    classifyFenceLines,
    upgradeNestedFences,
    repairAtomicFenceRuns,
    repairGluedFenceOpeners,
    stripBareProseFences,
    splitJsonSpecTrailingContent,
} from '../fenceScanner';

const GLUED = (n: number) =>
    `## ACT ${n}\n\nprose that ends and then glues the fence.\`\`\`vega-lite\n{"mark":"bar"}\n\`\`\`\n\nfollow-up prose for act ${n}.\n`;

function openers(m: string): string[] {
    const lines = m.split('\n');
    return classifyFenceLines(m)
        .map((c, i) => (c.kind === 'open' ? lines[i] : null))
        .filter((x): x is string => x !== null);
}

function pipeline(src: string): string {
    let m = upgradeNestedFences(src);
    m = applyOutsideFences(m, (s) =>
        s.replace(/(\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|_[^_]+_)\n(```[a-zA-Z0-9_-]*)/gm, '$1\n\n$2'));
    m = applyOutsideFences(m, (s) => s.replace(/(\*\*)\n(```)/g, '$1\n\n$2'));
    m = applyOutsideFences(m, (s) =>
        s.replace(/(^#{1,6}\s+[^\n`]+?)\s+(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/gm, '$1\n\n$2'));
    m = applyOutsideFences(m, (s) =>
        s.replace(/(\d+\.\s+[^\n`]+?)\s+(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/gm, '$1\n\n$2'));
    m = applyOutsideFences(m, (s) =>
        s.replace(/([^\n])\n(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2'));
    m = repairGluedFenceOpeners(m);
    m = splitJsonSpecTrailingContent(m);
    m = repairAtomicFenceRuns(m);
    m = stripBareProseFences(m);
    return m;
}

describe('preprocessing pass order with glued openers', () => {
    it.each([1, 2, 5, 9])('survives the full pipeline with %i glued openers', (n) => {
        const src = Array.from({ length: n }, (_, i) => GLUED(i + 1)).join('\n');
        const out = pipeline(src);
        expect(out.includes('fence.```vega-lite')).toBe(false);
        expect(openers(out)).toEqual(Array(n).fill('```vega-lite'));
    });
});
