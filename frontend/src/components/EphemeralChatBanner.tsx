/**
 * EphemeralChatBanner — persistent in-window indicator that the active
 * conversation is ephemeral (React-state only, never written to IndexedDB
 * or the server, lost on reload or project switch).
 *
 * Until now the only cue was an italic "· ephemeral" suffix in the chat
 * list, which is invisible when the sidebar is collapsed and easy to miss
 * when it isn't. This banner sits at the top of the chat container and
 * stays pinned while scrolling, and offers a one-click promotion to a
 * normally-persisted conversation.
 *
 * Renders nothing for non-ephemeral conversations, so it is safe to mount
 * unconditionally.
 */
import React, { useMemo, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';

export const EphemeralChatBanner: React.FC = () => {
    const { currentConversationId, promoteEphemeralToRetained } = useActiveChat();
    const { conversations } = useConversationList();
    const [promoting, setPromoting] = useState(false);

    const isEphemeral = useMemo(
        () => conversations.some(
            c => c.id === currentConversationId && c.isEphemeral === true,
        ),
        [conversations, currentConversationId],
    );

    if (!currentConversationId || !isEphemeral) return null;

    const handleKeep = async () => {
        if (promoting) return;
        setPromoting(true);
        try {
            await promoteEphemeralToRetained(currentConversationId);
        } finally {
            // If promotion succeeded this component unmounts on the next
            // render (isEphemeral flips false); if it failed we want the
            // button usable again.
            setPromoting(false);
        }
    };

    return (
        <div
            className="ephemeral-chat-banner"
            role="status"
            aria-live="polite"
            data-testid="ephemeral-chat-banner"
        >
            <span className="ephemeral-chat-banner-icon" aria-hidden="true">⌛</span>
            <span className="ephemeral-chat-banner-text">
                <strong>Ephemeral chat</strong>
                {' — '}not saved. Lost on reload or project switch.
            </span>
            <Tooltip title="Convert this into a normal, persisted conversation">
                <Button
                    size="small"
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={promoting}
                    onClick={handleKeep}
                    className="ephemeral-chat-banner-keep"
                >
                    Keep this chat
                </Button>
            </Tooltip>
        </div>
    );
};

export default EphemeralChatBanner;
