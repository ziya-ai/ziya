import {
    sanitizeJointGeometry,
    JOINT_ABSOLUTE_LIMIT,
} from '../jointGeometrySanitizer';

// Regression test for graphics-stress Issue 16 (joint geometry blowout).
//
// Pre-fix, the joint plugin had NO geometry sanitizer: a single element at position
// 1e8 with size 1e7x1e7, or a link vertex at ±1e9, blew the graph bounding box to
// tens-of-millions of px, drove the SVG viewBox to ~1e7, and put fit-to-content into a
// runaway resize loop that defeated the headless screenshot (total data loss, no image).
//
// These tests import the REAL shipped helper (not a copy) and pin BOTH the outlier-clamp
// behavior AND the guard cases (a legitimate evenly-spread diagram must pass through
// untouched). Every "outlier clamped" assertion would FAIL against the pre-fix code
// because the helper did not exist at all (import would throw / be undefined).

describe('sanitizeJointGeometry (Issue 16)', () => {
    it('clamps a lone huge-position/huge-size element back into a small window', () => {
        const elements = [
            { id: 'hub', position: { x: 0, y: 0 }, size: { width: 80, height: 80 } },
            { id: 'n1', position: { x: 200, y: 0 }, size: { width: 100, height: 50 } },
            { id: 'n2', position: { x: 300, y: 100 }, size: { width: 100, height: 50 } },
            { id: 'huge', position: { x: 1e8, y: -1e8 }, size: { width: 1e7, height: 1e7 } },
        ];
        const { elements: out } = sanitizeJointGeometry(elements, []);

        const huge = out.find(e => e.id === 'huge')!;
        // Position pulled far below the 1e8 original (cluster is at ~0..300).
        expect(Math.abs(huge.position.x)).toBeLessThanOrEqual(JOINT_ABSOLUTE_LIMIT);
        expect(Math.abs(huge.position.x)).toBeLessThan(1e6);
        expect(Math.abs(huge.position.y)).toBeLessThan(1e6);
        // Size clamped well below 1e7 — this is what stopped the ~1e7 viewBox blowout.
        expect(huge.size.width).toBeLessThanOrEqual(JOINT_ABSOLUTE_LIMIT);
        expect(huge.size.width).toBeLessThan(1e7);
        expect(huge.size.height).toBeLessThan(1e7);
        // A rendered viewBox derived from the clamped extents must be finite & modest.
        const maxExtent = Math.max(
            ...out.map(e => Math.abs(e.position.x) + e.size.width),
            ...out.map(e => Math.abs(e.position.y) + e.size.height),
        );
        expect(Number.isFinite(maxExtent)).toBe(true);
        expect(maxExtent).toBeLessThan(2 * JOINT_ABSOLUTE_LIMIT);
    });

    it('leaves a legitimate evenly-spread diagram effectively untouched (no false clamp)', () => {
        // A big but legitimate diagram: 10 nodes spread across ~4500px with a large MAD.
        const elements = Array.from({ length: 10 }, (_, i) => ({
            id: `n${i}`,
            position: { x: i * 500, y: i * 300 },
            size: { width: 120, height: 80 },
        }));
        const { elements: out } = sanitizeJointGeometry(elements, []);
        // Every position/size preserved exactly — the guard case a catch-all would break.
        out.forEach((e, i) => {
            expect(e.position.x).toBe(i * 500);
            expect(e.position.y).toBe(i * 300);
            expect(e.size.width).toBe(120);
            expect(e.size.height).toBe(80);
        });
    });

    it('coerces NaN/null/negative/zero geometry to finite sane values', () => {
        const elements = [
            { id: 'a', position: { x: 0, y: 0 }, size: { width: 100, height: 50 } },
            { id: 'b', position: { x: 100, y: 100 }, size: { width: 100, height: 50 } },
            { id: 'nan', position: { x: 'NaN', y: null }, size: { width: 100, height: 50 } },
            { id: 'zero', position: { x: 50, y: 50 }, size: { width: 0, height: 0 } },
            { id: 'neg', position: { x: 60, y: 60 }, size: { width: -150, height: -75 } },
        ];
        const { elements: out } = sanitizeJointGeometry(elements, []);
        for (const e of out) {
            expect(Number.isFinite(e.position.x)).toBe(true);
            expect(Number.isFinite(e.position.y)).toBe(true);
            expect(Number.isFinite(e.size.width)).toBe(true);
            expect(Number.isFinite(e.size.height)).toBe(true);
            // No zero/negative sizes survive (would render an invisible/degenerate cell).
            expect(e.size.width).toBeGreaterThan(0);
            expect(e.size.height).toBeGreaterThan(0);
        }
    });

    it('clamps ±1e9 link vertices while preserving in-bounds vertices', () => {
        const elements = [
            { id: 'a', position: { x: 0, y: 0 }, size: { width: 80, height: 80 } },
            { id: 'b', position: { x: 300, y: 200 }, size: { width: 80, height: 80 } },
        ];
        const connections = [
            {
                id: 'e',
                source: { id: 'a' },
                target: { id: 'b' },
                vertices: [
                    { x: 1e9, y: -1e9 },
                    { x: -1e9, y: 1e9 },
                    { x: 150, y: 100 },
                ],
            },
        ];
        const { connections: out } = sanitizeJointGeometry(elements, connections);
        const v = out[0].vertices!;
        for (const p of v) {
            expect(Math.abs(p.x)).toBeLessThanOrEqual(JOINT_ABSOLUTE_LIMIT);
            expect(Math.abs(p.y)).toBeLessThanOrEqual(JOINT_ABSOLUTE_LIMIT);
        }
        // The in-bounds vertex stays inside the (wide) window unchanged.
        expect(v[2].x).toBe(150);
        expect(v[2].y).toBe(100);
    });

    it('supports [x,y] tuple position form and preserves it', () => {
        const elements = [
            { id: 'a', position: [0, 0] as [number, number], size: { width: 80, height: 80 } },
            { id: 'b', position: [200, 100] as [number, number], size: { width: 80, height: 80 } },
            { id: 'huge', position: [1e8, 1e8] as [number, number], size: { width: 80, height: 80 } },
        ];
        const { elements: out } = sanitizeJointGeometry(elements, []);
        const huge = out.find(e => e.id === 'huge')!;
        expect(Array.isArray(huge.position)).toBe(true);
        expect(Math.abs((huge.position as number[])[0])).toBeLessThan(1e6);
    });

    it('does not mutate the input objects', () => {
        const elements = [
            { id: 'a', position: { x: 0, y: 0 }, size: { width: 80, height: 80 } },
            { id: 'huge', position: { x: 1e8, y: 0 }, size: { width: 1e7, height: 1e7 } },
        ];
        sanitizeJointGeometry(elements, []);
        // Originals untouched.
        expect(elements[1].position.x).toBe(1e8);
        expect(elements[1].size.width).toBe(1e7);
    });

    it('handles empty / malformed inputs without throwing', () => {
        expect(() => sanitizeJointGeometry([], [])).not.toThrow();
        // @ts-expect-error deliberately malformed
        expect(() => sanitizeJointGeometry(undefined, undefined)).not.toThrow();
    });
});
