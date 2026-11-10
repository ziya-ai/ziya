/**
 * orbitMath: SGP4 through satellite.js, coverage / elevation geometry, and the
 * scene the globe plots.  Expected values come from outside the code under
 * test: Vallado's SGP4 verification vector (catalogue 00005), the horizon
 * formula acos(R/(R+h)), and an independent visibility run over the real
 * 2026-11-04 ISS TLE (64 deg over Wallops at 09:25Z, over Kourou at 09:35Z).
 */
import { sgp4 } from 'satellite.js';

import {
  buildOrbitScene,
  centralAngleDeg,
  destinationPoint,
  elevationDeg,
  footprintHalfAngleDeg,
  geodesicCircle,
  grazingAltitudeKm,
  initialTime,
  isOrbitSpec,
  parseOrbitTime,
  parseTle,
  periodMinutes,
  prepareSatellites,
  straightSegment,
  subPoint,
  timelineConfig,
  OrbitSpec,
} from '../orbitMath';

const V5 = [
  '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753',
  '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667',
];
const ISS = [
  '1 25544U 98067A   26308.36953014  .00005228  00000+0  10236-3 0  9991',
  '2 25544  51.6329 326.3987 0007186 346.7494  13.3304 15.49173979593483',
];
const SITES = [
  { name: 'Wallops', lat: 37.94, lon: -75.47 },
  { name: 'Kourou', lat: 5.24, lon: -52.77 },
  { name: 'Svalbard', lat: 78.23, lon: 15.39 },
];
const spec = (extra: Partial<OrbitSpec> = {}): OrbitSpec => ({ type: 'orbit', satellites: [{ name: 'ISS', tle: ISS }], sites: SITES, ...extra });

describe('SGP4 through satellite.js', () => {
  it('reproduces the Vallado verification vector for 00005 at epoch', () => {
    const { sats, warnings } = prepareSatellites({ type: 'orbit', satellites: [{ name: 'V5', tle: V5 }] });
    expect(warnings).toEqual([]);
    const pv: any = sgp4(sats[0].satrec, 0);
    expect(pv.position.x).toBeCloseTo(7022.46529266, 6);
    expect(pv.position.y).toBeCloseTo(-1400.08296755, 6);
    expect(pv.position.z).toBeCloseTo(0.03995155, 6);
    expect(pv.velocity.x).toBeCloseTo(1.893841015, 6);
    expect(pv.velocity.y).toBeCloseTo(6.405893759, 6);
    expect(pv.velocity.z).toBeCloseTo(4.53480725, 6);
  });

  it('defaults the scene time to the first TLE epoch', () => {
    const s: OrbitSpec = { type: 'orbit', satellites: [{ tle: V5 }] };
    expect(initialTime(s, prepareSatellites(s)).toISOString()).toBe('2000-06-27T18:50:19.733Z');
  });

  it('keeps the ISS ground track inside its inclination and altitude band', () => {
    const { sats } = prepareSatellites(spec());
    const satrec = sats[0].satrec;
    const P = periodMinutes(satrec);
    expect(P).toBeGreaterThan(92);
    expect(P).toBeLessThan(94);
    const t0 = Date.parse('2026-11-04T09:00:00Z');
    for (let m = 0; m <= P; m += 2) {
      const p = subPoint(satrec, new Date(t0 + m * 60000));
      expect(p).not.toBeNull();
      expect(Math.abs(p!.lat)).toBeLessThanOrEqual(51.6329 + 0.25); // geodetic exceeds geocentric by <0.2 deg
      expect(p!.altKm).toBeGreaterThan(380);
      expect(p!.altKm).toBeLessThan(460);
    }
  });
});

describe('coverage geometry', () => {
  it('matches the horizon formula acos(R/(R+h)) at zero elevation', () => {
    expect(footprintHalfAngleDeg(550, 0)).toBeCloseTo((Math.acos(6371 / 6921) * 180) / Math.PI, 9);
  });

  it.each([[550, 0], [550, 25], [1200, 10], [35786, 5]])('h=%p km, eps=%p deg: the circle edge sits exactly at the minimum elevation', (h, e) => {
    const edge = destinationPoint(10, 20, 37, footprintHalfAngleDeg(h, e));
    expect(elevationDeg(edge.lat, edge.lng, { lat: 10, lng: 20, altKm: h })).toBeCloseTo(e, 6);
  });

  it('is zero at zenith and grows with altitude', () => {
    expect(footprintHalfAngleDeg(550, 90)).toBeCloseTo(0, 9);
    expect(footprintHalfAngleDeg(1200, 10)).toBeGreaterThan(footprintHalfAngleDeg(550, 10));
  });

  it('draws a closed circle whose every point is the requested arc from the centre', () => {
    const pts = geodesicCircle(60, 170, 20);
    expect(pts[0].lat).toBeCloseTo(pts[pts.length - 1].lat, 9);
    for (const p of pts) expect(centralAngleDeg(60, 170, p.lat, p.lng)).toBeCloseTo(20, 6);
  });

  it('keeps a nadir beam vertical and its endpoints exact', () => {
    const seg = straightSegment({ lat: 10, lng: 20, alt: 0.1 }, { lat: 10, lng: 20, alt: 0 });
    expect(seg[0].alt).toBeCloseTo(0.1, 9);
    expect(seg[seg.length - 1].alt).toBeCloseTo(0, 9);
    for (const p of seg) { expect(p.lat).toBeCloseTo(10, 9); expect(p.lng).toBeCloseTo(20, 9); }
  });
});

describe('scene', () => {
  const linkNames = (s: OrbitSpec, iso: string) =>
    buildOrbitScene(s, prepareSatellites(s), new Date(iso)).paths.filter((p) => p.kind === 'link').map((p) => p.name);

  it('autoLinks reach only the sites that see the satellite at that moment', () => {
    const s = spec({ autoLinks: { minElevationDeg: 10 } });
    expect(linkNames(s, '2026-11-04T09:25:00Z')).toEqual(['ISS → Wallops']);
    expect(linkNames(s, '2026-11-04T09:35:00Z')).toEqual(['ISS → Kourou']);
    expect(linkNames(s, '2026-11-04T09:30:00Z')).toEqual([]);
  });

  it('a satellite-site link reports range, light time and elevation', () => {
    const s = spec({ links: [{ from: 'ISS', to: 'Wallops' }] });
    const link = buildOrbitScene(s, prepareSatellites(s), new Date('2026-11-04T09:25:00Z')).paths.find((p) => p.kind === 'link')!;
    expect(link.tooltip).toMatch(/range [\d,]+ km, [\d.]+ ms one-way at c/);
    const el = Number(/elevation ([\d.]+)°/.exec(link.tooltip)![1]);
    expect(el).toBeGreaterThan(60);
    expect(el).toBeLessThan(68);
  });

  it('draws footprint, orbit and ground track only as requested', () => {
    // Per-satellite options are read from the prepared satellites, so the
    // scene and prepareSatellites must be given the same spec.
    const noOrbit = spec({ satellites: [{ name: 'ISS', tle: ISS, orbit: false }] });
    const plain = buildOrbitScene(noOrbit, prepareSatellites(noOrbit), new Date('2026-11-04T09:25:00Z'));
    expect(plain.paths).toEqual([]);
    expect(plain.labels.map((l) => l.name)).toContain('ISS');
    const s = spec({ satellites: [{ name: 'ISS', tle: ISS, footprint: true, groundTrack: true }] });
    const kinds = buildOrbitScene(s, prepareSatellites(s), new Date('2026-11-04T09:25:00Z')).paths.map((p) => p.kind).sort();
    expect(kinds).toEqual(['footprint', 'groundTrack', 'orbit']);
  });

  it('reports an unknown endpoint and a bad TLE without dropping the rest', () => {
    const s = spec({
      satellites: [{ name: 'ISS', tle: ISS }, { name: 'junk', tle: ['1 garbage', '2 garbage'] }],
      links: [{ from: 'ISS', to: 'Nowhere' }],
    });
    const scene = buildOrbitScene(s, prepareSatellites(s), new Date('2026-11-04T09:25:00Z'));
    expect(scene.warnings.join('\n')).toMatch(/junk: "tle" must hold/);
    expect(scene.warnings.join('\n')).toMatch(/unknown endpoint "Nowhere"/);
    expect(scene.labels.map((l) => l.name)).toEqual(['ISS', 'Wallops', 'Kourou', 'Svalbard']);
  });

  it('escapes names in the HTML tooltips', () => {
    const s: OrbitSpec = { type: 'orbit', sites: [{ name: '<img src=x onerror=alert(1)>', lat: 0, lon: 0 }] };
    const label = buildOrbitScene(s, prepareSatellites(s), new Date()).labels[0];
    expect(label.tooltip).not.toContain('<img');
    expect(label.tooltip).toContain('&lt;img');
  });
});

describe('spec parsing', () => {
  it('reads a TLE as two lines, three lines, or one string', () => {
    expect(parseTle(ISS)).toEqual({ line1: ISS[0], line2: ISS[1] });
    expect(parseTle(['ISS (ZARYA)', ...ISS])!.name).toBe('ISS (ZARYA)');
    expect(parseTle(`ISS\n${ISS[0]}\n${ISS[1]}\n`)!.line2).toBe(ISS[1]);
    expect(parseTle([ISS[0]])).toBeNull();
    expect(parseTle(['1 garbage', '2 garbage'])).toBeNull();
  });

  it('rejects an unparseable time with a usable message', () => {
    expect(() => parseOrbitTime('tomorrow-ish')).toThrow(/ISO 8601/);
  });

  it('accepts the render_diagram {type, definition} envelope and nothing else', () => {
    expect(isOrbitSpec(spec())).toBe(true);
    expect(isOrbitSpec({ type: 'orbit', definition: JSON.stringify({ sites: SITES }) })).toBe(true);
    expect(isOrbitSpec({ type: 'chord', matrix: [[1]] })).toBe(false);
    expect(isOrbitSpec({ type: 'orbit' })).toBe(false);
  });

  it('a timeline defaults to one orbital period', () => {
    const s = spec({ timeline: {} });
    const tl = timelineConfig(s, prepareSatellites(s))!;
    expect(tl.minutes).toBeGreaterThan(92);
    expect(tl.stepSeconds).toBe(60);
    expect(timelineConfig(spec(), prepareSatellites(spec()))).toBeNull();
  });
});

describe('line of sight', () => {
  const K: Record<string, string[]> = {
    K8: ['1 63724U 25088A   26308.73704461 -.00000332  00000+0 -14504-4 0  9993', '2 63724  51.8992 106.5223 0001051 112.5668 247.5405 14.79929608 83590'],
    K9: ['1 63725U 25088B   26308.66670139 -.00026368  00000+0 -34421-2 0  9995', '2 63725  51.9030 108.1474 0002873 109.1118 288.1977 14.79943656  5550'],
    K11: ['1 63727U 25088D   26308.66670139 -.00026221  00000+0 -34233-2 0  9996', '2 63727  51.8985 109.4831 0000836 113.6815 245.6297 14.79941026  3271'],
  };

  it('grazingAltitudeKm: a nadir beam bottoms out at the surface, an antipodal chord at the centre', () => {
    expect(grazingAltitudeKm({ lat: 0, lng: 0, alt: 0.1 }, { lat: 0, lng: 0, alt: 0 })).toBeCloseTo(0, 6);
    expect(grazingAltitudeKm({ lat: 0, lng: 0, alt: 0.1 }, { lat: 0, lng: 180, alt: 0.1 })).toBeCloseTo(-6371, 3);
  });

  it('flags a crosslink the Earth blocks and passes one that clears it', () => {
    const s: OrbitSpec = { type: 'orbit', satellites: ['K8', 'K9', 'K11'].map((n) => ({ name: n, tle: K[n], orbit: false })),
      links: [{ from: 'K8', to: 'K9' }, { from: 'K8', to: 'K11' }] };
    const scene = buildOrbitScene(s, prepareSatellites(s), new Date('2026-11-04T16:00:00Z'));
    const [blocked, clear] = scene.paths.filter((p) => p.kind === 'link');
    expect(blocked.blocked).toBe(true);
    expect(blocked.tooltip).toMatch(/blocked by the Earth/);
    expect(clear.blocked).toBe(false);
    expect(clear.tooltip).toMatch(/clears the surface by [\d,]+ km/);
    const w = scene.warnings.join('\n');
    expect(w).toMatch(/link 1 \(K8 → K9\): no line of sight/);
    expect(w).not.toMatch(/link 2/);
  });

  it('flags a beam to a site below the horizon but not one above it', () => {
    const s = spec({ links: [{ from: 'ISS', to: 'Wallops' }, { from: 'ISS', to: 'Svalbard' }] });
    const scene = buildOrbitScene(s, prepareSatellites(s), new Date('2026-11-04T09:25:00Z'));
    const [up, down] = scene.paths.filter((p) => p.kind === 'link');
    expect(up.blocked).toBe(false);
    expect(down.blocked).toBe(true);
    expect(down.tooltip).toMatch(/below the horizon/);
  });
});
