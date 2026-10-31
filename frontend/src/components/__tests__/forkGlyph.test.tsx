/**
 * @jest-environment jsdom
 *
 * Fork children in the sidebar carry a fork marker, the way handoff
 * continuations carry ⛓.
 *
 * A plain fork (lineageKind='fork') is nested under its source conversation
 * by the same branchedFrom placement continuations use, but until now it had
 * no marker of its own, so a fork child looked like any other conversation
 * that happened to sit one level down.
 *
 * DIRECTION: against the unpatched code ForkGlyph does not exist (import
 * fails) and MUIChatHistory has no isForkChild prop or rowIsFork value.
 */
import * as fs from 'fs';
import * as path from 'path';
import React from 'react';
import { render } from '@testing-library/react';
import ForkGlyph from '../ForkGlyph';

describe('ForkGlyph', () => {
    it('renders an inline SVG that inherits text colour', () => {
        const { getByTestId } = render(<ForkGlyph />);
        const el = getByTestId('fork-glyph');
        const svg = el.querySelector('svg');
        expect(svg).not.toBeNull();
        expect(svg!.getAttribute('stroke')).toBe('currentColor');
        // Three nodes: two tines and the stem.
        expect(svg!.querySelectorAll('circle')).toHaveLength(3);
        expect(el.getAttribute('title')).toMatch(/fork/i);
    });

    it('accepts a custom tooltip', () => {
        const { getByTestId } = render(<ForkGlyph title="custom" />);
        expect(getByTestId('fork-glyph').getAttribute('title')).toBe('custom');
    });
});

describe('seam: MUIChatHistory marks fork rows', () => {
    const src = fs.readFileSync(path.join(__dirname, '../MUIChatHistory.tsx'), 'utf8');

    it('imports ForkGlyph', () => {
        expect(src).toMatch(/import ForkGlyph from '\.\/ForkGlyph';/);
    });

    it('derives rowIsFork from lineageKind === "fork" alongside rowIsContinuation', () => {
        expect(src).toContain("const rowIsContinuation = !isFolder && node.conversation?.lineageKind === 'handoff';");
        expect(src).toContain("const rowIsFork = !isFolder && node.conversation?.lineageKind === 'fork';");
    });

    it('passes it to ChatTreeItem', () => {
        expect(src).toMatch(/isForkChild=\{rowIsFork\}/);
    });

    it('lineage glyph REPLACES the chat bubble in the leading slot', () => {
        // Leading slot is a chain: delegate badge → ⛓ → fork glyph → ChatIcon.
        // Both lineage glyphs must sit between the delegate branch and the
        // ChatIcon fallback, i.e. in the same conditional, not beside the bubble.
        const chatIcon = src.indexOf('<ChatIcon sx=');
        const chain = src.indexOf(') : isHandoffContinuation ? (');
        const fork = src.indexOf(') : isForkChild ? (');
        expect(chain).toBeGreaterThan(-1);
        expect(fork).toBeGreaterThan(chain);
        expect(chatIcon).toBeGreaterThan(fork);
        expect(src).toContain('data-testid="handoff-glyph"');
        // Continuation wins over fork (a continuation is also a branch), and
        // the ⛓ is no longer gated on the absence of a chain ordinal.
        expect(src).not.toContain('isHandoffContinuation && !handoffOrdinal');
        // Nothing lineage-related is rendered inside the label any more.
        const label = src.indexOf('{labelText}');
        expect(src.slice(label - 200, label)).not.toContain('ForkGlyph');
        expect(src.slice(label - 200, label)).not.toContain('⛓');
    });
});
