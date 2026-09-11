/**
 * @jest-environment jsdom
 *
 * Disabling an MCP server from MCPStatusModal must fold its panel shut.
 *
 * The Collapse is controlled by `expandedKeys` (keyed by server name). Before
 * the fix, toggleServer() only refreshed status, so a disabled server's panel
 * stayed open showing its now-stale tool list. Re-enabling leaves the panel
 * collapsed until the user expands it again (which refetches details).
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../MCPRegistryModal', () => () => null);
jest.mock('../MarkdownRenderer', () => () => null);

import MCPStatusModal from '../MCPStatusModal';

const SERVER = 'demo-server';
const TOOL = 'demo_tool_alpha';

function statusPayload(enabled: boolean) {
  return {
    enabled: true,
    servers: {
      [SERVER]: {
        name: SERVER, connected: enabled, resources: 0, tools: 1, prompts: 0, capabilities: {},
      },
    },
    server_configs: { [SERVER]: { enabled } },
    total_tools: 1, total_resources: 0, total_prompts: 0,
  };
}

function installFetchMock() {
  let enabled = true;
  const calls: Array<{ url: string; body?: any }> = [];
  (global as any).fetch = jest.fn(async (url: string, init?: any) => {
    const body = init?.body ? JSON.parse(init.body) : undefined;
    calls.push({ url, body });
    const ok = (json: any) => ({ ok: true, json: async () => json });
    if (url === '/api/mcp/status') return ok(statusPayload(enabled));
    if (url === '/api/mcp/builtin-tools/status') return ok({ categories: {} });
    if (url === '/api/mcp/permissions') return ok({ servers: {}, defaults: { server: 'enabled', tool: 'enabled' } });
    if (url === `/api/mcp/servers/${SERVER}/details`) {
      return ok({ tools: [{ name: TOOL, description: 'alpha' }], resources: [], prompts: [], logs: [] });
    }
    if (url === '/api/mcp/toggle-server') {
      enabled = body.enabled;
      return ok({ success: true, message: 'toggled' });
    }
    return ok({});
  });
  return calls;
}

describe('MCPStatusModal: disabling a server collapses its panel', () => {
  beforeAll(() => {
    // antd Collapse/Modal touch matchMedia in jsdom.
    (window as any).matchMedia = (window as any).matchMedia || (() => ({
      matches: false, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
    }));
  });

  it('shows tools when expanded, then hides them after the server is switched off', async () => {
    const calls = installFetchMock();
    render(<MCPStatusModal visible onClose={() => {}} />);

    // Expand the server panel via its header; details are fetched on expand.
    const header = await screen.findByText(SERVER);
    fireEvent.click(header);
    await screen.findByText(TOOL);
    expect(calls.some(c => c.url === `/api/mcp/servers/${SERVER}/details`)).toBe(true);
    expect(header.closest('.ant-collapse-item')!.classList.contains('ant-collapse-item-active')).toBe(true);

    // Flip the server-process Switch off. It lives in the panel body in the
    // "Server Process" Descriptions row, alongside per-tool switches, so pick
    // the one whose row carries that label.
    const serverSwitch = screen.getAllByRole('switch').find(s =>
      s.closest('tr')?.textContent?.includes('Server Process'));
    expect(serverSwitch).toBeTruthy();
    fireEvent.click(serverSwitch!);

    // Positive assertion that the toggle actually went to the backend...
    await waitFor(() => {
      expect(calls.find(c => c.url === '/api/mcp/toggle-server')?.body)
        .toEqual({ server_name: SERVER, enabled: false });
    });
    // ...and the panel is collapsed. antd keeps inactive panel content mounted
    // (destroyInactivePanel=false) and hides it, so assert on the item's
    // active class rather than on the tool text being removed from the DOM.
    const item = () => screen.getByText(SERVER).closest('.ant-collapse-item')!;
    await waitFor(() => {
      expect(item().classList.contains('ant-collapse-item-active')).toBe(false);
    });
    expect(item().querySelector('.ant-collapse-content-hidden, .ant-collapse-content-inactive')).not.toBeNull();
    // The panel itself (header) is still listed (getByText throws if not).
    expect(screen.getByText(SERVER)).toBeTruthy();
  });
});
