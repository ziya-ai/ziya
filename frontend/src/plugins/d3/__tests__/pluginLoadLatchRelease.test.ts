/**
 * The plugin-load latch that turned a transient stall into a permanent one.
 *
 * Observed failure (vega-lite, headless render, CL5 iteration 92):
 *
 *   Diagram render timed out after 35000ms (type=vega-lite)
 *   last_event='timeout-no-output'  pageerrors=[]
 *   console_tail: 'Starting render #232449, ... hasPlugin: false'
 *                 'D3RENDERER: Plugin already loading, skipping duplicate call'
 *
 * `isLoadingPluginRef` exists at exactly four sites in D3Renderer.tsx:
 * declaration, the early-return gate, set-true immediately before
 * `await findPluginForSpec(spec)`, and set-false immediately after.  There is
 * no try/finally, no timeout, and no reset on unmount, so the flag is a
 * one-way latch: if that await ever rejects OR never settles, every later
 * attempt returns at the gate and the component can never render again.
 * `hasPlugin` stays false forever and the only outcome is the 35s timeout.
 *
 * `registry.ts` supplies the reject half: findPluginForSpec calls
 * `plugin.canHandle(spec)` unguarded while walking plugins in priority
 * order, so a single throwing predicate rejects the whole search - and takes
 * down specs it was never even the right plugin for.
 *
 * Source-contract assertions follow this directory's convention (see
 * pluginSizingConfig.test.ts): importing D3Renderer or resolving the real
 * plugin chunks pulls in mermaid, vega-embed, plotly and @viz-js/viz, which
 * is impractical under jest.  Each block therefore carries a positive
 * control that fails if the detector itself stops matching, so a refactor
 * cannot silently turn these into vacuous passes.
 */
import * as fs from 'fs';
import * as path from 'path';

const D3RENDERER = fs.readFileSync(
    path.join(__dirname, '..', '..', '..', 'components', 'D3Renderer.tsx'),
    'utf-8',
);
const REGISTRY = fs.readFileSync(
    path.join(__dirname, '..', 'registry.ts'),
    'utf-8',
);

/** The `await findPluginForSpec(...)` region, which the latch must span.
 *
 * Bounded by STRUCTURE (the latch, then the `if (!loadedPlugin)` branch that
 * consumes the load result), never by a fixed byte count. A fixed-size slice
 * silently excludes whatever the fix appends: measured at 900 chars, the
 * patched `finally` sat 1948 chars past the latch and outside the window, so
 * the assertions failed against CORRECT code.
 */
function pluginLoadRegion(src: string): string {
    const start = src.indexOf('isLoadingPluginRef.current = true');
    expect(start).toBeGreaterThan(-1);          // positive control
    // Search from the latch FORWARD: a bare indexOf finds the top-of-file
    // import of findPluginForSpec, which precedes the latch, so the region
    // would be empty and every assertion below would fail regardless of
    // whether the fix is present.
    const loadIdx = src.indexOf('findPluginForSpec', start);
    expect(loadIdx).toBeGreaterThan(start);     // positive control: order
    // The result-consuming branch closes the load region in both the
    // unpatched (straight-line) and patched (try/catch/finally) shapes.
    const end = src.indexOf('if (!loadedPlugin)', loadIdx);
    expect(end).toBeGreaterThan(loadIdx);       // positive control: present
    return src.slice(start, end);
}

describe('D3Renderer plugin-load latch', () => {
    it('locates the latch and the load call (guards against a vacuous pass)', () => {
        expect(D3RENDERER).toContain('isLoadingPluginRef');
        expect(D3RENDERER).toContain('findPluginForSpec');
        // The gate whose early return is what wedges the component.
        expect(D3RENDERER).toMatch(/if\s*\(\s*isLoadingPluginRef\.current\s*\)/);
    });

    it('releases the latch in a finally, not only on the success path', () => {
        // The defect: `= true` ... await ... `= false` as straight-line code.
        // A rejection skips the reset entirely.
        const region = pluginLoadRegion(D3RENDERER);
        expect(region).toMatch(/finally\s*\{/);
    });

    it('resets the latch inside that finally', () => {
        const region = pluginLoadRegion(D3RENDERER);
        const finallyIdx = region.indexOf('finally');
        expect(finallyIdx).toBeGreaterThan(-1);
        expect(region.slice(finallyIdx)).toMatch(
            /isLoadingPluginRef\.current\s*=\s*false/,
        );
    });

    it('bounds the load so a never-settling import cannot latch forever', () => {
        // A finally alone does not help when the promise simply never
        // settles - which is what the evidence shows (pageerrors=[], so
        // nothing threw; hasPlugin false for 232k attempts). The await must
        // be raced against a timeout.
        const region = pluginLoadRegion(D3RENDERER);
        expect(region).toMatch(/Promise\.race|setTimeout|withTimeout/);
    });

    it('surfaces a load failure instead of failing silently', () => {
        // A released latch that reports nothing just re-enters the same
        // stall on the next attempt. The user-visible outcome must be an
        // error, not another 35s of nothing.
        //
        // Anchored on a CATCH that reports, not on setRenderError alone:
        // the region already contains a setRenderError for the distinct
        // "no compatible plugin found" case, so the bare-call assertion
        // passed against unpatched code and certified nothing.
        const region = pluginLoadRegion(D3RENDERER);
        const catchIdx = region.search(/catch\s*\(/);
        expect(catchIdx).toBeGreaterThan(-1);
        expect(region.slice(catchIdx)).toMatch(/setRenderError|setErrorDetails/);
    });
});

describe('findPluginForSpec canHandle isolation', () => {
    it('locates the canHandle call site (guards against a vacuous pass)', () => {
        expect(REGISTRY).toContain('export async function findPluginForSpec');
        expect(REGISTRY).toMatch(/canHandle\s*\(\s*spec\s*\)/);
    });

    it('does not call canHandle unguarded inside the priority walk', () => {
        // One throwing predicate must not reject the search for every other
        // plugin - the loop walks ALL plugins in priority order, so a
        // vega-lite spec is routed through basic-chart, vega and plotly
        // first, and any of them throwing loses the render.
        const fnIdx = REGISTRY.indexOf('export async function findPluginForSpec');
        const body = REGISTRY.slice(fnIdx, REGISTRY.indexOf('\n}', fnIdx));
        expect(body).toMatch(/try\s*\{|safeCanHandle/);
    });

    it('still returns undefined (not a throw) when nothing matches', () => {
        // Pins the existing contract the D3Renderer error path depends on:
        // it distinguishes "no plugin" (actionable message) from a crash.
        const fnIdx = REGISTRY.indexOf('export async function findPluginForSpec');
        const body = REGISTRY.slice(fnIdx, REGISTRY.indexOf('\n}', fnIdx));
        expect(body).toMatch(/return\s+undefined/);
    });
});
