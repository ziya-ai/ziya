/**
 * @jest-environment jsdom
 */
import {
    createScrollVelocityTracker,
    jumpToEdge,
    setSharedScrollVelocityTracker,
    getSharedScrollVelocityTracker,
} from '../scrollVelocity';

/** A scroll container stand-in with writable scrollTop/scrollHeight/clientHeight. */
function makeContainer(opts: { scrollHeight?: number; clientHeight?: number } = {}) {
    const el = document.createElement('div');
    let scrollTop = 0;
    let scrollHeight = opts.scrollHeight ?? 10000;
    Object.defineProperty(el, 'scrollTop', {
        get: () => scrollTop,
        set: (v: number) => { scrollTop = Math.max(0, Math.min(v, scrollHeight - (opts.clientHeight ?? 800))); },
        configurable: true,
    });
    Object.defineProperty(el, 'scrollHeight', { get: () => scrollHeight, configurable: true });
    Object.defineProperty(el, 'clientHeight', { get: () => opts.clientHeight ?? 800, configurable: true });
    return {
        el,
        setScrollHeight: (h: number) => { scrollHeight = h; },
        /** Simulate a USER scroll: the wheel input that drives it, then the
         *  browser moving scrollTop and dispatching 'scroll'.  Programmatic
         *  scrolls (no input) are covered in scrollVelocityProgrammatic.test.ts. */
        scrollTo: (top: number) => { el.dispatchEvent(new Event('wheel')); el.scrollTop = top; el.dispatchEvent(new Event('scroll')); },
    };
}

describe('createScrollVelocityTracker', () => {
    let clock: number;
    const now = () => clock;

    beforeEach(() => {
        jest.useFakeTimers();
        clock = 1000;
    });
    afterEach(() => {
        jest.clearAllTimers();
        jest.useRealTimers();
        setSharedScrollVelocityTracker(null);
    });

    it('is not flinging before any scroll', () => {
        const { el } = makeContainer();
        const t = createScrollVelocityTracker(el, { now });
        expect(t.isFlinging()).toBe(false);
        expect(t.velocity()).toBe(0);
    });

    it('a slow reading-speed scroll does not count as a fling', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
        // 20px every 16ms ≈ 1.25 px/ms — below threshold.
        for (let i = 1; i <= 5; i++) { clock += 16; scrollTo(i * 20); }
        expect(t.isFlinging()).toBe(false);
    });

    it('sustained fast motion counts as a fling', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
        // 120px every 16ms = 7.5 px/ms
        for (let i = 1; i <= 5; i++) { clock += 16; scrollTo(i * 120); }
        expect(t.isFlinging()).toBe(true);
        expect(t.velocity()).toBeGreaterThanOrEqual(2);
    });

    it('a single large wheel tick alone does not trip the gate (smoothing)', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
        // Slow lead-in, then one big jump.
        for (let i = 1; i <= 4; i++) { clock += 16; scrollTo(i * 10); }
        clock += 16; scrollTo(40 + 50); // 50px/16ms ≈ 3.1 raw, but EMA with ~0.6 → ~1.9
        expect(t.isFlinging()).toBe(false);
    });

    it('stops flinging once the settle window passes with no events', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2, settleMs: 120 });
        for (let i = 1; i <= 5; i++) { clock += 16; scrollTo(i * 120); }
        expect(t.isFlinging()).toBe(true);
        clock += 121;
        expect(t.isFlinging()).toBe(false);
    });

    it('fires onSettle once after the quiet period, then resets velocity', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, settleMs: 120 });
        const cb = jest.fn();
        for (let i = 1; i <= 3; i++) { clock += 16; scrollTo(i * 120); }
        t.onSettle(cb);
        jest.advanceTimersByTime(119);
        expect(cb).not.toHaveBeenCalled();
        jest.advanceTimersByTime(1);
        expect(cb).toHaveBeenCalledTimes(1);
        expect(t.velocity()).toBe(0);
        // Does not fire again on a later settle.
        clock += 200; scrollTo(1000);
        jest.advanceTimersByTime(200);
        expect(cb).toHaveBeenCalledTimes(1);
    });

    it('continued scrolling pushes the settle callback out (debounce)', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, settleMs: 120 });
        const cb = jest.fn();
        clock += 16; scrollTo(120);
        t.onSettle(cb);
        jest.advanceTimersByTime(100);
        clock += 100; scrollTo(240);      // re-arms
        jest.advanceTimersByTime(100);
        expect(cb).not.toHaveBeenCalled();
        jest.advanceTimersByTime(20);
        expect(cb).toHaveBeenCalledTimes(1);
    });

    it('onSettle returns an unsubscribe that prevents the callback', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, settleMs: 120 });
        const cb = jest.fn();
        clock += 16; scrollTo(120);
        const off = t.onSettle(cb);
        off();
        jest.advanceTimersByTime(200);
        expect(cb).not.toHaveBeenCalled();
    });

    it('dispose removes the listener and drops pending callbacks', () => {
        const { el, scrollTo } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, settleMs: 120 });
        const cb = jest.fn();
        clock += 16; scrollTo(120);
        t.onSettle(cb);
        t.dispose();
        jest.advanceTimersByTime(200);
        expect(cb).not.toHaveBeenCalled();
        // Scrolling after dispose is ignored.
        for (let i = 1; i <= 5; i++) { clock += 16; scrollTo(1000 + i * 120); }
        expect(t.isFlinging()).toBe(false);
    });

    it('shared instance getter/setter round-trips', () => {
        const { el } = makeContainer();
        const t = createScrollVelocityTracker(el, { now });
        expect(getSharedScrollVelocityTracker()).toBeNull();
        setSharedScrollVelocityTracker(t);
        expect(getSharedScrollVelocityTracker()).toBe(t);
        setSharedScrollVelocityTracker(null);
        expect(getSharedScrollVelocityTracker()).toBeNull();
    });
});

describe('jumpToEdge', () => {
    // Manual rAF: collect callbacks and step them by hand.
    let frames: Array<() => void>;
    const raf = (cb: () => void) => { frames.push(cb); return frames.length; };
    const step = () => { const f = frames.shift(); if (f) f(); };
    let clock: number;
    const now = () => clock;

    beforeEach(() => { frames = []; clock = 0; });

    it("'start' sets scrollTop to 0 and schedules nothing", () => {
        const { el, scrollTo } = makeContainer();
        scrollTo(5000);
        jumpToEdge(el, 'start', { raf, now });
        expect(el.scrollTop).toBe(0);
        expect(frames).toHaveLength(0);
    });

    it("'end' pins to the bottom and re-pins as the scroll height grows", () => {
        const c = makeContainer({ scrollHeight: 10000, clientHeight: 800 });
        jumpToEdge(c.el, 'end', { raf, now });
        expect(c.el.scrollTop).toBe(9200);
        // Shells at the destination mount and the document grows.
        c.setScrollHeight(12000);
        clock += 16; step();
        expect(c.el.scrollTop).toBe(11200);
        c.setScrollHeight(12500);
        clock += 16; step();
        expect(c.el.scrollTop).toBe(11700);
    });

    it("'end' stops re-pinning once the height is stable for a few frames", () => {
        const c = makeContainer({ scrollHeight: 10000, clientHeight: 800 });
        jumpToEdge(c.el, 'end', { raf, now });
        // Height never changes: after 3 stable frames no further rAF is queued.
        for (let i = 0; i < 3; i++) { clock += 16; step(); }
        expect(frames).toHaveLength(0);
        expect(c.el.scrollTop).toBe(9200);
    });

    it("'end' gives up after the settle window even if the height keeps moving", () => {
        const c = makeContainer({ scrollHeight: 10000, clientHeight: 800 });
        jumpToEdge(c.el, 'end', { raf, now, settleWindowMs: 100 });
        let h = 10000;
        let guard = 0;
        while (frames.length && guard++ < 100) {
            h += 100; c.setScrollHeight(h);
            clock += 16; step();
        }
        expect(guard).toBeLessThan(100);
        expect(clock).toBeGreaterThan(100);
    });

    it('cancel stops further re-pinning', () => {
        const c = makeContainer({ scrollHeight: 10000, clientHeight: 800 });
        const cancel = jumpToEdge(c.el, 'end', { raf, now });
        cancel();
        c.setScrollHeight(20000);
        clock += 16; step();
        expect(c.el.scrollTop).toBe(9200); // untouched after cancel
    });
});
