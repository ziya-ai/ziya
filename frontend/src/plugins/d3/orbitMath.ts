/**
 * Geometry and SGP4 propagation behind the orbit fence (orbitPlugin.ts).
 *
 * Kept free of three.js and globe.gl so it runs under jest, and so every
 * number the globe shows (sub-satellite point, coverage radius, elevation,
 * slant range, light time) comes from one tested place.  orbitPlugin only
 * plots the arrays buildOrbitScene returns.
 *
 * Units: latitude/longitude in degrees, east-positive.  GeoPoint.alt is in
 * globe radii (globe.gl's unit: km / EARTH_RADIUS_KM); SubPoint.altKm is in
 * kilometres.  Coverage and link geometry treat the Earth as a sphere of mean
 * radius 6371 km; satellite.js converts SGP4's output to geodetic latitude and
 * height, and the spherical simplification is well inside a TLE's own error.
 */
import {
  degreesLat,
  degreesLong,
  eciToGeodetic,
  gstime,
  propagate,
  twoline2satrec,
} from 'satellite.js';
import {
  cellToBoundary,
  cellToLatLng,
  getHexagonEdgeLengthAvg,
  gridDisk,
  isValidCell,
  latLngToCell,
} from 'h3-js';
import {
  elementsPropagator,
  lagrangePoints,
  moonPositionKm,
  sunPositionKm,
  tlePropagator,
  walkerConstellation,
  AU_KM,
  EciVec,
  MOON_RADIUS_KM,
  OrbitConstellationDef,
  OrbitElementsDef,
  Propagator,
} from './orbitSources';
import {
  apertureSpot,
  delayMs,
  groupColors,
  inEarthShadow,
  partitionCells,
  speedFraction,
  subsolarPoint,
  toEcef,
  PARTITION_PALETTE,
} from './orbitLayers';

export const EARTH_RADIUS_KM = 6371.0;
export const SPEED_OF_LIGHT_KM_S = 299792.458;
export const MAX_SATELLITES = 200;
export const MAX_AUTO_LINKS = 2000;
export const MAX_CELLS = 5000;
export const MAX_BEAM_LINES = 1500;
/** Cap on the hex disc searched for cells.around: 3k(k+1)+1 cells. */
const MAX_GRID_DISK = 60000;
export const DEFAULT_MIN_ELEVATION_DEG = 10;
/** Drawn ground features sit just above the country polygons (altitude 0.003). */
export const GROUND_ALT = 0.004;
/** H3 cells sit between the country polygons and drawn ground lines. */
export const CELL_ALT = 0.0035;
export const CELL_UNSERVED = { fill: 'rgba(128,128,128,0.14)', stroke: 'rgba(160,160,160,0.55)' };
export const CELL_NO_CAPACITY = { fill: 'rgba(239,68,68,0.30)', stroke: 'rgba(239,68,68,0.85)' };
export const ORBIT_PALETTE = [
  '#f59e0b', '#38bdf8', '#f472b6', '#a3e635', '#c084fc', '#fb7185', '#2dd4bf', '#facc15',
];
const SITE_LINK_COLOR = '#22c55e';

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const ORBIT_SAMPLES = 180;

type SatRec = ReturnType<typeof twoline2satrec>;

export interface GeoPoint { lat: number; lng: number; alt: number; }
export interface SubPoint { lat: number; lng: number; altKm: number; }

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** `v` as a finite number (numeric strings accepted), else `fallback`. */
export function finiteOr(v: unknown, fallback: number): number {
  const n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
  return typeof n === 'number' && Number.isFinite(n) ? n : fallback;
}

export function normalizeLng(lng: number): number {
  return ((((lng + 180) % 360) + 360) % 360) - 180;
}

const HTML_ESCAPES: Record<string, string> = {
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
};

/** Names from the spec are shown in globe.gl tooltips, which are innerHTML. */
export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

// ── Spherical geometry ─────────────────────────────────────────────────────

/**
 * Earth-central half-angle (degrees) of the region that sees a satellite at
 * `altKm` at or above `minElevationDeg`:
 *   lambda = 90 deg - eps - asin(R cos(eps) / (R + h)).
 * At eps = 0 this is the horizon circle, acos(R / (R + h)).
 */
export function footprintHalfAngleDeg(altKm: number, minElevationDeg = 0): number {
  if (!(altKm > 0)) return 0;
  const eps = clamp(minElevationDeg, 0, 90) * D2R;
  const nadir = Math.asin((EARTH_RADIUS_KM * Math.cos(eps)) / (EARTH_RADIUS_KM + altKm));
  return Math.max(0, (Math.PI / 2 - eps - nadir) * R2D);
}

/** Great-circle angle (degrees) between two points (haversine). */
export function centralAngleDeg(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const p1 = lat1 * D2R;
  const p2 = lat2 * D2R;
  const a = Math.sin((p2 - p1) / 2) ** 2
    + Math.cos(p1) * Math.cos(p2) * Math.sin(((lng2 - lng1) * D2R) / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(a))) * R2D;
}

/** Elevation (degrees) of a satellite above the local horizon at (lat, lng). */
export function elevationDeg(lat: number, lng: number, sub: SubPoint): number {
  const g = centralAngleDeg(lat, lng, sub.lat, sub.lng) * D2R;
  const ratio = EARTH_RADIUS_KM / (EARTH_RADIUS_KM + sub.altKm);
  return Math.atan2(Math.cos(g) - ratio, Math.sin(g)) * R2D;
}

/** Point reached by travelling `angleDeg` of arc from (lat, lng) on `bearingDeg`. */
export function destinationPoint(lat: number, lng: number, bearingDeg: number, angleDeg: number): { lat: number; lng: number } {
  const p1 = lat * D2R;
  const th = bearingDeg * D2R;
  const d = angleDeg * D2R;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(th));
  const l2 = lng * D2R + Math.atan2(
    Math.sin(th) * Math.sin(d) * Math.cos(p1),
    Math.cos(d) - Math.sin(p1) * Math.sin(p2),
  );
  return { lat: p2 * R2D, lng: normalizeLng(l2 * R2D) };
}

/** Closed small circle of angular radius `radiusDeg` around (lat, lng). */
export function geodesicCircle(lat: number, lng: number, radiusDeg: number, alt = GROUND_ALT, segments = 96): GeoPoint[] {
  const out: GeoPoint[] = [];
  for (let i = 0; i <= segments; i++) {
    out.push({ ...destinationPoint(lat, lng, (i * 360) / segments, radiusDeg), alt });
  }
  return out;
}

function toCartesian(p: GeoPoint): [number, number, number] {
  const r = 1 + p.alt;
  const la = p.lat * D2R;
  const lo = p.lng * D2R;
  return [r * Math.cos(la) * Math.cos(lo), r * Math.cos(la) * Math.sin(lo), r * Math.sin(la)];
}

function fromCartesian(x: number, y: number, z: number): GeoPoint {
  const r = Math.hypot(x, y, z);
  return { lat: Math.asin(clamp(z / r, -1, 1)) * R2D, lng: Math.atan2(y, x) * R2D, alt: r - 1 };
}

/** Straight-line distance (km) between two points in space. */
export function chordDistanceKm(a: GeoPoint, b: GeoPoint): number {
  const p = toCartesian(a);
  const q = toCartesian(b);
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) * EARTH_RADIUS_KM;
}

/**
 * Lowest altitude (km) the straight line between two points reaches.  Below
 * zero the Earth is in the way: no line of sight.
 */
export function grazingAltitudeKm(a: GeoPoint, b: GeoPoint): number {
  const p = toCartesian(a);
  const q = toCartesian(b);
  const d = [q[0] - p[0], q[1] - p[1], q[2] - p[2]];
  const dd = d[0] ** 2 + d[1] ** 2 + d[2] ** 2;
  const t = dd > 0 ? clamp(-(p[0] * d[0] + p[1] * d[1] + p[2] * d[2]) / dd, 0, 1) : 0;
  return (Math.hypot(p[0] + d[0] * t, p[1] + d[1] * t, p[2] + d[2] * t) - 1) * EARTH_RADIUS_KM;
}

/**
 * A straight line through space, sampled as lat/lng/alt points.  globe.gl
 * interpolates a path along the surface, so a two-point beam would bow with
 * the Earth; densifying the true chord keeps it straight.
 */
export function straightSegment(a: GeoPoint, b: GeoPoint, steps = 24): GeoPoint[] {
  const p = toCartesian(a);
  const q = toCartesian(b);
  const out: GeoPoint[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    out.push(fromCartesian(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t));
  }
  return out;
}

/** The great circle between two ground points, held at altitude `alt`. */
export function surfaceArc(a: { lat: number; lng: number }, b: { lat: number; lng: number }, alt = GROUND_ALT, steps = 64): GeoPoint[] {
  return straightSegment({ lat: a.lat, lng: a.lng, alt: 0 }, { lat: b.lat, lng: b.lng, alt: 0 }, steps)
    .map((g) => ({ lat: g.lat, lng: g.lng, alt }));
}

export const lightTimeMs = (km: number): number => (km / SPEED_OF_LIGHT_KM_S) * 1000;

// ── TLE / SGP4 ─────────────────────────────────────────────────────────────

export interface ParsedTle { name?: string; line1: string; line2: string; }

/**
 * Accept a TLE as [line1, line2], [name, line1, line2] or one string with
 * newlines.  satellite.js accepts any string without setting an error (and
 * then propagates NaN), so the line shape is checked here.
 */
export function parseTle(input: unknown): ParsedTle | null {
  const raw = Array.isArray(input) ? input.map(String) : typeof input === 'string' ? input.split(/\r?\n/) : [];
  const lines = raw.map((l) => l.trim()).filter((l) => l !== '');
  const line1 = lines.find((l) => /^1 \S/.test(l) && l.length >= 68);
  const line2 = lines.find((l) => /^2 \S/.test(l) && l.length >= 68);
  if (!line1 || !line2) return null;
  const other = lines.find((l) => l !== line1 && l !== line2);
  return other ? { name: other.replace(/^0\s+/, ''), line1, line2 } : { line1, line2 };
}

const isPropagator = (s: any): s is Propagator => !!s && typeof s.position === 'function';

/** Inertial position of a TLE or an elements propagator, or null when it cannot be computed. */
function positionOf(src: SatRec | Propagator, date: Date): EciVec | null {
  return isPropagator(src) ? src.position(date) : tlePropagator(src).position(date);
}

function eciToSub(eci: EciVec, gmstAt: Date): SubPoint | null {
  const g = eciToGeodetic(eci as any, gstime(gmstAt));
  const sub: SubPoint = { lat: degreesLat(g.latitude), lng: degreesLong(g.longitude), altKm: g.height };
  return [sub.lat, sub.lng, sub.altKm].every(Number.isFinite) ? sub : null;
}

/**
 * Sub-satellite point at `date`.  `gmstAt` picks the Earth rotation used to
 * turn the inertial position into lat/lng: the same instant for a ground
 * track, a fixed instant to draw the orbit as it sits in space.
 */
export function subPoint(src: SatRec | Propagator, date: Date, gmstAt: Date = date): SubPoint | null {
  const pos = positionOf(src, date);
  return pos ? eciToSub(pos, gmstAt) : null;
}

/** Orbital period in minutes (for a TLE, satrec.no is the mean motion in rad/min). */
export function periodMinutes(src: SatRec | Propagator): number {
  return isPropagator(src) ? src.periodMinutes : (2 * Math.PI) / src.no;
}

export function tleEpochDate(satrec: SatRec): Date {
  const s: any = satrec;
  const jd = s.jdsatepoch + (Number.isFinite(s.jdsatepochF) ? s.jdsatepochF : 0);
  return new Date((jd - 2440587.5) * 86400000);
}

// ── Spec ───────────────────────────────────────────────────────────────────

export interface OrbitSatelliteDef {
  name?: string;
  /** Give exactly one of tle (a tracked object) or elements (a nominal orbit). */
  tle?: string | string[];
  elements?: OrbitElementsDef;
  color?: string;
  orbit?: boolean;
  groundTrack?: boolean | { minutesBefore?: number; minutesAfter?: number };
  footprint?: boolean | { minElevationDeg?: number };
  /**
   * A cone of halfAngleDeg from the object to the ground, aimed at nadir or
   * at a site name / {lat, lon}: any field of view (camera, sensor, antenna).
   */
  aperture?: { halfAngleDeg: number; target?: string | { lat: number; lon?: number; lng?: number }; color?: string };
}
export interface OrbitSiteDef { name?: string; lat: number; lon?: number; lng?: number; color?: string; }
export interface OrbitLinkDef { from: string; to: string; label?: string; color?: string; width?: number; }
export interface OrbitBodiesDef { moon?: boolean; lagrange?: Array<string | { system: string; points?: string[] }>; }
/** A path through named sites/objects; hops between sites go along the ground at groundMedium speed. */
export interface OrbitRouteDef { name?: string; via: string[]; groundMedium?: string | number; pathFactor?: number; extraMs?: number; color?: string; }
export interface OrbitSpec {
  type: 'orbit';
  title?: string;
  time?: string | number;
  satellites?: OrbitSatelliteDef[];
  constellations?: OrbitConstellationDef[];
  bodies?: OrbitBodiesDef;
  sites?: OrbitSiteDef[];
  links?: OrbitLinkDef[];
  autoLinks?: boolean | { minElevationDeg?: number; color?: string };
  minElevationDeg?: number;
  timeline?: { minutes?: number; stepSeconds?: number };
  view?: { lat?: number; lon?: number; lng?: number; altitude?: number };
  graticules?: boolean;
  labelScale?: number;
  cells?: OrbitCellsDef;
  beams?: OrbitBeamsDef;
  /** The generic name for beams: who serves which cell, under what capacity. */
  assignment?: OrbitBeamsDef;
  routes?: OrbitRouteDef[];
  /** Day/night terminator and subsolar point; each object marked sunlit or in the Earth's shadow. */
  sun?: boolean;
  height?: number;
}

export interface OrbitCellItem { id?: string; lat?: number; lon?: number; lng?: number; demand?: number; label?: string; group?: string; }
export interface OrbitCellsDef {
  resolution?: number;
  around?: { lat: number; lon?: number; lng?: number; radiusKm: number };
  ids?: string[];
  items?: OrbitCellItem[];
  demand?: Record<string, number>;
  /** Fill by serving server (default), by a no-neighbours-alike partition, or by item group. */
  colorBy?: 'server' | 'partition' | 'group';
  /** Number of partition groups, 3-12 (default 3 when colorBy is "partition"). */
  partition?: number;
}
export type CellPolicy = 'highest-elevation' | 'nearest';
export interface OrbitBeamsDef {
  policy?: CellPolicy;
  maxBeamsPerSatellite?: number;
  /** Same limit, by its generic name. */
  capacity?: number;
  minElevationDeg?: number;
  /** Who serves cells: satellites (default), ground sites, or both. */
  servers?: 'satellites' | 'sites' | 'all';
  /** A ground site serves cells whose centre is within this distance (default 100 km). */
  maxRangeKm?: number;
  /** Draw a beam line to each served cell (default true). */
  show?: boolean;
}

/** Unwrap the {type, definition: "<json>"} envelope render_diagram sends. */
export function resolveOrbitSpec(raw: any): any {
  if (!raw || typeof raw !== 'object') return raw;
  let inner: any = raw.definition;
  if (typeof inner === 'string') {
    try {
      inner = JSON.parse(inner);
    } catch {
      return raw;
    }
  }
  if (inner && typeof inner === 'object' && !Array.isArray(inner)) {
    return { ...inner, type: inner.type || raw.type };
  }
  return raw;
}

export function isOrbitSpec(raw: any): boolean {
  const s = resolveOrbitSpec(raw);
  return !!s && typeof s === 'object' && s.type === 'orbit'
    && (Array.isArray(s.satellites) || Array.isArray(s.sites) || Array.isArray(s.constellations)
      || (!!s.cells && typeof s.cells === 'object') || (!!s.bodies && typeof s.bodies === 'object'));
}

export function parseOrbitTime(t: unknown): Date {
  if (t === 'now') return new Date();
  const d = new Date(t as any);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`orbit: "time" ${JSON.stringify(t)} is not a date; use ISO 8601 UTC, e.g. "2026-11-05T00:00:00Z"`);
  }
  return d;
}

/** The spec's time, else the first object's epoch (where propagation is most accurate). */
export function initialTime(spec: OrbitSpec, prepared: PreparedSatellites): Date {
  if (spec.time !== undefined && spec.time !== null && spec.time !== '') return parseOrbitTime(spec.time);
  return prepared.sats[0] ? prepared.sats[0].propagator.epoch : new Date();
}

export function timelineConfig(spec: OrbitSpec, prepared: PreparedSatellites): { minutes: number; stepSeconds: number } | null {
  if (!spec.timeline) return null;
  const tl = typeof spec.timeline === 'object' ? spec.timeline : {};
  const fallback = prepared.sats[0] ? prepared.sats[0].propagator.periodMinutes : 90;
  return {
    minutes: clamp(finiteOr(tl.minutes, fallback), 1, 10080),
    stepSeconds: clamp(finiteOr(tl.stepSeconds, 60), 1, 3600),
  };
}

export interface PreparedSatellite { name: string; def: OrbitSatelliteDef; propagator: Propagator; satrec?: SatRec; color: string; }
export interface PreparedSatellites { sats: PreparedSatellite[]; warnings: string[]; }

/**
 * Parse every TLE and elements block once, and expand constellations; the
 * time slider rebuilds scenes from these.
 */
export function prepareSatellites(spec: OrbitSpec): PreparedSatellites {
  const warnings: string[] = [];
  const sats: PreparedSatellite[] = [];
  // Elements without their own epoch are referred to the spec's time, so a
  // nominal orbit is where the author placed it at the instant they named.
  const fallbackEpoch = spec.time !== undefined && spec.time !== null && spec.time !== '' ? parseOrbitTime(spec.time) : new Date();
  let defs: OrbitSatelliteDef[] = Array.isArray(spec.satellites) ? [...spec.satellites] : [];
  (Array.isArray(spec.constellations) ? spec.constellations : []).forEach((c) => {
    const w = walkerConstellation(c);
    warnings.push(...w.warnings);
    defs.push(...w.satellites);
  });
  if (defs.length > MAX_SATELLITES) {
    warnings.push(`only the first ${MAX_SATELLITES} of ${defs.length} satellites are drawn`);
    defs = defs.slice(0, MAX_SATELLITES);
  }
  const add = (name: string, def: OrbitSatelliteDef, propagator: Propagator, satrec?: SatRec) => {
    if (sats.some((s) => s.name === name)) warnings.push(`duplicate satellite name "${name}"; links use the first`);
    sats.push({ name, def, propagator, satrec, color: def.color || ORBIT_PALETTE[sats.length % ORBIT_PALETTE.length] });
  };
  defs.forEach((def, i) => {
    if (def && def.elements !== undefined) {
      const name = String(def.name || `sat-${i + 1}`);
      if (def.tle !== undefined) warnings.push(`${name}: has both "tle" and "elements"; using elements`);
      try {
        add(name, def, elementsPropagator(def.elements, fallbackEpoch));
      } catch (e: any) {
        warnings.push(`${name}: ${e?.message || e}`);
      }
      return;
    }
    const tle = parseTle(def && def.tle);
    const name = String((def && def.name) || (tle && tle.name) || `sat-${i + 1}`);
    if (!tle) {
      warnings.push(`${name}: "tle" must hold TLE line 1 and line 2, 69 characters each (or give "elements")`);
      return;
    }
    let satrec: SatRec;
    try {
      satrec = twoline2satrec(tle.line1, tle.line2);
    } catch (e: any) {
      warnings.push(`${name}: TLE could not be parsed (${e?.message || e})`);
      return;
    }
    if (!satrec || (satrec as any).error || !(satrec.no > 0)) {
      warnings.push(`${name}: TLE rejected by SGP4`);
      return;
    }
    add(name, def || ({} as OrbitSatelliteDef), tlePropagator(satrec), satrec);
  });
  return { sats, warnings };
}

// ── Scene ──────────────────────────────────────────────────────────────────

export type LabelKind = 'satellite' | 'site' | 'body' | 'point';
export interface SceneLabel { kind: LabelKind; name: string; lat: number; lng: number; alt: number; color: string; tooltip: string; }
export interface ScenePath {
  kind: 'orbit' | 'groundTrack' | 'footprint' | 'link' | 'beam' | 'route' | 'aperture' | 'terminator';
  name: string; points: GeoPoint[]; color: string; stroke: number; tooltip: string; blocked?: boolean;
}
export interface SceneCell { id: string; ring: [number, number][]; status: CellStatus; sat?: string; fill: string; stroke: string; tooltip: string; }
/** A body drawn as a sphere; radius and alt in globe radii. */
export interface SceneBody { name: string; lat: number; lng: number; alt: number; radius: number; color: string; tooltip: string; }
export interface SceneRouteHop { from: string; to: string; medium: string; km: number; ms: number; blocked: boolean; }
/** km and ms are one-way totals; ms includes extraMs. */
export interface SceneRoute { name: string; color: string; hops: SceneRouteHop[]; km: number; ms: number; extraMs: number; blocked: boolean; }
export interface OrbitScene {
  time: Date; labels: SceneLabel[]; paths: ScenePath[]; warnings: string[]; center: { lat: number; lng: number };
  cells: SceneCell[]; cellStats?: CellStats; bodies: SceneBody[]; routes: SceneRoute[];
  subsolar?: { lat: number; lng: number };
  /** Altitude (globe radii) of the farthest drawn object, so the camera can back off to fit it. */
  farthestAlt: number;
}

interface SatEnd { kind: 'satellite' | 'body' | 'point'; name: string; sub: SubPoint; color: string; }
interface SiteEnd { kind: 'site'; name: string; lat: number; lng: number; }
type Endpoint = SatEnd | SiteEnd;

const fmtDeg = (v: number) => v.toFixed(2);
const fmtKm = (v: number) => `${Math.round(v).toLocaleString('en-US')} km`;
const fmtLight = (km: number) => {
  const ms = lightTimeMs(km);
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s one-way at c` : `${ms.toFixed(2)} ms one-way at c`;
};

function endpointPoint(e: Endpoint): GeoPoint {
  return e.kind === 'site'
    ? { lat: e.lat, lng: e.lng, alt: 0 }
    : { lat: e.sub.lat, lng: e.sub.lng, alt: e.sub.altKm / EARTH_RADIUS_KM };
}

export function linkPath(a: Endpoint, b: Endpoint, color: string, stroke: number, label?: string): ScenePath {
  const title = escapeHtml(label || `${a.name} → ${b.name}`);
  if (a.kind === 'site' && b.kind === 'site') {
    const km = centralAngleDeg(a.lat, a.lng, b.lat, b.lng) * D2R * EARTH_RADIUS_KM;
    return { kind: 'link', name: title, points: surfaceArc(a, b), color, stroke,
      tooltip: `${title}<br>great circle ${fmtKm(km)}, ${fmtLight(km)}` };
  }
  const pa = endpointPoint(a);
  const pb = endpointPoint(b);
  const km = chordDistanceKm(pa, pb);
  const clearance = grazingAltitudeKm(pa, pb);
  const blocked = clearance < -1;
  let tooltip = `${title}<br>range ${fmtKm(km)}, ${fmtLight(km)}`;
  const sat = a.kind !== 'site' ? a : b.kind !== 'site' ? b : null;
  const site = a.kind === 'site' ? a : b.kind === 'site' ? b : null;
  if (sat && site) tooltip += `<br>elevation ${elevationDeg(site.lat, site.lng, sat.sub).toFixed(1)}°`;
  if (blocked) tooltip += sat && site ? '<br><b>below the horizon: blocked by the Earth</b>' : '<br><b>blocked by the Earth</b>';
  else if (!site) tooltip += `<br>clears the surface by ${fmtKm(clearance)}`;
  return { kind: 'link', name: title, points: straightSegment(pa, pb), color, stroke, tooltip, blocked };
}

/** The orbit as it sits in space at `time`: one period, Earth rotation frozen. */
function sampleInertialOrbit(prop: Propagator, time: Date, period: number): GeoPoint[] {
  const out: GeoPoint[] = [];
  for (let i = 0; i <= ORBIT_SAMPLES; i++) {
    const t = new Date(time.getTime() + (i / ORBIT_SAMPLES - 0.5) * period * 60000);
    const sp = subPoint(prop, t, time);
    if (sp) out.push({ lat: sp.lat, lng: sp.lng, alt: sp.altKm / EARTH_RADIUS_KM });
  }
  return out;
}

/** The path traced on the rotating Earth, sampled every 30 s. */
function sampleGroundTrack(prop: Propagator, time: Date, before: number, after: number): GeoPoint[] {
  const steps = clamp(Math.ceil((before + after) * 2), 2, 2880);
  const out: GeoPoint[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = new Date(time.getTime() + (-before + ((before + after) * i) / steps) * 60000);
    const sp = subPoint(prop, t);
    if (sp) out.push({ lat: sp.lat, lng: sp.lng, alt: GROUND_ALT });
  }
  return out;
}

export function buildOrbitScene(spec: OrbitSpec, prepared: PreparedSatellites, time: Date, theme: { text: string } = { text: '#888888' }, preparedCells?: PreparedCells): OrbitScene {
  const warnings = [...prepared.warnings];
  const labels: SceneLabel[] = [];
  const paths: ScenePath[] = [];
  const minEl = clamp(finiteOr(spec.minElevationDeg, DEFAULT_MIN_ELEVATION_DEG), 0, 90);
  const satEnds = new Map<string, SatEnd>();
  const siteEnds = new Map<string, SiteEnd>();
  const sunEci = spec.sun ? sunPositionKm(time) : null;

  for (const s of prepared.sats) {
    const sub = subPoint(s.propagator, time);
    const name = escapeHtml(s.name);
    if (!sub) {
      warnings.push(`${s.name}: could not propagate to ${time.toISOString()} (decayed, or far from the epoch)`);
      continue;
    }
    if (!satEnds.has(s.name)) satEnds.set(s.name, { kind: 'satellite', name: s.name, sub, color: s.color });
    // With the Sun on, say whether the object is lit; dim its label in the Earth's shadow.
    let light = '';
    let labelColor = s.color;
    if (sunEci) {
      const eci = s.propagator.position(time);
      if (eci) {
        const dark = inEarthShadow(eci, sunEci);
        light = dark ? "<br>in Earth's shadow" : '<br>sunlit';
        if (dark) labelColor = withAlpha(s.color, 0.55);
      }
    }
    labels.push({ kind: 'satellite', name: s.name, lat: sub.lat, lng: sub.lng, alt: sub.altKm / EARTH_RADIUS_KM, color: labelColor,
      tooltip: `<b>${name}</b><br>${fmtDeg(sub.lat)}, ${fmtDeg(sub.lng)}<br>altitude ${fmtKm(sub.altKm)}${light}` });
    const period = s.propagator.periodMinutes;
    if (s.def.orbit !== false) {
      const pts = sampleInertialOrbit(s.propagator, time, period);
      if (pts.length > 1) paths.push({ kind: 'orbit', name: s.name, points: pts, color: s.color, stroke: 0.6, tooltip: `${name} orbit, period ${period.toFixed(1)} min` });
    }
    if (s.def.groundTrack) {
      const g = typeof s.def.groundTrack === 'object' ? s.def.groundTrack : {};
      const pts = sampleGroundTrack(s.propagator, time, Math.max(0, finiteOr(g.minutesBefore, 0)), Math.max(0, finiteOr(g.minutesAfter, period)));
      if (pts.length > 1) paths.push({ kind: 'groundTrack', name: s.name, points: pts, color: s.color, stroke: 0.4, tooltip: `${name} ground track` });
    }
    if (s.def.footprint) {
      const f = typeof s.def.footprint === 'object' ? s.def.footprint : {};
      const fe = clamp(finiteOr(f.minElevationDeg, minEl), 0, 90);
      const lam = footprintHalfAngleDeg(sub.altKm, fe);
      if (lam > 0) paths.push({ kind: 'footprint', name: s.name, points: geodesicCircle(sub.lat, sub.lng, lam), color: s.color, stroke: 0.5,
        tooltip: `${name} coverage at elevation ≥ ${fe}°: radius ${fmtKm(lam * D2R * EARTH_RADIUS_KM)}` });
    }
  }

  (Array.isArray(spec.sites) ? spec.sites : []).forEach((site, i) => {
    const name = String((site && site.name) || `site-${i + 1}`);
    const lat = finiteOr(site && site.lat, NaN);
    const lng = finiteOr(site && (site.lon ?? site.lng), NaN);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90) {
      warnings.push(`${name}: needs numeric "lat" and "lon"`);
      return;
    }
    const end: SiteEnd = { kind: 'site', name, lat, lng: normalizeLng(lng) };
    if (!siteEnds.has(name)) siteEnds.set(name, end);
    labels.push({ kind: 'site', name, lat, lng: end.lng, alt: GROUND_ALT, color: site.color || theme.text,
      tooltip: `<b>${escapeHtml(name)}</b><br>${fmtDeg(lat)}, ${fmtDeg(end.lng)}` });
  });

  // The Moon and Lagrange points: labels, link endpoints and (for the Moon)
  // a drawn sphere.  Earth-centred inertial positions go through the same
  // lat/lng/alt conversion as a satellite.
  const bodies: SceneBody[] = [];
  const bdefs = spec.bodies && typeof spec.bodies === 'object' ? spec.bodies : null;
  if (bdefs) {
    const addPoint = (kind: 'body' | 'point', name: string, eci: EciVec, color: string, detail: string, radius?: number) => {
      const sub = eciToSub(eci, time);
      if (!sub) return;
      const alt = sub.altKm / EARTH_RADIUS_KM;
      const tooltip = `<b>${escapeHtml(name)}</b><br>${detail}`;
      if (!satEnds.has(name)) satEnds.set(name, { kind, name, sub, color });
      labels.push({ kind, name, lat: sub.lat, lng: sub.lng, alt, color, tooltip });
      if (radius !== undefined) bodies.push({ name, lat: sub.lat, lng: sub.lng, alt, radius, color, tooltip });
    };
    if (bdefs.moon) {
      const m = moonPositionKm(time);
      const dist = Math.hypot(m.x, m.y, m.z);
      addPoint('body', 'Moon', m, '#d4d4d8', `${fmtKm(dist)} from the Earth's centre, ${fmtLight(dist)}`, MOON_RADIUS_KM / EARTH_RADIUS_KM);
    }
    (Array.isArray(bdefs.lagrange) ? bdefs.lagrange : []).forEach((entry) => {
      const system = String(typeof entry === 'string' ? entry : entry && entry.system).toLowerCase();
      if (system !== 'earth-moon' && system !== 'sun-earth') {
        warnings.push(`bodies.lagrange: unknown system "${system}"; use "earth-moon" or "sun-earth"`);
        return;
      }
      const want = typeof entry === 'object' && entry && Array.isArray(entry.points)
        ? entry.points.map((p) => String(p).toUpperCase()) : ['L1', 'L2', 'L3', 'L4', 'L5'];
      const lp = lagrangePoints(system, time);
      const prefix = system === 'earth-moon' ? 'EM-' : 'SE-';
      for (const p of want) {
        const eci = (lp.points as Record<string, EciVec>)[p];
        if (!eci) { warnings.push(`bodies.lagrange: no point "${p}" (L1 to L5)`); continue; }
        const dist = Math.hypot(eci.x, eci.y, eci.z);
        const name = prefix + p;
        addPoint('point', name, eci, '#facc15', `${system} ${p}<br>${fmtKm(dist)} from Earth, ${fmtLight(dist)}`);
        if (dist > AU_KM / 2) warnings.push(`${name} is about an astronomical unit from Earth (${fmtKm(dist)}); the view cannot fit it`);
      }
    });
  }

  // Fields of view: a cone from the object to nadir or a named site / point,
  // and the true spot where it meets the Earth.
  for (const s of prepared.sats) {
    const ap = s.def.aperture;
    const end = satEnds.get(s.name);
    if (!ap || !end || end.kind !== 'satellite') continue;
    const half = finiteOr(ap.halfAngleDeg, NaN);
    if (!(half > 0 && half < 90)) { warnings.push(`${s.name}: aperture.halfAngleDeg must be between 0 and 90`); continue; }
    let tgt = { lat: end.sub.lat, lng: end.sub.lng };
    let aim = 'nadir';
    if (typeof ap.target === 'string') {
      const site = siteEnds.get(ap.target);
      if (!site) { warnings.push(`${s.name}: aperture target "${ap.target}" is not a site`); continue; }
      tgt = site;
      aim = site.name;
    } else if (ap.target && typeof ap.target === 'object') {
      const la = finiteOr(ap.target.lat, NaN);
      const lo = finiteOr(ap.target.lon ?? ap.target.lng, NaN);
      if (!Number.isFinite(la) || !Number.isFinite(lo)) { warnings.push(`${s.name}: aperture target needs numeric lat and lon`); continue; }
      tgt = { lat: la, lng: normalizeLng(lo) };
      aim = `${fmtDeg(la)}, ${fmtDeg(lo)}`;
    }
    if (aim !== 'nadir' && elevationDeg(tgt.lat, tgt.lng, end.sub) < 0) {
      warnings.push(`${s.name}: aperture target ${aim} is below its horizon`);
      continue;
    }
    const spot = apertureSpot(toEcef(end.sub.lat, end.sub.lng, EARTH_RADIUS_KM + end.sub.altKm), toEcef(tgt.lat, tgt.lng, EARTH_RADIUS_KM), half);
    if (spot.ring.length < 3) { warnings.push(`${s.name}: an aperture of ${half}° misses the Earth`); continue; }
    if (spot.missed > 0) warnings.push(`${s.name}: an aperture of ${half}° reaches past the horizon; the spot is clipped`);
    const color = ap.color || s.color;
    const sname = escapeHtml(s.name);
    const tip = `${sname} aperture ${half}° toward ${escapeHtml(aim)}<br>spot about ${fmtKm(spot.alongKm)} × ${fmtKm(spot.crossKm)}`;
    const ring = spot.ring.map((p) => ({ lat: p.lat, lng: p.lng, alt: GROUND_ALT }));
    paths.push({ kind: 'aperture', name: `${sname} aperture`, points: spot.missed ? ring : [...ring, ring[0]], color, stroke: 0.5, tooltip: tip });
    const top = endpointPoint(end);
    for (let q = 0; q < 4; q++) {
      paths.push({ kind: 'aperture', name: `${sname} aperture edge`, points: straightSegment(top, ring[Math.floor((q * ring.length) / 4)], 16),
        color: withAlpha(color, 0.6), stroke: 0.15, tooltip: tip });
    }
  }

  // Day and night: the geometric terminator (no refraction) and the subsolar point.
  let subsolar: { lat: number; lng: number } | undefined;
  if (sunEci) {
    subsolar = subsolarPoint(time);
    paths.push({ kind: 'terminator', name: 'terminator', points: geodesicCircle(subsolar.lat, subsolar.lng, 90, GROUND_ALT, 180),
      color: '#fbbf24', stroke: 0.4, tooltip: 'Day/night terminator (geometric, no refraction)' });
    labels.push({ kind: 'point', name: 'Subsolar point', lat: subsolar.lat, lng: subsolar.lng, alt: GROUND_ALT, color: '#fbbf24',
      tooltip: `<b>Subsolar point</b><br>${fmtDeg(subsolar.lat)}, ${fmtDeg(subsolar.lng)}` });
  }

  const lookup = (n: unknown): Endpoint | undefined => satEnds.get(String(n)) || siteEnds.get(String(n));
  (Array.isArray(spec.links) ? spec.links : []).forEach((l, i) => {
    const a = lookup(l && l.from);
    const b = lookup(l && l.to);
    if (!a || !b) {
      warnings.push(`link ${i + 1}: unknown endpoint "${!a ? l && l.from : l && l.to}"`);
      return;
    }
    const sat = a.kind !== 'site' ? a : b.kind !== 'site' ? b : null;
    const p = linkPath(a, b, l.color || (sat ? sat.color : SITE_LINK_COLOR), clamp(finiteOr(l.width, 0.5), 0.1, 5), l.label);
    if (p.blocked) {
      warnings.push(`link ${i + 1} (${a.name} → ${b.name}): no line of sight, the Earth is in the way`);
    }
    paths.push(p);
  });

  if (spec.autoLinks) {
    const al = typeof spec.autoLinks === 'object' ? spec.autoLinks : {};
    const ae = clamp(finiteOr(al.minElevationDeg, minEl), 0, 90);
    let n = 0;
    let capped = false;
    for (const s of Array.from(satEnds.values())) {
      if (s.kind !== 'satellite') continue;
      for (const t of Array.from(siteEnds.values())) {
        if (elevationDeg(t.lat, t.lng, s.sub) < ae) continue;
        if (n >= MAX_AUTO_LINKS) { capped = true; break; }
        paths.push(linkPath(s, t, al.color || s.color, 0.4));
        n++;
      }
    }
    if (capped) warnings.push(`autoLinks stopped at ${MAX_AUTO_LINKS} links`);
  }

  // Routes: compare paths.  A hop between two sites runs along the great
  // circle at the ground medium's speed; a hop touching space is a straight
  // line at c.
  const ROUTE_PALETTE = ['#22c55e', '#f97316', '#a855f7', '#06b6d4', '#ef4444', '#eab308'];
  const routes: SceneRoute[] = [];
  (Array.isArray(spec.routes) ? spec.routes : []).forEach((r, i) => {
    const rname = String((r && r.name) || `route ${i + 1}`);
    const via = Array.isArray(r && r.via) ? r.via.map(String) : [];
    if (via.length < 2) { warnings.push(`${rname}: "via" needs at least two names`); return; }
    const ends = via.map((n) => lookup(n));
    const missing = via.filter((_, k) => !ends[k]);
    if (missing.length) {
      warnings.push(`${rname}: unknown name${missing.length > 1 ? 's' : ''} ${missing.map((m) => JSON.stringify(m)).join(', ')}`);
      return;
    }
    const groundFraction = speedFraction(r.groundMedium ?? 'fiber');
    if (groundFraction === null) {
      warnings.push(`${rname}: groundMedium must be "fiber", "vacuum" or a fraction of c in (0, 1], got ${JSON.stringify(r.groundMedium)}`);
      return;
    }
    const groundLabel = typeof r.groundMedium === 'number' ? `${r.groundMedium} c` : String(r.groundMedium ?? 'fiber').toLowerCase();
    const factor = Math.max(1, finiteOr(r.pathFactor, 1));
    const extra = Math.max(0, finiteOr(r.extraMs, 0));
    const color = r.color || ROUTE_PALETTE[routes.length % ROUTE_PALETTE.length];
    const hops: SceneRouteHop[] = [];
    const hopPaths: ScenePath[] = [];
    for (let k = 0; k + 1 < ends.length; k++) {
      const a = ends[k]!;
      const b = ends[k + 1]!;
      if (a.kind === 'site' && b.kind === 'site') {
        const km = centralAngleDeg(a.lat, a.lng, b.lat, b.lng) * D2R * EARTH_RADIUS_KM * factor;
        hops.push({ from: a.name, to: b.name, medium: groundLabel, km, ms: delayMs(km, groundFraction), blocked: false });
        hopPaths.push({ kind: 'route', name: escapeHtml(rname), points: surfaceArc(a, b), color, stroke: 0.9, tooltip: '' });
      } else {
        const p = linkPath(a, b, color, 0.9);
        const km = chordDistanceKm(endpointPoint(a), endpointPoint(b));
        hops.push({ from: a.name, to: b.name, medium: 'vacuum', km, ms: delayMs(km, 1), blocked: !!p.blocked });
        hopPaths.push({ kind: 'route', name: escapeHtml(rname), points: p.points, color, stroke: 0.9, tooltip: '', blocked: p.blocked });
      }
    }
    const km = hops.reduce((m, h) => m + h.km, 0);
    const ms = hops.reduce((m, h) => m + h.ms, 0) + extra;
    const blocked = hops.some((h) => h.blocked);
    const tip = `<b>${escapeHtml(rname)}</b><br>${escapeHtml(via.join(' → '))}<br>${fmtKm(km)}, ${ms.toFixed(1)} ms one way, ${(2 * ms).toFixed(1)} ms round trip`
      + (factor > 1 ? `<br>ground hops ${factor}× the great circle` : '')
      + (extra ? `<br>includes ${extra} ms fixed delay` : '')
      + (blocked ? '<br><b>a hop is blocked by the Earth</b>' : '');
    hopPaths.forEach((p) => { p.tooltip = tip; paths.push(p); });
    hops.filter((h) => h.blocked).forEach((h) => warnings.push(`${rname}: hop ${h.from} → ${h.to} has no line of sight, the Earth is in the way`));
    routes.push({ name: rname, color, hops, km, ms, extraMs: extra, blocked });
  });

  const layer = buildCellLayer(spec, preparedCells, satEnds, siteEnds, minEl, paths, warnings);
  const first = labels.find((l) => l.kind === 'satellite') || labels[0];
  const center = first ? { lat: first.lat, lng: first.lng } : layer.center || { lat: 0, lng: 0 };
  const farthestAlt = labels.reduce((m, l) => Math.max(m, l.alt), 0);
  return { time, labels, paths, warnings, center, cells: layer.cells, cellStats: layer.cellStats, bodies, routes, subsolar, farthestAlt };
}

// ── H3 cells and beam assignment ───────────────────────────────────────────

export interface PreparedCell {
  id: string; lat: number; lng: number; ring: [number, number][]; demand: number; label?: string;
  group?: string;
  /** Partition group, 0..k-1, when cells.partition / colorBy "partition" is set. */
  part?: number;
}
export interface PreparedCells { cells: PreparedCell[]; warnings: string[]; partitionK?: number; }
export type CellStatus = 'served' | 'no-capacity' | 'not-visible';
export interface CellAssignment { id: string; status: CellStatus; sat?: string; ground?: boolean; elevationDeg?: number; rangeKm?: number; visible: number; demand: number; }
/** Something that can serve a cell: a satellite (sub-point with altitude) or a ground site (altKm 0). */
export interface CellServer { name: string; sub: SubPoint; }
export interface CellStats {
  total: number; served: number; noCapacity: number; notVisible: number;
  /** Beams in use per satellite, in the order satellites were first assigned. */
  beamsUsed: Record<string, number>;
  maxBeamsPerSatellite: number | null;
}

/** `#rgb` / `#rrggbb` as rgba(); any other colour string is returned as is. */
export function withAlpha(color: string, alpha: number): string {
  let hex = String(color).trim();
  if (/^#[0-9a-f]{3}$/i.test(hex)) hex = '#' + hex.slice(1).split('').map((c) => c + c).join('');
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/**
 * Make consecutive longitudes differ by < 180 deg.  A cell on the
 * antimeridian comes back from H3 as 179 / -179 / 180 and would otherwise
 * be drawn the long way round the globe.
 */
export function unwrapRing(ring: number[][]): [number, number][] {
  const out: [number, number][] = [];
  let prev: number | null = null;
  for (const [lng0, lat] of ring) {
    let lng = lng0;
    if (prev !== null) {
      while (lng - prev > 180) lng -= 360;
      while (lng - prev < -180) lng += 360;
    }
    out.push([lng, lat]);
    prev = lng;
  }
  return out;
}

/** H3 cells for the spec, built once per render (they do not move). */
export function prepareCells(spec: OrbitSpec): PreparedCells {
  const warnings: string[] = [];
  const def = spec.cells;
  if (!def || typeof def !== 'object') return { cells: [], warnings };
  const byId = new Map<string, { demand: number; label?: string; group?: string }>();
  const bad: string[] = [];
  const add = (raw: unknown, demand = 0, label?: string, group?: string) => {
    const id = String(raw).trim().toLowerCase();
    if (!isValidCell(id)) { bad.push(String(raw)); return; }
    const e = byId.get(id);
    if (e) {
      e.demand += demand;
      if (label && !e.label) e.label = label;
      if (group !== undefined && e.group === undefined) e.group = group;
    } else byId.set(id, { demand, label, group });
  };
  const res = def.resolution === undefined ? NaN : Math.round(finiteOr(def.resolution, NaN));
  const resOk = Number.isFinite(res) && res >= 0 && res <= 15;
  if (def.resolution !== undefined && !resOk) warnings.push(`cells: "resolution" must be an integer 0-15, got ${JSON.stringify(def.resolution)}`);
  const needRes = (what: string) => {
    if (!resOk) warnings.push(`cells: ${what} needs "resolution" (an integer 0-15)`);
    return resOk;
  };

  if (def.around) {
    const a = def.around;
    const lat = finiteOr(a.lat, NaN);
    const lng = finiteOr(a.lon ?? a.lng, NaN);
    const r = finiteOr(a.radiusKm, NaN);
    if (![lat, lng, r].every(Number.isFinite) || r <= 0 || Math.abs(lat) > 90) {
      warnings.push('cells.around needs numeric lat, lon and radiusKm > 0');
    } else if (needRes('"around"')) {
      const spacing = getHexagonEdgeLengthAvg(res, 'km') * Math.sqrt(3);
      let k = Math.ceil(r / spacing) + 1;
      const kMax = Math.floor((-3 + Math.sqrt(9 + 12 * (MAX_GRID_DISK - 1))) / 6);
      if (k > kMax) {
        warnings.push(`cells.around: radius too large for resolution ${res}; searched ${kMax} rings`);
        k = kMax;
      }
      for (const id of gridDisk(latLngToCell(lat, normalizeLng(lng), res), k)) {
        const [clat, clng] = cellToLatLng(id);
        if (centralAngleDeg(lat, lng, clat, clng) * D2R * EARTH_RADIUS_KM <= r) add(id);
      }
    }
  }
  (Array.isArray(def.ids) ? def.ids : []).forEach((id) => add(id));
  (Array.isArray(def.items) ? def.items : []).forEach((it, i) => {
    if (!it || typeof it !== 'object') return;
    const demand = Math.max(0, finiteOr(it.demand, 0));
    const label = it.label !== undefined && it.label !== null ? String(it.label) : undefined;
    const group = it.group !== undefined && it.group !== null ? String(it.group) : undefined;
    if (it.id !== undefined) { add(it.id, demand, label, group); return; }
    const lat = finiteOr(it.lat, NaN);
    const lng = finiteOr(it.lon ?? it.lng, NaN);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90) {
      warnings.push(`cells.items[${i}]: needs "id" or numeric "lat" and "lon"`);
      return;
    }
    if (needRes('"items" with lat/lon')) add(latLngToCell(lat, normalizeLng(lng), res), demand, label, group);
  });
  if (def.demand && typeof def.demand === 'object') {
    for (const [id, v] of Object.entries(def.demand)) add(id, Math.max(0, finiteOr(v, 0)));
  }
  if (bad.length) warnings.push(`cells: ${bad.length} invalid H3 id${bad.length > 1 ? 's' : ''}: ${bad.slice(0, 3).map((b) => JSON.stringify(b)).join(', ')}`);

  let entries = Array.from(byId.entries());
  if (entries.length > MAX_CELLS) {
    warnings.push(`cells: only the first ${MAX_CELLS} of ${entries.length} cells are drawn (highest demand kept)`);
    entries = entries.map((e, i) => [e, i] as const)
      .sort((a, b) => b[0][1].demand - a[0][1].demand || a[1] - b[1])
      .slice(0, MAX_CELLS).map(([e]) => e);
  }
  const cells: PreparedCell[] = entries.map(([id, e]) => {
    const [lat, lng] = cellToLatLng(id);
    return { id, lat, lng: normalizeLng(lng), ring: unwrapRing(cellToBoundary(id, true)), demand: e.demand, label: e.label, group: e.group };
  });
  // A partition does not change with time, so it is computed once here.
  let partitionK: number | undefined;
  const kRaw = def.partition ?? (def.colorBy === 'partition' ? 3 : undefined);
  if (kRaw !== undefined) {
    const part = partitionCells(cells.map((c) => c.id), finiteOr(kRaw, NaN));
    warnings.push(...part.warnings);
    if (part.index.size > 0) partitionK = Math.round(finiteOr(kRaw, 3));
    cells.forEach((c) => { const v = part.index.get(c.id); if (v !== undefined) c.part = v; });
  }
  return { cells, warnings: Array.from(new Set(warnings)), partitionK };
}

/**
 * Serve each cell from at most one server.  Cells are taken in order of
 * demand, then best candidate, then id; each takes the best server that can
 * reach it (a satellite above the mask by highest elevation or nearest; a
 * ground site within maxRangeKm, nearest first) and still has capacity.  So
 * with too little capacity the busiest cells win, and the rest fall back to
 * the next server or go unserved.  Deterministic for a given input.
 */
export function assignCells(
  cells: PreparedCell[],
  sats: CellServer[],
  opts: { policy?: string; maxBeamsPerSatellite?: unknown; minElevationDeg: number; maxRangeKm?: number },
): { assignments: CellAssignment[]; stats: CellStats } {
  const nearest = opts.policy === 'nearest';
  const capRaw = finiteOr(opts.maxBeamsPerSatellite, Infinity);
  const cap = Number.isFinite(capRaw) ? Math.max(0, Math.floor(capRaw)) : Infinity;
  const maxRange = Math.max(0, finiteOr(opts.maxRangeKm, 100));
  const byName = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  const ranked = cells.map((c, index) => {
    const cand: { name: string; e: number; km: number; ground: boolean }[] = [];
    for (const s of sats) {
      if (!(s.sub.altKm > 0)) {
        const km = centralAngleDeg(c.lat, c.lng, s.sub.lat, s.sub.lng) * D2R * EARTH_RADIUS_KM;
        if (km <= maxRange) cand.push({ name: s.name, e: 0, km, ground: true });
        continue;
      }
      const e = elevationDeg(c.lat, c.lng, s.sub);
      if (e < opts.minElevationDeg) continue;
      const km = chordDistanceKm({ lat: c.lat, lng: c.lng, alt: 0 }, { lat: s.sub.lat, lng: s.sub.lng, alt: s.sub.altKm / EARTH_RADIUS_KM });
      cand.push({ name: s.name, e, km, ground: false });
    }
    cand.sort(nearest ? (a, b) => a.km - b.km || byName(a.name, b.name) : (a, b) => b.e - a.e || a.km - b.km || byName(a.name, b.name));
    const score = cand.length === 0 ? -Infinity : nearest ? -cand[0].km : cand[0].e;
    return { c, index, cand, score };
  });
  const order = [...ranked].sort((a, b) => (b.c.demand - a.c.demand) || (b.score - a.score) || byName(a.c.id, b.c.id));
  const load = new Map<string, number>();
  const assignments = new Array<CellAssignment>(cells.length);
  const stats: CellStats = { total: cells.length, served: 0, noCapacity: 0, notVisible: 0, beamsUsed: {}, maxBeamsPerSatellite: Number.isFinite(cap) ? cap : null };
  for (const r of order) {
    const base = { id: r.c.id, visible: r.cand.length, demand: r.c.demand };
    if (r.cand.length === 0) { assignments[r.index] = { ...base, status: 'not-visible' }; stats.notVisible++; continue; }
    const pick = r.cand.find((x) => (load.get(x.name) || 0) < cap);
    if (!pick) { assignments[r.index] = { ...base, status: 'no-capacity' }; stats.noCapacity++; continue; }
    load.set(pick.name, (load.get(pick.name) || 0) + 1);
    assignments[r.index] = { ...base, status: 'served', sat: pick.name, ground: pick.ground, elevationDeg: pick.e, rangeKm: pick.km };
    stats.served++;
  }
  load.forEach((n, name) => { stats.beamsUsed[name] = n; });
  return { assignments, stats };
}

export function describeCellStats(st: CellStats): string {
  let out = `cells: ${st.served}/${st.total} served · ${st.noCapacity} at capacity · ${st.notVisible} out of reach`;
  const used = Object.entries(st.beamsUsed);
  if (used.length) {
    const cap = st.maxBeamsPerSatellite !== null ? `/${st.maxBeamsPerSatellite}` : '';
    out += ' · load ' + used.map(([n, k]) => `${n} ${k}${cap}`).join(', ');
  }
  return out;
}

function buildCellLayer(
  spec: OrbitSpec, preparedCells: PreparedCells | undefined, satEnds: Map<string, SatEnd>, siteEnds: Map<string, SiteEnd>,
  minEl: number, paths: ScenePath[], warnings: string[],
): { cells: SceneCell[]; cellStats?: CellStats; center?: { lat: number; lng: number } } {
  if (!spec.cells || typeof spec.cells !== 'object') return { cells: [] };
  const pc = preparedCells || prepareCells(spec);
  warnings.push(...pc.warnings);
  if (pc.cells.length === 0) return { cells: [] };
  // "assignment" is the generic name; "beams" is accepted for older specs.
  const bdRaw = spec.assignment ?? spec.beams;
  const bd: OrbitBeamsDef = bdRaw && typeof bdRaw === 'object' ? bdRaw : {};
  const mask = clamp(finiteOr(bd.minElevationDeg, minEl), 0, 90);
  const servers = bd.servers === 'sites' || bd.servers === 'all' ? bd.servers : 'satellites';
  const maxRange = Math.max(0, finiteOr(bd.maxRangeKm, 100));
  const pool: CellServer[] = [];
  if (servers !== 'sites') satEnds.forEach((s) => { if (s.kind === 'satellite') pool.push({ name: s.name, sub: s.sub }); });
  if (servers !== 'satellites') siteEnds.forEach((s) => pool.push({ name: s.name, sub: { lat: s.lat, lng: s.lng, altKm: 0 } }));
  const { assignments, stats } = assignCells(pc.cells, pool, {
    policy: bd.policy, maxBeamsPerSatellite: bd.maxBeamsPerSatellite ?? bd.capacity, minElevationDeg: mask, maxRangeKm: maxRange,
  });
  const noneText = servers === 'sites' ? `no server within ${maxRange} km`
    : servers === 'all' ? `no satellite above ${mask}° or site within ${maxRange} km` : `no satellite above ${mask}°`;
  const serverWord = servers === 'satellites' ? 'satellite' : 'server';
  const showBeams = bd.show !== false;
  let drawn = 0;
  const cells: SceneCell[] = pc.cells.map((c, i) => {
    const a = assignments[i];
    const head = `<b>${escapeHtml(c.label ? `${c.label} (${c.id})` : c.id)}</b>` + (c.demand ? `<br>demand ${c.demand}` : '');
    const sat = a.sat && !a.ground ? satEnds.get(a.sat) : undefined;
    const site = a.sat && a.ground ? siteEnds.get(a.sat) : undefined;
    if (a.status === 'served' && (sat || site)) {
      const color = sat ? sat.color : SITE_LINK_COLOR;
      const sname = escapeHtml(a.sat!);
      const detail = sat
        ? `${a.elevationDeg!.toFixed(1)}°<br>range ${fmtKm(a.rangeKm!)}, ${fmtLight(a.rangeKm!)}`
        : `${fmtKm(a.rangeKm!)} away`;
      const others = a.visible > 1 ? `<br>${a.visible - 1} other ${serverWord}${a.visible > 2 ? 's' : ''} in reach` : '';
      if (showBeams && drawn < MAX_BEAM_LINES) {
        const target = { lat: c.lat, lng: c.lng, alt: CELL_ALT };
        const points = sat ? straightSegment(endpointPoint(sat), target, 12) : surfaceArc(site!, target, CELL_ALT, 16);
        paths.push({ kind: 'beam', name: `${sname} → ${escapeHtml(c.id)}`, color: withAlpha(color, 0.75), stroke: 0.15, points,
          tooltip: `${head}<br>link from ${sname}${sat ? ' at ' : ', '}${detail}` });
        drawn++;
      }
      return { id: c.id, ring: c.ring, status: a.status, sat: a.sat, fill: withAlpha(color, 0.45), stroke: withAlpha(color, 0.9),
        tooltip: `${head}<br>served by ${sname}${sat ? ' at ' : ', '}${detail}${others}` };
    }
    if (a.status === 'no-capacity') {
      return { id: c.id, ring: c.ring, status: a.status, ...CELL_NO_CAPACITY,
        tooltip: `${head}<br>${a.visible} ${serverWord}${a.visible > 1 ? 's' : ''} ${servers === 'satellites' ? `above ${mask}°` : 'in reach'}, all at capacity (${stats.maxBeamsPerSatellite})` };
    }
    return { id: c.id, ring: c.ring, status: 'not-visible' as const, ...CELL_UNSERVED, tooltip: `${head}<br>${noneText}` };
  });
  // Fill by partition group or item group instead of by server; the outline
  // and hover still say who serves the cell.
  const cdef = spec.cells as OrbitCellsDef;
  const colorBy = cdef.colorBy || (pc.partitionK !== undefined ? 'partition' : 'server');
  if (colorBy === 'partition' || colorBy === 'group') {
    const groups = colorBy === 'group' ? groupColors(pc.cells.map((c) => c.group)) : null;
    cells.forEach((sc, i) => {
      const c = pc.cells[i];
      const base = groups ? (c.group !== undefined ? groups.get(c.group) : undefined) : (c.part !== undefined ? PARTITION_PALETTE[c.part] : undefined);
      if (!base) return;
      sc.fill = withAlpha(base, sc.status === 'served' ? 0.5 : 0.2);
      sc.tooltip += groups ? `<br>group ${escapeHtml(c.group!)}` : `<br>partition ${c.part! + 1} of ${pc.partitionK}`;
    });
  }
  if (showBeams && stats.served > MAX_BEAM_LINES) {
    warnings.push(`link lines drawn for ${MAX_BEAM_LINES} of ${stats.served} served cells; cell outlines show every assignment`);
  }
  return { cells, cellStats: stats, center: { lat: pc.cells[0].lat, lng: pc.cells[0].lng } };
}
