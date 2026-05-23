/**
 * Tests for the two-part fix in DiffLine / MarkdownRenderer:
 *
 * 1. Long lines in prose diffs (.md, .txt, etc.) wrap instead of forcing
 *    horizontal scroll — driven by `shouldWrapForLanguage()`.
 * 2. The horizontal scroll position of a hunk is preserved across content
 *    reflow during streaming — the bug was that the inner table briefly
 *    narrows when a partial token arrives, the browser clamps `scrollLeft`
 *    to the new max, and the position is lost when the table grows again.
 *
 * Test (1) hits the pure helper.  Test (2) simulates the resize observer
 * callback against a fake DOM element, validating the restore logic
 * without a real browser.
 */

import { shouldWrapForLanguage } from '../MarkdownRenderer';

describe('shouldWrapForLanguage', () => {
    it('wraps markdown', () => {
        expect(shouldWrapForLanguage('markdown')).toBe(true);
    });

    it('wraps plaintext', () => {
        expect(shouldWrapForLanguage('plaintext')).toBe(true);
    });

    it('treats unknown / empty as plain text (wrap)', () => {
        expect(shouldWrapForLanguage('')).toBe(true);
        expect(shouldWrapForLanguage(undefined)).toBe(true);
    });

    it('does not wrap typescript', () => {
        expect(shouldWrapForLanguage('typescript')).toBe(false);
    });

    it('does not wrap python', () => {
        expect(shouldWrapForLanguage('python')).toBe(false);
    });

    it('does not wrap bash', () => {
        expect(shouldWrapForLanguage('bash')).toBe(false);
    });

    it('is case-insensitive', () => {
        expect(shouldWrapForLanguage('Markdown')).toBe(true);
        expect(shouldWrapForLanguage('PLAINTEXT')).toBe(true);
        expect(shouldWrapForLanguage('TypeScript')).toBe(false);
    });
});

/**
 * Pure JS port of the scroll-restore logic inside HunkScrollContainer's
 * ResizeObserver callback.  Keeping this in lock-step with the production
 * code lets us exercise the clamp/restore behavior without a JSDOM setup
 * that supports ResizeObserver.
 */
function restoreScrollLeft(
    el: { scrollLeft: number; scrollWidth: number; clientWidth: number },
    userScrollLeft: number,
): void {
    if (userScrollLeft <= 0) return;
    const maxScroll = Math.max(0, el.scrollWidth - el.clientWidth);
    const target = Math.min(userScrollLeft, maxScroll);
    if (el.scrollLeft < target) {
        el.scrollLeft = target;
    }
}

describe('hunk scroll restore (streaming clamp/grow cycle)', () => {
    it('restores scrollLeft after the table widens back out', () => {
        // Initial state: user has scrolled 800px right in a wide table.
        const el = { scrollLeft: 800, scrollWidth: 2000, clientWidth: 800 };
        let userScrollLeft = 800;

        // Streaming chunk arrives partial — table briefly narrows.
        // Browser clamps scrollLeft to the new max (= 400).
        el.scrollWidth = 1200;
        el.scrollLeft = 400;

        // Resize observer fires for the shrink — restore is a no-op
        // because we can't reach userScrollLeft yet.
        restoreScrollLeft(el, userScrollLeft);
        expect(el.scrollLeft).toBe(400);

        // Next streaming chunk completes the line — table grows back.
        el.scrollWidth = 2000;
        // scrollLeft stays clamped at 400 unless we restore.
        restoreScrollLeft(el, userScrollLeft);
        expect(el.scrollLeft).toBe(800);
    });

    it('does not over-scroll past the new max when content stays narrow', () => {
        const el = { scrollLeft: 0, scrollWidth: 1000, clientWidth: 800 };
        // User had scrolled to 800 in a previous wider state.
        const userScrollLeft = 800;
        restoreScrollLeft(el, userScrollLeft);
        // Max possible is 1000 - 800 = 200; we should clamp to that.
        expect(el.scrollLeft).toBe(200);
    });

    it('is a no-op when userScrollLeft is 0', () => {
        const el = { scrollLeft: 0, scrollWidth: 2000, clientWidth: 800 };
        restoreScrollLeft(el, 0);
        expect(el.scrollLeft).toBe(0);
    });
});
