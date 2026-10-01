/**
 * Token weight of what this conversation actually carries.  Reads
 * ``effective`` (not the lens view): weight is what goes out on the next turn,
 * whichever layer you happen to be editing.  Environment is the hatched fixed
 * base -- honest about its cost, but not presented as something to trim.
 */
import React from 'react';
import type { BenchItemView } from '../../hooks/useBench';
import { DeltaChip } from './DeltaChip';

export const formatTokens = (n: number): string =>
    n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);

export interface WeightGaugeProps {
    items: BenchItemView[];
    weight: { always: number; ondemand: number; environment: number };
    /** Transient: change in the on-hand total since the last view. */
    delta?: number;
}

export const WeightGauge: React.FC<WeightGaugeProps> = ({ items, weight, delta }) => {
    // A gated (held) item has a placement but is not sent; the server's weight omits it too.
    const onHand = items.filter(i => i.tier !== 'environment' && i.effective === 'always' && i.tokens > 0 && !i.health?.gate);
    // A drop leaves a hollow stub where the tokens were, for as long as the
    // chip shows, so the shrink reads as movement rather than a quiet renumber.
    const gone = delta && delta < 0 ? -delta : 0;
    const total = weight.environment + weight.always + gone;
    const pct = (n: number) => (total > 0 ? `${((100 * n) / total).toFixed(2)}%` : '0%');
    return (
        <div data-testid="weight-gauge" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
            <span style={{ opacity: 0.6 }}>weight</span>
            <div style={{ flex: 1, height: 10, borderRadius: 5, overflow: 'hidden', display: 'flex',
                          background: 'rgba(127,127,127,0.15)' }}>
                {weight.environment > 0 && (
                    <span data-testid="weight-env" title={`environment ${formatTokens(weight.environment)}`}
                          style={{ width: pct(weight.environment),
                                   background: 'repeating-linear-gradient(135deg, rgba(127,127,127,0.55) 0 3px, rgba(127,127,127,0.25) 3px 6px)' }} />
                )}
                {onHand.map(i => (
                    <span key={i.key} data-testid={`weight-seg-${i.key}`}
                          title={`${i.name} · ${formatTokens(i.tokens)}`}
                          style={{ width: pct(i.tokens), background: i.kind === 'mcp' ? '#0d9488' : '#b32ca8',
                                   borderLeft: '1px solid rgba(0,0,0,0.25)' }} />
                ))}
                {gone > 0 && (
                    <span data-testid="weight-withheld-stub" title={`${gone.toLocaleString()}t withheld just now`}
                          style={{ width: pct(gone), boxSizing: 'border-box', border: '1px dashed #0d9488', opacity: 0.7 }} />
                )}
            </div>
            <span>
                <b>{formatTokens(weight.always)}</b> on hand
                {' '}<DeltaChip delta={delta} testId="weight-delta" />
                <span style={{ opacity: 0.6 }}> + {formatTokens(weight.environment)} environment</span>
                {weight.ondemand > 0 && (
                    <span style={{ opacity: 0.6 }}> · {formatTokens(weight.ondemand)} within reach if loaded</span>
                )}
            </span>
        </div>
    );
};
