/**
 * @jest-environment jsdom
 *
 * TerminalPane rendering, and the seam that matters: MarkdownRenderer's
 * ToolBlock must actually route shell and shadow results to it.  The model
 * being right proves nothing if the block never mounts the pane.
 */
jest.mock('marked', () => {
    const marked = (s: string) => s;
    Object.assign(marked, { parse: (s: string) => s, setOptions: () => {}, use: () => {}, walkTokens: () => {}, parseInline: (s: string) => s });
    return { marked, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import React from 'react';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { TerminalPane, COLLAPSED_ROWS } from '../TerminalPane';
import { ToolBlock } from '../MarkdownRenderer';
import { buildTerminalPaneModel } from '../../utils/terminalPane';
import { publishAttachedShadow, resetShadowPresence } from '../../utils/shadowPresence';

afterEach(() => resetShadowPresence());

const SENT = 'cd ~/workplace/x && git status -sb';
const SEND = JSON.stringify({
    ok: true, session_id: '628111', sent: SENT, cmd_seq: 304, idle: true, timed_out: false, next_seq: 306,
    transcript: `[303] → done (exit code unknown)\n[304] $ ${SENT}\n[305] ● control_send {"lease_ref": "x"}`,
});
const WAIT = JSON.stringify({
    ok: true, session_id: '628111', reason: 'exit', still_running: false, from_seq: 306, next_seq: 308, elapsed_ms: 9,
    exit_code: null, exit_seq: 307,
    tail: `[306] ${SENT}\n     ## main\n     (26-10-09 20:42:48) <0> [~/workplace/x]\n[307] → done (exit code unknown)`,
});

describe('TerminalPane', () => {
    it('renders a REMOTE pane with session, cwd, seq gutter, chips and folded events', () => {
        const model = buildTerminalPaneModel('shadow_wait', WAIT)!;
        render(<TerminalPane model={model} isDarkMode={true} verified />);
        const pane = screen.getByTestId('terminal-pane');
        expect(pane.getAttribute('data-kind')).toBe('remote');
        expect(screen.getByTestId('tp-badge').textContent).toBe('REMOTE');
        expect(pane.textContent).toContain('shadow 628111');
        expect(screen.getByTestId('tp-cwd').textContent).toBe('~/workplace/x');
        expect(screen.getByTestId('tp-command').textContent).toContain('wait → exit');
        expect(screen.getAllByTestId('tp-gutter').map(g => g.textContent)).toEqual(['306', '', '307']);
        expect(screen.getAllByTestId('tp-chip').map(c => c.textContent)).toEqual(['✓ exit 0', '+2 lines · 9 ms']);
        expect(screen.getByTestId('tp-seq').textContent).toBe('seq 306 → 308');
        expect(screen.getByTestId('tp-toggle').textContent).toContain('🔐');
        // The prompt line is folded, not in the body, and is one click away.
        expect(pane.textContent).not.toContain('(26-10-09');
        fireEvent.click(screen.getByTestId('tp-hidden-toggle'));
        expect(within(screen.getByTestId('tp-hidden')).getByText(/prompt: \(26-10-09/)).toBeTruthy();
    });

    it('a LOCAL pane carries no tag unless asked: with no shadow in play there is nothing to tell apart', () => {
        const model = buildTerminalPaneModel('mcp_run_shell_command', '$ ls\na\nb')!;
        render(<TerminalPane model={model} isDarkMode={false} />);
        expect(screen.getByTestId('terminal-pane').getAttribute('data-kind')).toBe('local');
        expect(screen.queryByTestId('tp-badge')).toBeNull();
        // The stripe still identifies it.
        expect(screen.getByTestId('terminal-pane').style.borderLeft).toMatch(/4px solid/);
    });

    it('renders a LOCAL pane without a gutter and with the teal badge when a shadow is in play', () => {
        const model = buildTerminalPaneModel('mcp_run_shell_command', '$ ls\na\nb')!;
        render(<TerminalPane model={model} isDarkMode={false} showLocalBadge />);
        expect(screen.getByTestId('terminal-pane').getAttribute('data-kind')).toBe('local');
        expect(screen.getByTestId('tp-badge').textContent).toBe('LOCAL');
        expect(screen.queryAllByTestId('tp-gutter')).toHaveLength(0);
        expect(screen.getAllByTestId('tp-chip').map(c => c.textContent)).toEqual(['✓ exit 0', '2 lines']);
    });

    it('the remote indicator is not the warning amber or the error red', () => {
        const model = buildTerminalPaneModel('shadow_send', SEND)!;
        render(<TerminalPane model={model} isDarkMode={true} />);
        const stripe = screen.getByTestId('terminal-pane').style.borderLeft;
        expect(stripe).toMatch(/4px solid/);
        expect(stripe).not.toMatch(/#f59e0b|#fcd34d|#ef4444|#fca5a5|orange|#faad14/i);
        // Badge text and stripe share the colour, so the two read as one indicator.
        // jsdom serialises `color` as rgb() but leaves the border shorthand as
        // authored hex, so compare both through the same normalisation.
        const toRgb = (c: string) => {
            const m = /^#([0-9a-f]{6})$/i.exec(c.trim());
            if (!m) return c.trim();
            const n = parseInt(m[1], 16);
            return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
        };
        expect(toRgb(screen.getByTestId('tp-badge').style.color)).toBe(toRgb(stripe.replace(/^4px solid /, '')));
    });

    it('clips a long transcript to a few rows and expands on request', () => {
        const out = Array.from({ length: COLLAPSED_ROWS + 5 }, (_, i) => `line ${i}`).join('\n');
        const model = buildTerminalPaneModel('run_shell_command', `$ seq\n${out}`)!;
        render(<TerminalPane model={model} isDarkMode={false} />);
        expect(screen.getByTestId('terminal-pane').textContent).not.toContain(`line ${COLLAPSED_ROWS}`);
        const btn = screen.getByTestId('tp-expand');
        expect(btn.textContent).toBe('▸ 5 more lines');
        fireEvent.click(btn);
        expect(screen.getByTestId('terminal-pane').textContent).toContain(`line ${COLLAPSED_ROWS + 4}`);
        expect(screen.getByTestId('tp-expand').textContent).toBe('▴ collapse');
    });
});

describe('ToolBlock LOCAL tag follows shadow presence', () => {
    const LOCAL = <ToolBlock toolName="mcp_run_shell_command|Shell: $ ls|sh" content={'$ ls\na'} isDarkMode={false} />;
    const badgeOf = (kind: string) => {
        const pane = screen.getAllByTestId('terminal-pane').find(p => p.getAttribute('data-kind') === kind)!;
        return within(pane).queryByTestId('tp-badge');
    };

    it('no shadow used or attached: the local pane has no tag', () => {
        render(LOCAL);
        expect(screen.getByTestId('terminal-pane')).toBeTruthy();
        expect(badgeOf('local')).toBeNull();
    });

    it('a shadow result anywhere in the conversation tags local panes, including ones above it', () => {
        render(<>{LOCAL}<ToolBlock toolName="shadow_send|shadow send" content={SEND} isDarkMode={false} /></>);
        expect(badgeOf('local')!.textContent).toBe('LOCAL');
        expect(badgeOf('remote')!.textContent).toBe('REMOTE');
    });

    it('a shadow session attached to the conversation tags local panes', () => {
        render(LOCAL);
        expect(badgeOf('local')).toBeNull();
        act(() => publishAttachedShadow('', true));
        expect(badgeOf('local')!.textContent).toBe('LOCAL');
        act(() => publishAttachedShadow('', false));
        expect(badgeOf('local')).toBeNull();
    });
});

describe('ToolBlock seam', () => {
    it('routes a shell transcript to the pane', () => {
        render(<ToolBlock toolName="mcp_run_shell_command|Shell: $ ls|sh" content={'$ ls\na'} isDarkMode={false} />);
        expect(screen.getByTestId('terminal-pane').getAttribute('data-kind')).toBe('local');
    });

    it('routes shadow_send and shadow_wait results to the pane', () => {
        const { unmount } = render(<ToolBlock toolName="shadow_send|shadow send" content={SEND} isDarkMode={true} />);
        expect(screen.getByTestId('terminal-pane').getAttribute('data-tool')).toBe('shadow_send');
        unmount();
        render(<ToolBlock toolName="mcp_shadow_wait|shadow wait" content={WAIT} isDarkMode={true} />);
        expect(screen.getByTestId('terminal-pane').getAttribute('data-tool')).toBe('shadow_wait');
    });

    it('keeps the generic block for a blocked command and for a failed shadow result', () => {
        render(<ToolBlock toolName="mcp_run_shell_command|Shell: $ rm -rf /|sh"
                          content={'🚫 SECURITY BLOCK: Command not allowed: rm'} isDarkMode={false} />);
        expect(screen.queryByTestId('terminal-pane')).toBeNull();
        expect(screen.getByText(/Command Blocked/)).toBeTruthy();
        render(<ToolBlock toolName="shadow_send" content={JSON.stringify({ ok: false, error: 'no_lease', message: 'no lease' })} isDarkMode={false} />);
        expect(screen.queryByTestId('terminal-pane')).toBeNull();
    });

    it('a result that merely QUOTES the block sentinel is a normal result, not a Command Blocked alert', () => {
        // Observed: a file_write whose echoed content contained the fixture
        // string below rendered the whole result as "🚫 Command Blocked".
        const echo = JSON.stringify({ success: true, path: 'frontend/src/x.test.tsx', bytes_written: 42,
            message: 'Wrote frontend/src/x.test.tsx',
            content: "content={'🚫 SECURITY BLOCK: Command not allowed: rm'} isDarkMode={false} />" });
        render(<ToolBlock toolName="mcp_file_write|file write: frontend/src/x.test.tsx" content={echo} isDarkMode={false} />);
        expect(screen.queryByText(/Command Blocked/)).toBeNull();
        // And a shell transcript that greps for the sentinel goes to the pane.
        render(<ToolBlock toolName="mcp_run_shell_command|Shell: $ grep SECURITY app/|sh"
                          content={"$ grep SECURITY app/\napp/s.py:9: '🚫 SECURITY BLOCK: Command not allowed'"} isDarkMode={false} />);
        expect(screen.getByTestId('terminal-pane')).toBeTruthy();
    });
});
