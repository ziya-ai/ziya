/**
 * repairAtomicFenceRuns must not "repair" an atomic block whose body
 * legitimately contains a line that merely LOOKS like a fence opener.
 *
 * A mermaid node label, or html-mockup body text, can quote a fence marker
 * to discuss it. Such a line sits at column 0 and matches the CommonMark
 * opener shape, but its info string is prose, not a language tag. The
 * repair pass used to accept any info string, so it treated that line as
 * proof the block's close was missing, synthesized a close mid-diagram,
 * and promoted the label line to an opener -- rendering node A alone and
 * spilling the rest of the diagram as a code block.
 *
 * The discriminator is the info string: a genuine opener is a short
 * language token, optionally followed by simple word modifiers
 * ("html-mockup figure"). Anything carrying markup, quotes or punctuation
 * is body content.
 */
import { classifyFenceLines, repairAtomicFenceRuns } from '../fenceScanner';

const MERMAID_QUOTING_FENCES = [
    'Traced against ACT XI:',
    '',
    '```mermaid',
    'flowchart LR',
    '    A["whether.',
    '',
    '```vega-lite<br/><i>not startswith to INVISIBLE</i>"] --> B["in_block = False"]',
    '    B --> C["``` real closer, bare<br/><i>elif in_block is False</i>"]',
    '    D --> E["',
    '',
    '```html-mockup figure<br/><i>implicit close/reopen</i>"]',
    '    E --> F["``` closes it<br/>in_block = False"]',
    '```',
    '',
    'So the parity is inverted from the missed opener onward.',
].join('\n');

describe('repairAtomicFenceRuns vs. fence markers inside diagram bodies', () => {
    it('leaves a mermaid block whose labels quote fence markers intact', () => {
        const out = repairAtomicFenceRuns(MERMAID_QUOTING_FENCES);
        const cls = classifyFenceLines(out);
        const lines = out.split('\n');

        // Exactly one block, and it is the mermaid one.
        const openers = lines.filter((l, i) => cls[i]?.kind === 'open');
        expect(openers).toEqual(['```mermaid']);

        // Positive assertion that the block actually spans the whole
        // diagram: the last node line is still INSIDE the fence.
        const lastNode = lines.findIndex((l) => l.includes('E --> F['));
        expect(lastNode).toBeGreaterThan(-1);
        expect(cls[lastNode].kind).toBe('content');

        // No close was synthesized: same line count as the input.
        expect(lines.length).toBe(MERMAID_QUOTING_FENCES.split('\n').length);
    });

    it('still repairs a genuinely unclosed atomic block', () => {
        const broken = [
            '```mermaid',
            'graph TD',
            '  A --> B',
            '```vega-lite',
            '{"mark":"bar"}',
            '```',
        ].join('\n');
        const out = repairAtomicFenceRuns(broken);
        const cls = classifyFenceLines(out);
        const lines = out.split('\n');
        const openers = lines.filter((l, i) => cls[i]?.kind === 'open');
        // Both blocks recovered as openers -- the whole point of the pass.
        expect(openers).toEqual(['```mermaid', '```vega-lite']);
    });

    it('still treats a modifier-bearing opener as a real opener', () => {
        const broken = [
            '```mermaid',
            'graph TD',
            '  A --> B',
            '```html-mockup figure',
            '<div>x</div>',
            '```',
        ].join('\n');
        const out = repairAtomicFenceRuns(broken);
        const cls = classifyFenceLines(out);
        const lines = out.split('\n');
        const openers = lines.filter((l, i) => cls[i]?.kind === 'open');
        expect(openers).toEqual(['```mermaid', '```html-mockup figure']);
    });
});
