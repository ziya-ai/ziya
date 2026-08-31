/**
 * @jest-environment jsdom
 *
 * G-28 regression tests for the network engine (networkDiagram.ts).
 *
 * Every assertion is written so it FAILS against the pre-fix source and passes
 * only with the change (direction is asserted explicitly where a pure helper
 * lets us reproduce the old behaviour).
 *
 *  D-210 (recovery): isNetworkDiagramSpec gated canHandle with
 *        nodes.every(isValidNetworkId(id)) && links.every(...), so ONE malformed
 *        row (missing `target`, `to`/`from` alias, `name`-instead-of-`id`) made
 *        canHandle false -> registry finds no plugin -> D3Renderer 30s empty-DOM
 *        hang. Fix: normalizeNetworkAliases maps obvious aliases and detection is
 *        tolerant (>=1 renderable node); sanitizeNetworkGraph drops the rest.
 *
 *  D-211 (recovery): sanitizeNetworkGraph built `new Set(nodes.map(n=>n.id))`
 *        and filtered with strict `ids.has(l.source)`, so numeric node ids
 *        (1..5) vs string endpoints ("1".."5") discarded EVERY edge while the
 *        render reported success. Fix: String-keyed lookup + canonicalise each
 *        endpoint back to the matching node's actual id value (so the render's
 *        strict `n.id === l.source` lookup and d3-forceLink both resolve).
 *
 *  D-212 (recovery/theme): node fill was `nodeColors[group] || d.color ||
 *        '#69b3a2'` verbatim, so 'transparent' painted the node onto the
 *        background (erased) and an unresolvable token (var(--x),
 *        'theme.node.fill') fell to the CSS initial black. Fix: classifyColor
 *        rejects those forms and a theme-readable default is substituted; a
 *        valid author colour is honoured verbatim.
 *
 *  D-199 (structural): node labels were single centred <text> with the full
 *        string (a ~135-char id overran the 620px viewBox at both ends and
 *        double-exposed same-row neighbours). Fix: truncateLabel ellipsis to
 *        NETWORK_MAX_LABEL_CHARS with the full label kept in a <title> child.
 */
import {
    normalizeNetworkAliases,
    sanitizeNetworkGraph,
    networkDiagramPlugin,
    NETWORK_MAX_LABEL_CHARS,
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
    const d3: any = { select: () => selection([]) }; // no forceSimulation -> layout path not taken (nodes have x/y)
    return { d3, records };
}
const valuesFor = (records: Array<{ key: string; value: any }>, key: string) =>
    records.filter(r => r.key === key).map(r => r.value);

// ── D-210: alias mapping + tolerant detection (network-w4-15) ────────────────
describe('D-210 — missing/aliased endpoint fields no longer reject the whole graph', () => {
    it('normalizeNetworkAliases maps name->id, to->target, from->source; leaves a truly-missing target undefined', () => {
        const nodes = [{ id: 'm1' }, { id: 'm2' }, { id: 'm3' }, { name: 'm4', label: 'Nameless' }];
        const links = [{ source: 'm1', target: 'm2' }, { source: 'm2' }, { source: 'm2', to: 'm3' }, { from: 'm1', target: 'm3' }];
        const out = normalizeNetworkAliases(nodes, links);
        expect(out.nodes[3].id).toBe('m4');            // name -> id
        expect(out.links[2].target).toBe('m3');        // to -> target
        expect(out.links[3].source).toBe('m1');        // from -> source
        expect(out.links[1].target).toBeUndefined();   // no alias present -> stays missing (dropped later)
        // purity: original rows untouched
        expect((nodes[3] as any).id).toBeUndefined();
        expect((links[2] as any).target).toBeUndefined();
    });

    // DIRECTION: the old .every() detector rejected this exact spec (m4 has no
    // id, one link has no target) -> canHandle false. Assert the tolerant path
    // now claims it. A pure re-simulation of the old predicate proves non-vacuity.
    const w4_15 = {
        type: 'network',
        definition: JSON.stringify({
            nodes: [
                { id: 'm1', label: 'Frontend', x: 150, y: 190, size: 18 },
                { id: 'm2', label: 'Backend', x: 330, y: 190, size: 18 },
                { id: 'm3', label: 'Queue', x: 510, y: 120, size: 16 },
                { name: 'm4', label: 'Nameless', x: 510, y: 290, size: 16 },
            ],
            links: [{ source: 'm1', target: 'm2' }, { source: 'm2' }, { source: 'm2', to: 'm3' }],
            width: 640, height: 380,
        }),
    };

    it('the OLD all-or-nothing predicate would have rejected it (direction check)', () => {
        const parsed = JSON.parse(w4_15.definition);
        const oldEvery = parsed.nodes.every((n: any) => typeof n.id === 'string' && n.id.length > 0)
            && parsed.links.every((l: any) => typeof l.source === 'string' && typeof l.target === 'string');
        expect(oldEvery).toBe(false);
    });

    it('canHandle now ACCEPTS the w4-15 spec (name node + missing target + `to` alias)', () => {
        expect(networkDiagramPlugin.canHandle(w4_15)).toBe(true);
    });

    it('render draws the 4 nodes and only the 2 resolvable edges (missing-target link dropped)', () => {
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, w4_15, false);
        // one 'transform' per node group, one 'x1' per link line
        expect(valuesFor(r.records, 'transform')).toHaveLength(4);
        expect(valuesFor(r.records, 'x1')).toHaveLength(2);
    });
});

// ── D-211: numeric id / string endpoint reconciliation (network-w4-08) ───────
describe('D-211 — numeric-id / string-endpoint mismatch keeps every edge', () => {
    const nodes = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }];
    const links = [
        { source: '1', target: '2', weight: 3 },
        { source: '1', target: '3', weight: 3 },
        { source: '1', target: '4', weight: 3 },
        { source: '3', target: '5', weight: 2 },
    ];

    it('DIRECTION: the old strict-Set filter dropped ALL edges (5 !== "5")', () => {
        const oldIds = new Set(nodes.map(n => n.id));
        const survived = links.filter(l => oldIds.has(l.source as any) && oldIds.has(l.target as any));
        expect(survived).toHaveLength(0);
    });

    it('sanitizeNetworkGraph keeps all 4 edges and canonicalises endpoints to the node id value', () => {
        const { links: sl } = sanitizeNetworkGraph(nodes, links);
        expect(sl).toHaveLength(4);
        // endpoints canonicalised back to the numeric node id, so the render's
        // strict `n.id === l.source` lookup resolves.
        expect(sl.every(l => typeof l.source === 'number' && typeof l.target === 'number')).toBe(true);
        expect(sl[0].source).toBe(1);
        expect(sl[0].target).toBe(2);
        expect(nodes.find(n => n.id === sl[3].source)).toBeDefined(); // 3
        expect(nodes.find(n => n.id === sl[3].target)).toBeDefined(); // 5
    });

    it('reverse case (string node ids, numeric endpoints) also resolves', () => {
        const { links: sl } = sanitizeNetworkGraph(
            [{ id: '1' }, { id: '2' }],
            [{ source: 1, target: 2 }],
        );
        expect(sl).toHaveLength(1);
        expect(sl[0].source).toBe('1');
        expect(sl[0].target).toBe('2');
    });

    it('a genuinely dangling edge is STILL dropped (tolerance is not a catch-all)', () => {
        const { links: sl } = sanitizeNetworkGraph(nodes, [{ source: '1', target: '99' }]);
        expect(sl).toHaveLength(0);
    });
});

// ── D-212: node-fill validation / substitution (network-w4-12 / w4-13) ───────
describe('D-212 — semantically-empty node colours substituted; valid ones honoured', () => {
    const bgFor = (dark: boolean) => (dark ? NETWORK_DARK_BG : NETWORK_LIGHT_BG);

    const transparentSpec = {
        type: 'network',
        nodes: [
            { id: 't1', x: 160, y: 150, size: 24, color: 'transparent' },
            { id: 't2', x: 330, y: 150, size: 24, color: 'transparent' },
            { id: 't3', x: 500, y: 150, size: 24, color: '#2e6eaa' },
            { id: 't4', x: 330, y: 300, size: 24, color: 'transparent' },
        ],
        links: [{ source: 't1', target: 't2' }, { source: 't2', target: 't3' }],
        width: 640, height: 380,
    };
    const tokenSpec = {
        type: 'network',
        nodes: [
            { id: 'v1', x: 160, y: 140, size: 22, color: 'var(--ziya-node-accent)' },
            { id: 'v2', x: 330, y: 140, size: 22, color: 'var(--ziya-primary, )' },
            { id: 'v3', x: 500, y: 140, size: 22, color: 'theme.node.fill' },
            { id: 'v4', x: 330, y: 300, size: 22, color: '#2e6eaa' },
        ],
        links: [{ source: 'v1', target: 'v2' }, { source: 'v2', target: 'v4' }],
        width: 640, height: 380,
    };

    it.each([false, true])('transparent node fills -> readable default, valid hex kept (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, transparentSpec, dark);
        // node circles are drawn before labels; both record 'fill', so the first
        // 4 are the node-circle fills (one per node).
        const fills = valuesFor(r.records, 'fill').slice(0, 4);
        expect(fills).toHaveLength(4);
        expect(fills[2]).toBe('#2e6eaa'); // author colour honoured verbatim
        const bg = bgFor(dark);
        [0, 1, 3].forEach(i => {
            expect(String(fills[i]).toLowerCase()).not.toBe('transparent'); // not erased (was 'transparent')
            expect(fills[i]).toMatch(/^#[0-9a-fA-F]{6}$/);                   // resolved to a real colour
            expect(contrastRatio(fills[i], bg)).toBeGreaterThanOrEqual(3);   // visible on THIS theme's surface
        });
    });

    it.each([false, true])('unresolvable token fills -> readable default, not black (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, tokenSpec, dark);
        const fills = valuesFor(r.records, 'fill').slice(0, 4); // node-circle fills
        expect(fills[3]).toBe('#2e6eaa');
        const bg = bgFor(dark);
        [0, 1, 2].forEach(i => {
            expect(fills[i]).not.toMatch(/var\(|theme\./);   // token not passed through (would fall to black)
            expect(fills[i]).not.toBe('#000000');
            expect(contrastRatio(fills[i], bg)).toBeGreaterThanOrEqual(3);
        });
    });
});

// ── D-199: long node labels truncated with ellipsis, full text in <title> ────
describe('D-199 — long node labels are truncated (ellipsis) with full text preserved', () => {
    const longLabel =
        'Extremely-Long-Fully-Qualified-Service-Identifier-With-Region-And-Shard-Suffix-us-west-2-shard-000117-replica-b-canary-cell-alpha-prod-0';
    const spec = {
        type: 'network',
        nodes: [
            { id: 0, label: longLabel, x: 150, y: 50 },
            { id: 1, label: longLabel.replace(/0$/, '1'), x: 470, y: 50 },
        ],
        links: [{ source: 0, target: 1 }],
        width: 620, height: 380,
        style: { fontSize: 12 },
    };

    it.each([false, true])('visible label is ellipsis-truncated; full label kept in a <title> (isDarkMode=%p)', (dark) => {
        const r = makeRecorder();
        networkDiagramPlugin.render(document.createElement('div'), r.d3, spec, dark);
        const texts = valuesFor(r.records, 'text').map(String);
        // a truncated visible label: ends with the ellipsis and is within the cap
        const truncated = texts.filter(t => t.endsWith('\u2026'));
        expect(truncated.length).toBeGreaterThan(0);
        truncated.forEach(t => expect(t.length).toBeLessThanOrEqual(NETWORK_MAX_LABEL_CHARS));
        // the full label survives (in the <title> child) — unpatched code emitted
        // ONLY the full string as the visible <text> and no ellipsis at all.
        expect(texts).toContain(longLabel);
        expect(texts.some(t => t.length > NETWORK_MAX_LABEL_CHARS && !t.endsWith('\u2026'))).toBe(true);
    });
});
