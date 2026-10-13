/**
 * @jest-environment jsdom
 *
 * DigestPreview is the collapsed body of a tool block; ToolResultViewer is
 * its expanded body for JSON payloads (Table / Tree / Raw tabs, or Message).
 * These replace the first-200-characters preview, so the key assertion in
 * each case is that the useless shape ("{", the command echo) is gone and
 * the facts are present.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { DigestPreview, ToolResultViewer, chooseBody } from '../ToolDigestView';
import { digestToolResult } from '../../utils/toolDigest';

const rows = Array.from({ length: 12 }, (_, i) => ({
    key: `PROJ-${600 + i}`, summary: `Issue ${i}`, status: i % 2 ? 'Open' : 'Resolved',
    assignee: `u${i % 3}`, url: `https://x/${i}`, created: '2026-01-01',
}));
const collection = digestToolResult('mcp_search_jira_issues', JSON.stringify({ total: 225, issues: rows }));

describe('DigestPreview', () => {
    it('shows headline, range and chips for a collection, and no lone brace', () => {
        const onExpand = jest.fn();
        render(<DigestPreview digest={collection} isDarkMode={false} onExpand={onExpand} />);
        expect(screen.getByTestId('digest-headline')).toHaveTextContent('225 issues matched, showing 12');
        expect(screen.getByTestId('digest-range')).toHaveTextContent('PROJ-600 → PROJ-611');
        expect(screen.getByText('Open 6')).toBeInTheDocument();
        expect(screen.getByText('Resolved 6')).toBeInTheDocument();
        expect(screen.getByText('3 assignees')).toBeInTheDocument();
        expect(screen.getByTestId('digest-preview').textContent).not.toMatch(/^\{/);
        fireEvent.click(screen.getByTestId('digest-preview'));
        expect(onExpand).toHaveBeenCalledTimes(1);
    });

    it('shows a record as facts with a "+N more fields" line', () => {
        const d = digestToolResult('t', JSON.stringify({
            groupName: 'Payments Oncall', memberCount: 14, secondaryOwner: 'carol', nested: { a: 1 }, list: [1, 2],
        }));
        render(<DigestPreview digest={d} isDarkMode={false} onExpand={() => {}} />);
        expect(screen.getByTestId('digest-headline')).toHaveTextContent('Payments Oncall');
        expect(screen.getByText('secondaryOwner')).toBeInTheDocument();
        expect(screen.getByText('carol')).toBeInTheDocument();
        expect(screen.getByTestId('digest-more')).toHaveTextContent('+ 2 more fields');
    });

    it('shows shell output lines, never the command, with the exit chip', () => {
        const d = digestToolResult('mcp_run_shell_command', '$ ls /nope\nls: /nope: No such file\n[Exit code: 1]');
        render(<DigestPreview digest={d} isDarkMode={false} onExpand={() => {}} />);
        expect(screen.getByTestId('digest-lines')).toHaveTextContent('ls: /nope: No such file');
        expect(screen.getByTestId('digest-lines').textContent).not.toContain('$ ls');
        expect(screen.getByText('exit 1')).toBeInTheDocument();
    });
});

describe('ToolResultViewer', () => {
    it('opens a collection on the Table tab with the identifier column first and hidden columns counted', () => {
        render(<ToolResultViewer digest={collection} isDarkMode={false} raw={<pre data-testid="raw">RAW</pre>} />);
        const headers = screen.getAllByRole('columnheader').map(h => h.textContent);
        expect(headers[0]).toBe('key');
        expect(headers).toContain('status');
        expect(screen.getByRole('cell', { name: 'PROJ-600' })).toBeInTheDocument();
        expect(screen.getByTestId('viewer-hidden-columns')).toHaveTextContent('1 hidden');
        expect(screen.queryByTestId('raw')).toBeNull();
        fireEvent.click(screen.getByTestId('viewer-hidden-columns'));
        expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toContain('created');
    });

    it('caps rows and reveals the rest on request', () => {
        const big = digestToolResult('t', JSON.stringify(Array.from({ length: 80 }, (_, i) => ({ id: i, v: `r${i}` }))));
        render(<ToolResultViewer digest={big} isDarkMode={false} raw={<pre>RAW</pre>} />);
        expect(screen.queryByRole('cell', { name: 'r79' })).toBeNull();
        fireEvent.click(screen.getByTestId('viewer-show-all'));
        expect(screen.getByRole('cell', { name: 'r79' })).toBeInTheDocument();
    });

    it('switches to Raw and back, keeping the digest headline pinned above the tabs', () => {
        render(<ToolResultViewer digest={collection} isDarkMode={false} raw={<pre data-testid="raw">RAW</pre>} />);
        fireEvent.click(screen.getByRole('tab', { name: 'Raw' }));
        expect(screen.getByTestId('raw')).toBeInTheDocument();
        expect(screen.getByTestId('digest-headline')).toHaveTextContent('225 issues');
        fireEvent.click(screen.getByRole('tab', { name: 'Tree' }));
        expect(screen.getByTestId('json-tree')).toBeInTheDocument();
    });

    it('a record opens on Tree; nodes collapse and expand', () => {
        const d = digestToolResult('t', JSON.stringify({ groupName: 'Oncall', cti: { category: 'Payments', item: 'Oncall' } }));
        render(<ToolResultViewer digest={d} isDarkMode={false} raw={<pre>RAW</pre>} />);
        expect(screen.getByRole('tab', { name: 'Tree', selected: true })).toBeInTheDocument();
        expect(screen.getByText('Payments')).toBeInTheDocument();
        fireEvent.click(screen.getByTestId('tree-toggle-cti'));
        expect(screen.queryByText('Payments')).toBeNull();
    });

    it('a message renders as prose with the link as the primary action and the warning strip', () => {
        const d = digestToolResult('t', JSON.stringify({
            message: 'Workspace could not be found.\n\nCreated code review.\n\nhttps://review.example.com/reviews/CR-1234567/revisions/1',
        }));
        render(<ToolResultViewer digest={d} isDarkMode={false} raw={<pre data-testid="raw">RAW</pre>} />);
        const link = screen.getByRole('link', { name: /CR-1234567/ });
        expect(link).toHaveAttribute('href', 'https://review.example.com/reviews/CR-1234567/revisions/1');
        expect(screen.getByTestId('digest-warning')).toHaveTextContent('could not be found');
        expect(screen.getByTestId('message-body').textContent).not.toContain('\\n');
        expect(screen.queryByTestId('raw')).toBeNull();
    });
});

describe('chooseBody (the seam MarkdownRenderer calls)', () => {
    it('routes collections and messages to the viewer regardless of size, records only when large', () => {
        expect(chooseBody(collection, false)).toBe('viewer');
        const msg = digestToolResult('t', JSON.stringify({ message: 'A sentence that is long enough to be a message.' }));
        expect(chooseBody(msg, false)).toBe('viewer');
        const rec = digestToolResult('t', JSON.stringify({ ok: true, n: 3 }));
        expect(chooseBody(rec, false)).toBe('raw');
        expect(chooseBody(rec, true)).toBe('viewer');
        const sh = digestToolResult('mcp_run_shell_command', '$ ls\na\nb');
        expect(chooseBody(sh, true)).toBe('raw');
    });
});

describe('MarkdownRenderer wiring (lockstep guard)', () => {
    // The renderer is too large to mount here (see markdownRendererStubMount
    // spike).  Guard the seam at source level: the collapsed branch must use
    // the digest, and the first-line preview must be gone.
    const fs = require('fs');
    const path = require('path');
    const src: string = fs.readFileSync(path.join(__dirname, '..', 'MarkdownRenderer.tsx'), 'utf8');

    it('imports the digest and renders DigestPreview / ToolResultViewer', () => {
        expect(src).toMatch(/import \{[^}]*digestToolResult[^}]*\} from '\.\.\/utils\/toolDigest'/);
        expect(src).toMatch(/<DigestPreview\b/);
        expect(src).toMatch(/<ToolResultViewer\b/);
        expect(src).toMatch(/chooseBody\(/);
    });

    it('no longer previews the first line of the payload', () => {
        expect(src).not.toContain("cleanContent.split('\\n')[0]");
        expect(src).not.toContain('- Click to expand');
    });
});
