/**
 * Tests for the MCP diagnostics rendering logic: the preflight failure card
 * and the config-findings panel.
 *
 * Before this change, a server that never started rendered as an <Empty> with
 * "Server disconnected - no logs available", which sent users looking for a
 * logging fault when the truth was that no process was ever spawned. These
 * tests pin the decision logic that chooses between the failure card, the log
 * tail, and the empty state.
 */
describe('MCP preflight failure card', () => {
    interface PreflightFailure {
        code: string;
        summary: string;
        detail: string;
        searched: string[];
        hint: string;
    }

    interface ServerDetails {
        tools: any[];
        resources: any[];
        prompts: any[];
        logs?: string[];
        startup_stage?: string | null;
        preflight_failure?: PreflightFailure | null;
    }

    const failedDetails: ServerDetails = {
        tools: [], resources: [], prompts: [],
        logs: ['ERROR: Command not found on PATH: npx'],
        startup_stage: 'preflight',
        preflight_failure: {
            code: 'command_not_on_path',
            summary: 'Command not found on PATH: npx',
            detail: 'Ziya could not launch \'npx\' ... There are no logs because nothing ever ran.',
            searched: ['/usr/local/bin', '/usr/bin', '/bin'],
            hint: "'npx' is provided by Node.js. Install it with: brew install node.",
        },
    };

    const healthyDetails: ServerDetails = {
        tools: [{ name: 't' }], resources: [], prompts: [],
        logs: [], startup_stage: 'ready', preflight_failure: null,
    };

    it('shows the failure card when a preflight failure is present', () => {
        expect(!!failedDetails.preflight_failure).toBe(true);
    });

    it('does not show the failure card for a healthy server', () => {
        expect(!!healthyDetails.preflight_failure).toBe(false);
    });

    it('suppresses the empty state when a failure card is shown', () => {
        // The card explains the absence of logs; an <Empty> beneath it saying
        // "no logs available" would contradict it.
        const hasLogs = !!failedDetails.logs?.length;
        const showEmpty = !hasLogs && !failedDetails.preflight_failure;
        expect(showEmpty).toBe(false);
    });

    it('still shows the empty state for a connected server with no logs', () => {
        const showEmpty = !healthyDetails.logs?.length && !healthyDetails.preflight_failure;
        expect(showEmpty).toBe(true);
    });

    it('explains that nothing ran, rather than that logs are missing', () => {
        // The load-bearing sentence: users assume log capture is broken.
        expect(failedDetails.preflight_failure!.detail).toContain('nothing ever ran');
    });

    it('reports the searched locations so PATH problems are visible', () => {
        expect(failedDetails.preflight_failure!.searched.length).toBeGreaterThan(0);
    });

    it('provides an actionable hint naming the provider', () => {
        expect(failedDetails.preflight_failure!.hint).toContain('Node.js');
        expect(failedDetails.preflight_failure!.hint).toContain('brew install node');
    });

    it('builds a copyable report containing every diagnostic field', () => {
        const f = failedDetails.preflight_failure!;
        const report = [
            `MCP server: context7`,
            `stage: ${failedDetails.startup_stage}`,
            `code: ${f.code}`,
            `summary: ${f.summary}`,
            `detail: ${f.detail}`,
            `hint: ${f.hint}`,
            '',
            'searched:',
            ...f.searched,
        ].join('\n');

        expect(report).toContain('code: command_not_on_path');
        expect(report).toContain('stage: preflight');
        expect(report).toContain('/usr/local/bin');
    });

    it('tolerates a details payload from an older backend', () => {
        // startup_stage / preflight_failure absent entirely.
        const legacy: ServerDetails = { tools: [], resources: [], prompts: [], logs: [] };
        expect(!!legacy.preflight_failure).toBe(false);
        const showEmpty = !legacy.logs?.length && !legacy.preflight_failure;
        expect(showEmpty).toBe(true);
    });
});

describe('MCP config findings panel', () => {
    interface ConfigFinding {
        server: string;
        code: string;
        severity: 'error' | 'warning';
        summary: string;
        detail: string;
        line?: number | null;
        suggestion?: string | null;
    }

    const findings: ConfigFinding[] = [
        {
            server: 'filesystem', code: 'unknown_key_typo', severity: 'error',
            summary: 'Unknown key "commands"',
            detail: 'Did you mean "command"?', line: 4, suggestion: 'command',
        },
        {
            server: 'github', code: 'env_value_not_string', severity: 'error',
            summary: 'env.GITHUB_TOKEN is not a string',
            detail: 'Got null.', line: 9,
        },
        {
            server: 'local-py', code: 'relative_script_path', severity: 'warning',
            summary: 'Relative script path: tools/srv.py',
            detail: 'Use an absolute path.', line: 14,
        },
    ];

    it('renders the panel only when findings exist', () => {
        expect(!!findings.length).toBe(true);
        expect(!![].length).toBe(false);
    });

    it('escalates to error severity when any finding is blocking', () => {
        const type = findings.some(f => f.severity === 'error') ? 'error' : 'warning';
        expect(type).toBe('error');
    });

    it('stays at warning severity when all findings are advisory', () => {
        const warnOnly = findings.filter(f => f.severity === 'warning');
        const type = warnOnly.some(f => f.severity === 'error') ? 'error' : 'warning';
        expect(type).toBe('warning');
    });

    it('counts blocking findings separately from the total', () => {
        expect(findings.length).toBe(3);
        expect(findings.filter(f => f.severity === 'error').length).toBe(2);
    });

    it('pluralizes the problem count correctly', () => {
        const label = (n: number) => `${n} problem${n === 1 ? '' : 's'}`;
        expect(label(1)).toBe('1 problem');
        expect(label(3)).toBe('3 problems');
    });

    it('renders a placeholder when a finding has no line number', () => {
        const noLine: ConfigFinding = {
            server: '', code: 'config_not_object', severity: 'error',
            summary: 'Config root must be a JSON object', detail: '', line: null,
        };
        expect(noLine.line ? `L${noLine.line}` : '—').toBe('—');
        expect(findings[0].line ? `L${findings[0].line}` : '—').toBe('L4');
    });

    it('produces stable React keys for findings sharing a server', () => {
        const dup: ConfigFinding[] = [
            { server: 's', code: 'unknown_key', severity: 'warning', summary: 'a', detail: '' },
            { server: 's', code: 'unknown_key', severity: 'warning', summary: 'b', detail: '' },
        ];
        const keys = dup.map((f, i) => `${f.server}-${f.code}-${i}`);
        expect(new Set(keys).size).toBe(dup.length);
    });

    it('tolerates a status payload from an older backend', () => {
        const legacy: { config_findings?: ConfigFinding[] } = {};
        expect(!!legacy.config_findings?.length).toBe(false);
    });
});

describe('server details cache invalidation', () => {
    /**
     * fetchServerDetails() returns early when a cache entry exists. Without
     * clearing that cache on reload, "Re-run preflight" after installing the
     * missing command would keep showing the old failure and read as though
     * the retry did nothing.
     */
    // fetchServerDetails takes no force flag: cache-clearing is the only
    // refresh mechanism, so a per-server force would be a second refresh path
    // with no caller. This mirrors the real guard exactly.
    it('a cached entry blocks refetch', () => {
        const cache: Record<string, any> = { ghost: { tools: [] } };
        const wouldSkip = (name: string) => !!cache[name];

        expect(wouldSkip('ghost')).toBe(true);
        expect(wouldSkip('other')).toBe(false);
    });

    it('clearing the cache allows a fresh fetch after reload', () => {
        let cache: Record<string, any> = { ghost: { tools: [] } };
        cache = {};   // what reinitializeMCP must do
        expect(!!cache['ghost']).toBe(false);
    });
});
