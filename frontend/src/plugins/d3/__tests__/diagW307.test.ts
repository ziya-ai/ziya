/**
 * @jest-environment jsdom
 *
 * Superseded by graphvizGe3e70f.test.ts. This file previously held a throwaway
 * diagnostic that rendered via @viz-js/viz, which cannot be imported under
 * jest's CJS transform (import.meta). The real regression coverage for
 * G-e3e70f (D-284/D-285/D-286) lives in graphvizGe3e70f.test.ts.
 */
it.skip('diagnostic superseded by graphvizGe3e70f.test.ts', () => {
    expect(true).toBe(true);
});
