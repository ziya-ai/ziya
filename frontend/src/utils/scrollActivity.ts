/**
 * Scroll-activity tracker for scrollbar visibility.
 *
 * Toggles a CSS class on a scroll container while the user is actively
 * scrolling, then removes it after a short idle window. Used by
 * useScrollManager to brighten the dark-mode scrollbar thumb during scroll
 * (see index.css `.scrolling-active`). A class is required rather than
 * `:hover` because wheel/trackpad scrolling never places the cursor over
 * the thumb.
 */

export const SCROLL_ACTIVE_CLASS = 'scrolling-active';

export interface ScrollActivityTracker {
    /** Mark the element as actively scrolling and (re)arm the idle timer. */
    notify: () => void;
    /** Cancel any pending timer and remove the class. Call on unmount. */
    dispose: () => void;
}

export function createScrollActivityTracker(
    element: HTMLElement,
    options: { idleMs?: number; className?: string } = {},
): ScrollActivityTracker {
    const idleMs = options.idleMs ?? 800;
    const className = options.className ?? SCROLL_ACTIVE_CLASS;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const notify = (): void => {
        element.classList.add(className);
        if (timer !== null) {
            clearTimeout(timer);
        }
        timer = setTimeout(() => {
            element.classList.remove(className);
            timer = null;
        }, idleMs);
    };

    const dispose = (): void => {
        if (timer !== null) {
            clearTimeout(timer);
            timer = null;
        }
        element.classList.remove(className);
    };

    return { notify, dispose };
}
