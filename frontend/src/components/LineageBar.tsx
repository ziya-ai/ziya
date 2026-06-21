/**
 * LineageBar — persistent breadcrumb at the top of a branched conversation.
 *
 * Shows the lineage back to trunk (each segment clickable to navigate), plus
 * a seam note phrased in terms of *what was said* ("branched where you raised
 * X"), never message indices.  Renders nothing on a trunk conversation.
 * See design/bead-branching.md — this is Phase 0 of the conversation-graph
 * panel; the eventual graph reads the same lineage fields.
 */
import React, { useMemo } from 'react';
import { useTheme } from '../context/ThemeContext';
import { buildLineageChain, LineageConversationLike } from '../utils/lineage';

interface LineageBarProps {
    conversationId: string;
    conversations: LineageConversationLike[];
    onNavigate: (conversationId: string) => void;
}

const LineageBar: React.FC<LineageBarProps> = ({ conversationId, conversations, onNavigate }) => {
    const { isDarkMode } = useTheme();
    const chain = useMemo(
        () => buildLineageChain(conversationId, conversations),
        [conversationId, conversations],
    );

    // Only branched conversations have lineage to show.
    if (chain.length <= 1) return null;

    const seamLabel = chain[chain.length - 1].branchedFromLabel;

    return (
        <div
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, flexWrap: 'wrap' }}>
                {chain.map((node, i) => {
                    const isLast = i === chain.length - 1;
                    if (isLast) {
                        return (
                            <span key={node.id} style={{ color: isDarkMode ? '#e2e8f0' : '#1e293b', fontWeight: 600 }}>
                                {node.title}
                            </span>
                        );
                    }
                    return (
                        <React.Fragment key={node.id}>
                            <span
                                onClick={() => onNavigate(node.id)}
                                title={`Return to "${node.title}"`}
                                style={{
                                    color: isDarkMode ? '#4cc9f0' : '#1890ff',
                                    cursor: 'pointer',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: 4,
                                }}
                            >
                                {i === 0 && <span style={{ fontSize: 15 }}>↰</span>}
                                {node.title}
                            </span>
                            <span style={{ color: isDarkMode ? '#475569' : '#94a3b8' }}>›</span>
                        </React.Fragment>
                    );
                })}
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
