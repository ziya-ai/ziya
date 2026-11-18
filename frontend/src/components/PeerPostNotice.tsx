/**
 * Transcript row for a `peer` message: text another conversation posted
 * here (app/utils/chat_post.py).  Shows where it came from, as text, with
 * a link back, then the posted text.  The message's `content` (which adds a
 * provenance header for the model) is not shown.
 */
import React from 'react';
import type { Message } from '../utils/types';

type PeerPost = NonNullable<Message['peerPost']>;

export function peerPostLabel(post: PeerPost): string {
    return post.mode === 'submit'
        ? `From “${post.fromTitle}” (submitted)`
        : `From “${post.fromTitle}” (context)`;
}

const PeerPostNotice: React.FC<{
    post: PeerPost;
    muted?: boolean;
    onNavigate: (conversationId: string) => void;
    actions?: React.ReactNode;
}> = ({ post, muted, onNavigate, actions }) => (
    <div data-testid="peer-post" data-mode={post.mode}
         style={{ borderLeft: '3px solid #13a8a8', paddingLeft: 10 }}>
        <div className="message-header"
             style={{ display: 'flex', justifyContent: 'space-between', paddingRight: 8 }}>
            <div className="message-sender">
                <a href={post.link}
                   onClick={(e) => { e.preventDefault(); onNavigate(post.fromChatId); }}>
                    {peerPostLabel(post)}
                </a>
                {muted && (
                    <span style={{ fontSize: 12, marginLeft: 4, fontStyle: 'italic', fontWeight: 'normal' }}>
                        (muted)
                    </span>
                )}
            </div>
            {actions}
        </div>
        <div className="message-content" style={{ whiteSpace: 'pre-wrap' }}>
            {post.text}
        </div>
    </div>
);

export default PeerPostNotice;
