/**
 * @jest-environment jsdom
 *
 * A scroll away from the active end must stop autoscroll IMMEDIATELY, not
 * one React render later.
 *
 * Observed 2026-11-12: browser find (Ctrl/Cmd-F) while tools were running
 * and the view was following the bottom scrolled to its match and was
 * yanked back to the bottom within a frame.  The scroll handler recorded
 * "away" with setFollowMode(false), but the pins read follow mode through a
 * ref that was only assigned during render, and the follow pin already
 * queued in a requestAnimationFrame did not check follow mode at all (and
 * set it back to true).  A frame runs scroll events, then rAF callbacks,
 * then ResizeObserver -- all before React re-renders -- so both pins still
 * saw "following".
 *
 * These tests reproduce that ordering by running the scroll event, the
 * queued rAF and the ResizeObserver inside ONE act(), so no render happens
 * between them.
 */
import React from 'react';
import { renderHook, act } from '@testing-library/react';
import { useScrollManager } from '../useScrollManager';

type RoCb = (entries: any[]) => void;
let roCallbacks: RoCb[] = [];
let ioCallbacks: Array<(entries: any[]) => void> = [];
let rafQueue: Map<number, () => void> = new Map();
let rafId = 0;

class FakeRO {
    cb: RoCb;
    constructor(cb: RoCb) { this.cb = cb; roCallbacks.push(cb); }
    observe() { /* noop */ }
    disconnect() { roCallbacks = roCallbacks.filter(c => c !== this.cb); }
}
class FakeIO {
    cb: (entries: any[]) => void;
    constructor(cb: (entries: any[]) => void) { this.cb = cb; ioCallbacks.push(cb); }
    observe() { /* noop */ }
    disconnect() { /* noop */ }
}

/** Run every queued animation-frame callback; returns how many ran. */
function flushRaf(): number {
    const cbs = Array.from(rafQueue.values());
    rafQueue = new Map();
    cbs.forEach(cb => cb());
    return cbs.length;
}

function makeContainer(contentHeight: number, clientHeight = 500) {
    const el = document.createElement('div');
    el.className = 'chat-container';
    const content = document.createElement('div');
    content.className = 'chat-content-stabilizer';
    el.appendChild(content);
    const sentinel = document.createElement('div');
    content.appendChild(sentinel);
    let scrollTop = 0;
    let scrollHeight = contentHeight;
    Object.defineProperty(el, 'scrollTop', {
        get: () => scrollTop,
        set: (v: number) => { scrollTop = Math.max(0, Math.min(v, scrollHeight - clientHeight)); },
        configurable: true,
    });
    Object.defineProperty(el, 'scrollHeight', { get: () => scrollHeight, configurable: true });
    Object.defineProperty(el, 'clientHeight', { get: () => clientHeight, configurable: true });
    document.body.appendChild(el);
    return {
        el, sentinel,
        /** Content grew; layout fires the ResizeObserver. */
        resize: (h: number) => { scrollHeight = h; roCallbacks.forEach(cb => cb([])); },
        /** Find-in-page (or the user) moved the view; the browser fires scroll. */
        scrollAway: (to: number) => { scrollTop = to; el.dispatchEvent(new Event('scroll')); },
    };
}

function mount(c: ReturnType<typeof makeContainer>, isStreaming = true) {
    const containerRef = { current: c.el } as React.RefObject<HTMLElement>;
    const sentinelRef = { current: c.sentinel } as React.RefObject<HTMLElement>;
    return renderHook(
        ({ streaming, len }: { streaming: boolean; len: number }) => useScrollManager({
            containerRef, sentinelRef, isTopToBottom: true, isStreaming: streaming, contentLength: len,
        }),
        { initialProps: { streaming: isStreaming, len: 10 } },
    );
}

let realRaf: typeof requestAnimationFrame;
let realCaf: typeof cancelAnimationFrame;

beforeEach(() => {
    roCallbacks = [];
    ioCallbacks = [];
    rafQueue = new Map();
    rafId = 0;
    (global as any).ResizeObserver = FakeRO;
    (global as any).IntersectionObserver = FakeIO;
    realRaf = global.requestAnimationFrame;
    realCaf = global.cancelAnimationFrame;
    (global as any).requestAnimationFrame = (cb: () => void) => { rafQueue.set(++rafId, cb); return rafId; };
    (global as any).cancelAnimationFrame = (id: number) => { rafQueue.delete(id); };
});
afterEach(() => {
    (global as any).requestAnimationFrame = realRaf;
    (global as any).cancelAnimationFrame = realCaf;
    document.body.innerHTML = '';
});

describe('useScrollManager: scrolling away stops autoscroll in the same frame', () => {
    it('positive control: a queued follow pin does run when nothing scrolled away', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        const hook = mount(c, true);
        act(() => { flushRaf(); });
        c.el.scrollTop = 300;                          // off the bottom, no scroll event
        act(() => { hook.rerender({ streaming: true, len: 20 }); });
        let ran = 0;
        act(() => { ran = flushRaf(); });
        expect(ran).toBeGreaterThan(0);                // the pin path was exercised
        expect(c.el.scrollTop).toBe(500);
    });

    it('a pin already queued in rAF does not yank the view back after a scroll away', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;                          // following at the bottom
        const hook = mount(c, true);
        act(() => { flushRaf(); });
        act(() => { hook.rerender({ streaming: true, len: 20 }); });   // new chunk: queues a follow pin
        expect(rafQueue.size).toBeGreaterThan(0);
        // One frame, no React render in between: scroll event, rAF, ResizeObserver.
        act(() => {
            c.scrollAway(200);                         // find-in-page jumped to its match
            flushRaf();
            c.resize(1400);                            // tool output kept growing
        });
        expect(c.el.scrollTop).toBe(200);
    });

    it('stays off until the view returns to the bottom', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        const hook = mount(c, true);
        act(() => { flushRaf(); });
        act(() => { c.scrollAway(200); flushRaf(); });
        act(() => { hook.rerender({ streaming: true, len: 30 }); });
        act(() => { flushRaf(); c.resize(1800); });
        expect(c.el.scrollTop).toBe(200);
        // Sentinel back in view (the user scrolled down to the end).
        act(() => { ioCallbacks.forEach(cb => cb([{ isIntersecting: true }])); });
        act(() => { flushRaf(); c.resize(2000); });
        expect(c.el.scrollTop).toBe(1500);
    });

    it('a scroll away during the post-stream grace window is not pinned back', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        const hook = mount(c, true);
        act(() => { flushRaf(); });
        act(() => { hook.rerender({ streaming: false, len: 10 }); });  // stream ended; grace begins
        act(() => { c.resize(1200); });                // grace pin still works
        expect(c.el.scrollTop).toBe(700);
        act(() => { c.scrollAway(200); flushRaf(); c.resize(1600); });
        // Real timers and no waiting: still well inside FOLLOW_GRACE_MS.
        expect(c.el.scrollTop).toBe(200);
    });

    it('a jump that lands between the previous bottom and the new one counts as away', () => {
        // The pin assigns scrollTop directly, so the scroll handler's idea of
        // "where the view was" must follow the pin.  Otherwise a jump to a
        // match in the most recent output (below the old bottom, above the
        // new one) reads as downward movement and is pinned away.
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        mount(c, true);
        act(() => { flushRaf(); });
        act(() => { c.resize(1400); });                // pinned to 900, no scroll event yet
        expect(c.el.scrollTop).toBe(900);
        act(() => { c.scrollAway(700); flushRaf(); c.resize(1800); });
        expect(c.el.scrollTop).toBe(700);
    });

    it('an explicit scrollToActiveEnd still pins and resumes following', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        const hook = mount(c, true);
        act(() => { flushRaf(); });
        act(() => { c.scrollAway(200); flushRaf(); });
        act(() => { hook.result.current.scrollToActiveEnd(); });
        act(() => { flushRaf(); });
        expect(c.el.scrollTop).toBe(500);
        act(() => { c.resize(1400); });
        expect(c.el.scrollTop).toBe(900);
    });
});
