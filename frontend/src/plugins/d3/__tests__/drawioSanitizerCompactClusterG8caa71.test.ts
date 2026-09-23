/**
 * G-8caa71 / D-095 / D-383 — "oversized-dimension-clamp-buries-normal-cells" and
 * "coordinate-sanitizer-window-destroys-neighbors".
 *
 * The pre-fix sanitizer used FIXED floors (MIN_POS_WINDOW=3000, MIN_DIM_CAP=6000)
 * that do not shrink for a COMPACT diagram. A ~200px cell set given a 6000px cap
 * lets a 20000px box survive at 30x the real cells (drawio-w2-08: after fitCenter
 * the normal cells are illegible slivers next to a canvas-filling slab); a ~600px
 * cluster given a 3000px position window lets one outlier sit 5x the cluster away,
 * squashing the cluster into a corner of a mostly-empty canvas (drawio-w2-13).
 *
 * The fix makes both floors scale-adaptive/outlier-robust: a smaller position
 * floor (only binds tight clusters, where a wide window is harmful) and a
 * dimension cap that is a bounded MULTIPLE of the bulk median. Critically it must
 * NOT over-clamp a legitimately-large diagram — a container at a small multiple of
 * the bulk, or a uniformly-large spread, is left untouched.
 *
 * These assertions FAIL against the old constants (huge box would be 6000 wide;
 * outlier window 3000) and PASS with the fix. Imports the REAL shipped helper.
 */

import { sanitizeDrawioCoordinates } from '../drawioPlugin';

function widths(xml: string): number[] {
    const out: number[] = [];
    const re = /<mxGeometry\b[^>]*?\bwidth="(-?\d+\.?\d*)"[^>]*?>/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(xml)) !== null) out.push(Math.abs(parseFloat(m[1])));
    return out;
}
function xs(xml: string): number[] {
    const out: number[] = [];
    // absolute mxGeometry x + mxPoint x
    for (const re of [/<mxGeometry\b(?![^>]*relative="1")[^>]*?\bx="(-?\d+\.?\d*)"/g,
                      /<mxPoint\b[^>]*?\bx="(-?\d+\.?\d*)"/g]) {
        let m: RegExpExecArray | null;
        while ((m = re.exec(xml)) !== null) out.push(parseFloat(m[1]));
    }
    return out;
}

describe('sanitizeDrawioCoordinates compact-cluster adaptivity (G-8caa71)', () => {
    it('w2-08: an absurd 20000px box is pulled well below the old 6000 cap so the 200px cells are not buried', () => {
        const xml = `<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>` +
            `<mxCell id="h1" vertex="1" parent="1"><mxGeometry x="0" y="0" width="20000" height="15000" as="geometry"/></mxCell>` +
            `<mxCell id="h2" vertex="1" parent="1"><mxGeometry x="0" y="16000" width="18000" height="400" as="geometry"/></mxCell>` +
            `<mxCell id="h3" vertex="1" parent="1"><mxGeometry x="200" y="200" width="200" height="60" as="geometry"/></mxCell>` +
            `<mxCell id="h4" vertex="1" parent="1"><mxGeometry x="200" y="400" width="200" height="60" as="geometry"/></mxCell>` +
            `</root></mxGraphModel>`;
        const out = sanitizeDrawioCoordinates(xml);
        const w = widths(out);
        const maxW = Math.max(...w);
        // Old fixed cap left the huge box at 6000 (30x the 200px cells). The fix
        // caps it to bulkMedian*15 = 300*15 = 4500 — strictly below 5000, and the
        // 200px cells are preserved verbatim.
        expect(maxW).toBeLessThanOrEqual(5000);
        expect(maxW).toBeLessThan(6000); // fails against the pre-fix MIN_DIM_CAP=6000 floor
        expect(w.filter((v) => v === 200).length).toBe(2); // normal cells untouched
    });

    it('w2-13: a far outlier is pulled closer to a tight cluster than the old 3000px window allowed', () => {
        // 30-cell cluster on a 6-column, 100px grid (x in 0..500) + one far outlier at x=90000.
        let cells = '';
        for (let i = 0; i < 30; i++) {
            const x = (i % 6) * 100;
            const y = Math.floor(i / 6) * 60;
            cells += `<mxCell id="c${i}" vertex="1" parent="1"><mxGeometry x="${x}" y="${y}" width="80" height="40" as="geometry"/></mxCell>`;
        }
        cells += `<mxCell id="far" vertex="1" parent="1"><mxGeometry x="90000" y="45000" width="80" height="40" as="geometry"/></mxCell>`;
        const xml = `<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>${cells}</root></mxGraphModel>`;
        const out = sanitizeDrawioCoordinates(xml);
        const allX = xs(out);
        const maxX = Math.max(...allX);
        // Cluster x-median is 300. Old window floor 3000 clamped the outlier to
        // ~3300; the tightened floor (1000, MAD*12 dominates at ~2400) clamps it to
        // ~2700 — closer, so the cluster occupies more of the canvas after fit.
        expect(maxX).toBeLessThan(3300); // fails against the pre-fix MIN_POS_WINDOW=3000
        expect(maxX).toBeLessThanOrEqual(2800);
    });

    it('preservation: a legitimately-large container at a small multiple of the bulk is NOT clamped', () => {
        // Five 200px cells + one legit 2500px container (12.5x the bulk).
        const xml = `<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>` +
            `<mxCell id="a" vertex="1" parent="1"><mxGeometry x="0" y="0" width="200" height="80" as="geometry"/></mxCell>` +
            `<mxCell id="b" vertex="1" parent="1"><mxGeometry x="0" y="100" width="200" height="80" as="geometry"/></mxCell>` +
            `<mxCell id="c" vertex="1" parent="1"><mxGeometry x="0" y="200" width="200" height="80" as="geometry"/></mxCell>` +
            `<mxCell id="d" vertex="1" parent="1"><mxGeometry x="0" y="300" width="200" height="80" as="geometry"/></mxCell>` +
            `<mxCell id="grp" vertex="1" parent="1"><mxGeometry x="0" y="0" width="2500" height="500" as="geometry"/></mxCell>` +
            `</root></mxGraphModel>`;
        const out = sanitizeDrawioCoordinates(xml);
        // bulkMedian=200 -> cap = max(200*15, 4000) = 4000, so the 2500 container survives.
        expect(widths(out)).toContain(2500);
    });

    it('preservation: a uniformly-large, evenly-spread diagram is left completely untouched', () => {
        let cells = '';
        for (let i = 0; i < 8; i++) {
            cells += `<mxCell id="u${i}" vertex="1" parent="1"><mxGeometry x="${i * 5000}" y="0" width="4000" height="3000" as="geometry"/></mxCell>`;
        }
        const xml = `<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>${cells}</root></mxGraphModel>`;
        const out = sanitizeDrawioCoordinates(xml);
        // All widths remain 4000 (MAD/median large -> floors never bind).
        expect(widths(out).every((v) => v === 4000)).toBe(true);
        // Positions preserved: last box still at x=35000.
        expect(xs(out)).toContain(35000);
    });
});
