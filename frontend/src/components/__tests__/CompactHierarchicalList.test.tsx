/**
 * @jest-environment jsdom
 *
 * CompactHierarchicalList — the one-line-per-hit layout a formatter selects
 * with layout: 'compact'.  Asserts on what the user sees, and that
 * MarkdownRenderer actually routes compact results here.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { CompactHierarchicalList } from '../CompactHierarchicalList';

const colors = { border: '#ccc', contentText: '#111' };
const rows = [
    { title: 'RepoA/src/hugo/fpga.rs', content: 'BODY-0', group: 'RepoA', detail: '2 matches · 46.8 KB' },
    { title: 'RepoB/tests/pioneer.rs', content: 'BODY-1', group: 'RepoB', detail: '0 matches · 18.5 KB', dim: true },
    { title: 'RepoA/src/pipeline/mod.rs', content: 'BODY-2', group: 'RepoA', detail: '2 matches · 40.0 KB' },
];

const renderList = () => render(
    <CompactHierarchicalList rows={rows} colors={colors} renderBody={r => <span>{r.content}</span>} />,
);

describe('CompactHierarchicalList', () => {
    it('groups rows under their repository with a hit count', () => {
        const { container } = renderList();
        const groupA = container.querySelector('[data-group="RepoA"]') as HTMLElement;
        expect(groupA).toBeTruthy();
        expect(within(groupA).getByText('2 hits')).toBeInTheDocument();
        expect(within(groupA).getByText('fpga.rs')).toBeInTheDocument();
        expect(within(groupA).getByText('mod.rs')).toBeInTheDocument();
        expect(container.querySelector('[data-group="RepoB"]')).toHaveTextContent('1 hit');
    });

    it('shows the path without the repo prefix and the detail on the row', () => {
        renderList();
        expect(screen.getByText('src/hugo/')).toBeInTheDocument();
        expect(screen.queryByText(/RepoA\/src/)).toBeNull();
        expect(screen.getByText('2 matches · 46.8 KB')).toBeInTheDocument();
    });

    it('expands a row body on click and on Enter, collapsed by default', () => {
        renderList();
        expect(screen.queryByText('BODY-0')).toBeNull();
        const rowButton = screen.getByTitle('RepoA/src/hugo/fpga.rs');
        expect(rowButton).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(rowButton);
        expect(screen.getByText('BODY-0')).toBeInTheDocument();
        expect(screen.queryByText('BODY-2')).toBeNull();
        fireEvent.keyDown(screen.getByTitle('RepoA/src/pipeline/mod.rs'), { key: 'Enter' });
        expect(screen.getByText('BODY-2')).toBeInTheDocument();
    });

    it('fades dimmed rows only', () => {
        renderList();
        expect(screen.getByTitle('RepoB/tests/pioneer.rs')).toHaveStyle({ opacity: '0.5' });
        expect(screen.getByTitle('RepoA/src/hugo/fpga.rs')).toHaveStyle({ opacity: '1' });
    });

    it('is what MarkdownRenderer renders for layout compact, sharing the row body', () => {
        const src = fs.readFileSync(path.join(__dirname, '../MarkdownRenderer.tsx'), 'utf8');
        expect(src).toMatch(/layout === 'compact' \? \(\s*<CompactHierarchicalList/);
        expect(src).toContain('renderBody={renderHierarchicalBody}');
        expect(src).toContain('layout: parsed.layout');
    });
});
