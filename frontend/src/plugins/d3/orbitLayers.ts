/**
 * Overlay geometry for the orbit fence: hexagon-grid partitions, field-of-view
 * cones, the Sun (subsolar point, Earth's shadow) and propagation delay by
 * medium.  Plain geometry with no domain model: a cone is any field of view
 * (camera, sensor, antenna, lamp) and a partition is graph colouring on the
 * H3 lattice.
 *
 * No three.js and no runtime import from orbitMath (which imports this
 * module), so jest covers it directly and there is no import cycle.
 */
import { cellToLocalIj, gridDisk } from 'h3-js';
import { degreesLat, degreesLong, eciToGeodetic, gstime } from 'satellite.js';
import { sunPositionKm, EciVec } from './orbitSources';

/** Mean Earth radius: the sphere orbitMath uses (EARTH_RADIUS_KM). */
const R_KM = 6371.0;
const C_KM_S = 299792.458;
const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

/** Group index of standard single-mode fibre near 1550 nm: light travels at c / 1.4677. */
export const FIBER_GROUP_INDEX = 1.4677;

/** Distinct, colour-blind-tolerant categorical colours (Tableau 10 plus two). */
export const PARTITION_PALETTE = [
  '#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#b07aa1', '#edc948',
  '#76b7b2', '#ff9da7', '#9c755f', '#bab0ac', '#8cd17d', '#d4a6c8',
];
export const MAX_PARTITION = PARTITION_PALETTE.length;

// ── Vectors (Earth-fixed or inertial, km) ──────────────────────────────────

export type V3 = [number, number, number];
const sub3 = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add3 = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale3 = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
const dot3 = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross3 = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm3 = (a: V3) => Math.hypot(a[0], a[1], a[2]);
const unit3 = (a: V3): V3 => scale3(a, 1 / (norm3(a) || 1));

export function toEcef(latDeg: number, lngDeg: number, rKm: number): V3 {
  const la = latDeg * D2R;
  const lo = lngDeg * D2R;
  return [rKm * Math.cos(la) * Math.cos(lo), rKm * Math.cos(la) * Math.sin(lo), rKm * Math.sin(la)];
}

export function fromEcef(v: V3): { lat: number; lng: number; rKm: number } {
  const r = norm3(v);
  return { lat: Math.asin(Math.max(-1, Math.min(1, v[2] / r))) * R2D, lng: Math.atan2(v[1], v[0]) * R2D, rKm: r };
}

// ── Partitions of the hexagon grid ─────────────────────────────────────────

/**
 * Split cells into k groups so no two neighbours share one.  On H3's local
 * IJ lattice (relative to the first cell) the six neighbour offsets are
 * (+-1, 0), (0, +-1), +-(1, 1), so (i + s j) mod k is a proper colouring with
 * s = 1 for k = 3 and s = 2 for k >= 4 (3, 4 and 7 give the classic
 * repeating tilings).  A hexagon grid has odd cycles, so k = 2 cannot work.
 * Cells H3 cannot place on that plane (another icosahedron face, a pentagon,
 * a different resolution) are coloured greedily from their neighbours.
 */
export function partitionCells(ids: string[], k: unknown): { index: Map<string, number>; warnings: string[] } {
  const index = new Map<string, number>();
  const warnings: string[] = [];
  const kk = typeof k === 'number' && Number.isFinite(k) ? Math.round(k) : NaN;
  if (!(kk >= 3 && kk <= MAX_PARTITION)) {
    warnings.push(`cells.partition must be an integer from 3 to ${MAX_PARTITION} (a hexagon grid always has a neighbour pair in 2 groups), got ${JSON.stringify(k)}`);
    return { index, warnings };
  }
  if (ids.length === 0) return { index, warnings };
  const s = kk === 3 ? 1 : 2;
  const origin = ids[0];
  const pending: string[] = [];
  for (const id of ids) {
    try {
      const ij = cellToLocalIj(origin, id);
      index.set(id, (((ij.i + s * ij.j) % kk) + kk) % kk);
    } catch {
      pending.push(id);
    }
  }
  let conflicts = 0;
  for (const id of pending) {
    const used = new Set<number>();
    for (const n of gridDisk(id, 1)) {
      const v = index.get(n);
      if (n !== id && v !== undefined) used.add(v);
    }
    let c = 0;
    while (used.has(c) && c < kk) c++;
    if (c >= kk) { c = 0; conflicts++; }
    index.set(id, c);
  }
  if (conflicts > 0) warnings.push(`cells.partition: ${conflicts} cell${conflicts > 1 ? 's' : ''} share a group with a neighbour (off the local grid)`);
  return { index, warnings };
}

/** One palette colour per distinct value, in first-seen order. */
export function groupColors(values: Array<string | undefined>): Map<string, string> {
  const out = new Map<string, string>();
  for (const v of values) {
    if (v === undefined || out.has(v)) continue;
    out.set(v, PARTITION_PALETTE[out.size % PARTITION_PALETTE.length]);
  }
  return out;
}

// ── Field-of-view cones ────────────────────────────────────────────────────

export interface ApertureSpot {
  /** Where the cone's edge meets the Earth, in azimuth order (not closed). */
  ring: { lat: number; lng: number }[];
  /** Edge rays that miss the Earth (the cone reaches past the horizon). */
  missed: number;
  /** Largest extent in the plane of apex, Earth centre and target (along the look direction). */
  alongKm: number;
  /** Largest extent across that plane. */
  crossKm: number;
}

/**
 * Intersect a cone of half-angle `halfAngleDeg`, from `apex` toward `target`
 * (both Earth-fixed km), with the spherical Earth.  At nadir the spot is a
 * circle of radius ~h tan(theta); off nadir it stretches by ~1/sin(elevation)
 * along the look direction.
 */
export function apertureSpot(apex: V3, target: V3, halfAngleDeg: number, segments = 96): ApertureSpot {
  const b = unit3(sub3(target, apex));
  const helper: V3 = Math.abs(b[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const u = unit3(cross3(b, helper));
  const v = cross3(b, u);
  const th = halfAngleDeg * D2R;
  const cTh = Math.cos(th);
  const sTh = Math.sin(th);
  const pp = dot3(apex, apex) - R_KM * R_KM;
  const hits: V3[] = [];
  let missed = 0;
  for (let k = 0; k < segments; k++) {
    const f = (k / segments) * 2 * Math.PI;
    const d = add3(scale3(b, cTh), scale3(add3(scale3(u, Math.cos(f)), scale3(v, Math.sin(f))), sTh));
    const pd = dot3(apex, d);
    const disc = pd * pd - pp;
    const t = disc >= 0 ? -pd - Math.sqrt(disc) : -1;
    if (t <= 0) { missed++; continue; }
    hits.push(add3(apex, scale3(d, t)));
  }
  // Look plane through apex, Earth centre and target; at nadir it is undefined, use u.
  let n = cross3(apex, target);
  n = norm3(n) < 1e-9 * norm3(apex) * norm3(target) ? u : unit3(n);
  let alongKm = 0;
  let crossKm = 0;
  for (let i = 0; i < hits.length; i++) {
    for (let j = i + 1; j < hits.length; j++) {
      const d = sub3(hits[i], hits[j]);
      const c = dot3(d, n);
      crossKm = Math.max(crossKm, Math.abs(c));
      alongKm = Math.max(alongKm, norm3(sub3(d, scale3(n, c))));
    }
  }
  return { ring: hits.map((p) => { const g = fromEcef(p); return { lat: g.lat, lng: g.lng }; }), missed, alongKm, crossKm };
}

// ── The Sun ────────────────────────────────────────────────────────────────

/** Point on the Earth with the Sun at the zenith at `date`. */
export function subsolarPoint(date: Date): { lat: number; lng: number } {
  const s = sunPositionKm(date);
  const g = eciToGeodetic(s as any, gstime(date));
  return { lat: degreesLat(g.latitude), lng: degreesLong(g.longitude) };
}

/**
 * Inside the Earth's shadow, modelled as a cylinder of the Earth's radius
 * behind it (the penumbra and the umbra's taper are below a pixel at LEO-GEO
 * scale on this globe).  Both vectors Earth-centred inertial, km.
 */
export function inEarthShadow(r: EciVec, sun: EciVec): boolean {
  const s = unit3([sun.x, sun.y, sun.z]);
  const p: V3 = [r.x, r.y, r.z];
  const along = dot3(p, s);
  if (along >= 0) return false;
  return norm3(sub3(p, scale3(s, along))) < R_KM;
}

// ── Delay by medium ────────────────────────────────────────────────────────

/** Speed as a fraction of c: "fiber"/"fibre", "vacuum", or a number in (0, 1]; else null. */
export function speedFraction(medium: unknown): number | null {
  if (typeof medium === 'number') return medium > 0 && medium <= 1 ? medium : null;
  if (typeof medium !== 'string') return null;
  const m = medium.trim().toLowerCase();
  if (m === 'fiber' || m === 'fibre') return 1 / FIBER_GROUP_INDEX;
  if (m === 'vacuum') return 1;
  return null;
}

/** One-way time, ms, over `km` at `fraction` of the speed of light. */
export function delayMs(km: number, fraction: number): number {
  return (km / (C_KM_S * fraction)) * 1000;
}
