/**
 * Arm the native drag helper (app/utils/drag_pasteboard_helper.py).
 *
 * On macOS a server-side helper rewrites the drag pasteboard so a diagram
 * or table dragged out of the chat lands in Slack desktop as a PNG file —
 * something a web page cannot do itself (Chromium forbids file: URLs on a
 * drag).  The helper polls slowly until told a drag is imminent; this
 * fire-and-forget POST on pointerdown is that signal, and tells it which
 * origin's drags are ours.
 *
 * Throttled to one request per second (the helper stays armed for five),
 * and switched off for the session once the server says the helper is not
 * available on this platform, so a Linux or Windows install never pays for
 * it beyond the first request.
 */

const ARM_PATH = '/api/v1/drag/arm';
const MIN_INTERVAL_MS = 1000;

let lastArmAt = 0;
let unavailable = false;

/** Returns true when a request was sent this call. */
export function armNativeDrag(
    origin: string = window.location.origin,
    now: number = Date.now(),
): boolean {
    if (unavailable || typeof fetch !== 'function') return false;
    if (now - lastArmAt < MIN_INTERVAL_MS) return false;
    lastArmAt = now;
    fetch(ARM_PATH, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin }),
        keepalive: true,
    })
        .then(async (res) => {
            if (res.status === 404) { unavailable = true; return; }
            const body = await res.json().catch(() => null);
            if (body && body.supported === false) unavailable = true;
        })
        .catch(() => { /* the drag works without the helper */ });
    return true;
}

/** Test seam: forget the throttle and availability state. */
export function resetNativeDragArm(): void {
    lastArmAt = 0;
    unavailable = false;
}
