/**
 * @jest-environment jsdom
 *
 * The MCP servers panel heading reports what is SENT, split by bench
 * placement.  Before this change it showed every enabled tool of every
 * connected server as in context, so a within-reach server (one catalog
 * line sent) and a shelved server (nothing sent) both showed full weight.
 *
 * The last two tests are the seam: the modal asks the status route for the
 * open conversation, and renders the new heading from the route's
 * ``token_costs.breakdown``.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../MCPRegistryModal', () => () => null);
jest.mock('../MarkdownRenderer', () => () => null);

import MCPStatusModal from '../MCPStatusModal';
import { McpTokenHeading, McpTokenBreakdown, serverTokenLabel } from '../McpTokenHeading';

const bd = (over: Partial<McpTokenBreakdown> = {}): McpTokenBreakdown => ({
  scope: 'conversation', conversation_id: 'c1', deferral: false,
  on_hand: { tokens: 9200, servers: 4, tools: 61 },
  loaded: { tokens: 2100, servers: 2, tools: 7 },
  reach_summaries: { tokens: 900, servers: 6 },
  deferred: { tokens: 0, servers: 0, tools: 0 },
  instructions: { tokens: 2700 },
  sent_total: 14900,
  not_sent: { reach_unloaded: 38400, shelved: 12000, disabled: 1300 },
  ...over,
});

beforeAll(() => {
  (window as any).matchMedia = (window as any).matchMedia || (() => ({
    matches: false, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
  }));
});

describe('McpTokenHeading', () => {
  it('headlines the sent total and lists each part of it', () => {
    render(<McpTokenHeading breakdown={bd()} />);
    expect(screen.getByTestId('mcp-tokens-total').textContent).toContain('14.9K');
    expect(screen.getByTestId('mcp-tokens-row-on_hand').textContent).toMatch(/On hand.*4 servers, 61 tools.*9\.2K/);
    expect(screen.getByTestId('mcp-tokens-row-loaded').textContent).toMatch(/Loaded from within reach.*2 servers, 7 tools.*2\.1K/);
    expect(screen.getByTestId('mcp-tokens-row-reach_summaries').textContent).toMatch(/Within-reach summaries.*6 servers listed.*900/);
    expect(screen.getByTestId('mcp-tokens-row-instructions').textContent).toContain('2.7K');
    const notSent = screen.getByTestId('mcp-tokens-not-sent').textContent || '';
    expect(notSent).toContain('38.4K within reach if loaded');
    expect(notSent).toContain('12.0K shelved');
    expect(notSent).toContain('1.3K disabled');
    expect(screen.queryByTestId('mcp-tokens-row-deferred')).toBeNull();
  });

  it('shows a deferred row, outside the total, only when the model defers', () => {
    render(<McpTokenHeading breakdown={bd({
      deferral: true,
      loaded: { tokens: 0, servers: 0, tools: 0 },
      deferred: { tokens: 40500, servers: 6, tools: 88 },
      sent_total: 12800,
      not_sent: { reach_unloaded: 0, shelved: 12000, disabled: 0 },
    })} />);
    const row = screen.getByTestId('mcp-tokens-row-deferred').textContent || '';
    expect(row).toMatch(/Sent deferred.*6 servers, 88 tools.*40\.5K/);
    expect(row).toContain('not counted');
    expect(screen.getByTestId('mcp-tokens-total').textContent).toContain('12.8K');
    expect(screen.queryByTestId('mcp-tokens-row-loaded')).toBeNull();
    expect(screen.getByTestId('mcp-tokens-not-sent').textContent).not.toContain('within reach if loaded');
  });

  it('omits empty rows and the not-sent line, and says which scope it describes', () => {
    render(<McpTokenHeading breakdown={bd({
      scope: 'project', conversation_id: null,
      loaded: { tokens: 0, servers: 0, tools: 0 },
      reach_summaries: { tokens: 0, servers: 0 },
      not_sent: { reach_unloaded: 0, shelved: 0, disabled: 0 },
    })} />);
    expect(screen.queryByTestId('mcp-tokens-row-loaded')).toBeNull();
    expect(screen.queryByTestId('mcp-tokens-row-reach_summaries')).toBeNull();
    expect(screen.queryByTestId('mcp-tokens-not-sent')).toBeNull();
    // The scope label is in the Alert's title, outside the body block.
    expect(screen.getByText(/project placements/)).toBeTruthy();
    expect(screen.queryByText(/this conversation/)).toBeNull();
  });
});

describe('serverTokenLabel', () => {
  it('says what each placement sends', () => {
    expect(serverTokenLabel({ placement: 'on_hand', sent: 9200, held: 0 })).toEqual({ text: '9.2K tokens', sent: true });
    expect(serverTokenLabel({ placement: 'loaded', sent: 2100, held: 0 })).toEqual({ text: '2.1K tokens · loaded', sent: true });
    expect(serverTokenLabel({ placement: 'partial', sent: 1500, held: 1500 }))
      .toEqual({ text: '1.5K of 3.0K tokens · partly loaded', sent: true });
    expect(serverTokenLabel({ placement: 'reach', sent: 0, held: 5000 }))
      .toEqual({ text: 'within reach · 5.0K if loaded', sent: false });
    expect(serverTokenLabel({ placement: 'deferred', sent: 0, held: 5000 }))
      .toEqual({ text: 'sent deferred · 5.0K', sent: false });
    expect(serverTokenLabel({ placement: 'shelved', sent: 0, held: 4000 }))
      .toEqual({ text: 'shelved · 4.0K not sent', sent: false });
    expect(serverTokenLabel({ placement: 'disabled', sent: 0, held: 800 })).toBeNull();
    expect(serverTokenLabel(undefined)).toBeNull();
  });
});

describe('MCPStatusModal server rows', () => {
  it('label a within-reach server by what it sends, not its full weight', async () => {
    const base = { connected: true, resources: 0, tools: 2, prompts: 0, capabilities: {} };
    (global as any).fetch = jest.fn(async (url: string) => {
      const ok = (json: any) => ({ ok: true, json: async () => json });
      if (url.startsWith('/api/mcp/status')) {
        return ok({
          initialized: true, connected_servers: 2, total_servers: 2,
          servers: { reachy: { ...base, name: 'reachy' }, handy: { ...base, name: 'handy' } },
          server_configs: { reachy: { enabled: true, command: 'x' }, handy: { enabled: true, command: 'y' } },
          server_sources: {},
          token_costs: {
            servers: { reachy: 5000, handy: 9200 }, total_tool_tokens: 14200, enabled_tool_tokens: 14200,
            instructions: { total_instruction_tokens: 0, enabled_models: 1, total_models: 1, per_model_cost: 0 },
            breakdown: bd({ per_server: {
              reachy: { placement: 'reach', sent: 0, held: 5000 },
              handy: { placement: 'on_hand', sent: 9200, held: 0 },
            } }),
          },
        });
      }
      if (url === '/api/mcp/builtin-tools/status') return ok({ categories: {} });
      if (url === '/api/mcp/permissions') return ok({ servers: {}, defaults: { server: 'enabled', tool: 'enabled' } });
      return ok({ tools: [], resources: [], prompts: [], logs: [] });
    });
    render(<MCPStatusModal visible onClose={() => {}} conversationId="c1" />);
    expect(await screen.findByText('within reach · 5.0K if loaded')).toBeTruthy();
    expect(screen.getByText('9.2K tokens')).toBeTruthy();
    // Unpatched, the row read "5.0K tokens".
    expect(screen.queryByText('5.0K tokens')).toBeNull();
  });
});

describe('MCPStatusModal heading seam', () => {
  const base = { connected: true, resources: 0, tools: 0, prompts: 0, capabilities: {} };
  const instructions = { total_instruction_tokens: 2700, enabled_models: 1, total_models: 1, per_model_cost: 2700 };

  function installFetch(breakdown?: McpTokenBreakdown) {
    const calls: Array<{ url: string; init?: any }> = [];
    (global as any).fetch = jest.fn(async (url: string, init?: any) => {
      calls.push({ url, init });
      const ok = (json: any) => ({ ok: true, json: async () => json });
      if (url.startsWith('/api/mcp/status')) {
        return ok({
          initialized: true, connected_servers: 1, total_servers: 1,
          servers: { time: { ...base, name: 'time', builtin: true } },
          server_configs: { time: { enabled: true, builtin: true } }, server_sources: {},
          token_costs: { servers: { time: 50000 }, total_tool_tokens: 50000, enabled_tool_tokens: 50000,
                         instructions, ...(breakdown ? { breakdown } : {}) },
        });
      }
      if (url === '/api/mcp/builtin-tools/status') return ok({ categories: {} });
      if (url === '/api/mcp/permissions') return ok({ servers: {}, defaults: { server: 'enabled', tool: 'enabled' } });
      return ok({ tools: [], resources: [], prompts: [], logs: [] });
    });
    return calls;
  }

  it('asks for the open conversation and renders the breakdown, not the old total', async () => {
    const calls = installFetch(bd());
    render(<MCPStatusModal visible onClose={() => {}} conversationId="c 1" projectPath="/proj" />);
    expect((await screen.findByTestId('mcp-tokens-total')).textContent).toContain('14.9K');
    // The old heading would have shown enabled tools + instructions = 52.7K.
    expect(screen.queryByText('Total Context Token Usage')).toBeNull();
    const status = calls.find(c => c.url.startsWith('/api/mcp/status'))!;
    expect(status.url).toBe('/api/mcp/status?conversation_id=c%201');
    expect(status.init.headers['X-Project-Root']).toBe('/proj');
  });

  it('keeps the old heading and the bare URL when there is no breakdown or conversation', async () => {
    const calls = installFetch();
    render(<MCPStatusModal visible onClose={() => {}} />);
    await screen.findByText('Total Context Token Usage');
    await waitFor(() => expect(calls.some(c => c.url === '/api/mcp/status')).toBe(true));
    expect(screen.queryByTestId('mcp-token-heading')).toBeNull();
  });
});
