/**
 * Which Ziya installation is this browser tab talking to?
 *
 * Everything the browser persists (IndexedDB, localStorage, BroadcastChannel
 * names, Web Locks) is keyed by origin -- localhost:PORT -- and an origin says
 * nothing about the server behind it.  A port reused by a different server,
 * an ssh tunnel to another host, or a half-bound tunnel (ssh holding ::1
 * while a local Ziya holds 127.0.0.1) all present a foreign backend under a
 * familiar origin.  The server reports a stable id (~/.ziya/instance_id) in
 * StartupInfo.instanceId and on every response as X-Ziya-Instance; this
 * module is the one place the client reads it.
 *
 * Three rules, nothing else:
 *   1. Every origin-scoped name is suffixed with the instance id via
 *      scopedName().  An unowned legacy 'ZiyaDB' is never adopted -- there is
 *      no way to tell "ours from before the upgrade" from "a dead server's
 *      leftovers", and both incidents walked through exactly that door.
 *   2. The first id seen is latched.  A later response carrying a DIFFERENT
 *      id halts the tab: mutating API calls are refused and sync stops.  That
 *      is the half-bound-tunnel case, where the backend flips per connection
 *      and no amount of namespacing helps.
 *   3. A server that reports no id (pre-feature backend) is `null`: names are
 *      unsuffixed, exactly today's behaviour.  null never overrides a real
 *      id, and a real id arriving after null is a mismatch.
 *
 * Callers that need a name before the id is known must await
 * whenInstanceKnown().  scopedNameSync() throws if called too early -- a
 * name that silently came out unsuffixed would recreate the shared-cache bug
 * this module exists to close, so failing loudly is the safer default.
 */

export const INSTANCE_HEADER = 'X-Ziya-Instance';

/** `null` = connected server reports no instance id (legacy backend). */
export type InstanceId = string | null;

export interface InstanceHalt {
    expected: InstanceId;
    got: string;
    at: number;
}

export class InstanceMismatchError extends Error {
    constructor(public readonly halt: InstanceHalt, label: string) {
        super(
            `${label} refused: this address is now serving a different Ziya instance ` +
            `(expected ${halt.expected ?? 'legacy/none'}, got ${halt.got}). Reload after fixing the port conflict.`,
        );
        this.name = 'InstanceMismatchError';
        Object.setPrototypeOf(this, InstanceMismatchError.prototype);
    }
}

// undefined = not yet known.  Module state, not React state: db.ts and
// projectSync.ts are plain modules that need it before any provider mounts.
let known: InstanceId | undefined = undefined;
let halted: InstanceHalt | null = null;
let waiters: Array<(id: InstanceId) => void> = [];
const haltListeners = new Set<(h: InstanceHalt) => void>();

/** Suffix applied to every origin-scoped name for a given instance. */
export function storageSuffix(id: InstanceId): string {
    return id ? `__${id}` : '';
}

/** Scope an origin-shared name (DB, LS key, channel, lock) to an instance. */
export function scopedName(base: string, id: InstanceId): string {
    return `${base}${storageSuffix(id)}`;
}

/**
 * scopedName() using the latched id.  Throws if the id is not known yet:
 * see module comment for why silent fallback is the wrong default.
 */
export function scopedNameSync(base: string): string {
    if (known === undefined) {
        throw new Error(`scopedNameSync(${base}): instance id not known yet -- await whenInstanceKnown() first`);
    }
    return scopedName(base, known);
}

export function instanceIdSync(): InstanceId | undefined {
    return known;
}

export function whenInstanceKnown(): Promise<InstanceId> {
    if (known !== undefined) return Promise.resolve(known);
    return new Promise(resolve => { waiters.push(resolve); });
}

function triggerHalt(got: string): void {
    if (halted) return;
    halted = { expected: known ?? null, got, at: Date.now() };
    console.error(
        `🛑 Ziya instance mismatch: latched ${halted.expected ?? 'legacy/none'}, ` +
        `now receiving ${got}. Halting sync and writes for this tab.`,
    );
    for (const cb of Array.from(haltListeners)) {
        try { cb(halted); } catch (e) { console.error('instance halt listener failed', e); }
    }
}

/**
 * Record the connected server's id.  Called by the fetch latch on the first
 * response carrying the header, and explicitly by ProjectContext once
 * /projects/startup answers (or fails -- `null` for a legacy backend).
 */
export function setServerInstanceId(id: InstanceId): void {
    if (known === undefined) {
        // A `null` here only wins if no header has been seen; the explicit
        // call from ProjectContext arrives AFTER the startup response has
        // already passed through the latch, so a real id always lands first.
        known = id;
        console.log(`🔑 Ziya instance: ${id ?? 'legacy (server reports none)'}`);
        const ws = waiters; waiters = [];
        for (const w of ws) w(id);
        return;
    }
    if (id === null) return;          // null never overrides
    if (id !== known) triggerHalt(id);
}

/** Feed a response's headers to the latch. */
export function observeResponseHeaders(headers: Headers | null | undefined): void {
    const v = headers?.get(INSTANCE_HEADER);
    if (v) setServerInstanceId(v);
}

export function isInstanceHalted(): boolean { return halted !== null; }
export function instanceHalt(): InstanceHalt | null { return halted; }

export function onInstanceHalt(cb: (h: InstanceHalt) => void): () => void {
    haltListeners.add(cb);
    if (halted) cb(halted);
    return () => { haltListeners.delete(cb); };
}

/** Throw if the tab is halted.  For write paths that must not proceed. */
export function assertInstanceNotHalted(label: string): void {
    if (halted) throw new InstanceMismatchError(halted, label);
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
    const m = init?.method ?? (typeof Request !== 'undefined' && input instanceof Request ? input.method : 'GET');
    return (m || 'GET').toUpperCase();
}

function requestPath(input: RequestInfo | URL): string {
    try {
        const s = typeof input === 'string' ? input
            : input instanceof URL ? input.href
            : (input as Request).url;
        return new URL(s, window.location.href).pathname;
    } catch { return ''; }
}

let installed = false;

/**
 * Wrap window.fetch once so EVERY response is observed and, once halted,
 * every mutating /api request is refused.
 *
 * A wrapper rather than per-call-site plumbing for the reason timedFetch.ts
 * gives about its own deadline: sixty-odd files call fetch directly, and the
 * failure mode of a per-site guard is "one was missed".  Reads are still
 * allowed after a halt so the banner can render and the user can see state;
 * reads cannot corrupt the server record, and the sync loop checks
 * isInstanceHalted() itself so mixed answers do not reach IndexedDB.
 */
export function installFetchLatch(): void {
    if (installed || typeof window === 'undefined' || typeof window.fetch !== 'function') return;
    installed = true;
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        if (halted && !SAFE_METHODS.has(requestMethod(input, init)) && requestPath(input).startsWith('/api/')) {
            throw new InstanceMismatchError(halted, `${requestMethod(input, init)} ${requestPath(input)}`);
        }
        const res = await realFetch(input, init);
        try { observeResponseHeaders(res.headers); } catch { /* never break a response */ }
        return res;
    };
}

/** Test-only: forget everything, including the installed wrapper flag. */
export function _resetInstanceIdentityForTests(): void {
    known = undefined;
    halted = null;
    waiters = [];
    haltListeners.clear();
    installed = false;
}
