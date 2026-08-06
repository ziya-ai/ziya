/**
 * Joint.js geometry sanitizer — general fix for the "one runaway coordinate/size
 * annihilates the whole diagram" class of failure (graphics-stress Issue 16, the
 * joint analogue of drawio Issue 8).
 *
 * SYMPTOM: a single element at an extreme position (x=1e8) or with an absurd size
 * (1e7 x 1e7), or a link waypoint at ±1e9, inflates the JointJS graph bounding box to
 * the order of tens-of-millions/billions of px. The plugin's fit-to-content pass then
 * sets `SVG viewBox` to that size and enters a runaway "Paper dimensions updated /
 * Container width changed" resize loop. Every legitimate node (80x80 at the origin)
 * rasterizes sub-pixel, and — worse — the element never stabilizes, so the headless
 * screenshot's "waiting for element to be stable" never resolves and the page is closed:
 * total data loss, no image at all.
 *
 * Why a fixed absolute cap is wrong (same reasoning as drawio's sanitizeDrawioCoordinates):
 * the offending magnitude is RELATIVE, not absolute. A lone cell at x=100000 still squashes
 * a cluster sitting at the origin. The robust fix is OUTLIER detection: compute the median
 * position and the Median Absolute Deviation (MAD, which a single gross outlier barely
 * moves) for x and y, and clamp only coordinates far outside the bulk to the cluster edge.
 * Evenly-spread legitimate diagrams (even large ones spanning thousands of px) have a large
 * MAD, so the allowed window is wide and nothing is touched; a tightly-clustered diagram
 * with one runaway coordinate has a tiny MAD, so the runaway is pulled back — exactly the
 * "one bad cell must not annihilate the other N" behavior we want. Sizes are clamped to a
 * relative-to-median cap (with a generous floor so legitimate large containers survive), and
 * non-finite / negative / zero sizes are coerced to a sane default. A hard finite backstop
 * and NaN/Infinity/null coercion remain as a last line of defense.
 *
 * Exported as a pure data->data helper so it is unit-testable without a DOM or @joint/core.
 */

export interface SanitizePosition {
    x: number;
    y: number;
}

// Structural subset of JointElement / JointLink we touch. Kept local + permissive so this
// module has NO dependency on @joint/core (so the regression test can import it directly).
export interface SanitizableElement {
    id?: string;
    position?: { x: number | string | null; y: number | string | null } | [number, number] | any;
    size?: { width: number | string | null; height: number | string | null } | any;
    [k: string]: any;
}
export interface SanitizableLink {
    id?: string;
    vertices?: Array<{ x: number | string | null; y: number | string | null } | any>;
    [k: string]: any;
}

export const JOINT_ABSOLUTE_LIMIT = 100000; // hard finite backstop (NaN/Infinity/overflow)
const POS_OUTLIER_K = 12;                    // position window = max(MAD * K, MIN_POS_WINDOW)
const MIN_POS_WINDOW = 3000;                 // floor; only binds when cells are tightly clustered
const MIN_DIM_CAP = 6000;                    // keep legit large containers, kill absurd dimensions
const DEFAULT_DIM = 80;                      // fallback for non-finite / non-positive sizes

const toFinite = (v: any): number | null => {
    // Accept numbers and numeric strings; reject NaN/Infinity/null/undefined/"NaN".
    if (v === null || v === undefined) return null;
    const n = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(n) ? n : null;
};

const median = (arr: number[]): number => {
    if (arr.length === 0) return 0;
    const s = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};
const mad = (arr: number[], med: number): number =>
    arr.length === 0 ? 0 : median(arr.map(v => Math.abs(v - med)));

/** Read {x,y} from either object or [x,y] tuple form; returns null components for junk. */
const readPos = (pos: any): { x: number | null; y: number | null } => {
    if (Array.isArray(pos)) return { x: toFinite(pos[0]), y: toFinite(pos[1]) };
    if (pos && typeof pos === 'object') return { x: toFinite(pos.x), y: toFinite(pos.y) };
    return { x: null, y: null };
};

/**
 * Sanitize element positions/sizes and link vertices in place-safe fashion (returns new
 * objects; does not mutate the caller's inputs). Pure and DOM-free.
 */
export function sanitizeJointGeometry<
    E extends SanitizableElement,
    L extends SanitizableLink
>(elements: E[], connections: L[]): { elements: E[]; connections: L[] } {
    const els = Array.isArray(elements) ? elements : [];
    const conns = Array.isArray(connections) ? connections : [];

    // 1) Collect POSITION samples (element positions + link vertices) and DIMENSION samples.
    const xs: number[] = [];
    const ys: number[] = [];
    const dims: number[] = [];

    for (const el of els) {
        const { x, y } = readPos(el?.position);
        if (x !== null) xs.push(x);
        if (y !== null) ys.push(y);
        const size = el?.size;
        if (size && typeof size === 'object') {
            const w = toFinite(size.width);
            const h = toFinite(size.height);
            if (w !== null) dims.push(Math.abs(w));
            if (h !== null) dims.push(Math.abs(h));
        }
    }
    for (const link of conns) {
        if (Array.isArray(link?.vertices)) {
            for (const v of link.vertices) {
                const vx = toFinite(v?.x);
                const vy = toFinite(v?.y);
                if (vx !== null) xs.push(vx);
                if (vy !== null) ys.push(vy);
            }
        }
    }

    const mx = median(xs);
    const my = median(ys);
    const rx = Math.max(mad(xs, mx) * POS_OUTLIER_K, MIN_POS_WINDOW);
    const ry = Math.max(mad(ys, my) * POS_OUTLIER_K, MIN_POS_WINDOW);
    const dimCap = Math.min(Math.max(median(dims) * POS_OUTLIER_K, MIN_DIM_CAP), JOINT_ABSOLUTE_LIMIT);

    const clampPos = (raw: any, center: number, radius: number): number => {
        const v = toFinite(raw);
        const safe = v === null ? center : v; // NaN/null position snaps to the cluster center
        const lo = Math.max(center - radius, -JOINT_ABSOLUTE_LIMIT);
        const hi = Math.min(center + radius, JOINT_ABSOLUTE_LIMIT);
        return Math.max(lo, Math.min(hi, safe));
    };
    const clampDim = (raw: any): number => {
        const v = toFinite(raw);
        // non-finite / null / <=0 => default; else clamp to [1, dimCap]
        if (v === null || v <= 0) return DEFAULT_DIM;
        return Math.max(1, Math.min(dimCap, v));
    };

    const outElements = els.map(el => {
        const next: any = { ...el };
        // position (preserve tuple vs object form)
        if (Array.isArray(el?.position)) {
            next.position = [
                clampPos(el.position[0], mx, rx),
                clampPos(el.position[1], my, ry),
            ];
        } else if (el?.position && typeof el.position === 'object') {
            next.position = {
                ...el.position,
                x: clampPos(el.position.x, mx, rx),
                y: clampPos(el.position.y, my, ry),
            };
        }
        // size
        if (el?.size && typeof el.size === 'object') {
            next.size = {
                ...el.size,
                width: clampDim(el.size.width),
                height: clampDim(el.size.height),
            };
        }
        return next as E;
    });

    const outConnections = conns.map(link => {
        if (!Array.isArray(link?.vertices)) return link;
        const next: any = { ...link };
        next.vertices = link.vertices.map((v: any) => ({
            ...v,
            x: clampPos(v?.x, mx, rx),
            y: clampPos(v?.y, my, ry),
        }));
        return next as L;
    });

    return { elements: outElements, connections: outConnections };
}
