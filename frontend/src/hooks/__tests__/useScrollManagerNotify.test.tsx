/**
 * @jest-environment jsdom
 *
 * useScrollManager's notifyContentLength path.
 *
 * App used to pass `contentLength` computed from streamedContentMap, which
 * made App re-render on every streaming flush.  App now passes no
 * contentLength; a tiny child (StreamedContentLengthWatcher) calls
 * notifyContentLength instead.  The notify path must reproduce what the
 * contentLength effects did: note growth while the user is away, and clear
 * indicators when content resets.  The prop path is covered by
 * useScrollManagerFollow.test.tsx.
 */
import React from 'react';
import { renderHook, act } from '@testing-library/react';
import { useScrollManager } from '../useScrollManager';

class FakeRO { observe() {} disconnect() {} }
let ioCallbacks: Array<(entries: any[]) => void> = [];
class FakeIO {
    constructor(cb: (entries: any[]) => void) { ioCallbacks.push(cb); }
    observe() {} disconnect() {}
}
/** The sentinel leaves (or re-enters) the viewport, as a real scroll would report. */
const sentinelVisible = (v: boolean) => ioCallbacks.forEach(cb => cb([{ isIntersecting: v }]));

function makeContainer(contentHeight: number, clientHeight = 500) {
    const el = document.createElement('div');
    const content = document.createElement('div');
    content.className = 'chat-content-stabilizer';
    el.appendChild(content);
    const sentinel = document.createElement('div');
    content.appendChild(sentinel);
    let scrollTop = 0;
    Object.defineProperty(el, 'scrollTop', {
        get: () => scrollTop,
        set: (v: number) => { scrollTop = Math.max(0, Math.min(v, contentHeight - clientHeight)); },
        configurable: true,
    });
    Object.defineProperty(el, 'scrollHeight', { get: () => contentHeight, configurable: true });
    Object.defineProperty(el, 'clientHeight', { get: () => clientHeight, configurable: true });
    document.body.appendChild(el);
    return { el, sentinel, userScroll: (to: number) => { scrollTop = to; el.dispatchEvent(new Event('scroll')); } };
}

function mount(streaming: boolean) {
    const c = makeContainer(1000);
    c.el.scrollTop = 500;
    const containerRef = { current: c.el } as React.RefObject<HTMLElement>;
    const sentinelRef = { current: c.sentinel } as React.RefObject<HTMLElement>;
    const hook = renderHook(({ s }: { s: boolean }) => useScrollManager({
        containerRef, sentinelRef, isTopToBottom: true, isStreaming: s,
    }), { initialProps: { s: streaming } });
    // Mounting while streaming schedules the initial pin as an animation
    // frame.  A browser runs it before the user can scroll; under fake
    // timers it would otherwise fire at the test's first advance and look
    // like a later scroll.
    act(() => { jest.advanceTimersByTime(50); });
    return { c, hook };
}

beforeEach(() => {
    ioCallbacks = [];
    (global as any).ResizeObserver = FakeRO;
    (global as any).IntersectionObserver = FakeIO;
    jest.useFakeTimers();
});
afterEach(() => { jest.useRealTimers(); document.body.innerHTML = ''; });

describe('notifyContentLength', () => {
    it('is returned and stable across renders', () => {
        const { hook } = mount(true);
        const first = hook.result.current.notifyContentLength;
        expect(typeof first).toBe('function');
        hook.rerender({ s: true });
        expect(hook.result.current.notifyContentLength).toBe(first);
    });

    it('marks new content while the user is scrolled away', () => {
        const { c, hook } = mount(true);
        act(() => { c.userScroll(200); });                  // away: follow mode off
        act(() => { sentinelVisible(false); });             // ...and the end is out of view
        expect(hook.result.current.hasNewContentWhileAway).toBe(false);
        act(() => { hook.result.current.notifyContentLength(10); });
        expect(hook.result.current.hasNewContentWhileAway).toBe(true);
    });

    // The finished turn's map entry is deleted at stream end, so the length
    // drops to 0 after streaming stops.  That drop used to be read as "a new
    // user message was sent": it forced a scroll to the end 100 ms later and
    // cleared the indicators.  A reader scrolled up was yanked to the bottom
    // (streamend.mjs, reading run: the anchor moved 3,465 px), and the green
    // "completed while you were away" indicator vanished as it appeared.
    // New-message and conversation-switch scrolling live in App.tsx.
    it('stream end leaves a reader where they are and keeps the completed indicator', () => {
        const { c, hook } = mount(true);
        act(() => { c.userScroll(200); });
        act(() => { sentinelVisible(false); });
        act(() => { hook.result.current.notifyContentLength(10); });
        act(() => { hook.rerender({ s: false }); });
        // Positive: the completion path ran.
        expect(hook.result.current.streamCompletedWhileAway).toBe(true);
        act(() => { hook.result.current.notifyContentLength(0); });
        act(() => { jest.advanceTimersByTime(500); });
        expect(c.el.scrollTop).toBe(200);
        expect(hook.result.current.streamCompletedWhileAway).toBe(true);
    });

    it('the next turn reports new content from its first chunk', () => {
        const { c, hook } = mount(true);
        act(() => { c.userScroll(200); });
        act(() => { sentinelVisible(false); });
        act(() => { hook.result.current.notifyContentLength(1000); });
        act(() => { hook.rerender({ s: false }); });
        act(() => { hook.result.current.notifyContentLength(0); });
        expect(hook.result.current.hasNewContentWhileAway).toBe(false);
        // Next turn, still scrolled away: a reply shorter than the last one.
        act(() => { hook.rerender({ s: true }); });
        act(() => { hook.result.current.notifyContentLength(5); });
        expect(hook.result.current.hasNewContentWhileAway).toBe(true);
    });

    it('does nothing for growth when not streaming', () => {
        const { c, hook } = mount(false);
        act(() => { c.userScroll(200); });
        act(() => { hook.result.current.notifyContentLength(10); });
        expect(hook.result.current.hasNewContentWhileAway).toBe(false);
    });
});
