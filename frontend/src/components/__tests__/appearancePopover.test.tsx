/**
 * @jest-environment jsdom
 *
 * AppearancePanel → store → <html> attribute seam (design/visual-refresh.md).
 * Clicking a control in the panel must change the attribute theme-tokens.css
 * selects on; a panel wired to local state only would pass a render test and
 * change nothing on screen.
 *
 * Requires the stage-1 diffs (AppearancePopover.tsx, utils/appearance.ts)
 * to be applied; red until then.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { AppearancePanel } from '../AppearancePopover';
import { ThemeProvider } from '../../context/ThemeContext';
import { _resetAppearanceForTests, getAppearance } from '../../utils/appearance';

beforeEach(() => {
    localStorage.clear();
    _resetAppearanceForTests();
    window.matchMedia = window.matchMedia || ((() => ({
        matches: false, addEventListener: () => {}, removeEventListener: () => {},
    })) as any);
});

const mount = () => render(<ThemeProvider><AppearancePanel /></ThemeProvider>);

it('choosing a response font stamps data-font on <html> and persists it', () => {
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'Georgia' }));
    expect(document.documentElement.getAttribute('data-font')).toBe('georgia');
    expect(getAppearance().font).toBe('georgia');
});

it('choosing a paper tone stamps data-paper', () => {
    localStorage.setItem('ZIYA_THEME_PREFERENCE', 'false');
    mount();
    fireEvent.click(screen.getByRole('button', { name: 'Warm' }));
    expect(document.documentElement.getAttribute('data-paper')).toBe('warm');
});

it('paper swatches are disabled in dark mode (they only apply to Light)', () => {
    localStorage.setItem('ZIYA_THEME_PREFERENCE', 'true');
    mount();
    expect((screen.getByRole('button', { name: 'Linen' }) as HTMLButtonElement).disabled).toBe(true);
});

it('toggling the prose cap flips data-prose-cap', () => {
    mount();
    expect(document.documentElement.getAttribute('data-prose-cap')).toBe('off');
    fireEvent.click(screen.getByRole('switch'));
    expect(document.documentElement.getAttribute('data-prose-cap')).toBe('on');
});
