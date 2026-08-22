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
