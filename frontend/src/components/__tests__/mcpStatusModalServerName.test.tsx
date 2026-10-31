/**
 * @jest-environment jsdom
 *
 * The External MCP Servers panel header must show the server's NAME (its
 * mcp_config.json key) alongside the one-line description. Before the fix,
 * getServerDisplayName() returned the description INSTEAD of the name
 * whenever one existed, so a header read "Connected  Does demo things" with
 * no way to tell which config entry it was.
 *
 * Where the server came from (registry / config file / plugin / built-in,
 * and keys copied from another harness) belongs in the expanded panel.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../MCPRegistryModal', () => () => null);
jest.mock('../MarkdownRenderer', () => () => null);

import MCPStatusModal, { describeServerSource } from '../MCPStatusModal';

const SERVER = 'demo-server';
const DESCRIPTION = 'Does demo things for testing';
const CONFIG_FILE = '/home/u/.ziya/mcp_config.json';

function installFetchMock(source: any) {
  (global as any).fetch = jest.fn(async (url: string) => {
    const ok = (json: any) => ({ ok: true, json: async () => json });
    if (url === '/api/mcp/status') {
      return ok({
        initialized: true,
        servers: { [SERVER]: { name: SERVER, connected: true, resources: 0, tools: 0, prompts: 0, capabilities: {} } },
        server_configs: { [SERVER]: { enabled: true, description: DESCRIPTION } },
        server_sources: { [SERVER]: source },
      });
    }
    if (url === '/api/mcp/builtin-tools/status') return ok({ categories: {} });
    if (url === '/api/mcp/permissions') return ok({ servers: {}, defaults: { server: 'enabled', tool: 'enabled' } });
    if (url === `/api/mcp/servers/${SERVER}/details`) return ok({ tools: [], resources: [], prompts: [], logs: [] });
    return ok({});
  });
}

describe('MCPStatusModal: external server header and source', () => {
  beforeAll(() => {
    (window as any).matchMedia = (window as any).matchMedia || (() => ({
      matches: false, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
    }));
  });

  it('shows the name and the description in the header, the source on expansion', async () => {
    installFetchMock({ kind: 'registry', provider: 'official_mcp', file: CONFIG_FILE });
    render(<MCPStatusModal visible onClose={() => {}} />);

    const nameEl = await screen.findByText(SERVER);
    const header = nameEl.closest('.ant-collapse-header')!;
    expect(header).not.toBeNull();
    expect(header.textContent).toContain(DESCRIPTION);

    // Heritage is not in the header...
    expect(header.textContent).not.toContain('official_mcp');
    // ...it is in the expanded panel.
    fireEvent.click(nameEl);
    expect(await screen.findByText('Installed from registry: official_mcp')).toBeTruthy();
    expect(screen.getByText(CONFIG_FILE)).toBeTruthy();
  });

  it('notes keys copied from another harness', async () => {
    installFetchMock({ kind: 'config_file', file: CONFIG_FILE, foreign_keys: ['transportType'] });
    render(<MCPStatusModal visible onClose={() => {}} />);
    fireEvent.click(await screen.findByText(SERVER));
    expect(await screen.findByText(/Uses transportType/)).toBeTruthy();
  });
});

describe('describeServerSource', () => {
  it('names each kind', () => {
    expect(describeServerSource({ kind: 'config_file' })).toMatch(/config file/);
    expect(describeServerSource({ kind: 'plugin', provider: 'Ent' })).toBe('Contributed by plugin: Ent');
    expect(describeServerSource({ kind: 'builtin' })).toBe('Built-in');
    expect(describeServerSource({ kind: 'builtin', overridden_in: '/x' })).toMatch(/overrides/);
    expect(describeServerSource({ kind: 'runtime' })).toMatch(/runtime/);
  });
});
