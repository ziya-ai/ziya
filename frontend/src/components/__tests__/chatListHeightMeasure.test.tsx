/**
 * The conversation list's virtual scroller needs an explicit pixel height
 * (react-window cannot infer one), so MUIChatHistory measures its container
 * with a ResizeObserver.
 *
 * The container is mounted by a LATE branch of the render — while
 * hasLoadedConversations is false the sidebar shows a spinner instead, and the
 * container element does not exist.  With a plain ref and a mount-only effect
 * the observer was never attached: the effect ran once against a null ref,
 * bailed out, and had no dependency that would bring it back when the
 * container finally appeared.  The list then rendered at its fallback height
 * (600px) forever, ignoring the parent's real size.
 *
 * These tests pin the two behaviours: the observer must attach to a container
 * that mounts after first paint, and the measured height must reach the list.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { render, screen, act } from '@testing-library/react';

type ROCallback = (entries: Array<{ contentRect: { height: number } }>) => void;

/** Records every element handed to observe(), and lets tests fire resizes. */
class FakeResizeObserver {
    static observed: Element[] = [];
    static callbacks: ROCallback[] = [];
    static reset() {
        FakeResizeObserver.observed = [];
        FakeResizeObserver.callbacks = [];
    }
    static fire(height: number) {
        FakeResizeObserver.callbacks.forEach((cb) => cb([{ contentRect: { height } }]));
    }
    constructor(private cb: ROCallback) {
        FakeResizeObserver.callbacks.push(cb);
    }
    observe(el: Element) {
        FakeResizeObserver.observed.push(el);
    }
    disconnect() {
        FakeResizeObserver.callbacks = FakeResizeObserver.callbacks.filter((c) => c !== this.cb);
    }
    unobserve() { /* no-op */ }
}

beforeEach(() => {
    FakeResizeObserver.reset();
    (global as any).ResizeObserver = FakeResizeObserver;
});

const FALLBACK = 600;

/**
 * The broken shape: ref + mount-only effect, container gated behind `loaded`.
 */
const RefOnlyHarness: React.FC<{ loaded: boolean }> = ({ loaded }) => {
    const ref = useRef<HTMLDivElement | null>(null);
    const [height, setHeight] = useState(FALLBACK);
    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        const ro = new ResizeObserver(([entry]) => {
            if (entry.contentRect.height > 0) setHeight(entry.contentRect.height);
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    if (!loaded) return <div data-testid="spinner" />;
    return <div ref={ref} data-testid="container"><span data-testid="height">{height}</span></div>;
};

/**
 * The fixed shape: the container node lives in state, so the effect re-runs
 * when the element mounts.
 */
const CallbackRefHarness: React.FC<{ loaded: boolean }> = ({ loaded }) => {
    const [el, setEl] = useState<HTMLDivElement | null>(null);
    const attach = useCallback((node: HTMLDivElement | null) => setEl(node), []);
    const [height, setHeight] = useState(FALLBACK);
    useEffect(() => {
        if (!el) return;
        const ro = new ResizeObserver(([entry]) => {
            if (entry.contentRect.height > 0) setHeight(entry.contentRect.height);
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, [el]);
    if (!loaded) return <div data-testid="spinner" />;
    return <div ref={attach} data-testid="container"><span data-testid="height">{height}</span></div>;
};

describe('late-mounted container measurement (the regression)', () => {
    it('ref + mount-only effect never observes a container that mounts later', () => {
        const { rerender } = render(<RefOnlyHarness loaded={false} />);
        expect(FakeResizeObserver.observed).toHaveLength(0);

        rerender(<RefOnlyHarness loaded />);
        expect(screen.getByTestId('container')).toBeTruthy();
        // Nothing is observing it — this is the bug.
        expect(FakeResizeObserver.observed).toHaveLength(0);

        act(() => FakeResizeObserver.fire(842));
        expect(screen.getByTestId('height').textContent).toBe(String(FALLBACK));
    });

    it('callback ref in state observes the container once it mounts', () => {
        const { rerender } = render(<CallbackRefHarness loaded={false} />);
        expect(FakeResizeObserver.observed).toHaveLength(0);

        rerender(<CallbackRefHarness loaded />);
        expect(FakeResizeObserver.observed).toEqual([screen.getByTestId('container')]);

        act(() => FakeResizeObserver.fire(842));
        expect(screen.getByTestId('height').textContent).toBe('842');
    });

    it('keeps tracking resizes after the first measurement', () => {
        render(<CallbackRefHarness loaded />);
        act(() => FakeResizeObserver.fire(500));
        expect(screen.getByTestId('height').textContent).toBe('500');
        act(() => FakeResizeObserver.fire(300));
        expect(screen.getByTestId('height').textContent).toBe('300');
    });

    it('ignores a zero height (element hidden / detached)', () => {
        render(<CallbackRefHarness loaded />);
        act(() => FakeResizeObserver.fire(700));
        act(() => FakeResizeObserver.fire(0));
        expect(screen.getByTestId('height').textContent).toBe('700');
    });

    it('disconnects when the container unmounts', () => {
        const { rerender } = render(<CallbackRefHarness loaded />);
        expect(FakeResizeObserver.callbacks).toHaveLength(1);
        rerender(<CallbackRefHarness loaded={false} />);
        expect(FakeResizeObserver.callbacks).toHaveLength(0);
    });
});
