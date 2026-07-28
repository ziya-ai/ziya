/**
 * Regression tests for Issue 8: drawio silent near-total data loss from a
 * huge-coordinate fit-to-content bbox blowout.
 *
 * A single cell/waypoint at an extreme coordinate (x=1e9, y=-1e9) or an absurd
 * dimension (width=1000000) inflates the graph bbox so fitCenter scales every real
 * cell to sub-pixel invisibility → blank-but-HTTP-200 render. sanitizeDrawioCoordinates()
 * uses robust median+MAD outlier detection to pull the runaway coordinate back to the
 * cluster edge WITHOUT squashing evenly-spread legitimate diagrams, and it covers
 * <mxPoint> waypoints (which the pre-fix <mxGeometry>-only clamp missed).
 *
 * Imports the REAL shipped helper (not a re-implementation) so it detects drift.
 */

import { sanitizeDrawioCoordinates } from '../drawioPlugin';

const ABSOLUTE_LIMIT = 100000;

// Extract every numeric x/y from mxGeometry (absolute) + mxPoint tags.
function positions(xml: string): number[] {
  const out: number[] = [];
  const tagRe = /<(mxGeometry|mxPoint)\b[^>]*?>/g;
  let m: RegExpExecArray | null;
  while ((m = tagRe.exec(xml)) !== null) {
    const tag = m[0];
    if (tag.includes('relative="1"')) continue;
    const gx = tag.match(/\bx="(-?\d+\.?\d*(?:[eE][+-]?\d+)?)"/);
    const gy = tag.match(/\by="(-?\d+\.?\d*(?:[eE][+-]?\d+)?)"/);
    if (gx) out.push(parseFloat(gx[1]));
    if (gy) out.push(parseFloat(gy[1]));
  }
  return out;
}

describe('sanitizeDrawioCoordinates (Issue 8)', () => {
  it('pulls a lone huge-coordinate vertex back to the cluster window (was: sub-pixel data loss)', () => {
    const xml = `
      <mxGraphModel><root>
        <mxCell id="huge" vertex="1" parent="1"><mxGeometry x="1000000000" y="-1000000000" width="120" height="40" as="geometry"/></mxCell>
        <mxCell id="a" vertex="1" parent="1"><mxGeometry x="40" y="40" width="120" height="40" as="geometry"/></mxCell>
        <mxCell id="b" vertex="1" parent="1"><mxGeometry x="240" y="40" width="120" height="40" as="geometry"/></mxCell>
      </root></mxGraphModel>`;
    const out = sanitizeDrawioCoordinates(xml);
    // The ±1e9 coordinate must be gone.
    expect(out).not.toContain('1000000000');
    expect(out).not.toContain('-1000000000');
    // Every surviving coordinate must be within the absolute backstop...
    const coords = positions(out);
    for (const c of coords) expect(Math.abs(c)).toBeLessThanOrEqual(ABSOLUTE_LIMIT);
    // ...and, crucially, the outlier must be pulled CLOSE to the cluster (~a few
    // thousand px), not left at 100000 where it would still squash the cluster.
    const maxAbs = Math.max(...coords.map(Math.abs));
    expect(maxAbs).toBeLessThan(20000);
    // The normal cells (x=40..360) must be preserved verbatim.
    expect(out).toContain('x="40"');
    expect(out).toContain('x="240"');
  });

  it('clamps ±1e9 mxPoint edge waypoints (pre-fix regex only touched mxGeometry)', () => {
    const xml = `
      <mxGraphModel><root>
        <mxCell id="a" vertex="1" parent="1"><mxGeometry x="40" y="40" width="120" height="40" as="geometry"/></mxCell>
        <mxCell id="b" vertex="1" parent="1"><mxGeometry x="240" y="40" width="120" height="40" as="geometry"/></mxCell>
        <mxCell id="e" edge="1" parent="1" source="a" target="b"><mxGeometry relative="1" as="geometry">
          <Array as="points"><mxPoint x="1000000000" y="-1000000000"/><mxPoint x="-1000000000" y="1000000000"/></Array>
        </mxGeometry></mxCell>
      </root></mxGraphModel>`;
    const out = sanitizeDrawioCoordinates(xml);
    expect(out).not.toContain('1000000000');
    const coords = positions(out);
    for (const c of coords) expect(Math.abs(c)).toBeLessThanOrEqual(ABSOLUTE_LIMIT);
    // The edge itself is preserved (still present as a tag).
    expect(out).toContain('as="points"');
  });

  it('clamps absurd dimensions (width=1000000) while keeping legit sizes', () => {
    const xml = `
      <mxGraphModel><root>
        <mxCell id="huge" vertex="1" parent="1"><mxGeometry x="40" y="40" width="1000000" height="50" as="geometry"/></mxCell>
        <mxCell id="a" vertex="1" parent="1"><mxGeometry x="40" y="140" width="120" height="40" as="geometry"/></mxCell>
      </root></mxGraphModel>`;
    const out = sanitizeDrawioCoordinates(xml);
    expect(out).not.toContain('width="1000000"');
    const wm = out.match(/width="(\d+)"/g)!.map(s => parseInt(s.replace(/\D/g, ''), 10));
    for (const w of wm) expect(w).toBeLessThanOrEqual(ABSOLUTE_LIMIT);
    // legit width preserved
    expect(out).toContain('width="120"');
  });

  it('coerces NaN/Infinity coordinates to a finite value', () => {
    const xml = `<mxGraphModel><root>
      <mxCell id="x" vertex="1" parent="1"><mxGeometry x="Infinity" y="NaN" width="100" height="40" as="geometry"/></mxCell>
      <mxCell id="y" vertex="1" parent="1"><mxGeometry x="10" y="20" width="100" height="40" as="geometry"/></mxCell>
    </root></mxGraphModel>`;
    const out = sanitizeDrawioCoordinates(xml);
    expect(out).not.toContain('x="Infinity"');
    expect(out).not.toContain('y="NaN"');
    for (const c of positions(out)) expect(Number.isFinite(c)).toBe(true);
  });

  // GUARD (non-vacuous, prevents the fix becoming a catch-all): a legitimate
  // diagram whose cells are LEGITIMATELY spread across thousands of px must be
  // left UNTOUCHED — the MAD window is wide, so nothing is an outlier. A naive
  // fixed clamp of a few thousand px would corrupt this; median/MAD must not.
  it('leaves an evenly-spread large diagram unchanged', () => {
    const cells: string[] = [];
    for (let i = 0; i < 10; i++) {
      cells.push(`<mxCell id="c${i}" vertex="1" parent="1"><mxGeometry x="${i * 900}" y="${i * 600}" width="120" height="40" as="geometry"/></mxCell>`);
    }
    const xml = `<mxGraphModel><root>${cells.join('')}</root></mxGraphModel>`;
    const out = sanitizeDrawioCoordinates(xml);
    // Largest legit coordinate is x=8100 / y=5400 — both must survive verbatim.
    expect(out).toContain('x="8100"');
    expect(out).toContain('y="5400"');
    // No coordinate should have been altered.
    expect(positions(out).sort((a, b) => a - b)).toEqual(positions(xml).sort((a, b) => a - b));
  });

  it('never touches relative="1" edge-label geometries', () => {
    const xml = `<mxGraphModel><root>
      <mxCell id="a" vertex="1" parent="1"><mxGeometry x="40" y="40" width="120" height="40" as="geometry"/></mxCell>
      <mxCell id="e" edge="1" parent="1"><mxGeometry relative="1" x="0.5" y="-0.5" as="geometry"/></mxCell>
    </root></mxGraphModel>`;
    const out = sanitizeDrawioCoordinates(xml);
    expect(out).toContain('relative="1" x="0.5" y="-0.5"');
  });
});
