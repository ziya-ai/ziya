/**
 * CollapsedPanelStrip — what the sidebar footer and context bar showed,
 * rendered as one 28px line above the composer while the panel is
 * collapsed (design/visual-refresh.md, stage 3, option A).  Left to right:
 * model (opens the same config as the footer ⚙) · cache · active skill
 * pills with a +N overflow · token bar · ⇢ hand off.  The project name is
 * not repeated here: the header breadcrumb carries it.
 *
 * Reads only published state (utils/panelStatus.ts) and the shared skill
 * hook, so it shows exactly the numbers the footer last computed.
 */
import React from 'react';
import { Tooltip } from 'antd';
import { usePanelStatus } from '../utils/panelStatus';
import { useActiveSkillRows } from './ActiveContextBar';
import { ModelConfigButton } from './ModelConfigButton';
import { dispatchHandoffOpen } from '../utils/handoffOpen';
import { useActiveChatOptional } from '../context/ActiveChatContext';
import { TokenBreakdownBar, fmtK } from './TokenBreakdownBar';

/** Pills shown before the +N chip. */
export const STRIP_SKILL_CAP = 2;

export const CollapsedPanelStrip: React.FC = () => {
    const { tokens, model } = usePanelStatus();
    const { activeSkills, pickedUpSkills } = useActiveSkillRows();
    const activeChat = useActiveChatOptional();
    const conversationId = activeChat?.currentConversationId ?? null;

    const pills = [
        ...activeSkills.map(r => ({ key: r.skill.id, name: r.skill.name, color: r.skill.color, pickedUp: false })),
        ...pickedUpSkills.map(s => ({ key: `pickup-${s.id}`, name: s.name, color: s.color, pickedUp: true })),
    ];
    const shown = pills.slice(0, STRIP_SKILL_CAP);
    const overflow = pills.slice(STRIP_SKILL_CAP);

    const pct = tokens && tokens.limit > 0 ? Math.min(100, (tokens.total / tokens.limit) * 100) : 0;

    return (
        <div className="ziya-collapsed-strip" data-testid="collapsed-panel-strip" role="status">
            {model && (
                <span className="ziya-strip-model" data-testid="strip-model" title={model.pinned ? `${model.display} (pinned)` : model.display}>
                    {model.pinned && <span aria-hidden>📍 </span>}{model.display}
                </span>
            )}
            {model && <ModelConfigButton modelId={model.id} />}
            {tokens?.cacheWorking != null && (
                <Tooltip title={tokens.cacheWorking ? 'Prompt cache working' : 'Cache issues detected'}>
                    <span className={`ziya-strip-cache ${tokens.cacheWorking ? 'ok' : 'bad'}`}>● cache</span>
                </Tooltip>
            )}
            {pills.length > 0 && <span className="ziya-strip-sep" />}
            {shown.map(p => (
                <span key={p.key} className={`ziya-strip-pill ${p.pickedUp ? 'pickup' : ''}`}
                    style={p.pickedUp ? undefined : { background: p.color }}
                    title={p.pickedUp ? 'Loaded on demand by the model this turn' : 'Always-on skill'}>
                    {p.pickedUp && '✦ '}{p.name}
                </span>
            ))}
            {overflow.length > 0 && (
                <Tooltip title={overflow.map(p => p.name).join(' · ')}>
                    <span className="ziya-strip-pill more" data-testid="strip-skill-overflow">+{overflow.length}</span>
                </Tooltip>
            )}
            {tokens && (
                <span className="ziya-strip-tokens" data-testid="strip-tokens"
                    title={`files ${fmtK(tokens.files)} · chat ${fmtK(tokens.chat)} · ast ${fmtK(tokens.ast)} · tools ${fmtK(tokens.tools)}`}>
                    <TokenBreakdownBar breakdown={tokens} warning={tokens.warning} danger={tokens.danger} />
                    <span data-testid="strip-token-total">{fmtK(tokens.total)} / {fmtK(tokens.limit)}</span>
                    <span className="ziya-strip-muted">{Math.round(pct)}%</span>
                    {conversationId && (
                        <button type="button" className="ziya-strip-handoff" onClick={() => dispatchHandoffOpen(conversationId)}
                            title="Hand this conversation off to a fresh continuation">⇢ hand off</button>
                    )}
                </span>
            )}
        </div>
    );
};

export default CollapsedPanelStrip;
