/**
 * Shared request deadline for the conversation / folder sync APIs.
 *
 * A browser `fetch` has no default timeout.  A request can stay pending
 * indefinitely — laptop sleep/wake, a proxy that half-closes, a network
 * change mid-flight — and nothing will fail it for you.  ChatContext's
 * periodic sync arms `periodicSyncInFlightRef` before its `try` and clears
 * it only in the `finally`, so a request that never settles means the
 * `finally` never runs and every later 30s tick returns at the in-flight
 * guard: cross-instance updates stop arriving for the life of the page,
 * with no error logged anywhere.
 *
 * Every request in those two modules goes through here, deliberately.  The
 * failure mode is "one call site was missed", so the guarantee has to be
 * module-wide to be worth anything.
 */

/**
 * Kept under the 30s poll interval on purpose: a listChats still in flight
 * when the next tick fires can only lose to that tick, so waiting longer
 * buys nothing and extends the window in which the in-flight flag is held.
 */
export const LIST_TIMEOUT_MS = 25_000;
/** Single-record read; small payload, no reason to wait long. */
export const SINGLE_TIMEOUT_MS = 20_000;
/** Bulk body transfer legitimately moves megabytes, so it gets more room. */
export const BULK_TIMEOUT_MS = 60_000;
/** Writes and deletes: bounded, but tolerant of a busy server. */
export const MUTATE_TIMEOUT_MS = 30_000;

/** Raised when OUR deadline aborted the request (not a caller abort). */
export class SyncTimeoutError extends Error {
    readonly isTimeout = true;
    constructor(public readonly label: string, public readonly timeoutMs: number) {
        super(`${label} timed out after ${timeoutMs}ms`);
        this.name = 'SyncTimeoutError';
        // Subclassed Error loses its prototype under some transpile targets,
        // which silently breaks `instanceof` at the call sites that switch on it.
        Object.setPrototypeOf(this, SyncTimeoutError.prototype);
    }
}

/**
 * Raised for a non-2xx response.  Distinct from a transport failure because
 * the call sites treat them differently: "the server answered no" is often a
 * benign empty result, while "we could not ask" must never be mistaken for one.
 */
export class SyncHttpError extends Error {
    constructor(
        public readonly label: string,
        public readonly status: number,
        public readonly statusText: string = '',
    ) {
        super(`${label} failed: HTTP ${status}${statusText ? ` ${statusText}` : ''}`);
        this.name = 'SyncHttpError';
        Object.setPrototypeOf(this, SyncHttpError.prototype);
    }
}

/**
 * fetch + JSON parse under a single abort deadline.
 *
 * The timer spans the BODY read as well as the headers.  A stalled response
 * stream hangs `res.json()` exactly as a stalled request hangs `fetch`, so
 * clearing the deadline once headers arrive would leave that second — and
 * equally wedging — case unguarded.
 *
 * Throws SyncTimeoutError on deadline, SyncHttpError on non-2xx, and passes
 * any other transport error through untouched.
 */
export async function timedFetchJson<T>(
    url: string,
    init: RequestInit,
    timeoutMs: number,
    label: string,
): Promise<T> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    try {
        const res = await fetch(url, { ...init, signal: controller.signal });
        if (!res.ok) throw new SyncHttpError(label, res.status, res.statusText);
        return (await res.json()) as T;
    } catch (e) {
        // Our abort surfaces as a DOMException indistinguishable from a
        // caller-initiated one, so the flag — not the error — decides.
        if (timedOut) throw new SyncTimeoutError(label, timeoutMs);
        throw e;
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Status-only variant for requests with no body worth parsing (DELETE).
 *
 * Deliberately does NOT throw on a non-2xx: these callers inspect the status
 * themselves (a 404 on delete means "already gone", which is success).
 */
export async function timedFetchOk(
    url: string,
    init: RequestInit,
    timeoutMs: number,
    label: string,
): Promise<{ ok: boolean; status: number }> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    try {
        const res = await fetch(url, { ...init, signal: controller.signal });
        return { ok: res.ok, status: res.status };
    } catch (e) {
        if (timedOut) throw new SyncTimeoutError(label, timeoutMs);
        throw e;
    } finally {
        clearTimeout(timer);
    }
}
