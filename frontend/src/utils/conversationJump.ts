/**
 * Composer → chat-pane jump requests.  The composer (SendChatContainer)
 * has no handle on the scroll manager, which App owns; it asks over a
 * window event instead, the same decoupling composerInject uses.
 */
export const CONVERSATION_JUMP_EVENT = 'ziya:conversation-jump';

/** Conversation order, not screen position. */
export type ConversationJumpTarget = 'beginning' | 'end';

export interface ConversationJumpDetail {
  target: ConversationJumpTarget;
}

export function dispatchConversationJump(target: ConversationJumpTarget): void {
  window.dispatchEvent(
    new CustomEvent<ConversationJumpDetail>(CONVERSATION_JUMP_EVENT, { detail: { target } }));
}

/**
 * The scroll-container edge holding a conversation-order target.  The
 * bottom-up view renders messages reversed, so its first message sits at
 * the DOM end.
 */
export function conversationJumpEdge(
  target: ConversationJumpTarget, isTopToBottom: boolean,
): 'start' | 'end' {
  return (target === 'beginning') === isTopToBottom ? 'start' : 'end';
}
