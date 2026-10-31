/**
 * @jest-environment jsdom
 *
 * Copying the "Config Stanza" of an inspected MCP server must yield plain
 * JSON with ASCII whitespace.  Regression: a stanza pasted into
 * ~/.ziya/mcp_config.json carried U+00A0 NO-BREAK SPACE in its indentation and
 * the loader rejected the file.
 *
 * Both paths are covered: the Copy button (serializes the object, never the
 * DOM) and a manual mouse selection (the onCopy handler rewrites the
 * selection to text/plain only, with NBSP-family spaces mapped to U+0020 and
 * zero-width characters dropped).
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ isDarkMode: false }),
}));
jest.mock('../MCPRegistryModal', () => () => null);
jest.mock('../MarkdownRenderer', () => () => null);

import MCPStatusModal from '../MCPStatusModal';
import { cleanCopiedText } from '../../utils/plainTextCopy';
import { portableServerStanza } from '../../utils/mcpStanza';

const SERVER = 'stanza-server';
const CONFIG = {
  enabled: true,
  url: 'https://host.example/mcp',
  description: 'caf\u00e9 \u2014 demo',
  args: ['--flag', 'value'],
  env: { TOKEN_FILE: '/tmp/x' },
};
// What /api/mcp/status serves: the loader adds `builtin`, which is Ziya's
// bookkeeping, not part of the user's entry.
const SERVED = { ...CONFIG, builtin: false };
// What the tab shows and copies: a complete mcp_config.json, keyed by name,
// that another Ziya instance loads as-is.
const STANZA = { mcpServers: { [SERVER]: CONFIG } };

function installFetchMock() {
  (global as any).fetch = jest.fn(async (url: string) => {
    const ok = (json: any) => ({ ok: true, json: async () => json });
    if (url === '/api/mcp/status') {
      return ok({
        initialized: true,
        servers: { [SERVER]: { name: SERVER, connected: true, resources: 0, tools: 0, prompts: 0, capabilities: {} } },
        server_configs: { [SERVER]: SERVED },
        server_sources: { [SERVER]: { kind: 'config_file', file: '/x/mcp_config.json' } },
      });
    }
    if (url === '/api/mcp/builtin-tools/status') return ok({ categories: {} });
    if (url === '/api/mcp/permissions') return ok({ servers: {}, defaults: { server: 'enabled', tool: 'enabled' } });
    if (url === `/api/mcp/servers/${SERVER}/details`) return ok({ tools: [], resources: [], prompts: [], logs: [] });
    return ok({});
  });
}

/** True when every character outside JSON string literals is printable ASCII or JSON whitespace. */
const asciiOutsideStrings = (text: string): boolean =>
  !/[^\t\n\r\x20-\x7e]/.test(text.replace(/"(?:[^"\\]|\\.)*"/g, '""'));

async function openStanza(): Promise<HTMLElement> {
  render(<MCPStatusModal visible onClose={() => {}} />);
  fireEvent.click(await screen.findByText(SERVER));
  fireEvent.click(await screen.findByText('Config Stanza'));
  return screen.findByTestId('mcp-config-stanza');
}

describe('MCP config stanza copy', () => {
  let writeText: jest.Mock;
  beforeAll(() => {
    (window as any).matchMedia = (window as any).matchMedia || (() => ({
      matches: false, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
    }));
  });
  beforeEach(() => {
    installFetchMock();
    writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  });

  it('is rendered as plain text in a <pre>, not through markdown/linkify', async () => {
    const pre = await openStanza();
    expect(pre.tagName).toBe('PRE');
    expect(pre.querySelector('a')).toBeNull();
    expect(pre.textContent).toBe(JSON.stringify(STANZA, null, 2));
    expect(pre.textContent).not.toMatch(/\u00a0/);
  });

  it('Copy button writes the serialized object, which round-trips', async () => {
    await openStanza();
    fireEvent.click(screen.getByTestId('mcp-config-stanza-copy'));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const text: string = writeText.mock.calls[0][0];
    expect(text).toBe(JSON.stringify(STANZA, null, 2));
    expect(asciiOutsideStrings(text)).toBe(true);
    expect(JSON.parse(text)).toEqual(STANZA);
    // Carries its name; Ziya's load-time bookkeeping is not exported.
    expect(Object.keys(JSON.parse(text).mcpServers)).toEqual([SERVER]);
    expect(JSON.parse(text).mcpServers[SERVER]).not.toHaveProperty('builtin');
  });

  it('a manual selection is rewritten to clean text/plain only', async () => {
    const pre = await openStanza();
    // What a browser / rich-text path can hand back: NBSP-family indentation
    // and a zero-width space between tokens.
    const dirty = JSON.stringify(STANZA, null, 2)
      .replace(/^( +)/gm, (m) => '\u00a0'.repeat(m.length - 1) + ' ')
      .replace('"args"', '\u200b"args"')
      .replace('"env"', '\u202f"env"');
    expect(() => JSON.parse(dirty)).toThrow();
    const spy = jest.spyOn(window, 'getSelection').mockReturnValue({ toString: () => dirty } as any);
    const setData = jest.fn();
    fireEvent.copy(pre, { clipboardData: { setData } });
    spy.mockRestore();

    expect(setData).toHaveBeenCalledTimes(1);
    const [mime, text] = setData.mock.calls[0];
    expect(mime).toBe('text/plain');
    expect(asciiOutsideStrings(text)).toBe(true);
    expect(JSON.parse(text)).toEqual(STANZA);
  });
});

describe('cleanCopiedText', () => {
  it('maps NBSP-family spaces to U+0020 and drops zero-width characters', () => {
    expect(cleanCopiedText('\u00a0a\u2007b\u202fc\u200bd\ufeffe')).toBe(' a b cde');
  });
  it('leaves other non-ASCII text alone', () => {
    expect(cleanCopiedText('caf\u00e9 \u2014')).toBe('caf\u00e9 \u2014');
  });
});

describe('portableServerStanza', () => {
  it('wraps the entry under mcpServers by name and drops builtin', () => {
    expect(portableServerStanza('a', { command: 'x', builtin: true, enabled: false }))
      .toEqual({ mcpServers: { a: { command: 'x', enabled: false } } });
  });
  it('does not mutate the served config', () => {
    const cfg = { url: 'u', builtin: false };
    portableServerStanza('a', cfg);
    expect(cfg).toEqual({ url: 'u', builtin: false });
  });
});
