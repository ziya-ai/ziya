/**
 * @jest-environment jsdom
 *
 * Follow mode must track the CONTENT height, not only the streamed text.
 *
 * Observed 2026-10-20: autoscroll "landed early" -- the view sat on the top
 * of the last tool block's header with its body hidden beneath the sticky
 * composer dock.  useScrollManager pinned to the bottom in a rAF when
 * `contentLength` changed, but a tool fence / thinking block renders its
 * body a frame or more later, and while "Waiting for model response..."
 * nothing changes contentLength at all, so nothing re-pinned.  The hook now
 * observes the content box with a ResizeObserver and re-pins on growth while
 * following (streaming, or within a short grace after the stream ends).
 *
 * Also pinned: a scrollTop DECREASE caused by content shrinking (the browser
 * clamping to the new bottom) must not read as "the user scrolled away".
 */
import React from 'react';
import { renderHook, act } from '@testing-library/react';
import { useScrollManager, FOLLOW_GRACE_MS } from '../useScrollManager';

type RoCb = (entries: any[]) => void;
let roCallbacks: RoCb[] = [];
let ioCallbacks: Array<(entries: any[]) => void> = [];

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
        /** Content grew/shrank; the browser clamps scrollTop and layout fires RO. */
        resize: (h: number) => {
            scrollHeight = h;
            if (scrollTop > h - clientHeight) { scrollTop = Math.max(0, h - clientHeight); el.dispatchEvent(new Event('scroll')); }
            roCallbacks.forEach(cb => cb([]));
        },
        /** The user moved the thumb. */
        userScroll: (to: number) => { scrollTop = to; el.dispatchEvent(new Event('scroll')); },
        atBottom: () => scrollTop === scrollHeight - clientHeight,
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

beforeEach(() => {
    roCallbacks = [];
    ioCallbacks = [];
    (global as any).ResizeObserver = FakeRO;
    (global as any).IntersectionObserver = FakeIO;
    jest.useFakeTimers();
});
afterEach(() => {
    jest.useRealTimers();
    document.body.innerHTML = '';
});

describe('useScrollManager follows content height', () => {
    it('observes the content box and re-pins when it grows mid-stream with no text change', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;                       // at bottom
        mount(c, true);
        expect(roCallbacks.length).toBeGreaterThan(0);  // the observer is actually mounted
        act(() => { c.resize(1400); });             // tool fence body expanded; contentLength unchanged
        expect(c.el.scrollTop).toBe(900);
        expect(c.atBottom()).toBe(true);
    });

    it('does NOT re-pin once the user has scrolled away', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        mount(c, true);
        act(() => { c.userScroll(200); });          // 300px up, clearly away
        act(() => { c.resize(1400); });
        expect(c.el.scrollTop).toBe(200);
    });

    it('a shrink-clamp (scrollTop drops but still at the bottom) keeps following', () => {
        const c = makeContainer(1400);
        c.el.scrollTop = 900;
        mount(c, true);
        act(() => { c.resize(1000); });             // thinking block collapsed: browser clamps 900 -> 500
        expect(c.el.scrollTop).toBe(500);
        act(() => { c.resize(1600); });             // next growth must still pin
        expect(c.el.scrollTop).toBe(1100);
    });

    it('keeps pinning for a grace window after the stream ends, then stops', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        const hook = mount(c, true);
        act(() => { hook.rerender({ streaming: false, len: 10 }); });
        act(() => { c.resize(1200); });             // final message commit grew the DOM
        expect(c.el.scrollTop).toBe(700);
        act(() => { jest.advanceTimersByTime(FOLLOW_GRACE_MS + 50); });
        jest.setSystemTime(Date.now() + FOLLOW_GRACE_MS + 50);
        act(() => { c.resize(1600); });             // user expands something later: do not yank
        expect(c.el.scrollTop).toBe(700);
    });

    it('does not pin when idle (not streaming, no recent stream)', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        mount(c, false);
        act(() => { c.resize(1400); });
        expect(c.el.scrollTop).toBe(500);
    });

    it('returning to the bottom re-enables following', () => {
        const c = makeContainer(1000);
        c.el.scrollTop = 500;
        mount(c, true);
        act(() => { c.userScroll(100); });
        act(() => { c.resize(1200); });
        expect(c.el.scrollTop).toBe(100);
        act(() => { ioCallbacks.forEach(cb => cb([{ isIntersecting: true }])); }); // sentinel back in view
        act(() => { c.resize(1600); });
        expect(c.el.scrollTop).toBe(1100);
    });
});
