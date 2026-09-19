/**
 * G-09b125 — D2 flat (container-less) ELK layout collapsed non-table nodes to
 * point size, so boxes overlapped and every connection was hidden under the
 * target rect (defects D-065..D-070, all regressions).
 *
 * Root cause (differs from the original triage lead, which pointed at
 * layoutEngine.ts / node dimensions not being written to ELK): the flat path in
 * d2Plugin's ELKLayoutEngine sized non-sql_table leaves with
 * `elk.nodeSize.constraints: 'NODE_LABELS'` and no minimum. Once D-084 made
 * buildElkNodeLabels emit a bare {text} label (so ELK stopped throwing and the
 * real layered layout ran), NODE_LABELS resolved that dimensionless label to a
 * 0x0 node under the headless renderer (no font metrics) — ELK then packed
 * point-sized nodes and they overlapped. The fix pins EVERY leaf to its measured
 * box via MINIMUM_SIZE, matching the hierarchy path.
 *
 * These assertions are theme-invariant (geometry only, no colour), so one axis
 * covers both light and dark. Each is paired with the PRE-FIX value so a revert
 * flips the test red.
 */
import { buildElkFlatChild, d2NodeBoxSize } from '../d2Plugin';

describe('G-09b125 flat ELK path pins leaves to their measured box', () => {
  const plainNode = { id: 'a', label: 'Service A', shape: 'rectangle' };
  const circleNode = { id: 'c', label: 'Cache', shape: 'circle' };
  const tableNode = { id: 't', label: 'users', shape: 'sql_table', columns: ['id', 'name'] };

  it('uses MINIMUM_SIZE (not NODE_LABELS) for a plain rectangle node', () => {
    const child = buildElkFlatChild(plainNode);
    // PRE-FIX: 'NODE_LABELS' for any non-sql_table node -> 0x0 collapse headless.
    expect(child.layoutOptions['elk.nodeSize.constraints']).toBe('MINIMUM_SIZE');
  });

  it('pins the node minimum to its measured box so ELK cannot shrink it to 0x0', () => {
    const child = buildElkFlatChild(plainNode);
    const box = d2NodeBoxSize(plainNode);
    // PRE-FIX: 'elk.nodeSize.minimum' was undefined for non-table nodes.
    expect(child.layoutOptions['elk.nodeSize.minimum']).toBe(`(${box.width},${box.height})`);
    // A real, non-degenerate box.
    expect(box.width).toBeGreaterThan(0);
    expect(box.height).toBeGreaterThan(0);
  });

  it('applies the same pinning to circle and sql_table shapes (no NODE_LABELS anywhere)', () => {
    for (const n of [circleNode, tableNode]) {
      const child = buildElkFlatChild(n);
      const box = d2NodeBoxSize(n);
      expect(child.layoutOptions['elk.nodeSize.constraints']).toBe('MINIMUM_SIZE');
      expect(child.layoutOptions['elk.nodeSize.minimum']).toBe(`(${box.width},${box.height})`);
      // width/height are carried through so convertToELK / drawing agree.
      expect(child.width).toBe(box.width);
      expect(child.height).toBe(box.height);
    }
  });
});
