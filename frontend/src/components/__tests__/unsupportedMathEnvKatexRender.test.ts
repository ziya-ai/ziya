/**
 * Integration proof for the spec6-d1 fix at the KaTeX layer.
 *
 * The unit test unsupportedMathEnvAlias.test.ts pins the STRING transform.
 * This suite proves the CONSEQUENCE: feeding KaTeX the aliased string
 * produces a clean render with NO error, whereas the raw `multline`
 * environment produces a KaTeX error node. That is exactly the difference
 * MathRenderer's sanitize now makes (it calls the alias immediately before
 * the sole katex.renderToString), so this is the end-of-pipeline evidence
 * that the red-error pixel is resolved by real KaTeX — independent of the
 * screenshot harness (whose browser may serve a cached bundle).
 *
 * KaTeX signals a failed render under throwOnError:false by emitting a
 * <span class="katex-error"> (colored with errorColor). We assert on that
 * class, mirroring how the harness counts katex_error spans.
 */
import katex from 'katex';
import { normalizeUnsupportedMathEnvironments } from '../MarkdownRenderer';

jest.mock('marked', () => {
    const marked = (s: string) => s;
    Object.assign(marked, {
        parse: (s: string) => s, setOptions: () => {}, use: () => {},
        walkTokens: () => {}, parseInline: (s: string) => s,
    });
    return { marked, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

const MULTLINE = '\\begin{multline} a + b + c + d + e \\\\ + f + g + h + i + j \\end{multline}';

const render = (latex: string): string =>
    katex.renderToString(latex, { displayMode: true, throwOnError: false, strict: false, errorColor: '#cc0000' });

describe('spec6-d1: KaTeX render of multline before vs after alias', () => {
    it('bundled KaTeX genuinely lacks multline (raw input errors)', () => {
        // Establishes the defect is real at this KaTeX version.
        expect(render(MULTLINE)).toContain('katex-error');
    });

    it('the aliased string renders with NO katex-error', () => {
        const aliased = normalizeUnsupportedMathEnvironments(MULTLINE);
        const html = render(aliased);
        expect(html).not.toContain('katex-error');
        // Sanity: it produced real typeset output.
        expect(html).toContain('katex');
    });

    it('a natively-supported gather still renders cleanly (no regression)', () => {
        const gather = '\\begin{gather} x = 1 \\\\ y = 2 \\end{gather}';
        // Alias must be a no-op on supported envs, and KaTeX must render it.
        expect(normalizeUnsupportedMathEnvironments(gather)).toBe(gather);
        expect(render(gather)).not.toContain('katex-error');
    });
});
