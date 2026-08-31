/**
 * G-36 / D-106 — shape-not-implemented-falls-back-to-rect.
 *
 * maxGraph core registers no `parallelogram`/`process`/`step`/`note`/`cylinder3`, so a spec
 * using them draws a plain rectangle and loses the shape semantics in both themes.
 * registerDrawioExtraShapes fills exactly those names on the loaded module.
 *
 * The tests use a fake maxGraph module: a BaseRegistry-like ShapeRegistry that starts EMPTY
 * (mirroring the core registry for these names) and a RectangleShape base whose paintBackground
 * records a `rect` op. Direction check: before registration the names resolve to null (the
 * rectangle-fallback bug); after, each custom shape paints a POLYGON (moveTo/lineTo), not a rect.
 * Geometry-agnostic (kind:structural, theme-independent); pixel sufficiency is a render-stage check.
 */

import { registerDrawioExtraShapes } from '../drawioShapes';

class FakeRegistry {
    private values = new Map<string, any>();
    add(name: string, value: any) { this.values.set(name, value); }
    get(name: string) { return this.values.get(name) ?? null; }
    has(name: string) { return this.values.has(name); }
}

// Records every path op the shape issues so we can assert polygon vs rect.
function makeCanvas() {
    const ops: string[] = [];
    return {
        ops,
        begin: () => ops.push('begin'),
        moveTo: () => ops.push('moveTo'),
        lineTo: () => ops.push('lineTo'),
        close: () => ops.push('close'),
        fillAndStroke: () => ops.push('fillAndStroke'),
        stroke: () => ops.push('stroke'),
        rect: () => ops.push('rect'),
    };
}

class FakeRectangleShape {
    paintBackground(c: any, _x: number, _y: number, _w: number, _h: number) { c.rect(); c.fillAndStroke(); }
    paintForeground(_c: any, _x: number, _y: number, _w: number, _h: number) { /* rects have no foreground detail */ }
}
class FakeCylinderShape {}

function makeModule() {
    return {
        ShapeRegistry: new FakeRegistry(),
        RectangleShape: FakeRectangleShape,
        CylinderShape: FakeCylinderShape,
    };
}

describe('D-106: registerDrawioExtraShapes fills the shapes maxGraph core omits', () => {
    it('DIRECTION: the drawio shape names are unregistered by default (→ rectangle fallback)', () => {
        const mod = makeModule();
        for (const name of ['parallelogram', 'process', 'step', 'note', 'cylinder3']) {
            expect(mod.ShapeRegistry.get(name)).toBeNull();
        }
    });

    it('registers all five drawio shapes', () => {
        const mod = makeModule();
        const registered = registerDrawioExtraShapes(mod);
        expect(new Set(registered)).toEqual(new Set(['parallelogram', 'process', 'step', 'note', 'cylinder3']));
        for (const name of ['parallelogram', 'process', 'step', 'note']) {
            expect(typeof mod.ShapeRegistry.get(name)).toBe('function');
        }
    });

    it('parallelogram/step/note paint a POLYGON (moveTo/lineTo), not a rectangle', () => {
        const mod = makeModule();
        registerDrawioExtraShapes(mod);
        for (const name of ['parallelogram', 'step', 'note']) {
            const Ctor = mod.ShapeRegistry.get(name);
            const shape = new Ctor();
            const c = makeCanvas();
            shape.paintBackground(c, 0, 0, 120, 60);
            expect(c.ops).toContain('moveTo');
            expect(c.ops.filter((o) => o === 'lineTo').length).toBeGreaterThanOrEqual(3);
            expect(c.ops).not.toContain('rect'); // the bug was: rect fallback
            expect(c.ops).toContain('fillAndStroke');
        }
    });

    it('process keeps the rectangle body but adds the two inner rails in the foreground', () => {
        const mod = makeModule();
        registerDrawioExtraShapes(mod);
        const Ctor = mod.ShapeRegistry.get('process');
        const shape = new Ctor();
        const bg = makeCanvas();
        shape.paintBackground(bg, 0, 0, 120, 60); // inherited rectangle body
        expect(bg.ops).toContain('rect');
        const fg = makeCanvas();
        shape.paintForeground(fg, 0, 0, 120, 60); // rails
        expect(fg.ops.filter((o) => o === 'moveTo').length).toBeGreaterThanOrEqual(2);
        expect(fg.ops).toContain('stroke');
    });

    it('cylinder3 reuses the real cylinder geometry (not a rectangle)', () => {
        const mod = makeModule();
        registerDrawioExtraShapes(mod);
        expect(mod.ShapeRegistry.get('cylinder3')).toBe(FakeCylinderShape);
    });

    it('never throws and returns [] on a malformed module (rectangle fallback preserved)', () => {
        expect(registerDrawioExtraShapes({})).toEqual([]);
        expect(registerDrawioExtraShapes(null)).toEqual([]);
        expect(registerDrawioExtraShapes({ ShapeRegistry: {} })).toEqual([]);
    });
});
