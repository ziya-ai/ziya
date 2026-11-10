/**
 * orbitLayers (partitions, apertures, the Sun, delay by medium) and the scene
 * features built on them: routes, aperture cones, the terminator, cell
 * colouring by partition or group.
 *
 * Expected values come from outside the code under test:
 *   - partition: the property itself (no two H3 neighbours share a group),
 *     checked against h3-js's own adjacency (gridDisk(cell, 1));
 *   - aperture: the flat-Earth spot formulas 2 h tan(theta) at nadir and
 *     2 rho tan(theta) / sin(elevation) x 2 rho tan(theta) off nadir, which the
 *     ray-traced spot must approach for a small cone;
 *   - Sun: the published declination for 4 Nov (-15.5 deg) and equation of
 *     time (+16.4 min, so the subsolar point is 4.1 deg west at 12:00 UTC),
 *     and 23.44 deg at the June solstice;
 *   - routes: the Seattle-London great circle (~7,700 km) and fibre's group
 *     index 1.4677; the GEO relay path, 81,236 km, from an independent run.
 */
import * as fs from 'fs';
import * as path from 'path';
import { gridDisk, latLngToCell } from 'h3-js';

import {
  apertureSpot,
  delayMs,
  groupColors,
  inEarthShadow,
  partitionCells,
  speedFraction,
  subsolarPoint,
  toEcef,
  FIBER_GROUP_INDEX,
  PARTITION_PALETTE,
} from '../orbitLayers';
import { buildOrbitScene, describeCellStats, prepareCells, prepareSatellites, withAlpha, OrbitSpec } from '../orbitMath';
import { formatRoute } from '../orbitPlugin';

const R = 6371;
const D2R = Math.PI / 180;
const ISS = [
  '1 25544U 98067A   26308.36953014  .00005228  00000+0  10236-3 0  9991',
  '2 25544  51.6329 326.3987 0007186 346.7494  13.3304 15.49173979593483',
];
const scene = (s: OrbitSpec, iso?: string) =>
  buildOrbitScene(s, prepareSatellites(s), new Date(iso || (s.time as string) || '2026-11-04T16:00:00Z'), undefined, prepareCells(s));

describe('partitionCells', () => {
  const disk = gridDisk(latLngToCell(47.61, -122.33, 6), 6);
  const inSet = new Set(disk);

  it.each([3, 4, 7])('k=%p: every cell gets a group and no two neighbours share one', (k) => {
    const { index, warnings } = partitionCells(disk, k);
    expect(warnings).toEqual([]);
    expect(index.size).toBe(disk.length);
    expect(new Set(index.values()).size).toBe(k);
    for (const c of disk) {
      for (const n of gridDisk(c, 1)) {
        if (n !== c && inSet.has(n)) expect(index.get(n)).not.toBe(index.get(c));
      }
    }
  });

  it('refuses 2 (a hexagon grid needs at least 3) and anything past the palette', () => {
    expect(partitionCells(disk, 2).warnings.join()).toMatch(/from 3 to 12/);
    expect(partitionCells(disk, 2).index.size).toBe(0);
    expect(partitionCells(disk, 13).warnings.join()).toMatch(/from 3 to 12/);
  });

  it('still groups a cell H3 cannot place on the local grid', () => {
    const far = latLngToCell(-33.87, 151.21, 6);
    const { index } = partitionCells([...disk, far], 3);
    expect(index.get(far)).toBeGreaterThanOrEqual(0);
    expect(index.get(far)).toBeLessThan(3);
  });

  it('groupColors assigns palette colours in first-seen order', () => {
    const m = groupColors(['b', undefined, 'a', 'b']);
    expect(Array.from(m.entries())).toEqual([['b', PARTITION_PALETTE[0]], ['a', PARTITION_PALETTE[1]]]);
  });
});

describe('apertureSpot', () => {
  const h = 550;
  const apex = toEcef(0, 0, R + h);

  it('a 2 deg nadir cone from 550 km gives a 38.4 km round spot (2 h tan theta)', () => {
    const spot = apertureSpot(apex, toEcef(0, 0, R), 2);
    const flat = 2 * h * Math.tan(2 * D2R);
    expect(spot.missed).toBe(0);
    expect(spot.ring).toHaveLength(96);
    expect(spot.alongKm).toBeCloseTo(flat, 1);
    expect(spot.crossKm).toBeCloseTo(flat, 1);
  });

  it('aimed at 30 deg elevation the spot stretches by 1/sin(elevation) along the look direction', () => {
    // Ground angle to 30 deg elevation, from the footprint formula, and the slant range.
    const g = (60 - Math.asin((R * Math.cos(30 * D2R)) / (R + h)) / D2R) * D2R;
    const rho = Math.sqrt(R * R + (R + h) ** 2 - 2 * R * (R + h) * Math.cos(g));
    const spot = apertureSpot(apex, toEcef(0, g / D2R, R), 2);
    const cross = 2 * rho * Math.tan(2 * D2R);
    expect(Math.abs(spot.crossKm / cross - 1)).toBeLessThan(0.01);
    expect(Math.abs(spot.alongKm / (cross / Math.sin(30 * D2R)) - 1)).toBeLessThan(0.015);
  });

  it('a cone wider than the horizon reports the rays that miss the Earth', () => {
    expect(apertureSpot(apex, toEcef(0, 7.14, R), 40).missed).toBeGreaterThan(0);
  });
});

describe('the Sun', () => {
  it('subsolar point at 12:00 UTC on 4 Nov: declination -15.5, 4.1 deg west (equation of time)', () => {
    const p = subsolarPoint(new Date('2026-11-04T12:00:00Z'));
    expect(Math.abs(p.lat - -15.46)).toBeLessThan(0.15);
    expect(Math.abs(p.lng - -4.1)).toBeLessThan(0.2);
    expect(Math.abs(subsolarPoint(new Date('2026-06-21T12:00:00Z')).lat - 23.44)).toBeLessThan(0.05);
  });

  it('inEarthShadow is the cylinder behind the Earth', () => {
    const sun = { x: 1.5e8, y: 0, z: 0 };
    expect(inEarthShadow({ x: -7000, y: 0, z: 0 }, sun)).toBe(true);
    expect(inEarthShadow({ x: -7000, y: 7000, z: 0 }, sun)).toBe(false);
    expect(inEarthShadow({ x: 7000, y: 0, z: 0 }, sun)).toBe(false);
  });

  it('sun: true draws the terminator and subsolar point and lights each object', () => {
    const s: OrbitSpec = { type: 'orbit', time: '2026-11-04T09:25:00Z', sun: true, satellites: [{ name: 'ISS', tle: ISS }] };
    const sc = scene(s);
    const term = sc.paths.find((p) => p.kind === 'terminator')!;
    const sub = subsolarPoint(new Date('2026-11-04T09:25:00Z'));
    // Every terminator point is 90 deg from the subsolar point.
    for (const p of term.points.slice(0, 20)) {
      const c = Math.sin(p.lat * D2R) * Math.sin(sub.lat * D2R) + Math.cos(p.lat * D2R) * Math.cos(sub.lat * D2R) * Math.cos((p.lng - sub.lng) * D2R);
      expect(Math.abs(c)).toBeLessThan(1e-6);
    }
    expect(sc.labels.map((l) => l.name)).toContain('Subsolar point');
    expect(sc.labels.find((l) => l.name === 'ISS')!.tooltip).toMatch(/sunlit|in Earth's shadow/);
  });
});

describe('delay by medium', () => {
  it('fibre is c / 1.4677, vacuum c, a number is a fraction of c', () => {
    expect(speedFraction('fiber')).toBeCloseTo(1 / FIBER_GROUP_INDEX, 12);
    expect(speedFraction('fibre')).toBeCloseTo(1 / FIBER_GROUP_INDEX, 12);
    expect(speedFraction('vacuum')).toBe(1);
    expect(speedFraction(0.5)).toBe(0.5);
    expect(speedFraction('copper')).toBeNull();
    expect(speedFraction(2)).toBeNull();
    expect(delayMs(299792.458, 1)).toBeCloseTo(1000, 9);
  });
});

describe('routes', () => {
  const spec: OrbitSpec = {
    type: 'orbit', time: '2026-11-04T16:00:00Z',
    satellites: [{ name: 'Relay 60W', elements: { preset: 'geo', longitudeDeg: -60 }, orbit: false }],
    sites: [{ name: 'Seattle', lat: 47.61, lon: -122.33 }, { name: 'London', lat: 51.51, lon: -0.13 }],
    routes: [
      { name: 'Fibre', via: ['Seattle', 'London'] },
      { name: 'Fibre 1.3x', via: ['Seattle', 'London'], pathFactor: 1.3, extraMs: 2 },
      { name: 'GEO relay', via: ['Seattle', 'Relay 60W', 'London'] },
    ],
  };

  it('fibre on the great circle: ~7,700 km, 37.7 ms one way; a 1.3x route scales with it', () => {
    const [fibre, long] = scene(spec).routes;
    expect(Math.abs(fibre.km / 7700 - 1)).toBeLessThan(0.003);
    expect(fibre.ms).toBeCloseTo((fibre.km * FIBER_GROUP_INDEX) / 299.792458, 9);
    expect(Math.abs(fibre.ms - 37.69)).toBeLessThan(0.1);
    expect(long.km).toBeCloseTo(1.3 * fibre.km, 6);
    expect(long.ms).toBeCloseTo(1.3 * fibre.ms + 2, 6);
    expect(fibre.hops[0].medium).toBe('fiber');
  });

  it('the GEO relay: two vacuum hops, ~81,240 km, ~271 ms, line of sight from both ends', () => {
    const sc = scene(spec);
    const geo = sc.routes[2];
    expect(geo.hops.map((x) => x.medium)).toEqual(['vacuum', 'vacuum']);
    expect(Math.abs(geo.km - 81236)).toBeLessThan(150);
    expect(Math.abs(geo.ms - 271.0)).toBeLessThan(0.6);
    expect(geo.blocked).toBe(false);
    expect(sc.warnings).toEqual([]);
    expect(sc.paths.filter((p) => p.kind === 'route')).toHaveLength(4);
    const f = formatRoute(geo);
    expect(f.path).toBe('Seattle → Relay 60W → London');
    expect(f.roundTrip).toBe(`${(2 * geo.ms).toFixed(1)} ms`);
  });

  it('reports an unknown name and a bad medium without dropping the other routes', () => {
    const bad = scene({ ...spec, routes: [{ via: ['Seattle', 'Nowhere'] }, { name: 'x', via: ['Seattle', 'London'], groundMedium: 'copper' }, spec.routes![0]] });
    expect(bad.warnings.join('\n')).toMatch(/unknown name "Nowhere"/);
    expect(bad.warnings.join('\n')).toMatch(/groundMedium/);
    expect(bad.routes.map((r) => r.name)).toEqual(['Fibre']);
  });
});

describe('apertures in the scene', () => {
  const base: OrbitSpec = { type: 'orbit', time: '2026-11-04T09:25:00Z', sites: [{ name: 'Wallops', lat: 37.94, lon: -75.47 }, { name: 'Svalbard', lat: 78.23, lon: 15.39 }] };

  it('draws the spot and four cone edges, sized on hover', () => {
    const sc = scene({ ...base, satellites: [{ name: 'ISS', tle: ISS, orbit: false, aperture: { halfAngleDeg: 3, target: 'Wallops' } }] });
    const ap = sc.paths.filter((p) => p.kind === 'aperture');
    expect(ap).toHaveLength(5);
    expect(ap[0].tooltip).toMatch(/aperture 3° toward Wallops<br>spot about [\d,]+ km × [\d,]+ km/);
    expect(sc.warnings).toEqual([]);
  });

  it('a target below the horizon is a warning, not a spot', () => {
    const sc = scene({ ...base, satellites: [{ name: 'ISS', tle: ISS, orbit: false, aperture: { halfAngleDeg: 3, target: 'Svalbard' } }] });
    expect(sc.paths.filter((p) => p.kind === 'aperture')).toEqual([]);
    expect(sc.warnings.join()).toMatch(/Svalbard is below its horizon/);
  });
});

describe('cell colouring', () => {
  const sites = [{ name: 'North', lat: 47.85, lon: -122.3 }, { name: 'Central', lat: 47.6, lon: -122.33 }, { name: 'South', lat: 47.35, lon: -122.25 }];

  it('colorBy partition: adjacent cells never share a fill, the server stays in the outline and hover', () => {
    const s: OrbitSpec = {
      type: 'orbit', sites,
      cells: { resolution: 5, around: { lat: 47.6, lon: -122.3, radiusKm: 50 }, colorBy: 'partition', partition: 3 },
      assignment: { servers: 'sites', maxRangeKm: 30, capacity: 12 },
    };
    const sc = scene(s);
    const byId = new Map(sc.cells.map((c) => [c.id, c]));
    const base = (fill: string) => fill.replace(/,[\d.]+\)$/, ')');
    for (const c of sc.cells) {
      for (const n of gridDisk(c.id, 1)) {
        const o = byId.get(n);
        if (o && n !== c.id) expect(base(o.fill)).not.toBe(base(c.fill));
      }
    }
    const served = sc.cells.find((c) => c.status === 'served')!;
    expect(served.tooltip).toMatch(/served by (North|Central|South)/);
    expect(served.tooltip).toMatch(/partition [123] of 3/);
    expect(sc.cellStats!.served).toBeGreaterThan(0);
  });

  it('colorBy group: one palette colour per group value', () => {
    const s: OrbitSpec = {
      type: 'orbit',
      cells: { resolution: 5, items: [{ lat: 47.6, lon: -122.3, group: 'east' }, { lat: 45.5, lon: -122.7, group: 'west' }], colorBy: 'group' },
    };
    const sc = scene(s);
    expect(sc.cells.map((c) => c.fill)).toEqual([withAlpha(PARTITION_PALETTE[0], 0.2), withAlpha(PARTITION_PALETTE[1], 0.2)]);
    expect(sc.cells[0].tooltip).toMatch(/group east/);
  });

  it('the footer speaks of capacity and load, not of any one technology', () => {
    const s: OrbitSpec = { type: 'orbit', sites, cells: { resolution: 5, around: { lat: 47.6, lon: -122.3, radiusKm: 50 } }, assignment: { servers: 'sites', maxRangeKm: 30, capacity: 1 } };
    expect(describeCellStats(scene(s).cellStats!)).toMatch(/^cells: 3\/\d+ served · \d+ at capacity · \d+ out of reach · load /);
  });
});

describe('the plugin shows the route table', () => {
  it('renders formatRoute rows into an orbit-routes box', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../orbitPlugin.ts'), 'utf8');
    expect(src).toContain("routeBox.className = 'orbit-routes';");
    expect(src).toMatch(/s\.routes\.forEach\(\(r\) => \{\s*const f = formatRoute\(r\);/);
  });
});
