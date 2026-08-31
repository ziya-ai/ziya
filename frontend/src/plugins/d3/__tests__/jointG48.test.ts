/**
 * G-48 — joint element theming + network/port fixes (shared file: jointPlugin.ts).
 *
 * Defects covered:
 *   D-150 nested-container-label-occluded-by-children — container titles were
 *         centre-anchored and painted over by later-drawn children; in dark every
 *         container shared #4c566a (nested-fill contrast 1.00). computeJointElementStyle
 *         now top-anchors a container title and, in dark only, ramps the fill by depth.
 *   D-151 network-shapes-flattened-to-plain-rect — every device type rendered as an
 *         identical rounded rect. networkElementStyle now colour-codes per type (with a
 *         readable label) and picks a distinct shape (cloud -> ellipse); ports render.
 *   D-152 element-attrs-override-ignored — author `attrs` (body fill/stroke, label fill)
 *         were never merged. computeJointElementStyle merges them with colour-form
 *         normalisation (3-digit hex / rgb()/rgba() / CSS name -> hex; transparent ->
 *         none) AND clamps the label to a >=4.5 text contrast on the resolved fill in
 *         BOTH themes (the coupling the group warns about).
 *   D-153 port-position-string-invalid:layoutCallback-links-dropped — createPortFromSpec
 *         keyed `group` on portSpec.type with an undefined group -> no layout ->
 *         getPortCenter threw and links dropped. jointPortSide maps the string position
 *         to a side group that standardJointPortGroups defines.
 *
 * Pure helpers are exercised directly (importing them would fail against unpatched
 * code, which had no such export). Render-path wiring is pinned with source-level
 * assertions that first state the pre-fix shape.
 */

import * as fs from 'fs';
import * as path from 'path';
import {
    normalizeJointColor,
    computeJointElementStyle,
    isJointContainer,
    jointContainerFill,
    jointElementDepth,
    networkElementStyle,
    jointPortSide,
    standardJointPortGroups,
    jointContrastRatio,
    readableJointLabelFill,
} from '../jointPlugin';
import { namedColorToHex } from '../chartTheme';

const LIGHT = { theme: 'light' as const, defaultBodyFill: '#ffffff', pageBg: '#ffffff', depth: 0, isContainer: false };
const DARK = { theme: 'dark' as const, defaultBodyFill: '#4c566a', pageBg: '#1e1e1e', depth: 0, isContainer: false };

describe('namedColorToHex (chartTheme, additive)', () => {
    it('resolves the CSS names the joint specs use', () => {
        expect(namedColorToHex('rebeccapurple')).toBe('#663399');
        expect(namedColorToHex('lightgoldenrodyellow')).toBe('#fafad2');
        expect(namedColorToHex('tomato')).toBe('#ff6347');
        expect(namedColorToHex('WHITE')).toBe('#ffffff');
    });
    it('returns null for an unknown name (caller passes it through)', () => {
        expect(namedColorToHex('notacolour')).toBeNull();
        expect(namedColorToHex(undefined)).toBeNull();
    });
});

describe('D-152 normalizeJointColor — colour-form normalisation', () => {
    it('expands 3-digit hex (w4-11)', () => {
        expect(normalizeJointColor('#f90')).toEqual({ value: '#ff9900', hex: '#ff9900' });
        expect(normalizeJointColor('#0a0').value).toBe('#00aa00');
    });
    it('converts rgb()/rgba() to hex, dropping alpha (w4-12)', () => {
        expect(normalizeJointColor('rgb(52,152,219)').value).toBe('#3498db');
        expect(normalizeJointColor('rgba(52,152,219,0.85)').value).toBe('#3498db');
    });
    it('treats transparent / none / zero-alpha as absent', () => {
        expect(normalizeJointColor('transparent')).toMatchObject({ value: 'none', absent: true });
        expect(normalizeJointColor('none')).toMatchObject({ value: 'none', absent: true });
        expect(normalizeJointColor('rgba(0,0,0,0)')).toMatchObject({ value: 'none', absent: true });
    });
    it('resolves known CSS names to hex, passes unknown names through, leaves tokens as default', () => {
        expect(normalizeJointColor('tomato').value).toBe('#ff6347');
        expect(normalizeJointColor('mysterycolor')).toEqual({ value: 'mysterycolor', hex: null });
        expect(normalizeJointColor('var(--brand)')).toEqual({ value: null, hex: null });
    });
});

describe('D-152 computeJointElementStyle — author attrs honoured + legible', () => {
    it('is a no-op (null) for a plain element with no attrs and no embeds — byte-identical', () => {
        expect(computeJointElementStyle({ id: 'x', type: 'rect', label: 'X' }, LIGHT)).toBeNull();
        expect(computeJointElementStyle({ id: 'x', type: 'rect', label: 'X' }, DARK)).toBeNull();
    });

    it('honours author body fill/stroke + a high-contrast author label (w1-13 hot), BOTH themes', () => {
        const spec = { id: 'hot', type: 'rect', label: 'Hot path',
            attrs: { body: { fill: '#b71c1c', stroke: '#7f0000' }, label: { fill: '#ffffff' } } };
        for (const opts of [LIGHT, DARK]) {
            const out = computeJointElementStyle(spec, opts)!;
            expect(out.body!.fill).toBe('#b71c1c');       // was dropped -> creator default
            expect(out.body!.stroke).toBe('#7f0000');
            expect(out.label!.fill).toBe('#ffffff');      // white on #b71c1c = 6.57:1, honoured
        }
    });

    it('normalises a 3-digit author fill and keeps the label readable (w4-11 ok), BOTH themes', () => {
        const spec = { id: 'ok', type: 'rect', label: 'OK',
            attrs: { body: { fill: '#0a0' }, label: { fill: '#fff' } } };
        for (const opts of [LIGHT, DARK]) {
            const out = computeJointElementStyle(spec, opts)!;
            expect(out.body!.fill).toBe('#00aa00');       // 3-digit expanded
            // white on #00aa00 is 3.11:1 (<4.5) -> clamped to a readable label
            expect(out.label!.fill).not.toBe('#ffffff');
            expect(jointContrastRatio(out.label!.fill, '#00aa00')).toBeGreaterThanOrEqual(4.5);
        }
    });

    it('transparent fill: dark clamps the now-broken black label, light keeps it (w4-13 mid)', () => {
        const spec = { id: 'mid', type: 'rect', label: 'Middle',
            attrs: { body: { fill: 'transparent', stroke: 'black' }, label: { fill: 'black' } } };
        const dark = computeJointElementStyle(spec, DARK)!;
        expect(dark.body!.fill).toBe('none');
        // black on the dark page (#1e1e1e) is 1.26:1 verbatim -> clamped legible
        expect(dark.label!.fill).not.toBe('#000000');
        expect(jointContrastRatio(dark.label!.fill, '#1e1e1e')).toBeGreaterThanOrEqual(4.5);
        const light = computeJointElementStyle(spec, LIGHT)!;
        expect(light.body!.fill).toBe('none');
        expect(light.label!.fill).toBe('#000000');        // black on white = 21:1, honoured
    });

    it('resolves a named pale fill and rescues a white-on-pale label (w4-13 dst), BOTH themes', () => {
        const spec = { id: 'dst', type: 'rect', label: 'Dest',
            attrs: { body: { fill: 'lightgoldenrodyellow' }, label: { fill: 'white' } } };
        for (const opts of [LIGHT, DARK]) {
            const out = computeJointElementStyle(spec, opts)!;
            expect(out.body!.fill).toBe('#fafad2');
            // white on #fafad2 is 1.07:1 verbatim -> clamped legible
            expect(out.label!.fill).not.toBe('#ffffff');
            expect(jointContrastRatio(out.label!.fill, '#fafad2')).toBeGreaterThanOrEqual(4.5);
        }
    });
});

describe('D-150 nested containers — top-anchored title + dark depth ramp', () => {
    it('detects a container by a non-empty embeds list', () => {
        expect(isJointContainer({ id: 'c', embeds: ['a', 'b'] })).toBe(true);
        expect(isJointContainer({ id: 'l', embeds: [] })).toBe(false);
        expect(isJointContainer({ id: 'l' })).toBe(false);
    });

    it('computes nesting depth along the parent chain', () => {
        const byId = new Map<string, any>([
            ['lvl0', { id: 'lvl0' }],
            ['lvl1', { id: 'lvl1', parent: 'lvl0' }],
            ['lvl2', { id: 'lvl2', parent: 'lvl1' }],
        ]);
        expect(jointElementDepth('lvl0', byId)).toBe(0);
        expect(jointElementDepth('lvl1', byId)).toBe(1);
        expect(jointElementDepth('lvl2', byId)).toBe(2);
    });

    it('dark ramps container fill by depth (distinct); light leaves fill untouched', () => {
        expect(jointContainerFill(0, 'dark')).toBe('#2e3440');
        expect(jointContainerFill(1, 'dark')).toBe('#3b4252');
        expect(jointContainerFill(0, 'dark')).not.toBe(jointContainerFill(1, 'dark'));
        expect(jointContainerFill(99, 'dark')).toBe('#68758f'); // clamped
        expect(jointContainerFill(0, 'light')).toBeNull();
    });

    it('top-anchors the title in BOTH themes; dark also sets a depth fill, light does NOT', () => {
        const container = { id: 'lvl0', type: 'rect', embeds: ['a'], attrs: { label: { text: 'region' } } };
        const dark = computeJointElementStyle(container, { ...DARK, depth: 0, isContainer: true })!;
        expect(dark.label!.textVerticalAnchor).toBe('top');
        expect(dark.label!.refY).toBe(0.08);
        expect(dark.body!.fill).toBe('#2e3440');                 // depth ramp (was flat #4c566a)

        const light = computeJointElementStyle(container, { ...LIGHT, depth: 0, isContainer: true })!;
        expect(light.label!.textVerticalAnchor).toBe('top');     // occlusion fix applies to both
        // parity: the dark-only ramp must NOT leak into light output
        expect(light.body && light.body.fill).toBeUndefined();
    });
});

describe('D-151 network shape semantics', () => {
    it('gives each device type a distinct fill and a shape, cloud as ellipse', () => {
        const types = ['router', 'switch', 'server', 'firewall', 'cloud'];
        for (const theme of ['light', 'dark'] as const) {
            const fills = types.map(t => networkElementStyle(t, theme).fill);
            expect(new Set(fills).size).toBe(types.length); // all distinct
            expect(networkElementStyle('cloud', theme).shape).toBe('ellipse');
            expect(networkElementStyle('router', theme).shape).toBe('rect');
        }
    });
    it('every per-type fill yields a readable label in BOTH themes (>=4.5)', () => {
        for (const t of ['router', 'switch', 'server', 'firewall', 'cloud']) {
            for (const theme of ['light', 'dark'] as const) {
                const s = networkElementStyle(t, theme);
                expect(jointContrastRatio(readableJointLabelFill(s.fill), s.fill)).toBeGreaterThanOrEqual(4.5);
            }
        }
    });
});

describe('D-153 port side mapping + groups', () => {
    it('maps a string position to a side group, defaulting to top', () => {
        expect(jointPortSide('left')).toBe('left');
        expect(jointPortSide('RIGHT')).toBe('right');
        expect(jointPortSide('top')).toBe('top');
        expect(jointPortSide('bottom')).toBe('bottom');
        expect(jointPortSide('in')).toBe('top');       // unknown -> top (not a throw)
        expect(jointPortSide(undefined)).toBe('top');
    });
    it('defines all four side groups with a built-in position layout', () => {
        const g: any = standardJointPortGroups('light');
        for (const side of ['top', 'bottom', 'left', 'right']) {
            expect(g[side].position).toBe(side);
            expect(g[side].markup[0].selector).toBe('portBody');
        }
    });
});

describe('render-path wiring (source-level; pins the pre-fix shape)', () => {
    const SRC = fs.readFileSync(path.join(__dirname, '..', 'jointPlugin.ts'), 'utf8');

    it('createPortFromSpec keys group on the side (was portSpec.type -> undefined group -> layoutCallback throw)', () => {
        // The USED port creator now derives the group from the position side; the
        // element declares that group (asserted below), so getPortCenter has a layout.
        expect(SRC).toMatch(/group:\s*jointPortSide\(portSpec\.position\)/);
    });
    it('network + electrical elements declare the side port groups so ports render / links anchor', () => {
        expect(SRC).toMatch(/ports:\s*\{\s*groups:\s*standardJointPortGroups\(theme\)\s*\}/);
    });
    it('the element-creation loop applies computeJointElementStyle before addCell (D-150/D-152)', () => {
        expect(SRC).toMatch(/computeJointElementStyle\(elementSpec,\s*\{/);
    });
    it('the cloud creator can pick an Ellipse shape (D-151)', () => {
        expect(SRC).toMatch(/netStyle\.shape === 'ellipse' \? shapes\.standard\.Ellipse/);
    });
});
