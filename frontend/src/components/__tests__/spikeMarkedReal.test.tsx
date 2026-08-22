/**
 * @jest-environment jsdom
 *
 * SPIKE smoke test (Card I, Stage 1 feasibility): can jest load the REAL
 * ESM `marked` so we can exercise the genuine MarkdownRenderer tokenizer path?
 *
 * RUN THIS WITH THE ESM TRANSFORM OVERRIDE:
 *   cd frontend && CI=true npx craco test spikeMarkedReal --watchAll=false \
 *     --transformIgnorePatterns "node_modules/(?!(marked|uuid|react-diff-view)/)"
 *
 * Under a PLAIN `craco test` (no override), `marked` is ESM-only and cannot be
 * parsed by the CRA transform, so this suite SKIPS itself rather than breaking
 * the default run. The require is lazy + guarded to make that possible.
 */
let marked: any;
let ESM_TRANSFORM_ACTIVE = false;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    marked = require('marked').marked;
    ESM_TRANSFORM_ACTIVE = typeof marked?.lexer === 'function';
} catch {
    ESM_TRANSFORM_ACTIVE = false;
}

const maybe = ESM_TRANSFORM_ACTIVE ? test : test.skip;

maybe('real marked lexer produces code + diff tokens', () => {
    const md = '```python\nprint(1)\n```\n\n```diff\n+added\n-removed\n```\n';
    const tokens = marked.lexer(md);
    const langs = tokens.filter((t: any) => t.type === 'code').map((t: any) => t.lang);
    expect(langs).toEqual(expect.arrayContaining(['python', 'diff']));
});

// Guarantee at least one executed assertion so the file is never "empty".
test('spike file loads without breaking the default suite', () => {
    expect(true).toBe(true);
});
