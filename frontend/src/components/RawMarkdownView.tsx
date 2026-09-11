import React from 'react';
import { useActiveChatOptional } from '../context/ActiveChatContext';
import { expandThinkingMarkers } from '../utils/thinkingBlocks';

/**
 * Plain-text view of a message's markdown source (Ctrl+Shift+U).
 *
 * Message content holds only positional markers for thinking blocks; the
 * reasoning text itself lives in the session-scoped reasoningContentMap.
 * A bare <pre>{content}</pre> therefore omitted every thinking block.  This
 * resolves the markers so the raw view includes everything the pretty view
 * renders, collapsed or not.
 */
export const RawMarkdownView: React.FC<{ content: string }> = ({ content }) => {
    const activeChat = useActiveChatOptional();
    const map = activeChat?.reasoningContentMap;
    const expanded = React.useMemo(
        () => expandThinkingMarkers(content, map),
        [content, map],
    );
    return <pre className="raw-markdown-view">{expanded}</pre>;
};

export default RawMarkdownView;
