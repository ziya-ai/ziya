/**
 * D-361 (nodes-overlap-edges-hidden REGRESSION) — layout invariant guard.
 *
 * Both d2 ELK paths pin every leaf to its measured box with MINIMUM_SIZE, yet the
 * headless elkjs layered pass has been observed to return boxes packed at a
 * near-zero pitch against 50-70px rects, so adjacent nodes overpaint one another
 * and every connection is hidden under the target box. The fix validates the
 * layout invariant — no two node boxes may overlap — with the pure predicate
 * `d2NodesOverlap`, and falls back to the provably non-overlapping grid layout
 * (`d2SimpleLayout`, pitch = maxBox + 60) when the engine output is degenerate.
 *
 * These assertions FAIL without `d2NodesOverlap` (the export does not exist) and
 * pass with it. They also prove the fallback grid the guard routes to actually
 * satisfies the invariant, so the guard is not a no-op.
 */
import { d2NodesOverlap, d2SimpleLayout } from '../d2Plugin';

describe('D-361 d2 layout overlap invariant', () => {
    // A degenerate ELK-style result: five 140x50 boxes packed at ~20px vertical /
    // 0px horizontal pitch — the exact collapse the triage measured.
    const collapsed = () => ([
        { id: 'a', label: 'Ingest',    x: 100, y: 100, width: 140, height: 50 },
        { id: 'b', label: 'Transform', x: 100, y: 120, width: 140, height: 50 },
        { id: 'c', label: 'Store',     x: 100, y: 140, width: 140, height: 50 },
        { id: 'd', label: 'Alert',     x: 100, y: 160, width: 140, height: 50 },
        { id: 'e', label: 'Report',    x: 100, y: 180, width: 140, height: 50 },
    ]);

    const edges = [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
        { source: 'c', target: 'd' },
        { source: 'd', target: 'e' },
    ];

    it('detects overlapping boxes in a collapsed layout', () => {
        expect(d2NodesOverlap(collapsed())).toBe(true);
    });

    it('does not flag a properly spaced grid as overlapping', () => {
        const spaced = [
            { id: 'a', label: 'Ingest', x: 0,   y: 0, width: 140, height: 50 },
            { id: 'b', label: 'Two',    x: 300, y: 0, width: 140, height: 50 },
            { id: 'c', label: 'Three',  x: 600, y: 0, width: 140, height: 50 },
        ];
        expect(d2NodesOverlap(spaced)).toBe(false);
    });

    it('treats exactly-abutting boxes (touching edges) as non-overlapping', () => {
        const abut = [
            { id: 'a', x: 0,   y: 0, width: 100, height: 50 },
            { id: 'b', x: 100, y: 0, width: 100, height: 50 }, // shares the right edge only
        ];
        expect(d2NodesOverlap(abut)).toBe(false);
    });

    it('the fallback grid the guard routes to satisfies the invariant', () => {
        // d2SimpleLayout is what the ELK-overlap guard falls back to; its output
        // must never itself overlap, else the guard would trade one collapse for
        // another.
        const { nodes } = d2SimpleLayout(collapsed(), edges);
        expect(d2NodesOverlap(nodes)).toBe(false);
    });

    it('is a no-op for zero/one node', () => {
        expect(d2NodesOverlap([])).toBe(false);
        expect(d2NodesOverlap([{ id: 'solo', x: 0, y: 0, width: 140, height: 50 }])).toBe(false);
    });
});
