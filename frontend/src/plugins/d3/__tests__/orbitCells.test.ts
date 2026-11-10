/**
 * H3 cells and beam assignment for the orbit fence (orbitMath prepareCells /
 * assignCells / buildOrbitScene).  The constellation is a synthetic Walker
 * 48/6/1 delta pattern at 1200 km / 53 deg (orbitSources walkerConstellation)
 * so the fixture depends on no operator's catalogue.  Expected numbers were
 * computed with an independent script (h3-js + satellite.js + a two-body
 * propagator, same greedy rule):
 *   - a 600 km disc around Seattle at H3 resolution 3 holds 103 cells;
 *   - at the epoch, 2026-11-04T16:00Z, with 16 beams each, demo-2-3 and
 *     demo-3-2 fill up, demo-1-4 takes 2 overflow cells: 34 served, 69 see
 *     only full satellites, none see nothing;
 *   - at +5 min only 3 cells see a satellite (demo-2-3); at +20 min none.
 */
import * as fs from 'fs';
import * as path from 'path';
import { cellToLatLng, getResolution } from 'h3-js';

import {
  assignCells,
  buildOrbitScene,
  centralAngleDeg,
  describeCellStats,
  isOrbitSpec,
  MAX_CELLS,
  prepareCells,
  prepareSatellites,
  unwrapRing,
  withAlpha,
  OrbitSpec,
} from '../orbitMath';

const EPOCH = '2026-11-04T16:00:00Z';
const WALKER = { name: 'demo', total: 48, planes: 6, phasing: 1, altitudeKm: 1200, inclinationDeg: 53, orbit: false };
const SEATTLE = '8328d5fffffffff';
const VANCOUVER = '8328defffffffff';
const PORTLAND = '8328f0fffffffff';
const SPOKANE = '8312dbfffffffff';

const ITEMS = [
  { label: 'Seattle', lat: 47.61, lon: -122.33, demand: 40 },
  { label: 'Vancouver', lat: 49.28, lon: -123.12, demand: 30 },
  { label: 'Portland', lat: 45.52, lon: -122.68, demand: 25 },
  { label: 'Spokane', lat: 47.66, lon: -117.43, demand: 10 },
];

const cellSpec = (extra: Partial<OrbitSpec> = {}): OrbitSpec => ({
  type: 'orbit',
  time: EPOCH,
  constellations: [WALKER],
  cells: { resolution: 3, around: { lat: 47.61, lon: -122.33, radiusKm: 600 }, items: ITEMS },
  beams: { maxBeamsPerSatellite: 16, minElevationDeg: 35 },
  ...extra,
});

const sceneAt = (s: OrbitSpec, iso: string) =>
  buildOrbitScene(s, prepareSatellites(s), new Date(iso), undefined, prepareCells(s));
const plusMin = (m: number) => new Date(Date.parse(EPOCH) + m * 60000).toISOString();

describe('prepareCells', () => {
  it('fills a disc with resolution-3 cells whose centres lie inside it', () => {
    const { cells, warnings } = prepareCells(cellSpec());
    expect(warnings).toEqual([]);
    expect(cells).toHaveLength(103);
    for (const c of cells) {
      expect(getResolution(c.id)).toBe(3);
      expect(centralAngleDeg(47.61, -122.33, c.lat, c.lng) * (Math.PI / 180) * 6371).toBeLessThanOrEqual(600);
      expect(c.ring.length).toBeGreaterThanOrEqual(6);
    }
  });

  it('adds demand and labels from items to the cell that contains them', () => {
    const byId = new Map(prepareCells(cellSpec()).cells.map((c) => [c.id, c]));
    expect(byId.get(SEATTLE)).toMatchObject({ demand: 40, label: 'Seattle' });
    expect(byId.get(VANCOUVER)).toMatchObject({ demand: 30, label: 'Vancouver' });
    const twice = prepareCells({ type: 'orbit', cells: { ids: [SEATTLE], demand: { [SEATTLE]: 5 }, items: [{ id: SEATTLE, demand: 7 }] } });
    expect(twice.cells).toHaveLength(1);
    expect(twice.cells[0].demand).toBe(12);
  });

  it('reports invalid ids and a missing resolution instead of failing', () => {
    const bad = prepareCells({ type: 'orbit', cells: { ids: [SEATTLE, 'not-a-cell'], around: { lat: 0, lon: 0, radiusKm: 100 } } });
    expect(bad.cells.map((c) => c.id)).toEqual([SEATTLE]);
    expect(bad.warnings.join('\n')).toMatch(/invalid H3 id.*not-a-cell/);
    expect(bad.warnings.join('\n')).toMatch(/"around" needs "resolution"/);
  });

  it('caps at MAX_CELLS, keeping the cells that carry demand', () => {
    const big = prepareCells({ type: 'orbit', cells: { resolution: 5, around: { lat: 10, lon: 10, radiusKm: 1000 },
      items: [{ lat: 18.9, lon: 10, demand: 99 }] } });
    expect(big.cells).toHaveLength(MAX_CELLS);
    expect(big.warnings.join('\n')).toMatch(/only the first 5000/);
    expect(big.cells.some((c) => c.demand === 99)).toBe(true);
  });

  it('unwraps a cell outline that crosses the antimeridian', () => {
    const { cells } = prepareCells({ type: 'orbit', cells: { ids: ['837eb5fffffffff'] } });
    const lngs = cells[0].ring.map(([lng]) => lng);
    expect(Math.max(...lngs) - Math.min(...lngs)).toBeLessThan(10);
    expect(unwrapRing([[179, 0], [-179, 1], [-178, 0]]).map(([l]) => l)).toEqual([179, 181, 182]);
  });
});

describe('assignCells', () => {
  // A at 1200 km straight overhead; B at 400 km, 1 deg off: B is nearer, A higher.
  const [lat, lng] = cellToLatLng(SEATTLE);
  const cells = [{ id: SEATTLE, lat, lng, ring: [], demand: 0 }];
  const sats = [
    { name: 'A', sub: { lat, lng, altKm: 1200 } },
    { name: 'B', sub: { lat: lat + 1, lng, altKm: 400 } },
  ];

  it('highest-elevation (default) and nearest pick different satellites', () => {
    expect(assignCells(cells, sats, { minElevationDeg: 10 }).assignments[0].sat).toBe('A');
    expect(assignCells(cells, sats, { minElevationDeg: 10, policy: 'nearest' }).assignments[0].sat).toBe('B');
  });

  it('falls back to the next satellite when the best one is full', () => {
    const two = [...cells, { ...cells[0], id: VANCOUVER }];
    const { assignments, stats } = assignCells(two, sats, { minElevationDeg: 10, maxBeamsPerSatellite: 1 });
    expect(assignments.map((a) => a.sat).sort()).toEqual(['A', 'B']);
    expect(stats.beamsUsed).toEqual({ A: 1, B: 1 });
  });
});

describe('cells in the scene, Walker 48/6/1 fixture', () => {
  it('two satellites fill their 16 beams at the epoch and a third takes the overflow', () => {
    // Independent capped run: demo-2-3 16, demo-3-2 16, demo-1-4 2 (it sees
    // two cells above 35 deg that the full satellites turned away).
    const scene = sceneAt(cellSpec(), EPOCH);
    expect(scene.cellStats).toMatchObject({ total: 103, served: 34, noCapacity: 69, notVisible: 0, maxBeamsPerSatellite: 16 });
    expect(scene.cellStats!.beamsUsed).toEqual({ 'demo-2-3': 16, 'demo-3-2': 16, 'demo-1-4': 2 });
    expect(scene.paths.filter((p) => p.kind === 'beam')).toHaveLength(34);
    expect(scene.cells).toHaveLength(103);
    expect(describeCellStats(scene.cellStats!)).toMatch(/^cells: 34\/103 served · 69 at capacity · 0 out of reach · load /);
    expect(describeCellStats(scene.cellStats!)).toContain('demo-1-4 2/16');
  });

  it('handover over the timeline: coverage thins to three cells at +5 min and is gone at +20 min', () => {
    const five = sceneAt(cellSpec(), plusMin(5));
    expect(five.cellStats).toMatchObject({ served: 3, noCapacity: 0, notVisible: 100 });
    expect(five.cellStats!.beamsUsed).toEqual({ 'demo-2-3': 3 });
    const twenty = sceneAt(cellSpec(), plusMin(20));
    expect(twenty.cellStats).toMatchObject({ served: 0, notVisible: 103 });
    expect(twenty.cellStats!.beamsUsed).toEqual({});
  });

  it('with too few beams the busiest cells win, falling back to the next satellite', () => {
    // Independent capped run, 2 beams each: Seattle and Vancouver take
    // demo-2-3 (best), Portland and Spokane fall back to demo-3-2, and
    // demo-1-4 serves two zero-demand cells.
    const scene = sceneAt(cellSpec({ beams: { maxBeamsPerSatellite: 2, minElevationDeg: 35 } }), EPOCH);
    const byId = new Map(scene.cells.map((c) => [c.id, c]));
    expect(byId.get(SEATTLE)).toMatchObject({ status: 'served', sat: 'demo-2-3' });
    expect(byId.get(VANCOUVER)).toMatchObject({ status: 'served', sat: 'demo-2-3' });
    expect(byId.get(PORTLAND)).toMatchObject({ status: 'served', sat: 'demo-3-2' });
    expect(byId.get(SPOKANE)).toMatchObject({ status: 'served', sat: 'demo-3-2' });
    expect(scene.cellStats).toMatchObject({ served: 6, noCapacity: 97, notVisible: 0 });
    expect(scene.cellStats!.beamsUsed).toEqual({ 'demo-2-3': 2, 'demo-3-2': 2, 'demo-1-4': 2 });
  });

  it('colours a served cell by its satellite and explains each state on hover', () => {
    const scene = sceneAt(cellSpec(), EPOCH);
    const served = scene.cells.find((c) => c.status === 'served')!;
    const color = prepareSatellites(cellSpec()).sats.find((x) => x.name === served.sat)!.color;
    expect(served.fill).toBe(withAlpha(color, 0.45));
    expect(served.tooltip).toMatch(/served by demo-\d-\d at [\d.]+°<br>range [\d,]+ km/);
    expect(scene.cells.find((c) => c.status === 'no-capacity')!.tooltip).toMatch(/above 35°, all at capacity \(16\)/);
    expect(sceneAt(cellSpec(), plusMin(20)).cells[0].tooltip).toMatch(/no satellite above 35°/);
    expect(scene.cells.find((c) => c.id === SEATTLE)!.tooltip).toMatch(/Seattle \(8328d5fffffffff\)<\/b><br>demand 40/);
  });

  it('beams.show false keeps the assignment but draws no beam lines', () => {
    const scene = sceneAt(cellSpec({ beams: { maxBeamsPerSatellite: 16, minElevationDeg: 35, show: false } }), EPOCH);
    expect(scene.cellStats!.served).toBe(34);
    expect(scene.paths.filter((p) => p.kind === 'beam')).toEqual([]);
  });

  it('a cells-only spec is an orbit spec and centres on the cells', () => {
    const s: OrbitSpec = { type: 'orbit', cells: { ids: [SEATTLE] } };
    expect(isOrbitSpec(s)).toBe(true);
    const scene = sceneAt(s, EPOCH);
    expect(scene.cells[0].status).toBe('not-visible');
    expect(scene.center.lat).toBeCloseTo(cellToLatLng(SEATTLE)[0], 6);
  });

  it('withAlpha handles 6- and 3-digit hex and leaves other colours alone', () => {
    expect(withAlpha('#f59e0b', 0.5)).toBe('rgba(245,158,11,0.5)');
    expect(withAlpha('#fff', 0.2)).toBe('rgba(255,255,255,0.2)');
    expect(withAlpha('teal', 0.2)).toBe('teal');
  });
});

describe('the plugin draws the cells', () => {
  const FRONTEND = path.resolve(__dirname, '../../../..');
  it('prepares cells once, renders them as polygons with hover, and lists h3-js as a dependency', () => {
    const src = fs.readFileSync(path.join(FRONTEND, 'src/plugins/d3/orbitPlugin.ts'), 'utf8');
    expect(src).toContain('const preparedCells = prepareCells(spec);');
    expect(src).toMatch(/buildOrbitScene\(spec, prepared, t0, theme, preparedCells\)/);
    expect(src).toMatch(/buildOrbitScene\(spec, prepared, new Date\(t0\.getTime\(\) \+ m \* 60000\), theme, preparedCells\)/);
    expect(src).toContain('globe.polygonsData([...countries, ...cellFeatures]);');
    expect(src).toMatch(/\.polygonLabel\(\(d: any\) => \(isCell\(d\)/);
    expect(src).toContain('describeCellStats(s.cellStats)');
    const pkg = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'package.json'), 'utf8'));
    expect(pkg.dependencies['h3-js']).toBeDefined();
  });
});
