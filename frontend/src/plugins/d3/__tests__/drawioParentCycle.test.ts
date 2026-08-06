/**
 * Regression tests for Issue 22: drawio total hang from a CYCLIC mxCell
 * `parent` hierarchy.
 *
 * A parent cycle (g1 parent=g3, g2 parent=g1, g3 parent=g2) has no path to the
 * root "0"/"1". Every ancestor-walk in the plugin AND in maxGraph
 * (`while (cur.getId() !== '0') cur = cur.getParent()`) then loops forever — the
 * render hangs indefinitely (observed 300s, no image), NOT a silent blank like the
 * coordinate outliers. breakDrawioParentCycles() detects any cell whose parent chain
 * revisits a node (or exceeds a depth cap) and re-roots ONLY the offending cell to
 * "1", breaking the cycle while leaving every legitimate acyclic tree untouched.
 *
 * Imports the REAL shipped helper (not a re-implementation) so it detects drift.
 */

import { breakDrawioParentCycles } from '../drawioPlugin';

// Follow id -> parent and report whether the chain terminates at a root.
// This is the property the render relies on; if it's false the render hangs.
function chainTerminates(xml: string, startId: string): boolean {
  const parentOf = new Map<string, string | null>();
  const cellRe = /<mxCell\b[^>]*?>/g;
  let m: RegExpExecArray | null;
  while ((m = cellRe.exec(xml)) !== null) {
    const tag = m[0];
    const idM = tag.match(/\bid="([^"]*)"/);
    if (!idM) continue;
    const parM = tag.match(/\bparent="([^"]*)"/);
    if (!parentOf.has(idM[1])) parentOf.set(idM[1], parM ? parM[1] : null);
  }
  const seen = new Set<string>();
  let cur: string | null = startId;
  let depth = 0;
  while (cur !== null) {
    if (cur === '0' || cur === '1') return true;      // reached a root
    if (seen.has(cur)) return false;                  // cycle
    if (depth++ > 100000) return false;               // runaway
    seen.add(cur);
    if (!parentOf.has(cur)) return true;              // parent not declared -> maxGraph re-roots
    cur = parentOf.get(cur) ?? null;
    if (cur === null) return true;                    // top-level
  }
  return true;
}

function parentOfId(xml: string, id: string): string | null {
  const re = new RegExp(`<mxCell\\b[^>]*?\\bid="${id}"[^>]*?>|<mxCell\\b[^>]*?>`, 'g');
  let m: RegExpExecArray | null;
  const cellRe = /<mxCell\b[^>]*?>/g;
  while ((m = cellRe.exec(xml)) !== null) {
    const tag = m[0];
    const idM = tag.match(/\bid="([^"]*)"/);
    if (idM && idM[1] === id) {
      const parM = tag.match(/\bparent="([^"]*)"/);
      return parM ? parM[1] : null;
    }
  }
  return null;
}

describe('breakDrawioParentCycles (Issue 22)', () => {
  it('breaks a genuine 3-cycle (g1->g3->g2->g1) so every chain terminates at a root', () => {
    const xml = `<mxGraphModel><root>
      <mxCell id="0"/>
      <mxCell id="1" parent="0"/>
      <mxCell id="g1" style="group;" vertex="1" parent="g3"><mxGeometry x="40" y="40" width="600" height="500" as="geometry"/></mxCell>
      <mxCell id="g2" style="group;" vertex="1" parent="g1"><mxGeometry x="10" y="10" width="500" height="400" as="geometry"/></mxCell>
      <mxCell id="g3" style="group;" vertex="1" parent="g2"><mxGeometry x="10" y="10" width="400" height="300" as="geometry"/></mxCell>
    </root></mxGraphModel>`;

    // PRE-FIX: at least one of the cycle members does NOT terminate — proves the test is non-vacuous.
    expect(chainTerminates(xml, 'g1')).toBe(false);

    const out = breakDrawioParentCycles(xml);

    // POST-FIX: every cell's parent chain now terminates at a root.
    for (const id of ['g1', 'g2', 'g3']) {
      expect(chainTerminates(out, id)).toBe(true);
    }
    // Exactly the minimal break: one offender re-rooted to "1".
    const rerootedToOne = ['g1', 'g2', 'g3'].filter(id => parentOfId(out, id) === '1');
    expect(rerootedToOne.length).toBeGreaterThanOrEqual(1);
    // All three cells still present (no data loss).
    for (const id of ['g1', 'g2', 'g3']) expect(out).toContain(`id="${id}"`);
  });

  it('breaks a 2-cycle (a<->b)', () => {
    const xml = `<mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="a" vertex="1" parent="b"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>
      <mxCell id="b" vertex="1" parent="a"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>
    </root></mxGraphModel>`;
    expect(chainTerminates(xml, 'a')).toBe(false);
    const out = breakDrawioParentCycles(xml);
    expect(chainTerminates(out, 'a')).toBe(true);
    expect(chainTerminates(out, 'b')).toBe(true);
  });

  it('breaks a self-parent cycle (c parent=c)', () => {
    const xml = `<mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="c" vertex="1" parent="c"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>
    </root></mxGraphModel>`;
    expect(chainTerminates(xml, 'c')).toBe(false);
    const out = breakDrawioParentCycles(xml);
    expect(parentOfId(out, 'c')).toBe('1');
    expect(chainTerminates(out, 'c')).toBe(true);
  });

  // GUARD (non-vacuous, prevents the fix becoming a catch-all): a LEGITIMATE deep
  // acyclic nested hierarchy (the 30-level nest in the same adversarial spec) must be
  // left COMPLETELY UNTOUCHED. If the fix over-fired it would re-root inner groups and
  // flatten the diagram.
  it('leaves a legitimate 30-level deep acyclic nest unchanged', () => {
    const cells: string[] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>',
      '<mxCell id="d0" style="group;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="800" height="900" as="geometry"/></mxCell>'];
    for (let i = 1; i <= 29; i++) {
      cells.push(`<mxCell id="d${i}" style="group;" vertex="1" parent="d${i - 1}"><mxGeometry x="5" y="5" width="100" height="100" as="geometry"/></mxCell>`);
    }
    cells.push('<mxCell id="leaf" vertex="1" parent="d29"><mxGeometry x="10" y="10" width="80" height="60" as="geometry"/></mxCell>');
    const xml = `<mxGraphModel><root>${cells.join('')}</root></mxGraphModel>`;

    // Pre-fix this deep tree already terminates (it is valid) — the guard proves the
    // fix does NOT touch it.
    expect(chainTerminates(xml, 'leaf')).toBe(true);
    const out = breakDrawioParentCycles(xml);
    expect(out).toBe(xml); // byte-for-byte identical: nothing rewritten
  });

  it('leaves a normal flat diagram (all parent="1") unchanged', () => {
    const xml = `<mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="a" vertex="1" parent="1"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>
      <mxCell id="b" vertex="1" parent="1"><mxGeometry x="100" y="0" width="80" height="40" as="geometry"/></mxCell>
      <mxCell id="e" edge="1" parent="1" source="a" target="b"><mxGeometry relative="1" as="geometry"/></mxCell>
    </root></mxGraphModel>`;
    const out = breakDrawioParentCycles(xml);
    expect(out).toBe(xml);
  });

  it('leaves a cell whose parent is an undeclared id unchanged (maxGraph re-roots those itself)', () => {
    // A parent pointing at a non-existent cell is NOT a cycle — the walk terminates
    // when the parent id isn't a declared cell. Must not be re-rooted here.
    const xml = `<mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="a" vertex="1" parent="ghost_container"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>
    </root></mxGraphModel>`;
    expect(chainTerminates(xml, 'a')).toBe(true);
    const out = breakDrawioParentCycles(xml);
    expect(out).toBe(xml);
  });

  it('handles the empty / no-cell case without throwing', () => {
    expect(breakDrawioParentCycles('')).toBe('');
    expect(breakDrawioParentCycles('<mxGraphModel><root></root></mxGraphModel>'))
      .toBe('<mxGraphModel><root></root></mxGraphModel>');
  });
});
