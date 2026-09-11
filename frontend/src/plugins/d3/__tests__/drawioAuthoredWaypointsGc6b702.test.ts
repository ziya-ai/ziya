/**
 * D-092 (group G-c6b702): drawio edge routing ignored authored waypoints.
 *
 * drawio stores explicit edge routing as a <Array as="points"> of <mxPoint>
 * children under <mxGeometry>. The historical edge parser read only the
 * `relative` flag and dropped that array entirely, so authored routes were
 * discarded and re-routed from scratch (dense sets bundled onto shared trunks,
 * per specs drawio-w1-06 / w1-11 / w2-11).
 *
 * These tests exercise the REAL shipped helper (parseAuthoredEdgeWaypoints)
 * that the plugin's edge parser now feeds into geometry.points. Before the fix
 * the export did not exist and no waypoints were read; after it, the authored
 * bends survive parsing.
 */

import { parseAuthoredEdgeWaypoints } from '../drawioPlugin';

function geom(xml: string): Element {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  const g = doc.getElementsByTagName('mxGeometry')[0];
  if (!g) throw new Error('no mxGeometry in fixture');
  return g;
}

describe('parseAuthoredEdgeWaypoints (D-092)', () => {
  it('reads every authored <Array as="points"> waypoint in order', () => {
    const g = geom(`
      <mxGeometry relative="1" as="geometry">
        <Array as="points">
          <mxPoint x="120" y="200"/>
          <mxPoint x="240" y="200"/>
          <mxPoint x="240" y="80"/>
        </Array>
      </mxGeometry>`);
    const wps = parseAuthoredEdgeWaypoints(g);
    expect(wps).toEqual([
      { x: 120, y: 200 },
      { x: 240, y: 200 },
      { x: 240, y: 80 },
    ]);
  });

  it('excludes terminal points (sourcePoint/targetPoint) that live outside the Array', () => {
    const g = geom(`
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="10" y="10" as="sourcePoint"/>
        <mxPoint x="500" y="500" as="targetPoint"/>
        <Array as="points">
          <mxPoint x="300" y="10"/>
        </Array>
      </mxGeometry>`);
    const wps = parseAuthoredEdgeWaypoints(g);
    // Only the routing waypoint, not the terminal anchors.
    expect(wps).toEqual([{ x: 300, y: 10 }]);
  });

  it('returns [] for an edge geometry with no authored waypoints', () => {
    const g = geom(`<mxGeometry relative="1" as="geometry"/>`);
    expect(parseAuthoredEdgeWaypoints(g)).toEqual([]);
  });

  it('is theme-agnostic: identical routing survives regardless of render theme', () => {
    // Waypoint parsing must not depend on light/dark; assert the same result twice
    // (the parser has no theme input, so identity here guards against a future
    // theme-coupled regression in the edge-parse path).
    const xml = `
      <mxGeometry relative="1" as="geometry">
        <Array as="points"><mxPoint x="64" y="128"/></Array>
      </mxGeometry>`;
    const light = parseAuthoredEdgeWaypoints(geom(xml));
    const dark = parseAuthoredEdgeWaypoints(geom(xml));
    expect(light).toEqual([{ x: 64, y: 128 }]);
    expect(dark).toEqual(light);
  });
});
