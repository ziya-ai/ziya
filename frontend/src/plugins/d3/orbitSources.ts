/**
 * Orbit sources for the orbit fence other than TLEs: Keplerian elements and
 * presets (GEO, MEO, Molniya, sun-synchronous), Walker constellations, and
 * the Moon, the Sun and the Lagrange points of the Earth-Moon and Sun-Earth
 * systems.  Everything is Earth-centred inertial in kilometres (TEME for
 * TLEs, mean equator of date for the rest; the difference is far below a
 * TLE's own error).  Pure functions, no three.js, so jest covers it.
 */
import { gstime, propagate, sunPos, twoline2satrec } from 'satellite.js';

type SatRec = ReturnType<typeof twoline2satrec>;

export const MU_EARTH_KM3_S2 = 398600.4418;
export const EARTH_EQUATORIAL_RADIUS_KM = 6378.137;
export const J2 = 1.08262668e-3;
export const AU_KM = 149597870.7;
export const MOON_RADIUS_KM = 1737.4;
/** Secondary mass over total mass, per system. */
const MASS_RATIO: Record<LagrangeSystem, number> = { 'earth-moon': 0.012150585, 'sun-earth': 3.0034896e-6 };
const MIN_PERIGEE_ALT_KM = 100;
const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const JD_UNIX_EPOCH = 2440587.5;
const JD_J2000 = 2451545.0;
const TROPICAL_YEAR_S = 365.2422 * 86400;

export interface EciVec { x: number; y: number; z: number; }

/** One object's motion: where it is at a date, how long one revolution takes. */
export interface Propagator {
  kind: 'tle' | 'elements';
  epoch: Date;
  periodMinutes: number;
  position(date: Date): EciVec | null;
}

export interface OrbitElementsDef {
  preset?: string;
  epoch?: string | number;
  semiMajorAxisKm?: number;
  altitudeKm?: number;
  periapsisKm?: number;
  apoapsisKm?: number;
  eccentricity?: number;
  inclinationDeg?: number;
  raanDeg?: number;
  argPerigeeDeg?: number;
  meanAnomalyDeg?: number;
  trueAnomalyDeg?: number;
  /** Sub-satellite longitude at the epoch; sets RAAN.  The way to place a GEO slot. */
  longitudeDeg?: number;
  sunSynchronous?: boolean;
  /** Apply J2 secular drift of the node and perigee (default true). */
  j2?: boolean;
}

export interface NormalizedElements {
  a: number; e: number; inclinationDeg: number; raanDeg: number; argPerigeeDeg: number;
  meanAnomalyDeg: number; epoch: Date; j2: boolean;
}

export const ELEMENT_PRESETS: Record<string, Partial<OrbitElementsDef> & { note: string }> = {
  geo: { altitudeKm: 35786, inclinationDeg: 0, note: 'geostationary; give longitudeDeg for the slot' },
  gps: { altitudeKm: 20180, inclinationDeg: 55, note: 'GPS MEO shell, ~11 h 58 min' },
  galileo: { altitudeKm: 23222, inclinationDeg: 56, note: 'Galileo MEO shell' },
  glonass: { altitudeKm: 19130, inclinationDeg: 64.8, note: 'GLONASS MEO shell' },
  molniya: { semiMajorAxisKm: 26600, eccentricity: 0.74, inclinationDeg: 63.4, argPerigeeDeg: 270, note: '12 h highly elliptical, apogee over the north' },
  tundra: { semiMajorAxisKm: 42164, eccentricity: 0.27, inclinationDeg: 63.4, argPerigeeDeg: 270, note: '24 h highly elliptical' },
  sso: { altitudeKm: 700, sunSynchronous: true, note: 'sun-synchronous; inclination computed from the altitude' },
  polar: { altitudeKm: 800, inclinationDeg: 90, note: 'circular polar' },
};

const fin = (v: unknown): number | undefined => {
  const n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
  return typeof n === 'number' && Number.isFinite(n) ? n : undefined;
};
const norm360 = (d: number) => ((d % 360) + 360) % 360;
export const dateToJd = (d: Date): number => d.getTime() / 86400000 + JD_UNIX_EPOCH;

export function meanMotionRadS(aKm: number): number {
  return Math.sqrt(MU_EARTH_KM3_S2 / (aKm * aKm * aKm));
}

/** Inclination whose J2 node regression equals one revolution per tropical year. */
export function sunSynchronousInclinationDeg(aKm: number, e = 0): number {
  const p = aKm * (1 - e * e);
  const want = (2 * Math.PI) / TROPICAL_YEAR_S;
  const cosI = -want / (1.5 * meanMotionRadS(aKm) * J2 * (EARTH_EQUATORIAL_RADIUS_KM / p) ** 2);
  if (!(Math.abs(cosI) <= 1)) throw new Error(`no sun-synchronous inclination exists for a = ${Math.round(aKm)} km`);
  return Math.acos(cosI) * R2D;
}

/** Secular J2 rates of the node and the argument of perigee, degrees per day. */
export function j2RatesDegPerDay(el: { a: number; e: number; inclinationDeg: number }): { raan: number; argPerigee: number } {
  const p = el.a * (1 - el.e * el.e);
  const k = meanMotionRadS(el.a) * J2 * (EARTH_EQUATORIAL_RADIUS_KM / p) ** 2;
  const ci = Math.cos(el.inclinationDeg * D2R);
  return { raan: -1.5 * k * ci * R2D * 86400, argPerigee: 0.75 * k * (5 * ci * ci - 1) * R2D * 86400 };
}

export function trueToMeanAnomalyDeg(nuDeg: number, e: number): number {
  const nu = nuDeg * D2R;
  const E = 2 * Math.atan2(Math.sqrt(1 - e) * Math.sin(nu / 2), Math.sqrt(1 + e) * Math.cos(nu / 2));
  return norm360((E - e * Math.sin(E)) * R2D);
}

/** Validate and complete an elements block; throws a message naming what is wrong. */
export function normalizeElements(def: OrbitElementsDef, fallbackEpoch: Date): NormalizedElements {
  if (!def || typeof def !== 'object') throw new Error('"elements" must be an object');
  let merged: OrbitElementsDef = { ...def };
  const presetKey = def.preset === undefined ? undefined : String(def.preset).toLowerCase();
  if (presetKey !== undefined) {
    const preset = ELEMENT_PRESETS[presetKey];
    if (!preset) throw new Error(`unknown preset ${JSON.stringify(def.preset)}; presets are ${Object.keys(ELEMENT_PRESETS).join(', ')}`);
    const { note: _note, ...values } = preset;
    merged = { ...values, ...def };
  }
  let a: number | undefined;
  let e = fin(merged.eccentricity);
  const peri = fin(merged.periapsisKm);
  const apo = fin(merged.apoapsisKm);
  if (peri !== undefined && apo !== undefined) {
    const rp = EARTH_EQUATORIAL_RADIUS_KM + peri;
    const ra = EARTH_EQUATORIAL_RADIUS_KM + apo;
    if (ra < rp) throw new Error('apoapsisKm must not be below periapsisKm');
    a = (rp + ra) / 2;
    e = (ra - rp) / (ra + rp);
  } else if (fin(merged.semiMajorAxisKm) !== undefined) {
    a = fin(merged.semiMajorAxisKm);
  } else if (fin(merged.altitudeKm) !== undefined) {
    a = EARTH_EQUATORIAL_RADIUS_KM + fin(merged.altitudeKm)!;
  } else {
    throw new Error('elements need altitudeKm, semiMajorAxisKm, or periapsisKm + apoapsisKm');
  }
  if (e === undefined) e = 0;
  if (!(e >= 0 && e < 1)) throw new Error(`eccentricity must be in [0, 1), got ${e}`);
  if (!(a! > 0)) throw new Error('semi-major axis must be positive');
  const perigeeAlt = a! * (1 - e) - EARTH_EQUATORIAL_RADIUS_KM;
  if (perigeeAlt < MIN_PERIGEE_ALT_KM) {
    throw new Error(`perigee ${Math.round(perigeeAlt)} km is below ${MIN_PERIGEE_ALT_KM} km; the orbit would hit the atmosphere`);
  }
  let epoch = fallbackEpoch;
  if (merged.epoch !== undefined && merged.epoch !== null && merged.epoch !== '') {
    const d = new Date(merged.epoch as any);
    if (Number.isNaN(d.getTime())) throw new Error(`elements.epoch ${JSON.stringify(merged.epoch)} is not a date`);
    epoch = d;
  }
  const inclinationDeg = merged.sunSynchronous ? sunSynchronousInclinationDeg(a!, e) : (fin(merged.inclinationDeg) ?? 0);
  const argPerigeeDeg = fin(merged.argPerigeeDeg) ?? 0;
  const nu = fin(merged.trueAnomalyDeg);
  const meanAnomalyDeg = nu !== undefined ? trueToMeanAnomalyDeg(nu, e) : norm360(fin(merged.meanAnomalyDeg) ?? 0);
  let raan = fin(merged.raanDeg);
  const lon = fin(merged.longitudeDeg);
  if (lon !== undefined) {
    // Sub-point longitude = RAAN + argument of latitude - GMST (exact for a
    // circular equatorial orbit, the GEO case; a few degrees off otherwise).
    raan = gstime(epoch) * R2D + lon - argPerigeeDeg - meanAnomalyDeg;
  } else if (raan === undefined) {
    if (presetKey === 'geo') throw new Error('preset "geo" needs longitudeDeg (the slot) or raanDeg');
    raan = 0;
  }
  return { a: a!, e, inclinationDeg, raanDeg: norm360(raan), argPerigeeDeg, meanAnomalyDeg, epoch, j2: merged.j2 !== false };
}

/** Two-body motion with J2 secular precession of the node and perigee. */
export function keplerPropagator(el: NormalizedElements): Propagator {
  const n = meanMotionRadS(el.a);
  const rates = el.j2 ? j2RatesDegPerDay(el) : { raan: 0, argPerigee: 0 };
  const raanRate = (rates.raan * D2R) / 86400;
  const argpRate = (rates.argPerigee * D2R) / 86400;
  const i = el.inclinationDeg * D2R;
  const ci = Math.cos(i);
  const si = Math.sin(i);
  return {
    kind: 'elements',
    epoch: el.epoch,
    periodMinutes: (2 * Math.PI) / n / 60,
    position(date: Date): EciVec | null {
      const dt = (date.getTime() - el.epoch.getTime()) / 1000;
      if (!Number.isFinite(dt)) return null;
      const O = el.raanDeg * D2R + raanRate * dt;
      const w = el.argPerigeeDeg * D2R + argpRate * dt;
      const M = norm360((el.meanAnomalyDeg * D2R + n * dt) * R2D) * D2R;
      let E = el.e > 0.8 ? Math.PI : M;
      for (let k = 0; k < 50; k++) {
        const f = E - el.e * Math.sin(E) - M;
        const step = f / (1 - el.e * Math.cos(E));
        E -= step;
        if (Math.abs(step) < 1e-12) break;
      }
      const nu = 2 * Math.atan2(Math.sqrt(1 + el.e) * Math.sin(E / 2), Math.sqrt(1 - el.e) * Math.cos(E / 2));
      const r = el.a * (1 - el.e * Math.cos(E));
      const xp = r * Math.cos(nu);
      const yp = r * Math.sin(nu);
      const cO = Math.cos(O), sO = Math.sin(O), cw = Math.cos(w), sw = Math.sin(w);
      return {
        x: (cO * cw - sO * sw * ci) * xp + (-cO * sw - sO * cw * ci) * yp,
        y: (sO * cw + cO * sw * ci) * xp + (-sO * sw + cO * cw * ci) * yp,
        z: sw * si * xp + cw * si * yp,
      };
    },
  };
}

export function elementsPropagator(def: OrbitElementsDef, fallbackEpoch: Date): Propagator {
  return keplerPropagator(normalizeElements(def, fallbackEpoch));
}

export function tlePropagator(satrec: SatRec): Propagator {
  const s: any = satrec;
  const jd = s.jdsatepoch + (Number.isFinite(s.jdsatepochF) ? s.jdsatepochF : 0);
  return {
    kind: 'tle',
    epoch: new Date((jd - JD_UNIX_EPOCH) * 86400000),
    periodMinutes: (2 * Math.PI) / satrec.no,
    position(date: Date): EciVec | null {
      let pv: any;
      try { pv = propagate(satrec, date); } catch { return null; }
      const pos = pv && pv.position;
      if (!pos || typeof pos !== 'object' || ![pos.x, pos.y, pos.z].every(Number.isFinite)) return null;
      return { x: pos.x, y: pos.y, z: pos.z };
    },
  };
}

// ── Walker constellations ──────────────────────────────────────────────────

export interface OrbitConstellationDef {
  name?: string;
  /** delta: planes over 360 deg of RAAN (GPS, most LEO broadband); star: over 180 deg (Iridium). */
  pattern?: 'delta' | 'star';
  total: number;
  planes: number;
  /** Walker phasing F: adjacent planes are offset by F * 360 / total degrees. */
  phasing?: number;
  altitudeKm?: number;
  semiMajorAxisKm?: number;
  inclinationDeg: number;
  eccentricity?: number;
  argPerigeeDeg?: number;
  raanOffsetDeg?: number;
  epoch?: string | number;
  /** Passed through to every generated satellite. */
  color?: string;
  orbit?: boolean;
  groundTrack?: boolean | { minutesBefore?: number; minutesAfter?: number };
  footprint?: boolean | { minElevationDeg?: number };
}

/** A satellite definition produced by walkerConstellation (elements, never a TLE). */
export interface GeneratedSatellite {
  name: string;
  elements: OrbitElementsDef;
  color?: string;
  orbit?: boolean;
  groundTrack?: boolean | { minutesBefore?: number; minutesAfter?: number };
  footprint?: boolean | { minElevationDeg?: number };
}

/**
 * Walker pattern T/P/F: T satellites in P planes, adjacent planes offset in
 * phase by F * 360 / T degrees.  Delta spreads the planes over 360 deg of
 * RAAN; star over 180 deg.  Problems come back as warnings with no satellites.
 */
export function walkerConstellation(def: OrbitConstellationDef): { satellites: GeneratedSatellite[]; warnings: string[] } {
  const warnings: string[] = [];
  const name = String((def && def.name) || 'walker');
  const T = Math.round(fin(def && def.total) ?? 0);
  const P = Math.round(fin(def && def.planes) ?? 0);
  const F = Math.round(fin(def && def.phasing) ?? 0);
  if (!(T > 0 && P > 0) || T % P !== 0) {
    warnings.push(`${name}: total (${def && def.total}) must be a positive number divisible by planes (${def && def.planes})`);
    return { satellites: [], warnings };
  }
  if (F < 0 || F >= P) {
    warnings.push(`${name}: phasing must be an integer from 0 to planes - 1 (${P - 1}), got ${def.phasing}`);
    return { satellites: [], warnings };
  }
  const perPlane = T / P;
  const span = def.pattern === 'star' ? 180 : 360;
  const raan0 = fin(def.raanOffsetDeg) ?? 0;
  const base: OrbitElementsDef = {
    altitudeKm: def.altitudeKm, semiMajorAxisKm: def.semiMajorAxisKm, inclinationDeg: def.inclinationDeg,
    eccentricity: def.eccentricity, argPerigeeDeg: def.argPerigeeDeg, epoch: def.epoch,
  };
  const satellites: GeneratedSatellite[] = [];
  for (let p = 0; p < P; p++) {
    for (let k = 0; k < perPlane; k++) {
      satellites.push({
        name: `${name}-${p + 1}-${k + 1}`,
        elements: { ...base, raanDeg: norm360(raan0 + (p * span) / P), meanAnomalyDeg: norm360((k * 360) / perPlane + (p * F * 360) / T) },
        color: def.color, orbit: def.orbit, groundTrack: def.groundTrack, footprint: def.footprint,
      });
    }
  }
  return { satellites, warnings };
}

// ── Moon, Sun, Lagrange points ─────────────────────────────────────────────

const sd = (deg: number) => Math.sin(deg * D2R);
const cd = (deg: number) => Math.cos(deg * D2R);

/**
 * Geocentric Moon position, km, mean equator of date.  Low-precision series
 * (Montenbruck & Pfleger), about 0.3 deg in direction and 0.5% in distance:
 * right for drawing, not for navigation.
 */
export function moonPositionKm(date: Date): EciVec {
  const T = (dateToJd(date) - JD_J2000) / 36525;
  const lam = 218.32 + 481267.881 * T
    + 6.29 * sd(135.0 + 477198.87 * T) - 1.27 * sd(259.3 - 413335.36 * T) + 0.66 * sd(235.7 + 890534.22 * T)
    + 0.21 * sd(269.9 + 954397.74 * T) - 0.19 * sd(357.5 + 35999.05 * T) - 0.11 * sd(186.5 + 966404.03 * T);
  const bet = 5.13 * sd(93.3 + 483202.02 * T) + 0.28 * sd(228.2 + 960400.89 * T)
    - 0.28 * sd(318.3 + 6003.15 * T) - 0.17 * sd(217.6 - 407332.21 * T);
  const par = 0.9508 + 0.0518 * cd(135.0 + 477198.87 * T) + 0.0095 * cd(259.3 - 413335.36 * T)
    + 0.0078 * cd(235.7 + 890534.22 * T) + 0.0028 * cd(269.9 + 954397.74 * T);
  const r = EARTH_EQUATORIAL_RADIUS_KM / Math.sin(par * D2R);
  const eps = 23.439 * D2R;
  const l = lam * D2R;
  const b = bet * D2R;
  const x = Math.cos(b) * Math.cos(l);
  const y = Math.cos(eps) * Math.cos(b) * Math.sin(l) - Math.sin(eps) * Math.sin(b);
  const z = Math.sin(eps) * Math.cos(b) * Math.sin(l) + Math.cos(eps) * Math.sin(b);
  return { x: r * x, y: r * y, z: r * z };
}

/** Geocentric Sun position, km (satellite.js sunPos, converted from AU). */
export function sunPositionKm(date: Date): EciVec {
  const s = sunPos(dateToJd(date)).rsun;
  return { x: s.x * AU_KM, y: s.y * AU_KM, z: s.z * AU_KM };
}

export type LagrangeSystem = 'earth-moon' | 'sun-earth';
export interface LagrangeResult {
  system: LagrangeSystem;
  /** Earth-centred positions, km. */
  points: { L1: EciVec; L2: EciVec; L3: EciVec; L4: EciVec; L5: EciVec };
  separationKm: number;
  /** Earth-centred position of the other body (Moon, or Sun), km. */
  secondary: EciVec;
}

/** Root of the collinear-point equation in barycentric units, by bisection on [lo, hi]. */
function collinearRoot(mu: number, lo: number, hi: number): number {
  const f = (x: number) => x - ((1 - mu) * (x + mu)) / Math.abs(x + mu) ** 3 - (mu * (x - 1 + mu)) / Math.abs(x - 1 + mu) ** 3;
  for (let k = 0; k < 200; k++) {
    const mid = (lo + hi) / 2;
    if (f(lo) * f(mid) <= 0) hi = mid; else lo = mid;
  }
  return (lo + hi) / 2;
}

/**
 * Earth-centred L1..L5 of the Earth-Moon or Sun-Earth system at `date`.
 * Circular restricted three-body geometry: exact for the mass ratios, with
 * the real (slightly elliptical) separation at that instant.
 */
export function lagrangePoints(system: LagrangeSystem, date: Date): LagrangeResult {
  const mu = MASS_RATIO[system];
  if (mu === undefined) throw new Error(`unknown Lagrange system "${system}"; use "earth-moon" or "sun-earth"`);
  const posAt = system === 'earth-moon' ? moonPositionKm : sunPositionKm;
  const secondary = posAt(date);
  const later = posAt(new Date(date.getTime() + 3600e3));
  const sep = Math.hypot(secondary.x, secondary.y, secondary.z);
  // Unit axis from the PRIMARY to the SECONDARY, and Earth's offset along it.
  const sign = system === 'earth-moon' ? 1 : -1;
  const u = { x: (sign * secondary.x) / sep, y: (sign * secondary.y) / sep, z: (sign * secondary.z) / sep };
  const earthOffset = system === 'earth-moon' ? 0 : 1;
  // Orbital normal from two successive positions; its sign only swaps L4/L5.
  const n0 = { x: secondary.y * later.z - secondary.z * later.y, y: secondary.z * later.x - secondary.x * later.z, z: secondary.x * later.y - secondary.y * later.x };
  const nn = Math.hypot(n0.x, n0.y, n0.z) || 1;
  const n = { x: n0.x / nn, y: n0.y / nn, z: n0.z / nn };
  const v = { x: n.y * u.z - n.z * u.y, y: n.z * u.x - n.x * u.z, z: n.x * u.y - n.y * u.x }; // in-plane, 90 deg ahead of u
  const along = (d: number): EciVec => ({ x: (d - earthOffset) * sep * u.x, y: (d - earthOffset) * sep * u.y, z: (d - earthOffset) * sep * u.z });
  const tri = (s: number): EciVec => {
    const c = Math.cos(Math.PI / 3) - earthOffset;
    const w = s * Math.sin(Math.PI / 3);
    return { x: (c * u.x + w * v.x) * sep, y: (c * u.y + w * v.y) * sep, z: (c * u.z + w * v.z) * sep };
  };
  return {
    system, separationKm: sep, secondary,
    points: {
      L1: along(collinearRoot(mu, -mu + 1e-6, 1 - mu - 1e-6) + mu),
      L2: along(collinearRoot(mu, 1 - mu + 1e-6, 3) + mu),
      L3: along(collinearRoot(mu, -3, -mu - 1e-6) + mu),
      L4: tri(1),
      L5: tri(-1),
    },
  };
}
