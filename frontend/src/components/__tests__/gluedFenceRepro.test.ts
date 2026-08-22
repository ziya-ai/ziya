/**
 * Glued fence openers: a model writes "...rather than whether.```vega-lite"
 * with no newline, which CommonMark does not see as a fence at all.
 *
 * The repair pass has to iterate. A glued opener inverts fence parity for
 * everything downstream (the block's real closing ``` reads as an OPENER),
 * so applyOutsideFences classifies the rest of the message as fence content
 * and declines to touch it. One pass therefore repaired only the FIRST
 * glued opener in a message; in a long multi-chart answer every later one
 * survived and rendered as literal prose.
 */
import { repairGluedFenceOpeners, classifyFenceLines } from '../fenceScanner';

const GLUED = (n: number) =>
    `## ACT ${n}\n\nprose that ends and then glues the fence.\`\`\`vega-lite\n{"mark":"bar"}\n\`\`\`\n\nfollow-up prose for act ${n}.\n`;

function openers(m: string): string[] {
    const lines = m.split('\n');
    return classifyFenceLines(m)
        .map((c, i) => (c.kind === 'open' ? lines[i] : null))
        .filter((x): x is string => x !== null);
}

describe('repairGluedFenceOpeners', () => {
    it.each([1, 2, 3, 5, 9])('recovers every glued opener when there are %i', (n) => {
        const src = Array.from({ length: n }, (_, i) => GLUED(i + 1)).join('\n');
        const out = repairGluedFenceOpeners(src);
        expect(out.includes('fence.```vega-lite')).toBe(false);
        expect(openers(out)).toEqual(Array(n).fill('```vega-lite'));
    });

    it('recovers a later block after an earlier glued opener (ACT XI shape)', () => {
        const src = [
            '# ACT XI',
            '',
            'refuse to trust the reported rate, and choose where loss happens.```vega-lite',
            '{"mark":"bar"}',
            '```',
            '',
            'Where the burst credit comes from:',
            '',
            '```html-mockup figure',
            '<div>OBUF</div>',
            '```',
            '',
        ].join('\n');
        expect(openers(repairGluedFenceOpeners(src)))
            .toEqual(['```vega-lite', '```html-mockup figure']);
    });

    it('leaves a glued fence quoted inside a real code block alone', () => {
        const src = '````markdown\nexample: prose:```vega-lite\n{"a":1}\n```\n````\n';
        expect(repairGluedFenceOpeners(src)).toBe(src);
    });

    it('is idempotent on already-correct markdown', () => {
        const src = '## A\n\nprose\n\n```vega-lite\n{"mark":"bar"}\n```\n\nmore prose\n';
        expect(repairGluedFenceOpeners(src)).toBe(src);
    });

    it('does not treat a bare fence or a longer run as a glued opener', () => {
        expect(repairGluedFenceOpeners('text:```\ncode\n```\n')).toBe('text:```\ncode\n```\n');
        expect(repairGluedFenceOpeners('````python\ncode\n````\n')).toBe('````python\ncode\n````\n');
    });
});
