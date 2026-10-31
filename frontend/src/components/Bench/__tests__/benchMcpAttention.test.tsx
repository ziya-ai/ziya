/**
 * @jest-environment jsdom
 *
 * MCP attention (config errors, quarantined or unreachable servers) used to
 * light the header Bench button but nothing inside the Bench, so the user
 * opened it to find everything looking fine.  The attention now lands on the
 * "MCP servers" button in the panel, the door to the panel that explains it.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel } from '../BenchPanel';
import { BenchActionsProvider } from '../BenchActions';
import { mk } from '../benchTestUtils';
import { ATTENTION_COLORS } from '../../../utils/headerAttention';

const model: any = {
    ready: true, error: null,
    items: [mk('mcp:jira', { provenance: 'user' })],
    dropped: [], weight: { always: 10, ondemand: 0, environment: 0 },
    deltas: { conversation: 0, project: 0, user: 0 },
    lenses: ['project', 'user'], lens: 'project', setLens: jest.fn(), place: jest.fn(async () => {}),
};

const show = (mcpAttention?: any) => render(
    <BenchActionsProvider actions={{ openMcpServers: () => {} }}>
        <BenchPanel bench={model} mcpAttention={mcpAttention} />
    </BenchActionsProvider>);

it('a warning outlines the MCP servers button and says why', () => {
    show({ level: 'warning', reasons: ['2 warnings in your MCP config'] });
    const b = screen.getByTestId('bench-mcp-servers');
    expect(b).toHaveAttribute('data-attention', 'warning');
    expect(b).toHaveStyle({ borderColor: ATTENTION_COLORS.warning });
    expect(b.getAttribute('title')).toContain('2 warnings in your MCP config');
});

it('an error uses the error colour', () => {
    show({ level: 'error', reasons: ['MCP config error: line 4'] });
    expect(screen.getByTestId('bench-mcp-servers')).toHaveStyle({ borderColor: ATTENTION_COLORS.error });
});

it('no attention leaves the button plain', () => {
    show({ level: 'none', reasons: [] });
    const b = screen.getByTestId('bench-mcp-servers');
    expect(b).toHaveAttribute('data-attention', 'none');
    expect(b.getAttribute('title')).not.toMatch(/warning|error/i);
});

it('App passes the MCP attention through the modal to the panel', () => {
    const fs = require('fs'), path = require('path');
    const read = (f: string) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');
    const app = fs.readFileSync(path.join(__dirname, '../../App.tsx'), 'utf8');
    expect(app).toMatch(/mcpAttention=\{headerAttention\.mcp\}/);
    expect(read('BenchModal.tsx')).toMatch(/mcpAttention=\{mcpAttention\}/);
});
