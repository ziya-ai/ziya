/**
 * @jest-environment jsdom
 *
 * Terminal pane model (utils/terminalPane.ts): a local shell transcript and
 * the shadow_send / shadow_wait / shadow_read results become one shape.
 * The remote fixtures are the real results from the conversation that
 * motivated this (session 628111, seq 304–308), including the wrapper's own
 * `● control_send {…}` overlay echoed through the journal and the user's
 * `(date) <exit> [path]` prompt.
 */
import {
    abbreviateHome, buildTerminalPaneModel, formatElapsed, isTerminalTool,
    looksLikeBlockedCommand, parsePromptLine, parseTranscript, splitCdPrefix, toolHeaderFromParts,
} from '../terminalPane';
import { setKnownShellCwd } from '../mcpFormatter';

const SENT = 'cd ~/workplace/switch-mgr/src/KuiperEAR-switch-mgr && git status -sb && git stash list && git log --oneline -1 && grep versionSet ../../packageInfo';

const SEND_RESULT = {
    ok: true, session_id: '628111', sent: SENT, cmd_seq: 304, idle: true, timed_out: false, next_seq: 306,
    transcript:
        '[303] → done (exit code unknown)\n' +
        `[304] $ ${SENT}\n` +
        '[305] ● control_send {"lease_ref": "199a577b7a", "conversation_id": "2ae03e67", "text": "cd ~/x", "restriction": "gated", "policy": "builtin"}',
};

const WAIT_RESULT = {
    ok: true, session_id: '628111', reason: 'exit', still_running: false, new_records: 2, output_lines: 7,
    from_seq: 306, next_seq: 308, elapsed_ms: 9, tail_truncated: false, exit_code: null, exit_seq: 307,
    tail:
        `[306] ${SENT}\n` +
        '     ## dcohn/ggma-bfd-fd-gate...origin/dcohn/ggma-bfd-fd-gate [ahead 1, behind 2]\n' +
        '     stash@{0}: WIP on dcohn/ggma-bfd-fd-gate: be21ab21 [GGMA] BFD: gate peer BFD\n' +
        '     231fae54 (HEAD -> dcohn/ggma-bfd-fd-gate) fix(SWM): GGMA - gate peer BFD\n' +
        '     >  versionSet = "KuiperAnchorGGMA/payload-8.5.x";\n' +
        '     \n' +
        '     (26-10-09 20:42:48) <0> [~/workplace/switch-mgr/src/KuiperEAR-switch-mgr]\n' +
        '[307] → done (exit code unknown)',
};

afterEach(() => {
    setKnownShellCwd(null);
    delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
});

describe('helpers', () => {
    it('recognises the terminal tools with and without the mcp_ prefix', () => {
        for (const n of ['run_shell_command', 'mcp_run_shell_command', 'shadow_send', 'mcp_shadow_wait', 'shadow_read']) {
            expect(isTerminalTool(n)).toBe(true);
        }
        expect(isTerminalTool('shadow_list')).toBe(false);
        expect(isTerminalTool('file_read')).toBe(false);
    });

    it('recognises a real policy block only at the start of the message', () => {
        expect(looksLikeBlockedCommand('🚫 SECURITY BLOCK: Command not allowed: rm')).toBe(true);
        expect(looksLikeBlockedCommand('  🚫 WRITE BLOCKED: path outside policy')).toBe(true);
        expect(looksLikeBlockedCommand("{'error': True, 'message': \"🚫 SECURITY BLOCK: Command not allowed: rm\"}")).toBe(true);
        expect(looksLikeBlockedCommand('{"error": true, "message": "🚫 WRITE BLOCKED: x"}')).toBe(true);
        expect(looksLikeBlockedCommand('$ ls\nfoo')).toBe(false);
    });

    it('does not mistake output that merely quotes the sentinel for a block', () => {
        // The failure that motivated this: a file_write echoing a test fixture,
        // and a grep over the renderer's own source, both rendered as
        // "Command Blocked" because the check was a substring match.
        const fixture = "content={'🚫 SECURITY BLOCK: Command not allowed: rm'} isDarkMode={false} />";
        expect(looksLikeBlockedCommand(fixture)).toBe(false);
        expect(looksLikeBlockedCommand("$ grep -rn 'Command not allowed' app/\napp/x.py:12: '🚫 SECURITY BLOCK: '")).toBe(false);
        expect(looksLikeBlockedCommand('{"ok": true, "transcript": "🚫 SECURITY BLOCK in remote output"}')).toBe(false);
        expect(looksLikeBlockedCommand("{'error': True, 'message': 'disk full; see 🚫 SECURITY BLOCK docs'}")).toBe(false);
    });

    it('reads the header from fence parts, keeping pipes inside a shell command', () => {
        expect(toolHeaderFromParts(['Shell: $ cat f | head -5', 'sh'])).toBe('Shell: $ cat f | head -5');
        expect(toolHeaderFromParts(['Shell: $ cat f | head -5'])).toBe('Shell: $ cat f | head -5');
        expect(toolHeaderFromParts([])).toBe('');
    });

    it('splits a leading cd && and abbreviates home', () => {
        expect(splitCdPrefix('cd ~/a/b && git status -sb')).toEqual({ cwd: '~/a/b', command: 'git status -sb' });
        expect(splitCdPrefix('cd "/tmp/x y/" && ls')).toEqual({ cwd: '/tmp/x y', command: 'ls' });
        expect(splitCdPrefix('cd ~/a')).toEqual({ cwd: null, command: 'cd ~/a' });
        expect(splitCdPrefix('ls; cd x')).toEqual({ cwd: null, command: 'ls; cd x' });
        expect(abbreviateHome('/Users/dcohn/workspace/z')).toBe('~/workspace/z');
        expect(abbreviateHome('/home/dcohn')).toBe('~');
        expect(abbreviateHome('/opt/app')).toBe('/opt/app');
    });

    it('formats elapsed time', () => {
        expect(formatElapsed(9)).toBe('9 ms');
        expect(formatElapsed(2500)).toBe('2.5 s');
        expect(formatElapsed(45_000)).toBe('45 s');
        expect(formatElapsed(4 * 60_000 + 3000)).toBe('4 min 3 s');
    });
});

describe('parsePromptLine', () => {
    it.each([
        ['(26-10-09 20:42:48) <0> [~/workplace/switch-mgr]', { cwd: '~/workplace/switch-mgr', exitCode: 0 }],
        ['(26-10-09 20:42:48) <130> [/srv/app]', { cwd: '/srv/app', exitCode: 130 }],
        ['[~/x]$ ', { cwd: '~/x' }],
        ['dcohn@host:~/workplace/x$ ', { cwd: '~/workplace/x' }],
        ['root@box:/etc# ', { cwd: '/etc' }],
        ['[dcohn@host ~/work]$ ', { cwd: '~/work' }],
        ['~/work ❯ ', { cwd: '~/work' }],
        ['/opt/app> ', { cwd: '/opt/app' }],
        ['\x1b[32m~/work\x1b[0m $ ', { cwd: '~/work' }],
    ])('reads %s', (line, want) => {
        expect(parsePromptLine(line)).toEqual(want);
    });

    it.each([
        'total 12',
        'see config [/etc/hosts] for details',           // output that merely ends in a bracketed path
        '[dcohn@host build]$ ',                          // bare dir name: not confidently a path
        'done.',
        '',
    ])('leaves %s alone', (line) => {
        expect(parsePromptLine(line)).toBeNull();
    });
});

describe('parseTranscript', () => {
    it('groups records with their continuation lines and classifies them', () => {
        const recs = parseTranscript(SEND_RESULT.transcript);
        expect(recs.map(r => [r.seq, r.kind])).toEqual([[303, 'exit'], [304, 'cmd'], [305, 'meta']]);
        expect(recs[0].exitCode).toBeNull();
        expect(recs[1].text).toBe(SENT);
        const tail = parseTranscript(WAIT_RESULT.tail);
        expect(tail.map(r => [r.seq, r.kind])).toEqual([[306, 'output'], [307, 'exit']]);
        expect(tail[0].text.split('\n')).toHaveLength(7);
        expect(parseTranscript('[9] → exit 3')[0].exitCode).toBe(3);
        expect(parseTranscript('[1] a\n… [transcript truncated] …\n[2] b').map(r => r.kind)).toEqual(['output', 'meta', 'output']);
    });
});

describe('local shell', () => {
    it('builds a LOCAL pane from the transcript with the project cwd and exit 0', () => {
        (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/Users/dcohn/workspace/ziya';
        const m = buildTerminalPaneModel('mcp_run_shell_command', '$ git status -sb\n## main\n M a.ts\n', ['Shell: $ git status -sb', 'sh'])!;
        expect(m.kind).toBe('local');
        expect(m.command).toBe('git status -sb');
        expect(m.cwd).toBe('~/workspace/ziya');
        expect(m.lines).toEqual([{ kind: 'output', text: '## main\n M a.ts' }]);
        expect(m.chips.map(c => c.label)).toEqual(['✓ exit 0', '2 lines']);
        expect(m.outputLineCount).toBe(2);
    });

    it('reads a non-zero exit marker and hides it from the body', () => {
        const m = buildTerminalPaneModel('run_shell_command', '$ false\n\n[Exit code: 1]')!;
        expect(m.chips[0]).toEqual({ label: '✗ exit 1', tone: 'err', title: undefined });
        expect(m.lines).toEqual([]);
    });

    it('a cd to another directory moves the cwd; a cd to the project root is dropped', () => {
        (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/Users/dcohn/workspace/ziya';
        setKnownShellCwd('/Users/dcohn/workspace/ziya');
        const a = buildTerminalPaneModel('run_shell_command', '$ cd frontend && npx jest x\nok')!;
        expect(a.cwd).toBe('~/workspace/ziya/frontend');
        expect(a.command).toBe('npx jest x');
        const b = buildTerminalPaneModel('run_shell_command', '$ cd /Users/dcohn/workspace/ziya && ls\nok')!;
        expect(b.cwd).toBe('~/workspace/ziya');
        expect(b.command).toBe('ls');
    });

    it('falls back to the header for the command and strips persisted fade markup', () => {
        const m = buildTerminalPaneModel('mcp_run_shell_command',
            'line1\n<span style="opacity: 0.6; font-style: italic;">line2</span>', ['Shell: $ ls -la', 'sh'])!;
        expect(m.command).toBe('ls -la');
        expect(m.lines[0].text).toBe('line1\nline2');
    });
});

describe('shadow_send', () => {
    it('reads the session, the cd target as cwd, folds the lease overlay and the earlier exit', () => {
        const m = buildTerminalPaneModel('shadow_send', JSON.stringify(SEND_RESULT))!;
        expect(m.kind).toBe('remote');
        expect(m.session).toBe('628111');
        expect(m.cwd).toBe('~/workplace/switch-mgr/src/KuiperEAR-switch-mgr');
        expect(m.command).toBe('git status -sb && git stash list && git log --oneline -1 && grep versionSet ../../packageInfo');
        // Only the command record is shown; the control_send overlay and the
        // exit of the PREVIOUS command (seq 303 < cmd_seq 304) are folded.
        expect(m.lines).toEqual([{ kind: 'cmd', text: SENT, seq: 304 }]);
        expect(m.hidden).toHaveLength(2);
        expect(m.hidden[0]).toMatch(/^earlier: → done/);
        expect(m.hidden[1]).toMatch(/^● control_send/);
        // No exit for THIS command yet, so no exit chip is invented.
        expect(m.chips.map(c => c.label)).toEqual(['0 lines']);
        expect(m.status).toBe('idle');
        expect(m.seqRange).toEqual({ from: 304, to: 306 });
    });

    it('accepts a Python-repr result and reports still running with the next hint', () => {
        const py = "{'ok': True, 'session_id': '1', 'sent': 'make', 'cmd_seq': 10, 'idle': False, 'timed_out': True, " +
            "'next_seq': 12, 'transcript': '[10] $ make\\n[11] compiling…', 'still_running': True, " +
            "'next': \"shadow_wait(session='1', cmd_seq=10, from_seq=12, until=['exit'])\"}";
        const m = buildTerminalPaneModel('mcp_shadow_send', py)!;
        expect(m.chips.map(c => c.label)).toEqual(['⏱ still running', '1 lines']);
        expect(m.status).toBeUndefined();
        expect(m.next).toContain('shadow_wait(');
        expect(m.cwd).toBeNull();
    });

    it('returns null for a failed result so the generic error rendering is used', () => {
        expect(buildTerminalPaneModel('shadow_send', JSON.stringify({ ok: false, error: 'no_lease', message: 'x' }))).toBeNull();
        expect(buildTerminalPaneModel('shadow_send', 'not json at all')).toBeNull();
    });
});

describe('shadow_wait', () => {
    it('hides the prompt line, reads the cwd and exit status from it, and shows the tail with seqs', () => {
        const m = buildTerminalPaneModel('shadow_wait', JSON.stringify(WAIT_RESULT))!;
        expect(m.command).toBe('wait → exit');
        expect(m.cwd).toBe('~/workplace/switch-mgr/src/KuiperEAR-switch-mgr');
        expect(m.lines.map(l => [l.seq, l.kind])).toEqual([[306, 'output'], [307, 'exit']]);
        // The prompt and the blank line before it are gone; the output is intact.
        const body = m.lines[0].text.split('\n');
        expect(body).toHaveLength(5);
        expect(body[0]).toBe(SENT);
        expect(body[4]).toBe('>  versionSet = "KuiperAnchorGGMA/payload-8.5.x";');
        expect(m.hidden).toEqual([expect.stringMatching(/^prompt: \(26-10-09/)]);
        // exit_code was null, but the prompt reported <0>: shown, and labelled as such.
        expect(m.chips[0]).toEqual({ label: '✓ exit 0', tone: 'ok', title: 'exit status read from the prompt line' });
        expect(m.chips.map(c => c.label)).toEqual(['✓ exit 0', '+5 lines · 9 ms']);
        expect(m.seqRange).toEqual({ from: 306, to: 308 });
    });

    it('a timeout with the command still running is a warning, not an exit', () => {
        const m = buildTerminalPaneModel('shadow_wait', JSON.stringify({
            ok: true, session_id: '1', reason: 'timeout', still_running: true, from_seq: 308, next_seq: 346,
            elapsed_ms: 240_000, tail_truncated: true, tail: '[340] [INFO] Linking', next: 'shadow_wait(cmd_seq=310)',
        }))!;
        expect(m.command).toBe('wait → timeout');
        expect(m.chips.map(c => c.label)).toEqual(['⏱ timeout · still running', 'tail truncated', '+1 lines · 4 min']);
        expect(m.next).toBe('shadow_wait(cmd_seq=310)');
    });

    it('reports a real exit code over the prompt', () => {
        const m = buildTerminalPaneModel('shadow_wait', JSON.stringify({
            ok: true, session_id: '1', reason: 'exit', exit_code: 2, exit_seq: 9, tail: '[8] err\n     [~/x]$\n[9] → exit 2',
        }))!;
        expect(m.chips[0]).toEqual({ label: '✗ exit 2', tone: 'err', title: undefined });
        expect(m.cwd).toBe('~/x');
    });
});

describe('shadow_read', () => {
    it('shows the mode, label and record count', () => {
        const m = buildTerminalPaneModel('shadow_read', JSON.stringify({
            ok: true, session_id: '628111', label: 'prod-42', mode: 'tail', record_count: 3, last_seq: 12, pending_ask: true,
            transcript: '[10] $ uptime\n[11]  up 3 days\n[12] → exit 0',
        }))!;
        expect(m.command).toBe('read · tail');
        expect(m.sessionLabel).toBe('prod-42');
        expect(m.chips.map(c => c.label)).toEqual(['✓ exit 0', 'ask pending', '3 records', '1 lines']);
        expect(m.seqRange).toEqual({ from: 10, to: 12 });
    });
});
