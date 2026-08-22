/**
 * TaskMarkdown — drop-in substitute for `MarkdownRenderer` used across
 * Task Card surfaces (inline tile, run inspector, block detail panel,
 * artifact viewer).
 *
 * The chat's "Raw Markdown View" toggle (Conversation.tsx / StreamedContent.tsx)
 * only affected persisted messages and the live stream — Task Card tiles
 * render their own MarkdownRenderer instances directly and never consulted
 * `currentDisplayMode`, so the toggle had no visible effect on them. This
 * wrapper reads the mode from ActiveChatContext (non-throwing, since some
 * Task Card components mount standalone in tests/print views without a
 * provider) and renders the same `<pre className="raw-markdown-view">`
 * element the chat uses, falling back to `MarkdownRenderer` otherwise.
 */
import React from 'react';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { useActiveChatOptional } from '../../context/ActiveChatContext';

type MarkdownRendererProps = React.ComponentProps<typeof MarkdownRenderer>;

export const TaskMarkdown: React.FC<MarkdownRendererProps> = (props) => {
    const activeChat = useActiveChatOptional();
    if (activeChat?.currentDisplayMode === 'raw') {
        return <pre className="raw-markdown-view">{props.markdown}</pre>;
    }
    return <MarkdownRenderer {...props} />;
};
