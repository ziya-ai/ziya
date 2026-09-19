/**
 * Scroll-velocity tracker + edge-jump helper for the chat container.
 *
 * Why this exists
 * ---------------
 * Settled messages render as fixed-height placeholder "shells" until they
 * scroll near the viewport, at which point the real MarkdownRenderer mounts
 * (see LazyMarkdownRenderer in Conversation.tsx).  Each mount is 100-300ms
 * of synchronous main-thread work AND replaces an *estimated* height with
 * the real one.  Doing that in the middle of a fast scroll gesture is the
 * worst possible time: every shell the user flies past stalls the gesture
 * and reflows the document under them, so travelling to the top or bottom
 * of a long conversation takes forever.
 *
 * The tracker answers one question — "is the user flinging right now?" —
 * and offers a settle callback that fires once scrolling has stopped, so
 * shells can defer their reveal until the user has arrived somewhere.
 *
 * A single shared instance is registered by useScrollManager (which owns
 * the container ref) and consumed by Conversation.tsx (which does not).
 */

export interface ScrollVelocityOptions {
    /** |Δy|/Δt above this counts as a fling.  Default 2 px/ms (≈2000 px/s). */
    flingPxPerMs?: number;
    /** Quiet period after the last scroll event before `onSettle` fires. */
    settleMs?: number;
    /**
     * How long an input event (wheel, touch, scrollbar pointerdown, nav key)
     * keeps the tracker armed with no scroll event following it, and how long
     * each accepted scroll event renews the arm.  Default 500 ms.
     */
    armMs?: number;
    /** Injectable clock for tests. */
    now?: () => number;
}

export interface ScrollVelocityTracker {
    /** True while a fast scroll is in progress (recent event + high velocity). */
    isFlinging: () => boolean;
    /** Most recent smoothed velocity in px/ms (0 once settled). */
    velocity: () => number;
    /**
     * Register a callback for the next time scrolling settles.  Fires at most
     * once; returns an unsubscribe.  Callers should check `isFlinging` first
     * and only register when it is true.
     */
    onSettle: (cb: () => void) => () => void;
    /** Remove listeners and timers. */
    dispose: () => void;
}

const NAV_KEYS = new Set(['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown', ' ', 'Spacebar']);
const EDITABLE_TAG = /^(INPUT|TEXTAREA|SELECT)$/;

export function createScrollVelocityTracker(
    element: HTMLElement,
    options: ScrollVelocityOptions = {},
): ScrollVelocityTracker {
    const flingPxPerMs = options.flingPxPerMs ?? 2;
    const settleMs = options.settleMs ?? 120;
    const armMs = options.armMs ?? 500;
    const now = options.now ?? (() => (typeof performance !== 'undefined' ? performance.now() : Date.now()));

    let lastTop = element.scrollTop;
    let lastAt = now();
    let lastEventAt = -Infinity;
    let currentVelocity = 0;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    let settleCallbacks: Array<() => void> = [];

    // Only gesture-driven scrolling can be a fling.  A scroll event alone
    // cannot say who caused it: the conversation-switch scrollIntoView / pin
    // loop moves scrollTop by thousands of pixels in one frame and was read
    // as a 77 px/ms "fling" -- which parked every deferred mount and held the
    // VISIBLE message's mount until that fake gesture "settled" (~600 ms on
    // every switch, per the 2026-09-18 MSG_QUEUE trace).  A real gesture is
    // always preceded by an input event on the container (wheel, touch,
    // scrollbar pointerdown) or a navigation key, and a programmatic scroll
    // never is.  Those inputs ARM the tracker; a scroll event is sampled only
    // while armed, and each accepted scroll renews the arm so touch momentum
    // (no touchmove after touchend) and trackpad momentum run to the end.
    let armedUntil = -Infinity;
    const arm = () => { armedUntil = now() + armMs; };
    const onKey = (e: KeyboardEvent) => {
        const target = e.target as HTMLElement | null;
        if (target && (target.isContentEditable || EDITABLE_TAG.test(target.tagName || ''))) return;
        if (NAV_KEYS.has(e.key)) arm();
    };
    const doc = element.ownerDocument;
    element.addEventListener('wheel', arm, { passive: true });
    element.addEventListener('touchstart', arm, { passive: true });
    element.addEventListener('touchmove', arm, { passive: true });
    element.addEventListener('pointerdown', arm, { passive: true });
    doc?.addEventListener('keydown', onKey);

    const fireSettle = () => {
        settleTimer = null;
        currentVelocity = 0;
        const cbs = settleCallbacks;
        settleCallbacks = [];
        for (const cb of cbs) {
            try { cb(); } catch (e) { console.warn('scroll settle callback threw:', e); }
        }
    };

    const onScroll = () => {
        const t = now();
        const top = element.scrollTop;
        // The settle timer runs for every scroll so callbacks registered
        // during a gesture still fire if a programmatic scroll ends it.
        if (settleTimer !== null) clearTimeout(settleTimer);
        settleTimer = setTimeout(fireSettle, settleMs);
        if (t > armedUntil) {
            // Programmatic: track position so the next gesture's first
            // sample is measured from where the content actually is, but
            // contribute no velocity and do not count as a scroll event.
            lastTop = top;
            lastAt = t;
            currentVelocity = 0;
            return;
        }
        armedUntil = t + armMs;
        // Guard dt against 0 (two events in the same ms) so a tiny nudge
        // can't read as infinite velocity.
        const dt = Math.max(1, t - lastAt);
        const dy = Math.abs(top - lastTop);
        // Light smoothing: one large sample (a single wheel tick) should not
        // flip us into fling mode on its own, but sustained fast motion should.
        const sample = dy / dt;
        currentVelocity = currentVelocity === 0 ? sample : currentVelocity * 0.5 + sample * 0.5;
        lastTop = top;
        lastAt = t;
        lastEventAt = t;
    };

    element.addEventListener('scroll', onScroll, { passive: true });

    const isFlinging = () =>
        (now() - lastEventAt) < settleMs && currentVelocity >= flingPxPerMs;

    const onSettle = (cb: () => void) => {
        settleCallbacks.push(cb);
        return () => {
            const idx = settleCallbacks.indexOf(cb);
            if (idx >= 0) settleCallbacks.splice(idx, 1);
        };
    };

    const dispose = () => {
        element.removeEventListener('scroll', onScroll);
        element.removeEventListener('wheel', arm);
        element.removeEventListener('touchstart', arm);
        element.removeEventListener('touchmove', arm);
        element.removeEventListener('pointerdown', arm);
        doc?.removeEventListener('keydown', onKey);
        if (settleTimer !== null) { clearTimeout(settleTimer); settleTimer = null; }
        settleCallbacks = [];
        // A disposed tracker must never report a fling — the shared getter
        // may still hand it out for a frame or two during effect teardown.
        currentVelocity = 0;
        lastEventAt = -Infinity;
        armedUntil = -Infinity;
    };

    return { isFlinging, velocity: () => currentVelocity, onSettle, dispose };
}

// ---------------------------------------------------------------------------
// Shared instance plumbing.  The owner of the chat container registers; any
// module (notably the deferred-mount code in Conversation.tsx) may read.
// ---------------------------------------------------------------------------

let shared: ScrollVelocityTracker | null = null;

export function setSharedScrollVelocityTracker(tracker: ScrollVelocityTracker | null): void {
    shared = tracker;
}

export function getSharedScrollVelocityTracker(): ScrollVelocityTracker | null {
    return shared;
}

// ---------------------------------------------------------------------------
// Edge jump.
// ---------------------------------------------------------------------------

/**
 * Jump a scroll container straight to its start or end.
 *
 * Jumping to the END is not a single assignment: the placeholder shells that
 * land in view will mount over the next few frames and change the scroll
 * height, which would leave us stranded a screen or two short of the real
 * bottom.  So after the initial jump we keep re-pinning to the bottom for a
 * short window while the height is still moving.  The START (scrollTop=0)
 * is stable regardless of what mounts, so no re-pinning is needed there.
 *
 * Returns a cancel function.
 */
export function jumpToEdge(
    container: HTMLElement,
    edge: 'start' | 'end',
    options: { settleWindowMs?: number; raf?: (cb: () => void) => number; now?: () => number } = {},
): () => void {
    const settleWindowMs = options.settleWindowMs ?? 600;
    const raf = options.raf ?? ((cb) => requestAnimationFrame(cb));
    const now = options.now ?? (() => (typeof performance !== 'undefined' ? performance.now() : Date.now()));

    if (edge === 'start') {
        container.scrollTop = 0;
        return () => { /* nothing pending */ };
    }

    let cancelled = false;
    const startedAt = now();
    let lastHeight = -1;
    let stableFrames = 0;

    const pin = () => {
        if (cancelled) return;
        const h = container.scrollHeight;
        container.scrollTop = h - container.clientHeight;
        if (h === lastHeight) stableFrames++; else stableFrames = 0;
        lastHeight = h;
        // Stop once the height has held still for a few frames, or when the
        // window expires — whichever comes first.
        if (stableFrames >= 3 || now() - startedAt > settleWindowMs) return;
        raf(pin);
    };
    pin();

    return () => { cancelled = true; };
}
