/**
 * User-actionable state for the header buttons (Shell Configuration, MCP
 * Servers, Task Cards): what needs the user's attention, how urgent it is,
 * and how the button should look.
 *
 * Pure functions over the existing status payloads.  The severity policy
 * lives here, in one place:
 *   - error   (red)    — nothing works until fixed: MCP config errors.
 *   - warning (orange) — something is held pending a decision the user must
 *                        make in the UI or a terminal: a quarantined server
 *                        awaiting Re-authorize, shell/task-card escalations
 *                        awaiting `ziya-approve`.
 *   - info    (blue)   — worth a look but nothing is waiting on the user: an
 *                        enabled MCP server that failed to connect (the panel
 *                        has its logs and preflight diagnosis).
 * A startup log line cannot be acted on from the browser, so these are the
 * signal that brings the user to the panel where the action lives.
 */
import type { CSSProperties } from 'react';

export type AttentionLevel = 'none' | 'info' | 'warning' | 'error';

export interface Attention {
    level: AttentionLevel;
    /** Short, user-facing reasons; shown in the button tooltip. */
    reasons: string[];
}

export const NO_ATTENTION: Attention = { level: 'none', reasons: [] };

const RANK: Record<AttentionLevel, number> = { none: 0, info: 1, warning: 2, error: 3 };

export function maxLevel(a: AttentionLevel, b: AttentionLevel): AttentionLevel {
    return RANK[a] >= RANK[b] ? a : b;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** From GET /api/mcp/status. */
export function deriveMcpAttention(status: any): Attention {
    if (!status || status.disabled || !status.initialized) return NO_ATTENTION;
    let level: AttentionLevel = 'none';
    const reasons: string[] = [];

    if (status.config_error) {
        level = 'error';
        reasons.push(`MCP config error: ${status.config_error}`);
    }
    const findings: any[] = Array.isArray(status.config_findings) ? status.config_findings : [];
    const blocking = findings.filter(f => f?.severity === 'error').length;
    const advisory = findings.length - blocking;
    if (blocking > 0) {
        level = 'error';
        reasons.push(`${plural(blocking, 'blocking problem', 'blocking problems')} in your MCP config`);
    }
    if (advisory > 0) {
        level = maxLevel(level, 'warning');
        reasons.push(`${plural(advisory, 'warning', 'warnings')} in your MCP config`);
    }

    const quarantined = Object.entries(status.servers || {})
        .filter(([, s]: [string, any]) => s?.quarantined)
        .map(([name]) => name);
    if (quarantined.length > 0) {
        level = maxLevel(level, 'warning');
        reasons.push(
            `Tool definitions changed for ${quarantined.join(', ')} — ` +
            'open MCP Servers and click Re-authorize to accept them',
        );
    }

    // Enabled servers with no live connection.  get_server_status reports
    // connected=false for DISABLED servers too (they are configured but never
    // get a client), so cross-check server_configs or every disabled server
    // would light the button.  A quarantined server is connected and is
    // reported above, not here.
    const configs: Record<string, any> = status.server_configs || {};
    const failed = Object.entries(status.servers || {})
        .filter(([name, s]: [string, any]) =>
            s && !s.connected && !s.quarantined && configs[name]?.enabled !== false)
        .map(([name]) => name);
    if (failed.length > 0) {
        level = maxLevel(level, 'info');
        reasons.push(
            `${plural(failed.length, 'server', 'servers')} failed to connect ` +
            `(${failed.join(', ')}) — open MCP Servers for the logs`,
        );
    }
    return level === 'none' ? NO_ATTENTION : { level, reasons };
}

/** From GET /api/mcp/shell-config. */
export function deriveShellAttention(cfg: any): Attention {
    if (!cfg || cfg.disabled) return NO_ATTENTION;
    const reasons: string[] = [];
    const sig = cfg.signatureStatus;
    if (sig?.hasEscalation && !sig.authorized) {
        reasons.push('Shell escalation is not signed — run sudo ziya-approve, then Restart');
    }
    if (cfg.sessionPending) {
        reasons.push('A session escalation is staged and waiting to be signed/applied');
    }
    return reasons.length ? { level: 'warning', reasons } : NO_ATTENTION;
}

/** From GET /api/v1/projects/{id}/task-cards/signature-summary. */
export function deriveTaskCardAttention(summary: any): Attention {
    const cards: any[] = Array.isArray(summary?.cardsNeedingSignature)
        ? summary.cardsNeedingSignature : [];
    if (cards.length === 0) return NO_ATTENTION;
    const names = cards.map(c => c?.name || c?.id).filter(Boolean).slice(0, 3);
    const more = cards.length - names.length;
    return {
        level: 'warning',
        reasons: [
            `${plural(cards.length, 'task card needs', 'task cards need')} signing: ` +
            names.join(', ') + (more > 0 ? ` and ${more} more` : ''),
        ],
    };
}

// antd's blue-6 / volcano-6 / red-5. Colour is the point here, so these are
// fixed rather than theme tokens; all three read on light and dark surfaces.
export const ATTENTION_COLORS: Record<Exclude<AttentionLevel, 'none'>, string> = {
    info: '#1677ff',
    warning: '#fa8c16',
    error: '#ff4d4f',
};

/** Outline for a header Button; undefined leaves the default look alone. */
export function attentionButtonStyle(level: AttentionLevel): CSSProperties | undefined {
    if (level === 'none') return undefined;
    const color = ATTENTION_COLORS[level];
    return {
        borderColor: color,
        color,
        // A 1px ring in addition to the border so the outline survives the
        // hover/focus border-colour swap antd applies.
        boxShadow: `0 0 0 1px ${color}`,
    };
}

/** Tooltip text: the plain label, or the label plus what needs doing. */
export function attentionTooltip(base: string, attention: Attention): string {
    if (attention.level === 'none' || attention.reasons.length === 0) return base;
    return `${base} — ${attention.reasons.join('; ')}`;
}
