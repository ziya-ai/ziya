/**
 * G-27 / D-030 — directed-graph-layout-throws-at-scale: grid fallback.
 *
 * DirectedGraph.layout throws `TypeError: Cannot read properties of undefined
 * (reading 'x')` out of DirectedGraph.fromGraphLib for very large graphs
 * (empirically between ~80 nodes, which lay out fine, and ~131 nodes, which
 * throw) even with NO malformed cells present (joint-w2-02, 131 nodes). The
 * render loop caught the throw but did nothing afterwards, so every auto-layout
 * element — each created at the default {x:0,y:0} — stayed stacked at the origin
 * and the whole graph collapsed into one illegible pile of overlapping nodes.
 *
 * computeGridFallbackPositions lays the elements out in a deterministic near-
 * square reading-order grid so a large graph degrades to a legible matrix
 * instead. This is the ONE D-030 sub-mechanism that had no in-source remedy; the
 * other 8 clusters (raw-dia-element-missing-type D-144, content-exceeds-viewport
 * D-145, label-geometry D-146, link-overdraw/label-plate D-147/D-148,
 * port-position-string D-153, network-shapes D-151, nested-container-label D-150)
 * were already resolved in source and are covered by jointG17/G26/G47/G48.
 *
 * Direction: computeGridFallbackPositions does NOT exist in the unpatched module,
 * so the import is `undefined` and every call throws — the suite fails against
 * unpatched code and passes only with the fix. Structural defect (geometry only),
 * so no per-theme colour assertions; both-theme parity is confirmed at the shared
 * render stage.
 */

import { computeGridFallbackPositions } from '../jointPlugin';

interface Cell { id: string; width: number; height: number; }

const uniformCells = (n: number, w = 120, h = 60): Cell[] =>
    Array.from({ length: n }, (_, i) => ({ id: `n${i}`, width: w, height: h }));

// Axis-aligned box overlap test (half-open intervals).
const overlaps = (
    a: { x: number; y: number; w: number; h: number },
    b: { x: number; y: number; w: number; h: number },
): boolean =>
    a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

describe('D-030 — computeGridFallbackPositions (layout-throws-at-scale)', () => {
    it('is exported by the module (absent in the unpatched tree)', () => {
        expect(typeof computeGridFallbackPositions).toBe('function');
    });

    it('places every one of 131 nodes at a distinct, non-origin-stacked point', () => {
        const cells = uniformCells(131);
        const out = computeGridFallbackPositions(cells);
        expect(out).toHaveLength(131);
        // No two nodes share a coordinate (the pre-fix pile put all at 0,0).
        const seen = new Set(out.map(p => `${p.x},${p.y}`));
        expect(seen.size).toBe(131);
        // Every id is placed exactly once, order preserved (reading order).
        expect(out.map(p => p.id)).toEqual(cells.map(c => c.id));
    });

    it('produces a NON-overlapping grid for 131 uniform nodes', () => {
        const cells = uniformCells(131, 120, 60);
        const placed = computeGridFallbackPositions(cells).map(p => ({
            x: p.x, y: p.y, w: 120, h: 60,
        }));
        for (let i = 0; i < placed.length; i++) {
            for (let j = i + 1; j < placed.length; j++) {
                expect(overlaps(placed[i], placed[j])).toBe(false);
            }
        }
    });

    it('uses a near-square column count (ceil(sqrt(n)))', () => {
        const out = computeGridFallbackPositions(uniformCells(131));
        // 12 columns for 131 nodes: row 0 is columns 0..11, row 1 starts a new y.
        const firstRowY = out[0].y;
        const cols = out.filter(p => p.y === firstRowY).length;
        expect(cols).toBe(Math.ceil(Math.sqrt(131))); // 12
    });

    it('honours a uniform cell box sized to the LARGEST node so mixed sizes never overlap', () => {
        const cells: Cell[] = [
            { id: 'big', width: 300, height: 200 },
            { id: 'small', width: 40, height: 20 },
            { id: 'mid', width: 120, height: 60 },
            { id: 'tall', width: 60, height: 240 },
        ];
        const boxes = computeGridFallbackPositions(cells).map((p, i) => ({
            x: p.x, y: p.y, w: cells[i].width, h: cells[i].height,
        }));
        for (let i = 0; i < boxes.length; i++) {
            for (let j = i + 1; j < boxes.length; j++) {
                expect(overlaps(boxes[i], boxes[j])).toBe(false);
            }
        }
    });

    it('returns [] for an empty graph and a single placement for one node', () => {
        expect(computeGridFallbackPositions([])).toEqual([]);
        const one = computeGridFallbackPositions([{ id: 'solo', width: 100, height: 50 }]);
        expect(one).toHaveLength(1);
        expect(one[0].id).toBe('solo');
    });

    it('is deterministic (identical output for identical input)', () => {
        const cells = uniformCells(50);
        expect(computeGridFallbackPositions(cells)).toEqual(
            computeGridFallbackPositions(cells),
        );
    });
});
