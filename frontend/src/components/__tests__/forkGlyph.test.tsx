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

    it('renders the glyph next to the label, after the continuation marker', () => {
        const label = src.indexOf('{labelText}');
        const glyph = src.indexOf('<ForkGlyph />');
        const chain = src.indexOf('>⛓</span>');
        expect(glyph).toBeGreaterThan(-1);
        expect(chain).toBeGreaterThan(-1);
        expect(glyph).toBeGreaterThan(chain);
        expect(glyph).toBeLessThan(label);
        expect(src).toContain('{isForkChild && !isHandoffContinuation && <ForkGlyph />}');
    });
});
