/**
 * Math inside reasoning ("thinking") blocks.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * ThinkingBlock does not use MarkdownRenderer's preprocessing pipeline; it
 * calls `marked.parse` on the reasoning text directly.  The pipeline stage that
 * lifts `$...$` out for KaTeX therefore never ran on reasoning content, and
 * math rendered as literal dollar-delimited source.
 *
 * These tests drive the FULL round trip -- protect, real marked lexer, restore
 * -- because the defect lived in the interaction between the markdown parser
 * and the math pass, not in either half alone.  A test that only inspected
 * `protectThinkingMath`'s output string would pass even if marked destroyed the
 * marker on the way through.
 */
// marked 16 ships ESM-only from its package "exports"; the UMD build keeps this
// suite self-contained under the current jest transform config (same reasoning
// as inlineMathMarker.test.ts).
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { marked } = require('marked/lib/marked.umd.js');

import {
    protectThinkingMath,
    restoreThinkingMath,
    hasThinkingMathMarkers,
} from '../thinkingMath';
import { MATH_INLINE_MARKER_PREFIX } from '../inlineMathClassifier';

interface RenderCall {
    latex: string;
    displayMode: boolean;
}

/**
 * Run the exact sequence ThinkingBlock runs, with a stub renderer standing in
 * for KaTeX so the assertions do not depend on KaTeX's output markup.
 */
function renderThinking(source: string): { html: string; calls: RenderCall[] } {
    const calls: RenderCall[] = [];
    const { source: protectedSource, displayMath } = protectThinkingMath(source);
    const parsed = marked.parse(protectedSource, { breaks: true, gfm: true }) as string;
    const html = restoreThinkingMath(parsed, displayMath, (latex, displayMode) => {
        calls.push({ latex, displayMode });
        return `<math-stub mode="${displayMode ? 'display' : 'inline'}">${latex}</math-stub>`;
    });
    return { html, calls };
}

describe('thinking-block math round trip', () => {
    it('renders an inline span and leaves no literal delimiters behind', () => {
        const { html, calls } = renderThinking('so the energy is $E = mc^2$ here');

        expect(calls).toEqual([{ latex: 'E = mc^2', displayMode: false }]);
        expect(html).toContain('<math-stub mode="inline">E = mc^2</math-stub>');
        // The whole point of the fix: no raw dollar sign survives.
        expect(html).not.toContain('$');
    });

    it('renders a display span in display mode', () => {
        const { html, calls } = renderThinking('therefore\n\n$$\\int_0^1 x\\,dx = \\tfrac12$$\n');

        expect(calls).toEqual([
            { latex: '\\int_0^1 x\\,dx = \\tfrac12', displayMode: true },
        ]);
        expect(html).toContain('mode="display"');
        expect(html).not.toContain('$');
    });

    it('does not leave a stray delimiter when display and inline could both match', () => {
        // `$$x$$` is the regression case for ordering: the inline pattern
        // matches the INNER `$x$`, so processing inline first would emit
        // "$<math>$" with orphaned dollars on each side.
        const { html, calls } = renderThinking('$$a + b$$');

        expect(calls.map(c => c.displayMode)).toEqual([true]);
        expect(html).not.toContain('$');
    });

    it('leaves math inside a code span alone', () => {
        const { html, calls } = renderThinking('write it as `$x$` in the source');

        expect(calls).toEqual([]);
        expect(html).toContain('$x$');
        expect(html).not.toContain(MATH_INLINE_MARKER_PREFIX);
    });

    it('leaves math inside a fenced block alone', () => {
        const { html, calls } = renderThinking('```\nprice = $x$\n```\n');

        expect(calls).toEqual([]);
        expect(html).toContain('$x$');
        expect(html).not.toContain(MATH_INLINE_MARKER_PREFIX);
    });

    it('does not treat currency as math', () => {
        const { html, calls } = renderThinking('the plan costs $5 and the other costs $10');

        expect(calls).toEqual([]);
        expect(html).toContain('$5');
        expect(html).toContain('$10');
    });

    it('survives markdown-active characters in the payload', () => {
        // Emphasis inside math would be eaten by marked if the payload were not
        // opaque; this is the property base64 encoding buys.
        const { calls } = renderThinking('given $a*b*c$ we get');

        expect(calls).toEqual([{ latex: 'a*b*c', displayMode: false }]);
    });

    it('emits no marker text when the source contains no math', () => {
        const { html, calls } = renderThinking('just some reasoning, no math at all');

        expect(calls).toEqual([]);
        expect(html).not.toContain(MATH_INLINE_MARKER_PREFIX);
    });
});

describe('restoreThinkingMath failure handling', () => {
    it('keeps an undecodable marker visible rather than deleting content', () => {
        const corrupt = `${MATH_INLINE_MARKER_PREFIX}!!!not-base64!!!\u27E9`;
        const out = restoreThinkingMath(corrupt, [], () => '<math-stub/>');

        expect(out).toBe(corrupt);
    });

    it('keeps a display marker whose index has no entry', () => {
        const orphan = '\u27E8THINKING_DISPLAY_MATH:7\u27E9';
        const out = restoreThinkingMath(orphan, [], () => '<math-stub/>');

        expect(out).toBe(orphan);
    });
});

describe('hasThinkingMathMarkers', () => {
    it('reports true for display math with no inline markers', () => {
        expect(hasThinkingMathMarkers('<p>no markers</p>', ['x'])).toBe(true);
    });

    it('reports false for content with neither', () => {
        expect(hasThinkingMathMarkers('<p>no markers</p>', [])).toBe(false);
    });

    it('is not stateful across repeated calls', () => {
        // A global regex with `.test()` would alternate true/false here, which
        // would make KaTeX loading depend on how many blocks rendered before.
        const html = `<p>${MATH_INLINE_MARKER_PREFIX}eA==\u27E9</p>`;
        expect(hasThinkingMathMarkers(html, [])).toBe(true);
        expect(hasThinkingMathMarkers(html, [])).toBe(true);
        expect(hasThinkingMathMarkers(html, [])).toBe(true);
    });
});
