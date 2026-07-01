/**
 * @jest-environment jsdom
 */
import { createScrollActivityTracker, SCROLL_ACTIVE_CLASS } from '../scrollActivity';

describe('createScrollActivityTracker', () => {
    let el: HTMLElement;

    beforeEach(() => {
        jest.useFakeTimers();
        el = document.createElement('div');
    });

    afterEach(() => {
        jest.clearAllTimers();
        jest.useRealTimers();
    });

    it('adds the active class on notify', () => {
        const tracker = createScrollActivityTracker(el);
        tracker.notify();
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(true);
    });

    it('removes the class after the idle window elapses', () => {
        const tracker = createScrollActivityTracker(el, { idleMs: 800 });
        tracker.notify();
        jest.advanceTimersByTime(799);
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(true);
        jest.advanceTimersByTime(1);
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(false);
    });

    it('debounces: a later notify resets the idle countdown', () => {
        const tracker = createScrollActivityTracker(el, { idleMs: 800 });
        tracker.notify();
        jest.advanceTimersByTime(500);
        tracker.notify();
        // 1000ms elapsed total, but only 500ms since the last notify
        jest.advanceTimersByTime(500);
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(true);
        jest.advanceTimersByTime(300);
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(false);
    });

    it('dispose clears the timer and removes the class', () => {
        const tracker = createScrollActivityTracker(el);
        tracker.notify();
        tracker.dispose();
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(false);
        jest.advanceTimersByTime(2000);
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(false);
    });

    it('respects a custom className', () => {
        const tracker = createScrollActivityTracker(el, { className: 'custom-active' });
        tracker.notify();
        expect(el.classList.contains('custom-active')).toBe(true);
        expect(el.classList.contains(SCROLL_ACTIVE_CLASS)).toBe(false);
    });
});
