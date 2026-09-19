/**
 * Joint.js link routing/connector name sanitizer — general fix for the
 * "one unknown router name annihilates the whole diagram" class of failure
 * (graphics-stress Issue 29).
 *
 * SYMPTOM: a single link whose `router` (or `connector`) is an unknown name — or,
 * worse, an OBJECT like `{ name: "exotic-nonexistent-router" }` — makes JointJS
 * throw `dia.LinkView: unknown router: "[object Object]"` while rendering the link
 * view. Because JointJS flushes ALL pending link views in one shared batch, that
 * throw surfaces during EVERY link's `addCell` (even links with no router at all),
 * so the per-link try/catch drops every link, and the subsequent `DirectedGraph`
 * auto-layout (`fromGraphLib`/`importElement`) re-throws the same error and aborts
 * the entire render — a totally blank canvas, complete data loss.
 *
 * ROOT CAUSE (two compounding bugs):
 *   1. `createEnhancedLink`/`createLink` build `router: { name: linkSpec.router }`.
 *      When `linkSpec.router` is the object `{ name: "..." }` (the shape render_diagram
 *      passes), the router NAME becomes an object → `routers[[object Object]]` misses.
 *   2. Even a valid-shaped but unrecognized name string (`"metroX"`) throws, and the
 *      throw is not isolated to the offending link.
 *
 * FIX: normalize the raw `router`/`connector` — whatever its shape — to a KNOWN
 * JointJS name string, falling back to a safe default for anything unrecognized.
 * This coerces the whole family of malformed routing values (object-shaped, unknown
 * string, null, number, empty) to something JointJS can always resolve, so no throw
 * ever reaches the shared view-flush or the auto-layout. `args` supplied alongside a
 * valid name are preserved; args attached to an unknown name are dropped with it.
 *
 * Exported as a pure data->data helper so it is unit-testable without a DOM or
 * @joint/core.
 */

// The router/connector names JointJS registers by default. Keep these in sync with
// @joint/core's `routers` and `connectors` namespaces. An unknown name is exactly what
// makes `findRoute()` throw, so this set is the authoritative "will not throw" list.
export const KNOWN_JOINT_ROUTERS: ReadonlySet<string> = new Set([
    'normal',
    'manhattan',
    'metro',
    'orthogonal',
    'oneSide',
    'rightAngle',
]);

export const KNOWN_JOINT_CONNECTORS: ReadonlySet<string> = new Set([
    'normal',
    'rounded',
    'smooth',
    'jumpover',
    'straight',
    'curve',
]);

export const DEFAULT_JOINT_ROUTER = 'normal';
export const DEFAULT_JOINT_CONNECTOR = 'rounded';

/**
 * Pull a candidate name string out of whatever shape the spec used:
 *   - a bare string        -> the string
 *   - `{ name: "x", ... }`  -> "x"
 *   - anything else         -> null
 */
const extractName = (raw: any): string | null => {
    if (typeof raw === 'string') return raw.trim() || null;
    if (raw && typeof raw === 'object' && typeof raw.name === 'string') {
        return raw.name.trim() || null;
    }
    return null;
};

const extractArgs = (raw: any): any => {
    if (raw && typeof raw === 'object' && !Array.isArray(raw) && raw.args && typeof raw.args === 'object') {
        return raw.args;
    }
    return undefined;
};

/**
 * Normalize a raw router value to a JointJS router config `{ name, args? }` whose
 * `name` is guaranteed to be a KNOWN router. Unknown/malformed values (object-shaped
 * unknown names, garbage strings, null, numbers) fall back to `defaultName`.
 */
export function sanitizeRouter(
    raw: any,
    defaultName: string = DEFAULT_JOINT_ROUTER,
    defaultArgs?: any
): { name: string; args?: any } {
    const name = extractName(raw);
    if (name !== null && KNOWN_JOINT_ROUTERS.has(name)) {
        const args = extractArgs(raw);
        return args !== undefined ? { name, args } : (defaultArgs !== undefined ? { name, args: defaultArgs } : { name });
    }
    return defaultArgs !== undefined ? { name: defaultName, args: defaultArgs } : { name: defaultName };
}

/**
 * Normalize a raw connector value to a JointJS connector config `{ name, args? }`
 * whose `name` is guaranteed to be a KNOWN connector. Same fallback semantics as
 * sanitizeRouter.
 */
export function sanitizeConnector(
    raw: any,
    defaultName: string = DEFAULT_JOINT_CONNECTOR,
    defaultArgs?: any
): { name: string; args?: any } {
    const name = extractName(raw);
    if (name !== null && KNOWN_JOINT_CONNECTORS.has(name)) {
        const args = extractArgs(raw);
        return args !== undefined ? { name, args } : (defaultArgs !== undefined ? { name, args: defaultArgs } : { name });
    }
    return defaultArgs !== undefined ? { name: defaultName, args: defaultArgs } : { name: defaultName };
}

/** True iff `raw` (string or {name}) resolves to a router JointJS will accept. */
export function isKnownRouter(raw: any): boolean {
    const name = extractName(raw);
    return name !== null && KNOWN_JOINT_ROUTERS.has(name);
}

/** True iff `raw` (string or {name}) resolves to a connector JointJS will accept. */
export function isKnownConnector(raw: any): boolean {
    const name = extractName(raw);
    return name !== null && KNOWN_JOINT_CONNECTORS.has(name);
}

// ---------------------------------------------------------------------------
// Link endpoint identity, self-loop routing and label de-collision.
//
// D-411 (self-loop-zero-length-invisible): a link whose source and target are
// the SAME element was anchored modelCenter->modelCenter with a boundary
// connectionPoint, so both ends resolved to the node centre and the whole link
// (and its label) collapsed to zero length inside the body — invisible. A
// self-loop must instead terminate on two DIFFERENT sides of the node so it
// draws as a visible arc that bows out past the boundary.
//
// D-131 / D-407 (link-overdraw-no-label-background / link-label-collision-
// overdraw): labels were placed at `position: 0.5` centred ON the stroke, so
// every label was bisected lengthwise by its own line, and parallel links
// between the same node pair (e.g. the a<->b 2-cycle) stacked their labels at
// the identical midpoint into an unreadable pile. Labels must be lifted
// perpendicular OFF the stroke and, when several links share a node pair,
// staggered along the link and to alternating sides so they separate.
// ---------------------------------------------------------------------------

/** Extract the element id from a link endpoint (string or `{ id }` object). */
export function endpointId(raw: any): string | null {
    if (typeof raw === 'string') return raw || null;
    if (raw && typeof raw === 'object' && typeof raw.id === 'string') return raw.id || null;
    return null;
}

/** True when a link's source and target resolve to the same element id (self-loop). */
export function isSelfLoop(source: any, target: any): boolean {
    const s = endpointId(source);
    const t = endpointId(target);
    return s !== null && t !== null && s === t;
}

/**
 * An unordered key for the node pair a link connects, so antiparallel links
 * (a->b and b->a) and true parallels share one bucket for label staggering.
 */
export function linkPairKey(source: any, target: any): string {
    const s = endpointId(source) ?? '';
    const t = endpointId(target) ?? '';
    return s <= t ? `${s}\u0000${t}` : `${t}\u0000${s}`;
}

/**
 * Anchors + connector for a self-loop so it draws as a visible arc instead of
 * collapsing to the node centre. Source leaves the top, target re-enters the
 * right side; a smooth connector bows the segment out past the corner. The
 * boundary connectionPoint clamps each end to the node edge so no stroke runs
 * under the body.
 */
export function selfLoopEndpointConfig(): {
    sourceAnchor: { name: string };
    targetAnchor: { name: string };
    connectionPoint: { name: string };
    connector: { name: string; args?: any };
} {
    return {
        sourceAnchor: { name: 'top' },
        targetAnchor: { name: 'right' },
        connectionPoint: { name: 'boundary' },
        connector: { name: 'smooth' },
    };
}

/** Perpendicular distance (px) a label is lifted off its own link stroke. */
export const LABEL_STROKE_OFFSET = 14;

/**
 * Compute a JointJS label `position` ({ distance, offset }) that keeps the
 * label off the stroke and, when a node pair carries several links, staggers
 * the labels so they do not pile up at one midpoint.
 *
 * - `offset` is the perpendicular distance from the connection (a number is
 *   interpreted by JointJS as an offset normal to the link), so a non-zero
 *   value always lifts the text clear of the line it labels.
 * - `distance` is the fractional position along the link (0..1).
 *
 * With a single link the label sits at mid-link, lifted above the stroke. With
 * `count > 1` the labels spread across the middle of the link and alternate
 * sides, so antiparallel/parallel links separate.
 */
export function computeLabelPlacement(
    index: number = 0,
    count: number = 1
): { distance: number; offset: number } {
    const off = LABEL_STROKE_OFFSET;
    if (!Number.isFinite(count) || count <= 1) {
        return { distance: 0.5, offset: -off };
    }
    const i = Number.isFinite(index) ? Math.max(0, Math.min(index, count - 1)) : 0;
    // Spread label anchors across the central 40% of the link.
    const distance = 0.3 + (0.4 * i) / (count - 1);
    // Alternate sides and grow the magnitude slightly per pair member so labels
    // that share a distance band still separate.
    const side = i % 2 === 0 ? -1 : 1;
    const magnitude = off + Math.floor(i / 2) * 6;
    return { distance, offset: side * magnitude };
}
