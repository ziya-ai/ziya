/**
 * Heading of the MCP servers panel: tokens SENT for MCP on each request,
 * split by bench placement.  A renderer of ``token_costs.breakdown`` from
 * GET /api/mcp/status (app/utils/mcp_token_breakdown.py); it derives nothing
 * about placement itself.
 */
import React from 'react';
import { Alert } from 'antd';
import { formatTokens } from './Bench/WeightGauge';

interface Bucket { tokens: number; servers: number; tools: number }

/** One server's share: what of it is sent, and what is held back. */
export interface McpServerTokens {
    placement: 'on_hand' | 'loaded' | 'partial' | 'reach' | 'deferred' | 'shelved' | 'disabled';
    sent: number;
    /** Unloaded, deferred or shelved tokens. */
    held: number;
}

export interface McpTokenBreakdown {
    scope: 'conversation' | 'project' | 'none';
    conversation_id: string | null;
    /** The current endpoint + model send within-reach tools deferred. */
    deferral: boolean;
    on_hand: Bucket;
    loaded: Bucket;
    reach_summaries: { tokens: number; servers: number };
    /** Sent with defer_loading; held out of context by the provider. Not in sent_total. */
    deferred: Bucket;
    instructions: { tokens: number };
    sent_total: number;
    not_sent: { reach_unloaded: number; shelved: number; disabled: number };
    per_server?: Record<string, McpServerTokens>;
}

/**
 * Text for a server row's token tag.  Null when the row should keep its own
 * wording (no breakdown, or a server disabled in config).
 */
export function serverTokenLabel(row: McpServerTokens | undefined): { text: string; sent: boolean } | null {
    if (!row) return null;
    switch (row.placement) {
        case 'on_hand': return { text: `${formatTokens(row.sent)} tokens`, sent: true };
        case 'loaded': return { text: `${formatTokens(row.sent)} tokens · loaded`, sent: true };
        case 'partial':
            return { text: `${formatTokens(row.sent)} of ${formatTokens(row.sent + row.held)} tokens · partly loaded`, sent: true };
        case 'reach': return { text: `within reach · ${formatTokens(row.held)} if loaded`, sent: false };
        case 'deferred': return { text: `sent deferred · ${formatTokens(row.held)}`, sent: false };
        case 'shelved': return { text: `shelved · ${formatTokens(row.held)} not sent`, sent: false };
        default: return null;
    }
}

const COLORS = {
    on_hand: '#0f8f83',
    loaded: '#5fb8ae',
    reach_summaries: '#d4a017',
    instructions: '#8c959f',
    deferred: 'transparent',
};

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`;

const SCOPE_LABEL: Record<McpTokenBreakdown['scope'], string> = {
    conversation: 'this conversation',
    project: 'project placements (no conversation open)',
    none: 'bench placements unavailable, all servers counted on hand',
};

export const McpTokenHeading: React.FC<{ breakdown: McpTokenBreakdown }> = ({ breakdown: b }) => {
    const rows: Array<{ id: keyof typeof COLORS; label: string; detail: string; tokens: number; show: boolean }> = [
        { id: 'on_hand', label: 'On hand',
          detail: `${count(b.on_hand.servers, 'server')}, ${count(b.on_hand.tools, 'tool')}`,
          tokens: b.on_hand.tokens, show: true },
        { id: 'loaded', label: 'Loaded from within reach',
          detail: `${count(b.loaded.servers, 'server')}, ${count(b.loaded.tools, 'tool')}`,
          tokens: b.loaded.tokens, show: b.loaded.servers > 0 },
        { id: 'reach_summaries', label: 'Within-reach summaries',
          detail: `${count(b.reach_summaries.servers, 'server')} listed`,
          tokens: b.reach_summaries.tokens, show: b.reach_summaries.servers > 0 },
        { id: 'instructions', label: 'Instructions', detail: 'current model',
          tokens: b.instructions.tokens, show: true },
        { id: 'deferred', label: 'Sent deferred',
          detail: `${count(b.deferred.servers, 'server')}, ${count(b.deferred.tools, 'tool')} · held by the provider, not counted in the total`,
          tokens: b.deferred.tokens, show: b.deferral && b.deferred.servers > 0 },
    ];
    const pct = (n: number) => (b.sent_total > 0 ? `${((100 * n) / b.sent_total).toFixed(2)}%` : '0%');
    const notSent = [
        b.not_sent.reach_unloaded > 0 && `${formatTokens(b.not_sent.reach_unloaded)} within reach if loaded`,
        b.not_sent.shelved > 0 && `${formatTokens(b.not_sent.shelved)} shelved`,
        b.not_sent.disabled > 0 && `${formatTokens(b.not_sent.disabled)} disabled`,
    ].filter(Boolean) as string[];

    return (
        <Alert
            type="info"
            showIcon
            style={{ height: '100%' }}
            message={
                <span>
                    MCP tokens in context
                    <span style={{ fontSize: 12, marginLeft: 8, opacity: 0.7 }}>· {SCOPE_LABEL[b.scope]}</span>
                </span>
            }
            description={
                <div data-testid="mcp-token-heading">
                    <div data-testid="mcp-tokens-total" style={{ fontSize: 20, fontWeight: 'bold', marginBottom: 8 }}>
                        {formatTokens(b.sent_total)}{' '}
                        <span style={{ fontSize: 14, fontWeight: 'normal', opacity: 0.7 }}>tokens sent each request</span>
                    </div>
                    <div style={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden', marginBottom: 8,
                                  background: 'rgba(127,127,127,0.15)' }}>
                        {rows.filter(r => r.show && r.id !== 'deferred' && r.tokens > 0).map(r => (
                            <span key={r.id} title={`${r.label} · ${formatTokens(r.tokens)}`}
                                  style={{ width: pct(r.tokens), background: COLORS[r.id] }} />
                        ))}
                    </div>
                    <div style={{ fontSize: 12 }}>
                        {rows.filter(r => r.show).map(r => (
                            <div key={r.id} data-testid={`mcp-tokens-row-${r.id}`}
                                 style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '2px 0',
                                          opacity: r.id === 'deferred' ? 0.8 : 1 }}>
                                <span style={{ flex: 'none', width: 9, height: 9, borderRadius: 2, background: COLORS[r.id],
                                               border: r.id === 'deferred' ? '1px dashed currentColor' : 'none',
                                               boxSizing: 'border-box' }} />
                                <span style={{ flex: 1 }}>
                                    <b>{r.label}</b>
                                    <span style={{ opacity: 0.65 }}> · {r.detail}</span>
                                </span>
                                <b style={{ fontVariantNumeric: 'tabular-nums' }}>{formatTokens(r.tokens)}</b>
                            </div>
                        ))}
                    </div>
                    {notSent.length > 0 && (
                        <div data-testid="mcp-tokens-not-sent"
                             style={{ borderTop: '1px solid rgba(127,127,127,0.3)', marginTop: 8, paddingTop: 6,
                                      fontSize: 12, opacity: 0.75 }}>
                            Not sent: {notSent.join(' · ')}
                        </div>
                    )}
                </div>
            }
        />
    );
};

export default McpTokenHeading;
