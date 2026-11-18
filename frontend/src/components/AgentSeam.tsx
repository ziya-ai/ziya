/**
 * AgentSeam -- the thin transcript line marking where a packaged agent was
 * turned on or off (design/agent-overlay.md).  Its message is role 'system'
 * and muted, so it is never sent to the model.
 */
import React from 'react';

export interface AgentNoticeData { kind: 'started' | 'ended'; name: string; }

const rule: React.CSSProperties = { flex: 1, borderTop: '1px solid rgba(127,127,127,0.35)' };

export const AgentSeam: React.FC<{ notice: AgentNoticeData }> = ({ notice }) => (
    <div data-testid="agent-seam" data-kind={notice.kind}
         style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, opacity: 0.65, padding: '4px 0' }}>
        <div style={rule} />
        <span>{notice.name} {notice.kind === 'started' ? 'from here' : 'ended'}</span>
        <div style={rule} />
    </div>
);
