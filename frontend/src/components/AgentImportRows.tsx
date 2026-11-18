/**
 * How an agent definition was imported (design/agent-overlay.md, "Import
 * view"): per part, what the source said and what Ziya does with it.
 * Shared by the composer's chip panel and the Bench's agent list.
 */
import React from 'react';
import type { AgentImportRow } from '../hooks/useAgent';

const border = '1px solid rgba(127,127,127,0.35)';
const OUTCOME_COLOR: Record<string, string> = {
    applied: '#0d9488', mapped: '#0d9488', unavailable: '#d46b08', startup: '#d46b08',
};

export const AgentImportRows: React.FC<{ rows: AgentImportRow[] }> = ({ rows }) => (
    <>
        {rows.map(r => (
            <div key={r.part} data-testid={`agent-row-${r.part}`} title={r.items.join('\n')}
                 style={{ display: 'grid', gridTemplateColumns: '120px 1fr 1fr', gap: 8, padding: '2px 0',
                          borderTop: border, fontSize: 12 }}>
                <span style={{ opacity: 0.7 }}>{r.part}</span>
                <span style={{ overflowWrap: 'anywhere' }}>{r.says}</span>
                <span style={{ color: OUTCOME_COLOR[r.outcome] ?? 'inherit', opacity: OUTCOME_COLOR[r.outcome] ? 1 : 0.7 }}>
                    {r.does}{r.outcome === 'unavailable' && r.items.length > 0 ? `: ${r.items.join(', ')}` : ''}
                </span>
            </div>
        ))}
    </>
);
