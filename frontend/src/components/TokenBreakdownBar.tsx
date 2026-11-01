/**
 * TokenBreakdownBar — the context-budget bar, stacked by what is spending
 * it (files · chat · tools · ast), used by both the sidebar footer
 * (TokenCountDisplay) and the collapsed strip (CollapsedPanelStrip) so the
 * two can never disagree on colour or proportion.  Colours are the
 * --z-tok-* tokens in styles/theme-tokens.css; the track turns red past the
 * danger threshold.
 */
import React from 'react';

export interface TokenBreakdown {
    files: number;
    chat: number;
    tools: number;
    ast: number;
    limit: number;
}

export const TOKEN_SEGMENTS = ['files', 'chat', 'tools', 'ast'] as const;
export type TokenSegment = typeof TOKEN_SEGMENTS[number];

export const fmtK = (n: number): string =>
    n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k` : String(n);

/** Segment widths as percentages of the limit, capped so they sum to <= 100. */
export function segmentPercents(b: TokenBreakdown): Record<TokenSegment, number> {
    const out = { files: 0, chat: 0, tools: 0, ast: 0 } as Record<TokenSegment, number>;
    if (b.limit <= 0) return out;
    let used = 0;
    for (const s of TOKEN_SEGMENTS) {
        const pct = Math.max(0, (b[s] / b.limit) * 100);
        const w = Math.min(pct, 100 - used);
        out[s] = w;
        used += w;
    }
    return out;
}

/** `warning` / `danger` draw the budget zone as an outline on the whole bar
 *  (theme-tokens.css); danger wins when both are set. */
export const TokenBreakdownBar: React.FC<{ breakdown: TokenBreakdown; warning?: boolean; danger?: boolean; className?: string }> =
    ({ breakdown, warning = false, danger = false, className }) => {
        const pct = segmentPercents(breakdown);
        const zone = danger ? 'danger' : warning ? 'warning' : '';
        return (
            <span className={`ziya-tok-bar ${zone} ${className || ''}`} data-testid="token-breakdown-bar" aria-hidden>
                {TOKEN_SEGMENTS.map(s => (
                    <span key={s} className={s} data-testid={`tok-seg-${s}`} style={{ width: `${pct[s]}%` }} />
                ))}
            </span>
        );
    };

export const TokenBreakdownLegend: React.FC<{ breakdown: TokenBreakdown }> = ({ breakdown }) => (
    <div className="ziya-tok-legend" data-testid="token-breakdown-legend">
        {TOKEN_SEGMENTS.filter(s => s !== 'tools' && s !== 'ast' ? true : breakdown[s] > 0).map(s => (
            <span key={s}><i className={`sw ${s}`} />{s} {fmtK(breakdown[s])}</span>
        ))}
    </div>
);

export default TokenBreakdownBar;
