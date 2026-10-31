/**
 * @jest-environment jsdom
 *
 * The MCP status modal lists External MCP Servers before Builtin Tools (and
 * the builtin servers inside that section).  Before the change the builtin
 * section came first and the user's own servers were below it.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../MCPRegistryModal', () => () => null);
jest.mock('../MarkdownRenderer', () => () => null);

import MCPStatusModal from '../MCPStatusModal';

const base = { connected: true, resources: 0, tools: 0, prompts: 0, capabilities: {} };

function installFetchMock(withExternal: boolean) {
  const servers: Record<string, any> = { time: { ...base, name: 'time', builtin: true } };
  const configs: Record<string, any> = { time: { enabled: true, builtin: true } };
  if (withExternal) {
    servers['my-external'] = { ...base, name: 'my-external' };
    configs['my-external'] = { enabled: true, url: 'https://host/mcp', builtin: false };
  }
  (global as any).fetch = jest.fn(async (url: string) => {
    const ok = (json: any) => ({ ok: true, json: async () => json });
    if (url === '/api/mcp/status') {
      return ok({ initialized: true, servers, server_configs: configs, server_sources: {} });
    }
    if (url === '/api/mcp/builtin-tools/status') return ok({ categories: {} });
    if (url === '/api/mcp/permissions') return ok({ servers: {}, defaults: { server: 'enabled', tool: 'enabled' } });
    return ok({ tools: [], resources: [], prompts: [], logs: [] });
  });
}

const precedes = (a: Element, b: Element) =>
  !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('MCPStatusModal section order', () => {
  beforeAll(() => {
    (window as any).matchMedia = (window as any).matchMedia || (() => ({
      matches: false, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
    }));
  });

  it('shows External MCP Servers above Builtin Tools', async () => {
    installFetchMock(true);
    render(<MCPStatusModal visible onClose={() => {}} />);
    const external = await screen.findByText('External MCP Servers');
    const builtin = await screen.findByText('Builtin Tools');
    expect(precedes(external, builtin)).toBe(true);
    // The external server's panel is above the builtin server's too.
    expect(precedes(await screen.findByText('my-external'), builtin)).toBe(true);
  });

  it('puts the "none configured" notice in the external slot when there are none', async () => {
    installFetchMock(false);
    render(<MCPStatusModal visible onClose={() => {}} />);
    const notice = await screen.findByText('No external MCP servers configured');
    const builtin = await screen.findByText('Builtin Tools');
    expect(screen.queryByText('External MCP Servers')).toBeNull();
    expect(precedes(notice, builtin)).toBe(true);
  });
});
