/**
 * @jest-environment jsdom
 */
/**
 * The ⋯ move menu and the palette panel follow the app theme.
 *
 * They used ``var(--ant-color-bg-elevated, Canvas)``.  The app never enables
 * antd's cssVar, so the variable is undefined and ``Canvas`` applies -- white
 * without a document color-scheme (the theme is a ``body.dark`` class), under
 * dark-theme text that inherits near-white: unreadable.  The surfaces now come
 * from antd tokens, set together with their foreground.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ConfigProvider, theme } from 'antd';
import { CapabilityCard } from '../CapabilityCard';
import { BenchPalette } from '../BenchPalette';
import { mk } from '../benchTestUtils';

const DARK = theme.getDesignToken({ algorithm: theme.darkAlgorithm });
const LIGHT = theme.getDesignToken({ algorithm: theme.defaultAlgorithm });

const wrap = (algorithm: any, node: React.ReactNode) =>
    render(<ConfigProvider theme={{ algorithm }}>{node}</ConfigProvider>);

it('the two themes really differ (the assertions below can tell them apart)', () => {
    expect(DARK.colorBgElevated).not.toEqual(LIGHT.colorBgElevated);
    expect(DARK.colorText).not.toEqual(LIGHT.colorText);
});

it.each([['dark', theme.darkAlgorithm, DARK], ['light', theme.defaultAlgorithm, LIGHT]])(
    'the ⋯ menu in %s mode has the theme surface and text colour', (_n, algorithm, tok) => {
        wrap(algorithm, <CapabilityCard item={mk('skill:Code Review')} onIntent={() => {}} />);
        fireEvent.click(screen.getByRole('button', { name: 'Move Code Review' }));
        const menu = screen.getByRole('menu');
        expect(menu.style.background).not.toMatch(/Canvas|var\(/);
        expect(menu).toHaveStyle({ backgroundColor: tok.colorBgElevated, color: tok.colorText });
        expect(screen.getAllByRole('menuitem').length).toBeGreaterThan(0);
    });

it('the palette panel in dark mode has the theme surface and text colour', () => {
    const bench = { items: [mk('skill:Code Review')], lens: 'project', lenses: ['project'],
                    setLens: () => {}, place: async () => {} };
    wrap(theme.darkAlgorithm, <BenchPalette bench={bench as any} open onClose={() => {}} />);
    const panel = screen.getByTestId('palette-input').closest('div')!.parentElement as HTMLElement;
    expect(panel.style.background).not.toMatch(/Canvas|var\(/);
    expect(panel).toHaveStyle({ backgroundColor: DARK.colorBgElevated, color: DARK.colorText });
});
