/**
 * The mockup iframe must supply a foreground colour that contrasts with the
 * preview surface behind it. It previously set neither, so `color` fell back
 * to the browser default (black) while the surface was painted #1f1f1f in
 * dark mode — mockups that didn't hardcode their own colour were unreadable,
 * and the preview was not representative of the real UI.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { HTMLMockupRenderer } from '../HTMLMockupRenderer';

let mockIsDarkMode = true;
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: mockIsDarkMode }),
}));
jest.mock('../../utils/domSanitize', () => ({
    sanitizeMockupHtml: (s: string) => s,
}));

const MOCKUP = '<div><p>uncoloured text</p></div>';

const srcDocOf = (container: HTMLElement): string => {
    const frame = container.querySelector('iframe[title="HTML Mockup Preview"]');
    expect(frame).not.toBeNull();
    return (frame as HTMLIFrameElement).getAttribute('srcdoc') || '';
};

describe('HTMLMockupRenderer iframe theming', () => {
    afterEach(() => { mockIsDarkMode = true; });

    it('sets a light foreground on the iframe body in dark mode', () => {
        mockIsDarkMode = true;
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} />);
        const doc = srcDocOf(container);
        expect(doc).toMatch(/color:\s*#e6e6e6/);
        expect(doc).toMatch(/color-scheme:\s*dark/);
    });

    it('sets a dark foreground on the iframe body in light mode', () => {
        mockIsDarkMode = false;
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} />);
        const doc = srcDocOf(container);
        expect(doc).toMatch(/color:\s*#1f1f1f/);
        expect(doc).toMatch(/color-scheme:\s*light/);
    });

    it('never leaves body colour unset — the original defect', () => {
        for (const dark of [true, false]) {
            mockIsDarkMode = dark;
            const { container, unmount } = render(<HTMLMockupRenderer html={MOCKUP} />);
            const bodyRule = /body\s*\{[^}]*\}/.exec(srcDocOf(container));
            expect(bodyRule).not.toBeNull();
            expect(bodyRule![0]).toMatch(/(^|[^-])color:/);
            unmount();
        }
    });

    it('exposes border/muted tokens so mockups need not hardcode a palette', () => {
        mockIsDarkMode = true;
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} />);
        const doc = srcDocOf(container);
        expect(doc).toContain('--mockup-border');
        expect(doc).toContain('--mockup-muted');
    });

    it('keeps the preview surface and iframe foreground on the same theme', () => {
        mockIsDarkMode = true;
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} />);
        const surface = (container.querySelector(
            'iframe[title="HTML Mockup Preview"]',
        ) as HTMLElement).parentElement!;
        // Dark surface must pair with the light foreground asserted above.
        expect(surface.style.backgroundColor).toBe('rgb(31, 31, 31)');
        expect(srcDocOf(container)).toMatch(/color:\s*#e6e6e6/);
    });

    it('keeps the background toggle off the inline header', () => {
        // The inline header also frames conversational figures, so a
        // design-review control does not belong on it.
        render(<HTMLMockupRenderer html={MOCKUP} />);
        expect(screen.queryByLabelText('Toggle preview background')).toBeNull();
        expect(screen.getByLabelText('View source')).toBeInTheDocument();
        expect(screen.getByLabelText('Copy HTML')).toBeInTheDocument();
        expect(screen.getByLabelText('Pop-out')).toBeInTheDocument();
    });

    it('flips the preview theme from the pop-out without touching the app theme', () => {
        mockIsDarkMode = true;
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} />);
        expect(srcDocOf(container)).toMatch(/color:\s*#e6e6e6/);

        fireEvent.click(screen.getByLabelText('Pop-out'));
        fireEvent.click(screen.getByLabelText('Toggle preview background'));

        const doc = srcDocOf(container);
        expect(doc).toMatch(/color:\s*#1f1f1f/);
        expect(doc).toMatch(/color-scheme:\s*light/);
        const surface = (container.querySelector(
            'iframe[title="HTML Mockup Preview"]',
        ) as HTMLElement).parentElement!;
        expect(surface.style.backgroundColor).toBe('rgb(255, 255, 255)');
    });
});

describe('HTMLMockupRenderer figure variant', () => {
    afterEach(() => { mockIsDarkMode = true; });

    it('renders no chrome at all', () => {
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} variant="figure" />);
        expect(container.querySelectorAll('button')).toHaveLength(0);
        expect(container.textContent).not.toContain('UI Mockup');
        expect(container.querySelector('iframe[title="HTML Mockup Preview"]')).not.toBeNull();
    });

    it('sits on a transparent background so it reads as part of the message', () => {
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} variant="figure" />);
        const frame = container.querySelector(
            'iframe[title="HTML Mockup Preview"]',
        ) as HTMLIFrameElement;
        expect(frame.style.background).toBe('transparent');
        // `border: none` is deliberately not asserted. jsdom's cssstyle drops
        // that declaration outright — it is absent from both cssText and the
        // style attribute — so it cannot be observed through the CSSOM at all
        // (a width-bearing shorthand like '2px solid #303030' survives, so the
        // gap is specific to the `none` keyword). The no-chrome property is
        // covered by 'renders no chrome at all' above, which asserts only
        // things jsdom can actually see.
    });

    it('still supplies a theme-matched foreground and keeps the sandbox', () => {
        mockIsDarkMode = true;
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} variant="figure" />);
        const frame = container.querySelector(
            'iframe[title="HTML Mockup Preview"]',
        ) as HTMLIFrameElement;
        expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
        expect(srcDocOf(container)).toMatch(/color:\s*#e6e6e6/);
    });

    it('preserves the mockup markup itself', () => {
        const { container } = render(<HTMLMockupRenderer html={MOCKUP} />);
        expect(srcDocOf(container)).toContain('uncoloured text');
    });
});
