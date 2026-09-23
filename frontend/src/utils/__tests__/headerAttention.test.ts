import * as fs from 'fs';
import * as path from 'path';
import {
    deriveMcpAttention, deriveShellAttention, deriveTaskCardAttention,
    attentionButtonStyle, attentionTooltip, ATTENTION_COLORS, NO_ATTENTION,
} from '../headerAttention';

const mcpBase = { initialized: true, disabled: false, servers: {} };

describe('deriveMcpAttention', () => {
    it('is quiet when MCP is disabled, not yet initialized, or clean', () => {
        expect(deriveMcpAttention(null)).toBe(NO_ATTENTION);
        expect(deriveMcpAttention({ disabled: true })).toBe(NO_ATTENTION);
        expect(deriveMcpAttention({ initialized: false })).toBe(NO_ATTENTION);
        expect(deriveMcpAttention({ ...mcpBase, servers: { shell: { connected: true } } }))
            .toBe(NO_ATTENTION);
    });

    it('a quarantined server is a WARNING that names the server and the UI action', () => {
        const a = deriveMcpAttention({
            ...mcpBase,
            // Both connected: a server with no live connection is reported
            // separately (info), and that must not leak into this assertion.
            servers: { fetch: { connected: true, quarantined: true },
                       shell: { connected: true, quarantined: false } },
        });
        expect(a.level).toBe('warning');
        expect(a.reasons.join(' ')).toMatch(/fetch/);
        expect(a.reasons.join(' ')).toMatch(/Re-authorize/);
        expect(a.reasons.join(' ')).not.toMatch(/shell/);
    });

    it('a config error is an ERROR, and outranks a quarantine in the same payload', () => {
        const a = deriveMcpAttention({
            ...mcpBase,
            config_error: 'Invalid JSON at line 4',
            servers: { fetch: { quarantined: true } },
        });
        expect(a.level).toBe('error');
        expect(a.reasons).toHaveLength(2);
    });

    it('config findings: blocking -> error, advisory-only -> warning', () => {
        expect(deriveMcpAttention({
            ...mcpBase, config_findings: [{ severity: 'error' }, { severity: 'warning' }],
        }).level).toBe('error');
        expect(deriveMcpAttention({
            ...mcpBase, config_findings: [{ severity: 'warning' }],
        }).level).toBe('warning');
    });

    it('an ENABLED server that failed to connect is INFO (blue), naming the server', () => {
        const a = deriveMcpAttention({
            ...mcpBase,
            servers: { fetch: { connected: false }, shell: { connected: true } },
            server_configs: { fetch: { enabled: true }, shell: { enabled: true } },
        });
        expect(a.level).toBe('info');
        expect(a.reasons.join(' ')).toMatch(/fetch/);
        expect(a.reasons.join(' ')).not.toMatch(/shell/);
    });

    it('a DISABLED server is not a failure (status reports it connected=false too)', () => {
        expect(deriveMcpAttention({
            ...mcpBase,
            servers: { fetch: { connected: false } },
            server_configs: { fetch: { enabled: false } },
        })).toBe(NO_ATTENTION);
    });

    it('info ranks below warning and error, and a quarantine is not double-counted as a failure', () => {
        const a = deriveMcpAttention({
            ...mcpBase,
            servers: { fetch: { connected: false }, git: { connected: true, quarantined: true } },
            server_configs: { fetch: { enabled: true }, git: { enabled: true } },
        });
        expect(a.level).toBe('warning');
        expect(a.reasons.filter(r => /failed to connect/.test(r))).toHaveLength(1);
        expect(a.reasons.find(r => /failed to connect/.test(r))).not.toMatch(/git/);
        expect(deriveMcpAttention({
            ...mcpBase, config_error: 'bad', servers: { fetch: { connected: false } },
            server_configs: { fetch: { enabled: true } },
        }).level).toBe('error');
    });
});

describe('deriveShellAttention', () => {
    it('unsigned escalation and staged session request each warn; signed is quiet', () => {
        expect(deriveShellAttention({ signatureStatus: { hasEscalation: true, authorized: false } }).level)
            .toBe('warning');
        expect(deriveShellAttention({ sessionPending: true }).level).toBe('warning');
        expect(deriveShellAttention({ signatureStatus: { hasEscalation: true, authorized: true } }))
            .toBe(NO_ATTENTION);
        expect(deriveShellAttention({ signatureStatus: { hasEscalation: false, authorized: true } }))
            .toBe(NO_ATTENTION);
        expect(deriveShellAttention({ disabled: true, sessionPending: true })).toBe(NO_ATTENTION);
    });
});

describe('deriveTaskCardAttention', () => {
    it('names the unsigned cards, capped, and is quiet on an empty summary', () => {
        expect(deriveTaskCardAttention(null)).toBe(NO_ATTENTION);
        expect(deriveTaskCardAttention({ cardsNeedingSignature: [], count: 0 })).toBe(NO_ATTENTION);
        const a = deriveTaskCardAttention({ cardsNeedingSignature: [
            { id: '1', name: 'Deploy' }, { id: '2', name: 'Release' },
            { id: '3', name: 'Backfill' }, { id: '4', name: 'Sweep' },
        ] });
        expect(a.level).toBe('warning');
        expect(a.reasons[0]).toMatch(/4 task cards need signing/);
        expect(a.reasons[0]).toMatch(/Deploy, Release, Backfill and 1 more/);
    });
});

describe('button presentation', () => {
    it('none leaves the default look; info blue, warning orange, error red, all distinct', () => {
        expect(attentionButtonStyle('none')).toBeUndefined();
        expect(attentionButtonStyle('info')?.borderColor).toBe(ATTENTION_COLORS.info);
        expect(attentionButtonStyle('warning')?.borderColor).toBe(ATTENTION_COLORS.warning);
        expect(attentionButtonStyle('error')?.borderColor).toBe(ATTENTION_COLORS.error);
        expect(new Set(Object.values(ATTENTION_COLORS)).size).toBe(3);
    });

    it('tooltip is the bare label when quiet, and carries the reasons otherwise', () => {
        expect(attentionTooltip('MCP Servers', NO_ATTENTION)).toBe('MCP Servers');
        expect(attentionTooltip('MCP Servers', { level: 'warning', reasons: ['a', 'b'] }))
            .toBe('MCP Servers — a; b');
    });
});

describe('wiring: App.tsx actually mounts the indicator on all three buttons', () => {
    // A derivation that exists but is never applied to the header would pass
    // every assertion above.  Assert the seam: the hook is called, and each
    // of the three buttons reads its own attention slot.
    const src = fs.readFileSync(path.join(__dirname, '../../components/App.tsx'), 'utf8');

    it('calls useHeaderAttention with the MCP flag and the project id', () => {
        expect(src).toMatch(/useHeaderAttention\(\{\s*mcpEnabled,\s*projectId:\s*currentProject\?\.id\s*\}\)/);
    });

    it.each([
        ['Shell Configuration', 'shell', 'CodeOutlined'],
        ['MCP Servers', 'mcp', 'ApiOutlined'],
        ['Task Cards', 'taskCards', 'AppstoreOutlined'],
    ])('%s button is styled and titled from headerAttention.%s', (label, slot, icon) => {
        const tooltip = new RegExp(`attentionTooltip\\('${label}',\\s*headerAttention\\.${slot}\\)`);
        expect(src).toMatch(tooltip);
        // The style must sit on the SAME Button as the icon that identifies it.
        // Bounded lazy match: the onClick arrow (=>) between them defeats [^>]*.
        const button = new RegExp(
            `<Button icon=\\{<${icon} />\\}[\\s\\S]{0,160}?style=\\{attentionButtonStyle\\(headerAttention\\.${slot}\\.level\\)\\}`,
            's');
        expect(src).toMatch(button);
    });

    it('re-polls when any of the three panels closes (signing happened out of band)', () => {
        const closes = src.match(/headerAttention\.refresh\(\)/g) || [];
        expect(closes.length).toBeGreaterThanOrEqual(3);
    });
});
