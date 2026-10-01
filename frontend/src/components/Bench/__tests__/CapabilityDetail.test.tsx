/**
 * @jest-environment jsdom
 *
 * The detail drawer: where the selected capability sits and who put it
 * there, what it costs, and -- for an MCP server -- every tool with its
 * description, own token cost and switch.  A renderer over detail/place.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { CapabilityDetail, toolRows } from '../CapabilityDetail';
import { mk } from '../benchTestUtils';

const pinned = () => ({
    ...mk('skill:Tests', { project: 'always', def: 'off', provenance: 'builtin' }),
    meta: { project: { via: 'migration', at: 1 } },
});
const jira = () => mk('mcp:jira', { provenance: 'user' });
const jiraDetail = (bEnabled = true) => ({
    key: 'mcp:jira', kind: 'mcp' as const, name: 'jira', description: 'KBO Jira', connected: true,
    tools: ['mcp_a', 'mcp_b'], tool_permissions: { mcp_a: true, mcp_b: bEnabled },
    tool_details: [
        { name: 'mcp_a', description: 'Search issues by JQL', tokens: 1200, enabled: true },
        { name: 'mcp_b', description: 'Transition an issue', tokens: 800, enabled: bEnabled },
    ],
});
// Stable references: an inline arrow would re-run the drawer's load effect every render.
const skillDetail = async () => ({ key: 'skill:Tests', kind: 'skill' as const, name: 'Tests',
    description: 'Test everything', prompt: 'Create tests for every function.', path: '/x/SKILL.md' });
const loadJira = async () => jiraDetail();
const namesOnly = async () => ({ key: 'mcp:jira', kind: 'mcp' as const, name: 'jira', description: '',
    tools: ['mcp_a'], tool_permissions: { mcp_a: false } });

describe('CapabilityDetail', () => {
    it('layer table with via text, effective line, per-layer clear through place, and close', async () => {
        const place = jest.fn(async () => {});
        const onClose = jest.fn();
        render(<CapabilityDetail item={pinned()} detail={skillDetail} place={place} onClose={onClose} />);
        const sits = screen.getByTestId('sits-project');
        expect(sits).toHaveTextContent('on hand');
        expect(sits).toHaveTextContent('migrated from your previous settings');
        expect(screen.getByTestId('sits-user')).toHaveTextContent('—');
        expect(screen.getByTestId('sits-effective')).toHaveTextContent('on hand — from project');
        await waitFor(() => expect(screen.getByTestId('skill-prompt')).toHaveTextContent('Create tests for every function.'));
        expect(screen.getByTestId('skill-path')).toHaveTextContent('/x/SKILL.md');
        await act(async () => { fireEvent.click(within(sits).getByRole('button', { name: 'Clear Tests at project' })); });
        expect(place).toHaveBeenCalledWith('skill:Tests', null, 'project');
        expect(within(screen.getByTestId('sits-user')).queryByRole('button')).toBeNull();
        fireEvent.click(screen.getByTestId('detail-close'));
        expect(onClose).toHaveBeenCalled();
    });

    it("verbs are the board's move options and route through place at the lens", async () => {
        const place = jest.fn(async () => {});
        render(<CapabilityDetail item={pinned()} place={place} onClose={() => {}} />);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Move to Within reach' })); });
        expect(place).toHaveBeenCalledWith('skill:Tests', 'ondemand');
        // Pinned on hand where shelved is inherited: dropping on shelved CLEARS, as on the board.
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Move to Shelved (inherit)' })); });
        expect(place).toHaveBeenLastCalledWith('skill:Tests', null);
    });

    it('mcp: one row per tool with description, own cost and a switch; totals follow; filter narrows', async () => {
        const setTool = jest.fn(async (_s: string, _t: string, enabled: boolean) => jiraDetail(enabled));
        render(<CapabilityDetail item={jira()} detail={loadJira} onToolToggle={setTool} onClose={() => {}} />);
        const table = await screen.findByTestId('detail-tools');
        const a = within(table).getByTestId('detail-tool-mcp_a');
        expect(a).toHaveTextContent('Search issues by JQL');
        expect(a).toHaveTextContent('1,200');
        expect(screen.getByTestId('detail-tools-summary')).toHaveTextContent('2 of 2 enabled · 2,000t');
        await act(async () => { fireEvent.click(screen.getByRole('checkbox', { name: 'mcp_b enabled' })); });
        expect(setTool).toHaveBeenCalledWith('jira', 'mcp_b', false);
        expect(screen.getByRole('checkbox', { name: 'mcp_b enabled' })).not.toBeChecked();
        expect(screen.getByTestId('detail-tools-summary')).toHaveTextContent('1 of 2 enabled · 1,200t');
        fireEvent.change(screen.getByTestId('detail-tool-filter'), { target: { value: 'transition' } });
        expect(screen.queryByTestId('detail-tool-mcp_a')).toBeNull();
        expect(screen.getByTestId('detail-tool-mcp_b')).toBeInTheDocument();
    });

    it('without onToolToggle the rows are read-only; a names-only detail still lists tools', async () => {
        render(<CapabilityDetail item={jira()} detail={namesOnly} onClose={() => {}} />);
        await screen.findByTestId('detail-tools');
        expect(screen.queryByRole('checkbox')).toBeNull();
        expect(screen.getByTestId('detail-tool-mcp_a')).toHaveTextContent('mcp_a');
        expect(toolRows({ key: 'k', kind: 'mcp', name: 'n', description: '', tools: ['x'], tool_permissions: { x: false } }))
            .toEqual([{ name: 'x', description: '', tokens: 0, enabled: false }]);
    });
});
