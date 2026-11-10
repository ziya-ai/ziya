/**
 * orbitSources: orbits from elements and presets (GEO, MEO, Molniya,
 * sun-synchronous), Walker constellations, the Moon, the Sun and Lagrange
 * points.  Expected values come from outside the code under test: the GEO
 * period (1436.07 min), the Molniya apogee for a = 26,600 km, e = 0.74, the
 * 700 km sun-synchronous inclination (98.19 deg), the 5 Nov 2025 full moon
 * (13:19 UTC, 356,980 km), Earth-Moon L1/L2 at 0.849 / 1.168 of the
 * separation, Sun-Earth L1/L2 at 1.49 / 1.50 million km, and an independent
 * run of the Walker 48/6/1 fixture over the Seattle H3 disc.
 */
import {
  elementsPropagator,
  j2RatesDegPerDay,
  lagrangePoints,
  moonPositionKm,
  normalizeElements,
  sunPositionKm,
  sunSynchronousInclinationDeg,
  walkerConstellation,
  ELEMENT_PRESETS,
  EARTH_EQUATORIAL_RADIUS_KM,
  MOON_RADIUS_KM,
} from '../orbitSources';
import {
  buildOrbitScene,
  prepareCells,
  prepareSatellites,
  subPoint,
  EARTH_RADIUS_KM,
  OrbitSpec,
} from '../orbitMath';

const EPOCH = new Date('2026-11-04T16:00:00Z');
const norm = (v: { x: number; y: number; z: number }) => Math.hypot(v.x, v.y, v.z);
// Clamped: two exactly collinear vectors can give a cosine a rounding step above 1.
const angleDeg = (a: { x: number; y: number; z: number }, b: { x: number; y: number; z: number }) =>
  (Math.acos(Math.max(-1, Math.min(1, (a.x * b.x + a.y * b.y + a.z * b.z) / (norm(a) * norm(b))))) * 180) / Math.PI;

describe('elements', () => {
  it('a GEO slot stays over its longitude for a day', () => {
    const p = elementsPropagator({ preset: 'geo', longitudeDeg: -75 }, EPOCH);
    expect(p.periodMinutes).toBeCloseTo(1436.07, 1);
    for (const h of [0, 6, 12, 24]) {
      const sp = subPoint(p, new Date(EPOCH.getTime() + h * 3600e3))!;
      expect(sp.lat).toBeCloseTo(0, 2);
      // altitudeKm 35786 gives a = 42164.137 km, ~0.03 km short of exact
      // sidereal synchronism, so the slot drifts ~0.007 deg east per day.
      expect(sp.lng).toBeCloseTo(-75, 1);
      expect(sp.altKm).toBeCloseTo(35786, 0);
    }
  });

  it('the Molniya preset has a 12 h period, a 39,900 km apogee and reaches 63.4 deg', () => {
    const p = elementsPropagator({ preset: 'molniya' }, EPOCH);
    expect(p.periodMinutes).toBeCloseTo(719.6, 0);
    let maxAlt = 0;
    let maxLat = 0;
    for (let m = 0; m <= 720; m += 2) {
      const sp = subPoint(p, new Date(EPOCH.getTime() + m * 60000))!;
      maxAlt = Math.max(maxAlt, sp.altKm);
      maxLat = Math.max(maxLat, sp.lat);
    }
    expect(maxAlt).toBeGreaterThan(39700);
    expect(maxAlt).toBeLessThan(40000);
    expect(maxLat).toBeCloseTo(63.4, 0);
  });

  it('sun-synchronous: 98.19 deg at 700 km, and the node precesses 0.9856 deg/day under J2', () => {
    expect(sunSynchronousInclinationDeg(EARTH_RADIUS_KM + 700)).toBeCloseTo(98.19, 1);
    const el = normalizeElements({ altitudeKm: 700, sunSynchronous: true }, EPOCH);
    expect(el.inclinationDeg).toBeCloseTo(98.19, 1);
    expect(j2RatesDegPerDay(el).raan).toBeCloseTo(360 / 365.2422, 2);
    // Non-polar circular orbits regress; polar ones do not.
    expect(j2RatesDegPerDay(normalizeElements({ altitudeKm: 550, inclinationDeg: 53 }, EPOCH)).raan).toBeLessThan(-4);
    expect(j2RatesDegPerDay(normalizeElements({ altitudeKm: 550, inclinationDeg: 90 }, EPOCH)).raan).toBeCloseTo(0, 6);
  });

  it('accepts perigee/apogee, true anomaly and a per-satellite epoch', () => {
    const el = normalizeElements({ periapsisKm: 500, apoapsisKm: 39500, inclinationDeg: 63.4, argPerigeeDeg: 270, trueAnomalyDeg: 180, epoch: '2026-01-01T00:00:00Z' }, EPOCH);
    expect(el.a).toBeCloseTo(EARTH_EQUATORIAL_RADIUS_KM + 20000, 3);
    expect(el.e).toBeCloseTo((39500 - 500) / (2 * EARTH_EQUATORIAL_RADIUS_KM + 40000), 9);
    expect(el.meanAnomalyDeg).toBeCloseTo(180, 6); // apogee is apogee in every anomaly
    expect(el.epoch.toISOString()).toBe('2026-01-01T00:00:00.000Z');
  });

  it('refuses an orbit that hits the ground, an unknown preset and a GEO slot without longitude', () => {
    expect(() => normalizeElements({ altitudeKm: 50 }, EPOCH)).toThrow(/perigee/);
    expect(() => normalizeElements({ semiMajorAxisKm: 26600, eccentricity: 0.9 }, EPOCH)).toThrow(/perigee/);
    expect(() => normalizeElements({ preset: 'warp' }, EPOCH)).toThrow(/preset.*geo, gps/);
    expect(() => normalizeElements({ preset: 'geo' }, EPOCH)).toThrow(/longitudeDeg/);
    expect(Object.keys(ELEMENT_PRESETS)).toEqual(expect.arrayContaining(['geo', 'gps', 'galileo', 'glonass', 'molniya', 'tundra', 'sso', 'polar']));
  });
});

describe('Walker constellations', () => {
  const def = { name: 'demo', total: 48, planes: 6, phasing: 1, altitudeKm: 1200, inclinationDeg: 53 };

  it('builds total satellites over evenly spaced planes with the inter-plane phase offset', () => {
    const { satellites, warnings } = walkerConstellation(def);
    expect(warnings).toEqual([]);
    expect(satellites).toHaveLength(48);
    expect(satellites[0].name).toBe('demo-1-1');
    expect(satellites[47].name).toBe('demo-6-8');
    const raans = Array.from(new Set(satellites.map((s) => s.elements!.raanDeg))).sort((a, b) => a! - b!);
    expect(raans).toEqual([0, 60, 120, 180, 240, 300]);
    expect(satellites[8].elements!.meanAnomalyDeg).toBeCloseTo(360 / 48, 9); // plane 2, slot 1: F * 360 / T
    expect(satellites[1].elements!.meanAnomalyDeg).toBeCloseTo(45, 9);
  });

  it('a star pattern spreads the planes over 180 degrees', () => {
    const raans = new Set(walkerConstellation({ ...def, pattern: 'star' }).satellites.map((s) => s.elements!.raanDeg));
    expect(Array.from(raans).sort((a, b) => a! - b!)).toEqual([0, 30, 60, 90, 120, 150]);
  });

  it('rejects an uneven split and an out-of-range phasing with a warning, not a throw', () => {
    expect(walkerConstellation({ ...def, total: 50 }).warnings.join()).toMatch(/divisible/);
    expect(walkerConstellation({ ...def, phasing: 6 }).warnings.join()).toMatch(/phasing/);
    expect(walkerConstellation({ ...def, total: 50 }).satellites).toEqual([]);
  });

  it('the first satellite is at its ascending node at the epoch', () => {
    const spec: OrbitSpec = { type: 'orbit', time: EPOCH.toISOString(), constellations: [def] };
    const { sats, warnings } = prepareSatellites(spec);
    expect(warnings).toEqual([]);
    expect(sats).toHaveLength(48);
    expect(subPoint(sats[0].propagator, EPOCH)!.lat).toBeCloseTo(0, 3);
    expect(subPoint(sats[0].propagator, EPOCH)!.altKm).toBeCloseTo(1200, 0);
  });

  it('serves the Seattle disc at epoch: two full satellites plus two overflow cells (independent capped run)', () => {
    const spec: OrbitSpec = {
      type: 'orbit', time: EPOCH.toISOString(), constellations: [{ ...def, orbit: false }],
      cells: { resolution: 3, around: { lat: 47.61, lon: -122.33, radiusKm: 600 } },
      beams: { maxBeamsPerSatellite: 16, minElevationDeg: 35 },
    };
    const scene = buildOrbitScene(spec, prepareSatellites(spec), EPOCH, undefined, prepareCells(spec));
    expect(scene.cellStats).toMatchObject({ total: 103, served: 34, noCapacity: 69, notVisible: 0 });
    expect(scene.cellStats!.beamsUsed).toEqual({ 'demo-2-3': 16, 'demo-3-2': 16, 'demo-1-4': 2 });
  });
});

describe('Moon, Sun and Lagrange points', () => {
  it('matches the 5 Nov 2025 full moon: opposite the Sun, 357,000 km away', () => {
    const t = new Date('2025-11-05T13:19:00Z');
    const m = moonPositionKm(t);
    expect(norm(m)).toBeGreaterThan(356000);
    expect(norm(m)).toBeLessThan(359000);
    expect(angleDeg(m, sunPositionKm(t))).toBeGreaterThan(174);
    expect(angleDeg(moonPositionKm(new Date('2026-11-09T07:00:00Z')), sunPositionKm(new Date('2026-11-09T07:00:00Z')))).toBeLessThan(6); // new moon
    expect(norm(sunPositionKm(t)) / 149597870.7).toBeCloseTo(0.99, 1); // early November: ~0.992 AU
  });

  it('Earth-Moon L1/L2/L3 sit at 0.849 / 1.168 / 0.993 of the separation; L4/L5 complete equilateral triangles', () => {
    const { points, separationKm, secondary } = lagrangePoints('earth-moon', EPOCH);
    expect(separationKm).toBeCloseTo(norm(secondary), 6);
    expect(norm(points.L1) / separationKm).toBeCloseTo(0.8491, 3);
    expect(norm(points.L2) / separationKm).toBeCloseTo(1.1678, 3);
    expect(norm(points.L3) / separationKm).toBeCloseTo(0.9929, 3);
    expect(angleDeg(points.L1, secondary)).toBeCloseTo(0, 6);
    expect(angleDeg(points.L3, secondary)).toBeCloseTo(180, 6);
    for (const L of [points.L4, points.L5]) {
      expect(norm(L) / separationKm).toBeCloseTo(1, 6);
      expect(Math.hypot(L.x - secondary.x, L.y - secondary.y, L.z - secondary.z) / separationKm).toBeCloseTo(1, 6);
    }
    expect(angleDeg(points.L4, points.L5)).toBeCloseTo(120, 6);
  });

  it('Sun-Earth L1/L2 sit 0.997% / 1.004% of the Sun distance from Earth, toward and away from the Sun', () => {
    // 1.4915 / 1.5015 million km at exactly 1 AU; on 4 Nov the Sun is ~0.9917 AU
    // away, so the points are proportionally nearer (~1.479 / 1.489 Mkm).
    const { points, separationKm } = lagrangePoints('sun-earth', EPOCH);
    expect(separationKm / 149597870.7).toBeGreaterThan(0.990);
    expect(separationKm / 149597870.7).toBeLessThan(0.993);
    // Reference ratios used mu = 3.003e-6; the code uses 3.0035e-6, and the
    // collinear distances scale as mu^(1/3): a 5e-5 relative difference.
    expect(norm(points.L1) / separationKm).toBeCloseTo(1491472 / 149597870.7, 5);
    expect(norm(points.L2) / separationKm).toBeCloseTo(1501451 / 149597870.7, 5);
    expect(angleDeg(points.L1, sunPositionKm(EPOCH))).toBeCloseTo(0, 4);
    expect(angleDeg(points.L2, sunPositionKm(EPOCH))).toBeCloseTo(180, 4);
  });

  it('bodies appear in the scene as labels, link endpoints and a drawn sphere', () => {
    const spec: OrbitSpec = {
      type: 'orbit', bodies: { moon: true, lagrange: ['earth-moon'] },
      sites: [{ name: 'Svalbard', lat: 78.23, lon: 15.39 }],
      links: [{ from: 'Svalbard', to: 'Moon' }, { from: 'EM-L1', to: 'EM-L2' }],
    };
    const scene = buildOrbitScene(spec, prepareSatellites(spec), EPOCH);
    expect(scene.labels.map((l) => l.name)).toEqual(['Svalbard', 'Moon', 'EM-L1', 'EM-L2', 'EM-L3', 'EM-L4', 'EM-L5']);
    expect(scene.bodies).toHaveLength(1);
    expect(scene.bodies[0]).toMatchObject({ name: 'Moon', radius: MOON_RADIUS_KM / EARTH_RADIUS_KM });
    expect(scene.bodies[0].alt).toBeGreaterThan(55);
    expect(scene.farthestAlt).toBeGreaterThan(65); // EM-L2 at ~449,000 km
    const link = scene.paths.find((p) => p.name === 'Svalbard → Moon')!;
    expect(link.tooltip).toMatch(/range 3\d\d,\d\d\d km, 1\.2\d s one-way at c/);
    expect(scene.paths.find((p) => p.name === 'EM-L1 → EM-L2')!.blocked).toBe(false);
    // The Moon may be below Svalbard's horizon at this instant; that warning is correct, not noise.
    expect(scene.warnings.filter((w) => !/line of sight/.test(w))).toEqual([]);
  });

  it('Sun-Earth L4/L5 come with a warning that they are an AU away', () => {
    const spec: OrbitSpec = { type: 'orbit', bodies: { lagrange: [{ system: 'sun-earth', points: ['L2', 'L4'] }] } };
    const scene = buildOrbitScene(spec, prepareSatellites(spec), EPOCH);
    expect(scene.labels.map((l) => l.name)).toEqual(['SE-L2', 'SE-L4']);
    expect(scene.warnings.join()).toMatch(/SE-L4.*astronomical unit/);
  });
});

describe('ground servers for cells', () => {
  it('a site can serve cells within range, nearest first, under its capacity', () => {
    const spec: OrbitSpec = {
      type: 'orbit', sites: [{ name: 'Seattle', lat: 47.61, lon: -122.33 }],
      cells: { resolution: 3, around: { lat: 47.61, lon: -122.33, radiusKm: 600 } },
      beams: { servers: 'sites', maxRangeKm: 200, capacity: 3 },
    };
    const scene = buildOrbitScene(spec, prepareSatellites(spec), EPOCH, undefined, prepareCells(spec));
    expect(scene.cellStats).toMatchObject({ served: 3, maxBeamsPerSatellite: 3 });
    expect(scene.cellStats!.beamsUsed).toEqual({ Seattle: 3 });
    const served = scene.cells.filter((c) => c.status === 'served');
    expect(served.every((c) => /served by Seattle, [\d,]+ km away/.test(c.tooltip))).toBe(true);
    expect(scene.cells.find((c) => c.status === 'not-visible')!.tooltip).toMatch(/no server within 200 km/);
  });
});
