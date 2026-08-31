/**
 * @jest-environment jsdom
 *
 * G-61 regression tests for the network engine (networkDiagram.ts).
 *
 * Every assertion is written so it FAILS against the pre-fix source and passes
 * only with the change.
 *
 *  D-206 (theme:light): a categorical palette supplied via style.nodeColors was
 *        applied to node fills VERBATIM with no contrast validation, so ~half a
 *        standard Tableau palette (e.g. #edc948 = 1.61:1) was an invisible disc
 *        on the white canvas that the white node outline cannot rescue; all the
 *        same entries clear the floor on the dark canvas. Fix: ensureReadableFill
 *        nudges a below-floor fill toward the surface-opposite (light entries
 *        fixed; dark entries pass through unchanged -> no dark regression).
 *        Asserted in BOTH themes.
 *
 *  D-209 (recovery): resolveNetworkSpec probed only top-level and `data`, so a
 *        graph wrapped in a `graph` envelope (network-w4-07) was never found ->
 *        node-less -> canHandle false -> 30s empty-DOM hang. Fix: findGraphContainer
 *        locates the first descendant object carrying a `nodes` array.
 *
 *  D-213 (recovery:light): the render read ONLY `resolved.style`, but the plural
 *        keyed `styles` dialect and the `nodeStyle` alias (network-w4-14) were
 *        silently dropped, so authored colours fell to defaults (ghost in light,
 *        fine in dark). Fix: resolveNetworkStyle merges style/styles/nodeStyle.
 */
import {
    resolveNetworkSpec,
    resolveNetworkStyle,
    findGraphContainer,
    networkDiagramPlugin,
    NETWORK_LIGHT_BG,
    NETWORK_DARK_BG,
} from '../networkDiagram';
import { contrastRatio } from '../chartTheme';

// ── recording d3 mock (evaluates function-valued attrs over bound data) ──────
function makeRecorder() {
    const records: Array<{ key: string; value: any }> = [];
    function selection(data: any[], bound = false): any {
        const self: any = {};
        const rec = (key: string, val: any) => {
            if (typeof val === 'function') {
                const rows = bound ? data : (data.length ? data : [undefined]);
                rows.forEach((d, i) => records.push({ key, value: val(d, i) }));
            } else {
                records.push({ key, value: val });
            }
        };
        self.append = () => selection(data, bound);
        self.select = () => selection(data, bound);
        self.selectAll = () => selection([], false);
        self.data = (arr: any[]) => selection(Array.isArray(arr) ? arr : [], true);
        self.datum = (d: any) => selection([d], true);
        self.enter = () => self;
        self.exit = () => self;
        self.merge = () => self;
        self.join = () => selection(data, bound);
        self.filter = (fn?: any) => selection(typeof fn === 'function' ? data.filter(fn) : data, bound);
        self.each = () => self;
        self.call = () => self;
        self.remove = () => self;
        self.attr = (k: string, v: any) => { rec(k, v); return self; };
        self.style = (k: string, v: any) => { rec('style:' + k, v); return self; };
        self.text = (v: any) => { rec('text', v); return self; };
        return self;
    }
    const d3: any = { select: () => selection([]) }; // no forceSimulation -> nodes carry x/y
    return { d3, records };
}
const valuesFor = (records: Array<{ key: string; value: any }>, key: string) =>
    records.filter(r => r.key === key).map(r => r.value);

// ── D-206: palette entries contrast-validated against the active canvas ──────
describe('D-206 — categorical node palette validated against the active background (BOTH themes)', () => {
    // A single node whose group maps to a pale palette entry (#edc948, 1.61:1 on
    // white). fills recorded across the render are: the node circle fill and the
    // label fill; a below-floor entry must never reach the node fill on light.
    const spec = {
        type: 'network',
        nodes: [{ id: 'n0', group: 'g05', x: 200, y: 150, size: 12 }],
        links: [],
        width: 600, height: 400,
        style: { nodeColors: { g05: '#edc948' } },
    };

    it('a pale palette entry (#edc948) is NOT applied verbatim on the light canvas and is nudged >=3:1', () => {
        // DIRECTION: pre-fix resolveNodeFill returned the hex verbatim, so
        // '#edc948' reached the node fill in light and failed the 3:1 floor.
        expect(contrastRatio('#edc948', NETWORK_LIGHT_BG)).toBeLessThan(3);
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, false);
        const fills = valuesFor(r.records, 'fill');
        expect(fills).not.toContain('#edc948');
        // some fill is a valid hex clearing the graphical floor on white (the node fill)
        const readable = fills.filter(f => typeof f === 'string' && /^#[0-9a-f]{6}$/i.test(f)
            && contrastRatio(f, NETWORK_LIGHT_BG) >= 3);
        expect(readable.length).toBeGreaterThan(0);
    });

    it('the SAME entry is honoured verbatim on the dark canvas (it already clears the floor -> no dark regression)', () => {
        expect(contrastRatio('#edc948', NETWORK_DARK_BG)).toBeGreaterThanOrEqual(3);
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, true);
        const fills = valuesFor(r.records, 'fill');
        expect(fills).toContain('#edc948');
    });

    it('a well-contrasting author colour (#4e79a7, 4.55:1 on white) is preserved verbatim in light', () => {
        const s = { ...spec, style: { nodeColors: { g05: '#4e79a7' } } };
        expect(contrastRatio('#4e79a7', NETWORK_LIGHT_BG)).toBeGreaterThanOrEqual(3);
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, s, false);
        expect(valuesFor(r.records, 'fill')).toContain('#4e79a7');
    });
});

// ── D-209: `graph` envelope search ───────────────────────────────────────────
describe('D-209 — a graph wrapped in a `graph` envelope is recovered', () => {
    const w4_07 = {
        type: 'network',
        definition: JSON.stringify({
            graph: {
                nodes: [
                    { id: 'VPC', x: 320, y: 80, size: 20 },
                    { id: 'SubnetA', x: 180, y: 220, size: 16 },
                ],
                links: [{ source: 'VPC', target: 'SubnetA' }],
                width: 640, height: 380,
                style: { labelColor: '#7e7e7e', fontSize: 13 },
            },
        }),
    };

    it('findGraphContainer locates the `graph` envelope and rejects a node-less object', () => {
        const parsed = { graph: { nodes: [{ id: 'a' }], links: [] } };
        expect(findGraphContainer(parsed)).toBe(parsed.graph);
        // no hijack: an object with no `nodes` anywhere returns undefined
        expect(findGraphContainer({ foo: { bar: 1 } })).toBeUndefined();
        expect(findGraphContainer({ type: 'chord', matrix: [[1]] })).toBeUndefined();
    });

    it('resolveNetworkSpec lifts nodes/links/width/height/style out of the envelope', () => {
        // DIRECTION: the old two-path probe (parsed.nodes / parsed.data.nodes)
        // never saw parsed.graph.nodes, so nodes stayed absent.
        const resolved = resolveNetworkSpec(w4_07);
        expect(Array.isArray(resolved.nodes)).toBe(true);
        expect(resolved.nodes.length).toBe(2);
        expect(Array.isArray(resolved.links)).toBe(true);
        expect(resolved.width).toBe(640);
        expect(resolved.style.labelColor).toBe('#7e7e7e');
    });

    it('canHandle now claims the enveloped spec (was false -> unclaimable -> 30s hang)', () => {
        expect(networkDiagramPlugin.canHandle(w4_07)).toBe(true);
    });
});

// ── D-213: plural `styles` dialect + `nodeStyle` alias ───────────────────────
describe('D-213 — the plural `styles` dialect and `nodeStyle` alias are read', () => {
    const w4_14 = {
        type: 'network',
        definition: JSON.stringify({
            version: 2,
            nodes: [{ id: 'd1', label: 'Ingress', x: 150, y: 130, size: 18 }],
            links: [],
            width: 640, height: 380,
            styles: { default: { labelColor: '#7e7e7e', fontSize: 14, linkColor: '#7e7e7e', linkOpacity: 1 } },
            nodeStyle: { fill: '#2e6eaa' },
        }),
    };

    it('resolveNetworkStyle flattens styles.default and maps nodeStyle.fill -> nodeFill', () => {
        // DIRECTION: pre-fix the render read `resolved.style` which is undefined
        // here, so labelColor/fontSize/linkColor/nodeFill were all lost.
        const resolved = resolveNetworkSpec(w4_14);
        expect(resolved.style).toBeUndefined();        // author used the plural form only
        const style = resolveNetworkStyle(resolved);
        expect(style.labelColor).toBe('#7e7e7e');
        expect(style.linkColor).toBe('#7e7e7e');
        expect(style.fontSize).toBe(14);
        expect(style.nodeFill).toBe('#2e6eaa');
    });

    it('explicit singular `style` wins over a conflicting styles.default entry', () => {
        const style = resolveNetworkStyle({
            style: { labelColor: '#111111' },
            styles: { default: { labelColor: '#999999', fontSize: 20 } },
        });
        expect(style.labelColor).toBe('#111111');       // style wins
        expect(style.fontSize).toBe(20);                // non-conflicting field still merged
    });

    it('render applies the authored node fill (#2e6eaa, readable both themes) not the plugin default', () => {
        // #2e6eaa clears 3:1 on both surfaces, so it survives ensureReadableFill verbatim.
        expect(contrastRatio('#2e6eaa', NETWORK_LIGHT_BG)).toBeGreaterThanOrEqual(3);
        expect(contrastRatio('#2e6eaa', NETWORK_DARK_BG)).toBeGreaterThanOrEqual(3);
        for (const dark of [false, true]) {
            const r = makeRecorder();
            networkDiagramPlugin.render(document.createElement('div'), r.d3, w4_14, dark);
            expect(valuesFor(r.records, 'fill')).toContain('#2e6eaa');
        }
    });
});
