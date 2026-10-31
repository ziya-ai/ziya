/**
 * @jest-environment jsdom
 *
 * The context-budget bar is stacked by spender (files · chat · tools · ast)
 * and shared by the sidebar footer and the collapsed strip.  Before this the
 * strip painted tools in the text colour (a tool-heavy session read as one
 * black bar) and the footer used a single-colour AntD Progress.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { TokenBreakdownBar, TokenBreakdownLegend, segmentPercents, fmtK } from '../TokenBreakdownBar';

const b = { files: 31_200, chat: 9_400, tools: 18_600, ast: 1_100, limit: 200_000 };

describe('segmentPercents', () => {
    it('scales each spender against the limit', () => {
        const p = segmentPercents(b);
        expect(p.files).toBeCloseTo(15.6, 1);
        expect(p.tools).toBeCloseTo(9.3, 1);
    });
    it('never exceeds 100 in total', () => {
        const p = segmentPercents({ files: 150_000, chat: 80_000, tools: 10_000, ast: 0, limit: 200_000 });
        expect(p.files + p.chat + p.tools + p.ast).toBeLessThanOrEqual(100);
        expect(p.tools).toBe(0);
    });
    it('is all zero without a limit', () => {
        expect(Object.values(segmentPercents({ ...b, limit: 0 })).every(v => v === 0)).toBe(true);
    });
});

describe('TokenBreakdownBar', () => {
    it('renders the four segments in spender order with token classes, not inline colours', () => {
        render(<TokenBreakdownBar breakdown={b} />);
        const segs = Array.from(screen.getByTestId('token-breakdown-bar').children) as HTMLElement[];
        expect(segs.map(s => s.className)).toEqual(['files', 'chat', 'tools', 'ast']);
        for (const s of segs) expect(s.style.background).toBe('');
        expect(segs[2].style.width).toMatch(/^9\.3/);
    });
    it('marks the track danger past the threshold', () => {
        const { rerender } = render(<TokenBreakdownBar breakdown={b} />);
        expect(screen.getByTestId('token-breakdown-bar').className).not.toMatch(/danger/);
        rerender(<TokenBreakdownBar breakdown={b} danger />);
        expect(screen.getByTestId('token-breakdown-bar').className).toMatch(/danger/);
    });
    it('marks the bar warning in the orange zone; danger wins when both are set', () => {
        const { rerender } = render(<TokenBreakdownBar breakdown={b} warning />);
        expect(screen.getByTestId('token-breakdown-bar').className).toMatch(/\bwarning\b/);
        rerender(<TokenBreakdownBar breakdown={b} warning danger />);
        const cls = screen.getByTestId('token-breakdown-bar').className;
        expect(cls).toMatch(/\bdanger\b/);
        expect(cls).not.toMatch(/\bwarning\b/);
    });
});

describe('TokenBreakdownLegend', () => {
    it('carries a swatch per spender and hides zero tools/ast', () => {
        render(<TokenBreakdownLegend breakdown={{ ...b, ast: 0 }} />);
        const sw = screen.getByTestId('token-breakdown-legend').querySelectorAll('.sw');
        expect(Array.from(sw).map(e => e.className)).toEqual(['sw files', 'sw chat', 'sw tools']);
        expect(screen.getByText(/tools 18\.6k/)).toBeTruthy();
    });
    it('fmtK', () => {
        expect(fmtK(999)).toBe('999');
        expect(fmtK(31_200)).toBe('31.2k');
        expect(fmtK(200_000)).toBe('200k');
    });
});
