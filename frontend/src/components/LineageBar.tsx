/**
 * LineageBar — persistent breadcrumb at the top of a branched conversation.
 *
 * Shows the lineage back to trunk (each segment clickable to navigate), plus
 * a seam note phrased in terms of *what was said* ("branched where you raised
 * X"), never message indices.  Renders nothing on a trunk conversation.
 * See design/bead-branching.md — this is Phase 0 of the conversation-graph
 * panel; the eventual graph reads the same lineage fields.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useTheme } from '../context/ThemeContext';
import { buildLineageChain, LineageConversationLike } from '../utils/lineage';

interface LineageBarProps {
    conversationId: string;
    conversations: LineageConversationLike[];
    onNavigate: (conversationId: string) => void;
}

// "Title (3)": the ordinal a fork or handoff appends to its source's title.
const ORDINAL_SUFFIX = /\s*\((\d+)\)\s*$/;
// Chains longer than this fold their middle ancestors behind a count.
const COLLAPSE_ABOVE = 3;
const CLIP: React.CSSProperties = {
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
};

/**
 * Label for an ancestor in the breadcrumb.  A fork keeps its source's title
 * and appends an ordinal, so a chain of forks repeats one long title per
 * segment; an ancestor that differs from the current title only by that
 * ordinal is shown as the ordinal alone ("(1)" when it has none).  Any other
 * title is returned unchanged.
 */
export function abbreviateLineageTitle(title: string, currentTitle: string): string {
    const base = (t: string) => t.replace(ORDINAL_SUFFIX, '').trim();
    if (!base(title) || base(title) !== base(currentTitle)) return title;
    const m = title.match(ORDINAL_SUFFIX);
    return m ? `(${m[1]})` : '(1)';
}

const LineageBar: React.FC<LineageBarProps> = ({ conversationId, conversations, onNavigate }) => {
    const { isDarkMode } = useTheme();
    const [expanded, setExpanded] = useState(false);
    const chain = useMemo(
        () => buildLineageChain(conversationId, conversations),
        [conversationId, conversations],
    );
    useEffect(() => { setExpanded(false); }, [conversationId]);

    // Only branched conversations have lineage to show.
    if (chain.length <= 1) return null;

    const current = chain[chain.length - 1];
    const ancestors = chain.slice(0, -1);
    const folded = chain.length > COLLAPSE_ABOVE && !expanded;
    const hiddenCount = folded ? ancestors.length - 2 : 0;
    const shown = folded ? [ancestors[0], ancestors[ancestors.length - 1]] : ancestors;
    const seamLabel = current.branchedFromLabel;
    const linkColor = isDarkMode ? '#4cc9f0' : '#1890ff';
    const dim = isDarkMode ? '#475569' : '#94a3b8';
    const chevron = (key: string) => (
        <span key={key} style={{ color: dim, flexShrink: 0 }}>›</span>
    );
    const toggle = (
        <span
            key="toggle"
            role="button"
            data-testid="lineage-toggle"
            onClick={() => setExpanded(v => !v)}
            title={expanded ? 'Show only the first and the previous segment' : 'Show every earlier segment'}
            style={{
                color: '#64748b',
                cursor: 'pointer',
                flexShrink: 0,
                whiteSpace: 'nowrap',
                fontSize: 12,
                marginLeft: expanded ? 'auto' : undefined,
            }}
        >
            {expanded ? 'collapse' : `… ${hiddenCount} more`}
        </span>
    );

    return (
        <div
            data-testid="lineage-bar"
            style={{
                position: 'sticky',
                top: 0,
                zIndex: 5,
                background: isDarkMode ? '#1a2230' : '#eef4fb',
                borderBottom: `1px solid ${isDarkMode ? '#2a3a52' : '#cfe0f2'}`,
                padding: '8px 14px',
                marginBottom: 8,
            }}
        >
            <div
                data-testid="lineage-chain"
                style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    fontSize: 13,
                    flexWrap: expanded ? 'wrap' : 'nowrap',
                }}
            >
                <span
                    onClick={() => onNavigate(chain[0].id)}
                    title={`Return to "${chain[0].title}"`}
                    style={{ color: linkColor, cursor: 'pointer', fontSize: 15, flexShrink: 0 }}
                >↰</span>
                {shown.map((node, i) => (
                    <React.Fragment key={node.id}>
                        <span
                            data-testid={`lineage-node-${node.id}`}
                            onClick={() => onNavigate(node.id)}
                            title={`Return to "${node.title}"`}
                            style={{ ...CLIP, color: linkColor, cursor: 'pointer', flex: '0 1 auto', maxWidth: 240 }}
                        >
                            {abbreviateLineageTitle(node.title, current.title)}
                        </span>
                        {chevron(`sep-${node.id}`)}
                        {folded && i === 0 && [toggle, chevron('sep-toggle')]}
                    </React.Fragment>
                ))}
                <span
                    data-testid="lineage-current"
                    title={current.title}
                    style={{ ...CLIP, flex: '0 1 auto', color: isDarkMode ? '#e2e8f0' : '#1e293b', fontWeight: 600 }}
                >
                    {current.title}
                </span>
                {expanded && chain.length > COLLAPSE_ABOVE && toggle}
            </div>
            {seamLabel && (
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 5 }}>
                    branched where you raised “{seamLabel}” · everything before that point
                    came along; the original keeps going past it
                </div>
            )}
        </div>
    );
};

export default LineageBar;
