/**
 * A programmatic scroll is not a fling.
 *
 * Regression for the 2026-09-18 conversation-switch trace: the switch's
 * scrollIntoView / pin loop moved scrollTop thousands of pixels in one frame,
 * the tracker read 77 px/ms and reported a fling, every deferred mount was
 * parked, and the visible message's mount waited ~600 ms for that fake
 * gesture to "settle" (`fling: deferring markdown-mount-290`, then
 * `hold: user is flinging (v=77.0px/ms)`).  Only input-driven motion may arm
 * the tracker.  Each "does not fling" case is paired with the same motion
 * preceded by an input, which MUST fling, so a dead tracker cannot pass.
 */
import { createScrollVelocityTracker } from '../scrollVelocity';

function makeContainer() {
    const el = document.createElement('div');
    let scrollTop = 0;
    Object.defineProperty(el, 'scrollTop', {
        get: () => scrollTop, set: (v: number) => { scrollTop = Math.max(0, v); }, configurable: true,
    });
    document.body.appendChild(el);
    return {
        el,
        /** The browser reacting to a scrollTop assignment: 'scroll' only. */
        programmatic: (top: number) => { el.scrollTop = top; el.dispatchEvent(new Event('scroll')); },
        input: (type: string) => el.dispatchEvent(new Event(type)),
    };
}

const fastMotion = (scroll: (top: number) => void, tick: () => void) => {
    // 120px every 16ms = 7.5 px/ms, well above the 2 px/ms gate.
    for (let i = 1; i <= 5; i++) { tick(); scroll(i * 120); }
};

describe('scroll velocity tracker: programmatic scroll is not a fling', () => {
    let clock: number;
    const now = () => clock;
    beforeEach(() => { jest.useFakeTimers(); clock = 1000; });
    afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); document.body.innerHTML = ''; });

    it('scroll events with no preceding input never fling (the switch shape)', () => {
        const { el, programmatic } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
        clock += 16; programmatic(30000);          // one-frame jump to the bottom
        fastMotion(programmatic, () => { clock += 16; });
        expect(t.isFlinging()).toBe(false);
        expect(t.velocity()).toBe(0);
    });

    it.each(['wheel', 'touchstart', 'touchmove', 'pointerdown'])(
        'the same motion after a %s input flings', (type) => {
            const { el, programmatic, input } = makeContainer();
            const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
            input(type);
            fastMotion(programmatic, () => { clock += 16; });
            expect(t.isFlinging()).toBe(true);
        });

    it('a navigation key on the document arms it; the same key in a textarea does not', () => {
        const { el, programmatic } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
        const ta = document.createElement('textarea');
        document.body.appendChild(ta);
        ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown', bubbles: true }));
        fastMotion(programmatic, () => { clock += 16; });
        expect(t.isFlinging()).toBe(false);

        clock += 1000;
        document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown', bubbles: true }));
        fastMotion(programmatic, () => { clock += 16; });
        expect(t.isFlinging()).toBe(true);
    });

    it('the arm expires: input long before the scroll does not count', () => {
        const { el, programmatic, input } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2, armMs: 500 });
        input('wheel');
        clock += 600;
        fastMotion(programmatic, () => { clock += 16; });
        expect(t.isFlinging()).toBe(false);
    });

    it('accepted scroll events renew the arm (momentum with no further input)', () => {
        const { el, programmatic, input } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2, armMs: 500 });
        input('touchstart');
        // 2 s of continuous momentum, 16 ms apart, no further input.
        for (let i = 1; i <= 125; i++) { clock += 16; programmatic(i * 120); }
        expect(t.isFlinging()).toBe(true);
    });

    it('a programmatic scroll still schedules settle so gesture callbacks are not stranded', () => {
        const { el, programmatic, input } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2, settleMs: 120 });
        input('wheel');
        fastMotion(programmatic, () => { clock += 16; });
        const cb = jest.fn();
        t.onSettle(cb);
        clock += 600; programmatic(50000);           // programmatic pin ends the gesture
        clock += 120; jest.advanceTimersByTime(120);
        expect(cb).toHaveBeenCalledTimes(1);
    });

    it('dispose removes the input listeners', () => {
        const { el, programmatic, input } = makeContainer();
        const t = createScrollVelocityTracker(el, { now, flingPxPerMs: 2 });
        t.dispose();
        input('wheel');
        fastMotion(programmatic, () => { clock += 16; });
        expect(t.isFlinging()).toBe(false);
    });
});
