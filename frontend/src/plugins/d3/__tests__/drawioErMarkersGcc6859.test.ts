/**
 * G-cc6859 / D-091 — custom-and-orthogonal-arrowheads-dropped (ER crow's-foot half).
 *
 * maxGraph core registers no ERone/ERmany/ERzeroToOne/ERzeroToMany/ERoneToMany/ERmandOne
 * marker, so an ER edge's cardinality terminator resolves to null in EdgeMarkerRegistry and
 * is dropped entirely — a bare line, in both themes. registerDrawioExtraEdgeMarkers fills
 * exactly those names on the loaded module.
 *
 * The tests use a fake maxGraph module: a BaseRegistry-like EdgeMarkerRegistry seeded with a
 * core-like 'classic' marker (to prove additivity) and a fake canvas that records path ops
 * plus a Point with clone()/mutation. DIRECTION: before registration ERone/ERmany resolve to
 * null (the dropped-marker bug); after, each ER name is a factory whose paint closure strokes
 * a polyline (moveTo/lineTo/stroke) — a crow's foot has ≥3 line prongs, a bar exactly one —
 * and the factory shortens the end point `pe` so the connecting line stops at the terminal.
 * Marker geometry is theme-independent (stroke-only in the edge colour), so ONE assertion set
 * covers both themes; pixel sufficiency is a render-stage check.
 */

import { registerDrawioExtraEdgeMarkers } from '../drawioShapes';

class FakeMarkerRegistry {
    private values = new Map<string, any>();
    add(name: string, value: any) { this.values.set(name, value); }
    get(name: string) { return this.values.get(name) ?? null; }
    has(name: string) { return this.values.has(name); }
}

// Records every path op so we can assert a stroked polyline vs a dropped (empty) marker.
function makeCanvas() {
    const ops: string[] = [];
    return {
        ops,
        begin: () => ops.push('begin'),
        moveTo: () => ops.push('moveTo'),
        lineTo: () => ops.push('lineTo'),
        ellipse: () => ops.push('ellipse'),
        close: () => ops.push('close'),
        fillAndStroke: () => ops.push('fillAndStroke'),
        stroke: () => ops.push('stroke'),
    };
}

// Minimal maxGraph Point stand-in with clone() + mutable x/y.
function makePoint(x: number, y: number) {
    return { x, y, clone() { return makePoint(this.x, this.y); } };
}

function makeModule(seedClassic = true) {
    const reg = new FakeMarkerRegistry();
    if (seedClassic) reg.add('classic', () => () => { /* core marker sentinel */ });
    return { EdgeMarkerRegistry: reg };
}

const ER_NAMES = ['ERone', 'ERmany', 'ERzeroToOne', 'ERzeroToMany', 'ERoneToMany', 'ERmandOne'];

// Invoke a registered marker factory the way maxGraph's CellRenderer does and return the
// recorded canvas ops plus how far the end point was pulled back.
function paint(factory: any) {
    const canvas = makeCanvas();
    // pe at (100,0); unit vector (1,0) => edge travels left→right into the terminal.
    const pe = makePoint(100, 0);
    const draw = factory(canvas, {}, 'ERx', pe, 1, 0, 6, null, 1, false);
    expect(typeof draw).toBe('function');
    draw();
    return { ops: canvas.ops, pe };
}

describe("D-091: registerDrawioExtraEdgeMarkers fills the ER markers maxGraph core omits", () => {
    it('DIRECTION: ER markers are unregistered by default (→ dropped cardinality)', () => {
        const mod = makeModule();
        for (const name of ER_NAMES) {
            expect(mod.EdgeMarkerRegistry.get(name)).toBeNull();
        }
    });

    it('registers all six ER markers as factory functions', () => {
        const mod = makeModule();
        const registered = registerDrawioExtraEdgeMarkers(mod);
        expect(new Set(registered)).toEqual(new Set(ER_NAMES));
        for (const name of ER_NAMES) {
            expect(typeof mod.EdgeMarkerRegistry.get(name)).toBe('function');
        }
    });

    it('ERmany paints a crow\'s foot: three prongs (≥3 lineTo) stroked, not filled', () => {
        const mod = makeModule();
        registerDrawioExtraEdgeMarkers(mod);
        const { ops, pe } = paint(mod.EdgeMarkerRegistry.get('ERmany'));
        expect(ops).toContain('moveTo');
        expect(ops.filter((o) => o === 'lineTo').length).toBeGreaterThanOrEqual(3);
        expect(ops).toContain('stroke');
        expect(ops).not.toContain('fillAndStroke'); // ER markers are stroke-only
        // The end point is pulled back toward the source so the line meets the terminal.
        expect(pe.x).toBeLessThan(100);
    });

    it('ERone paints exactly one perpendicular bar (one moveTo + one lineTo)', () => {
        const mod = makeModule();
        registerDrawioExtraEdgeMarkers(mod);
        const { ops } = paint(mod.EdgeMarkerRegistry.get('ERone'));
        expect(ops.filter((o) => o === 'moveTo').length).toBe(1);
        expect(ops.filter((o) => o === 'lineTo').length).toBe(1);
        expect(ops).toContain('stroke');
    });

    it('ERmandOne draws two bars; ERzeroToMany/ERzeroToOne include a circle', () => {
        const mod = makeModule();
        registerDrawioExtraEdgeMarkers(mod);
        const mand = paint(mod.EdgeMarkerRegistry.get('ERmandOne'));
        expect(mand.ops.filter((o) => o === 'lineTo').length).toBe(2); // two bars
        const zeroMany = paint(mod.EdgeMarkerRegistry.get('ERzeroToMany'));
        expect(zeroMany.ops).toContain('ellipse'); // the optional "zero" circle
        const zeroOne = paint(mod.EdgeMarkerRegistry.get('ERzeroToOne'));
        expect(zeroOne.ops).toContain('ellipse');
    });

    it('is additive: never overwrites a marker already in the registry (e.g. classic)', () => {
        const mod = makeModule(true);
        const before = mod.EdgeMarkerRegistry.get('classic');
        registerDrawioExtraEdgeMarkers(mod);
        expect(mod.EdgeMarkerRegistry.get('classic')).toBe(before);
    });

    it('is idempotent: a second call keeps the same factory instances', () => {
        const mod = makeModule();
        registerDrawioExtraEdgeMarkers(mod);
        const first = mod.EdgeMarkerRegistry.get('ERmany');
        registerDrawioExtraEdgeMarkers(mod);
        expect(mod.EdgeMarkerRegistry.get('ERmany')).toBe(first);
    });

    it('never throws and returns [] on a malformed module (bare-line fallback preserved)', () => {
        expect(registerDrawioExtraEdgeMarkers({})).toEqual([]);
        expect(registerDrawioExtraEdgeMarkers(null)).toEqual([]);
        expect(registerDrawioExtraEdgeMarkers({ EdgeMarkerRegistry: {} })).toEqual([]);
    });
});
