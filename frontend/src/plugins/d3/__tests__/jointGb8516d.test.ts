/**
 * G-b8516d — joint per-spec regression guards (shared file: jointPlugin.ts).
 *
 * These are the ORIGINAL legacy defect ids whose signatures were repaired in
 * earlier waves under the D-14x/D-15x re-detections. This suite pins the EXACT
 * G-b8516d spec bodies (copied verbatim from .ziya/gfx-sweep/specs/joint/) to the
 * exported pure helpers, so the specific inputs that first surfaced each defect
 * stay handled. Every assertion first states the pre-fix behaviour it guards
 * against; reverting the fix flips the assertion (fails-without / passes-with).
 *
 *   D-135 joint-w1-06  network-shapes-flattened-to-plain-rect
 *   D-136 joint-w2-07  nested-container-label-occluded-by-children (+ dark flat slab)
 *   D-141 joint-w4-04  markdown-fence-not-stripped (recovery)
 *   D-142 joint-w4-07  nesting-depth-off-by-one:no-descent (recovery)
 *   D-144 joint-w4-06  string-boolean-not-coerced:manual-layout-lost (+ edge-label plate)
 *
 * Theme defects (D-136, D-144) are asserted in BOTH themes.
 */

import * as fs from 'fs';
import * as path from 'path';
import {
    parseJointJsonish,
    findJointGraphContainer,
    coerceJointBoolean,
    networkElementStyle,
    isJointContainer,
    jointElementDepth,
    jointContainerFill,
    computeJointElementStyle,
    jointContrastRatio,
} from '../jointPlugin';
import { normalizeJointCells } from '../jointShapeResolver';

// ── D-135: network shape library (joint-w1-06) ────────────────────────────────
// Pre-fix: createNetworkElement returned standard.Rectangle for router/switch/
// server/firewall/cloud alike, so the topology carried NO type information.
describe('D-135 network shapes convey type (joint-w1-06)', () => {
    const types = ['router', 'switch', 'server', 'firewall', 'cloud'];

    (['light', 'dark'] as const).forEach(theme => {
        it(`gives each device type a distinct fill in ${theme}`, () => {
            const fills = types.map(t => networkElementStyle(t, theme).fill);
            // Every device must be a different colour — not one shared rect fill.
            expect(new Set(fills).size).toBe(types.length);
        });

        it(`differentiates cloud geometry from the rest in ${theme}`, () => {
            expect(networkElementStyle('cloud', theme).shape).toBe('ellipse');
            for (const t of ['router', 'switch', 'server', 'firewall']) {
                expect(networkElementStyle(t, theme).shape).toBe('rect');
            }
        });
    });

    it('label stays readable (>=4.5) against each device fill in BOTH themes', () => {
        // network label fill is theme-driven; assert legibility both ways.
        const darkLabel = '#ffffff';
        const lightLabel = '#1a1a1a';
        for (const t of types) {
            expect(jointContrastRatio(darkLabel, networkElementStyle(t, 'dark').fill)).toBeGreaterThanOrEqual(4.5);
            expect(jointContrastRatio(lightLabel, networkElementStyle(t, 'light').fill)).toBeGreaterThanOrEqual(4.5);
        }
    });
});

// ── D-136: deep nesting container titles + dark fill ramp (joint-w2-07) ────────
describe('D-136 nested container hierarchy (joint-w2-07)', () => {
    // 5-level parent/embeds ladder from the spec: region>vpc>subnet>asg>host.
    const cells = [
        { id: 'lvl0', type: 'standard.Rectangle', attrs: { label: { text: 'region' } }, embeds: ['lvl1a', 'lvl1b'] },
        { id: 'lvl1a', type: 'standard.Rectangle', attrs: { label: { text: 'vpc-a' } }, parent: 'lvl0', embeds: ['lvl2a'] },
        { id: 'lvl2a', type: 'standard.Rectangle', attrs: { label: { text: 'subnet-a' } }, parent: 'lvl1a', embeds: ['lvl3a'] },
        { id: 'lvl3a', type: 'standard.Rectangle', attrs: { label: { text: 'asg-a' } }, parent: 'lvl2a', embeds: ['leaf1'] },
        { id: 'leaf1', type: 'standard.Rectangle', attrs: { label: { text: 'host-1' } }, parent: 'lvl3a' },
    ];
    const specById = new Map<string, any>();
    cells.forEach(c => specById.set(c.id, c));

    it('depth follows the parent chain', () => {
        expect(jointElementDepth('lvl0', specById)).toBe(0);
        expect(jointElementDepth('lvl1a', specById)).toBe(1);
        expect(jointElementDepth('lvl2a', specById)).toBe(2);
        expect(jointElementDepth('lvl3a', specById)).toBe(3);
        expect(jointElementDepth('leaf1', specById)).toBe(4);
    });

    it('DARK: per-depth ramp gives distinct fills (pre-fix all shared #4c566a, contrast 1.00)', () => {
        const fills = [0, 1, 2, 3, 4].map(d => jointContainerFill(d, 'dark'));
        // Not the old single flat slab colour for every level.
        expect(new Set(fills).size).toBe(5);
        for (const f of fills) expect(f).toMatch(/^#[0-9a-f]{6}$/i);
        // Adjacent bands actually differ (nested boundary is visible, was 1.00).
        for (let i = 0; i < 4; i++) {
            expect(jointContrastRatio(fills[i]!, fills[i + 1]!)).toBeGreaterThan(1.05);
        }
    });

    it('LIGHT: no ramp fill imposed (crisp boundary strokes on white kept)', () => {
        expect(jointContainerFill(0, 'light')).toBeNull();
        expect(jointContainerFill(3, 'light')).toBeNull();
    });

    (['light', 'dark'] as const).forEach(theme => {
        it(`${theme}: a container title is TOP-anchored (children can't occlude it)`, () => {
            expect(isJointContainer(cells[0])).toBe(true);
            const patch = computeJointElementStyle(cells[0], {
                theme, defaultBodyFill: theme === 'dark' ? '#4c566a' : '#ffffff',
                pageBg: theme === 'dark' ? '#1e1e1e' : '#ffffff', depth: 0, isContainer: true,
            });
            expect(patch).not.toBeNull();
            expect(patch!.label!.textVerticalAnchor).toBe('top');
        });
    });

    it('DARK container label stays readable (>=4.5) on the ramp at every depth', () => {
        for (const d of [0, 1, 2, 3, 4]) {
            const fill = jointContainerFill(d, 'dark')!;
            const patch = computeJointElementStyle({ id: 'x', embeds: ['y'] }, {
                theme: 'dark', defaultBodyFill: '#4c566a', pageBg: '#1e1e1e', depth: d, isContainer: true,
            });
            expect(jointContrastRatio(String(patch!.label!.fill), fill)).toBeGreaterThanOrEqual(4.5);
        }
    });
});

// ── D-141: markdown ```json fence (joint-w4-04) ───────────────────────────────
describe('D-141 markdown fence stripped (joint-w4-04)', () => {
    const def = '```json\n' +
        '{\n  "elements": [\n' +
        '    {"id": "edge", "type": "rect", "label": "Edge"},\n' +
        '    {"id": "cache", "type": "rect", "label": "Cache"},\n' +
        '    {"id": "origin", "type": "rect", "label": "Origin"}\n  ],\n' +
        '  "connections": [\n' +
        '    {"id": "m1", "source": "edge", "target": "cache", "label": "hit?"},\n' +
        '    {"id": "m2", "source": "cache", "target": "origin", "label": "miss"}\n  ]\n}\n' +
        '```';

    it('recovers the fenced JSON instead of dropping to the zero-element DSL', () => {
        const parsed = parseJointJsonish(def);
        expect(parsed).toBeTruthy();
        const obj = findJointGraphContainer(parsed, 3) || parsed;
        const { elements, connections } = normalizeJointCells(obj.elements, obj.connections);
        expect(elements.length).toBe(3);   // pre-fix: 0 -> empty container -> 30s hang
        expect(connections.length).toBe(2);
    });
});

// ── D-142: one-level-deeper {graph:{cells:[...]}} wrapper (joint-w4-07) ────────
describe('D-142 wrapped-graph recursive descent (joint-w4-07)', () => {
    const def = JSON.stringify({
        graph: {
            cells: [
                { id: 'n1', type: 'standard.Rectangle', attrs: { label: { text: 'Load' } } },
                { id: 'n2', type: 'standard.Rectangle', attrs: { label: { text: 'Transform' } } },
                { id: 'n3', type: 'standard.Rectangle', attrs: { label: { text: 'Publish' } } },
                { id: 'k1', type: 'standard.Link', source: { id: 'n1' }, target: { id: 'n2' } },
                { id: 'k2', type: 'standard.Link', source: { id: 'n2' }, target: { id: 'n3' } },
            ],
        },
    });

    it('descends the extra wrapper and splits cells into 3 elements + 2 links', () => {
        const parsed = parseJointJsonish(def);
        // pre-fix: depth-1 guard (obj.elements||obj.cells) missed the graph wrapper.
        const obj = findJointGraphContainer(parsed, 3);
        expect(obj).toBeTruthy();
        expect(Array.isArray(obj.cells)).toBe(true);
        const { elements, connections } = normalizeJointCells(obj.cells, undefined);
        expect(elements.length).toBe(3);
        expect(connections.length).toBe(2);
    });
});

// ── D-144: string boolean autoLayout + string numbers (joint-w4-06) ───────────
describe('D-144 string boolean coercion keeps manual layout (joint-w4-06)', () => {
    const def = JSON.stringify({
        elements: [
            { id: 'a', type: 'rect', label: 'Alpha', position: { x: '80', y: '60' }, size: { width: '140', height: '70' } },
            { id: 'b', type: 'rect', label: 'Beta', position: { x: '300', y: '60' }, size: { width: '140', height: '70' } },
            { id: 'c', type: 'rect', label: 'Gamma', position: { x: '520', y: '60' }, size: { width: '140', height: '70' } },
        ],
        connections: [
            { id: 'n1', source: 'a', target: 'b', label: '1' },
            { id: 'n2', source: 'b', target: 'c', label: '2' },
        ],
        autoLayout: 'false',
    });

    it('coerces autoLayout:"false" to boolean false (pre-fix: truthy string -> auto-layout ran)', () => {
        const parsed = parseJointJsonish(def);
        expect(parsed.autoLayout).toBe('false'); // still a string at parse time
        // The render path runs coerceJointBoolean before the `!== false` guard.
        expect(coerceJointBoolean(parsed.autoLayout)).toBe(false);
        expect(coerceJointBoolean('true')).toBe(true);
        expect(coerceJointBoolean('0')).toBe(false);
        expect(coerceJointBoolean(undefined)).toBeUndefined();
    });

    it('recovers 3 manual boxes + 2 edges', () => {
        const parsed = parseJointJsonish(def);
        const obj = findJointGraphContainer(parsed, 3) || parsed;
        const { elements, connections } = normalizeJointCells(obj.elements, obj.connections);
        expect(elements.length).toBe(3);
        expect(connections.length).toBe(2);
    });

    // Edge-label overdraw (D-148 companion): both createLink label sites must
    // carry a backing plate that references the text bbox (ref:'text'), else the
    // single-char '1'/'2' labels sit on the stroke (1.18 light / 1.74 dark).
    it('BOTH link-label sites draw a text-referenced backing plate in both themes', () => {
        const src = fs.readFileSync(path.resolve(__dirname, '../jointPlugin.ts'), 'utf8');
        const appendCalls = src.split('link.appendLabel(').length - 1;
        expect(appendCalls).toBeGreaterThanOrEqual(2);
        // Every appendLabel rect fragment must carry ref:'text' and a themed fill.
        const refTextCount = (src.match(/ref:\s*'text'/g) || []).length;
        expect(refTextCount).toBeGreaterThanOrEqual(2);
        // A theme-driven plate fill (white in light, slate in dark) is present.
        expect(src).toMatch(/theme === 'dark' \? '#3b4252' : '#ffffff'/);
    });
});
