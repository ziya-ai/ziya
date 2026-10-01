/**
 * Below-the-line environment (design/capabilities-hub.md "Environment").
 * One quiet row, unaffected by the lens, never a lane.  ``inspect`` expands a
 * detail list with a toggle per builtin tool category.  A renderer: toggles
 * report through ``onToggle`` (useBench.setEnvironment); the warning and the
 * shell's signing state arrive as props.  Toggles are user-wide -- there is
 * no lens here -- and a category turned off is listed dimmed, weighing 0.
 */
import React, { useState } from 'react';
import type { BenchItemView } from '../../hooks/useBench';
import type { Attention } from '../../utils/headerAttention';
import { ATTENTION_COLORS } from '../../utils/headerAttention';
import { formatTokens } from './WeightGauge';

export interface EnvironmentRowProps {
    items: BenchItemView[];
    /** Toggle a builtin category; absent = read-only row. */
    onToggle?: (category: string, enabled: boolean) => void;
    /** Consequence of the last toggle the server wants said (skills off). */
    warning?: string | null;
    /** Shell escalation state from useHeaderAttention; badge opens its config. */
    shellAttention?: Attention;
    onOpenShellConfig?: () => void;
}

const isEnabled = (i: BenchItemView) => i.health?.enabled !== false;
const categoryOf = (i: BenchItemView) => i.key.slice(i.key.indexOf(':') + 1);

export const EnvironmentRow: React.FC<EnvironmentRowProps> = ({ items, onToggle, warning, shellAttention, onOpenShellConfig }) => {
    const [open, setOpen] = useState(false);
    if (items.length === 0) return null;
    const tokens = items.filter(isEnabled).reduce((a, i) => a + i.tokens, 0);
    const shellLevel = shellAttention?.level ?? 'none';
    return (
        <div data-testid="environment-row"
             style={{ borderTop: '1px dashed var(--mockup-border, rgba(127,127,127,0.4))',
                      paddingTop: 8, fontSize: 11, opacity: 0.8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ letterSpacing: '.06em' }}>ENVIRONMENT</span>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {items.filter(isEnabled).map(i => i.name).join(' · ')}
                </span>
                {shellLevel !== 'none' && (
                    <button type="button" data-testid="environment-shell-badge"
                            title={(shellAttention?.reasons ?? []).join('\n') || 'Shell configuration needs attention'}
                            onClick={onOpenShellConfig}
                            style={{ border: 'none', background: 'none', cursor: onOpenShellConfig ? 'pointer' : 'default',
                                     color: ATTENTION_COLORS[shellLevel], fontWeight: 600, padding: 0 }}>
                        ⚠ shell
                    </button>
                )}
                <span>{formatTokens(tokens)}</span>
                <button type="button" aria-expanded={open} onClick={() => setOpen(o => !o)}
                        style={{ border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))',
                                 borderRadius: 5, background: 'none', color: 'inherit',
                                 cursor: 'pointer', padding: '1px 8px' }}>
                    {open ? 'collapse ▴' : 'inspect ▸'}
                </button>
            </div>
            {open && (
                <div data-testid="environment-detail"
                     style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                              gap: '4px 16px', margin: '6px 0 0' }}>
                    {items.map(i => {
                        const on = isEnabled(i);
                        const toggleable = i.kind === 'builtin' && !!onToggle;
                        return (
                            <label key={i.key} data-testid={`env-${i.key}`}
                                   style={{ display: 'flex', alignItems: 'center', gap: 6, opacity: on ? 1 : 0.5,
                                            cursor: toggleable ? 'pointer' : 'default' }}>
                                {toggleable && (
                                    <input type="checkbox" checked={on} aria-label={`${i.name} enabled`}
                                           onChange={e => onToggle(categoryOf(i), e.target.checked)}
                                           style={{ margin: 0 }} />
                                )}
                                <b>{i.name}</b>
                                <span style={{ opacity: 0.6 }}>
                                    {i.kind === 'builtin' ? `${i.health?.tool_count ?? 0} tools` : i.kind} · {formatTokens(i.tokens)}
                                </span>
                                {i.health?.connected === false && <span style={{ color: '#d46b08' }}>· not connected</span>}
                                {i.key === 'mcp:shell' && shellLevel !== 'none' && onOpenShellConfig && (
                                    <button type="button" onClick={onOpenShellConfig}
                                            style={{ border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
                                                     textDecoration: 'underline', padding: 0, fontSize: 10 }}>configure</button>
                                )}
                            </label>
                        );
                    })}
                </div>
            )}
            {warning && (
                <div role="status" data-testid="environment-warning" style={{ color: '#d46b08', marginTop: 4 }}>{warning}</div>
            )}
            <div style={{ opacity: 0.6, marginTop: 4 }}>
                always present in every conversation; not part of any lens
                {open && onToggle && ' · toggles apply to you everywhere and take effect on the next turn'}
            </div>
        </div>
    );
};
