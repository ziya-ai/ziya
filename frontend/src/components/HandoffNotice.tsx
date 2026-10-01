/**
 * HandoffNotice — the local-only transcript row a handoff leaves behind.
 *
 * Two states, decided by whether a continuation exists yet:
 *   - not yet handed off: "Review handoff" opens the drawer;
 *   - handed off: a forward link to the continuation.  The target comes from
 *     the notice itself (`handoffNotice.targetId`, stamped at commit time) or,
 *     for a 'drafted' notice written before the commit, from the source
 *     record's `handedOffTo`.  So the last item of a handed-off segment always
 *     says where the track went, whichever notice happens to be last.
 *
 * The message is role 'system' + muted and is never sent to the model.
 */
import React from 'react';
import { Button } from 'antd';
import type { Message } from '../utils/types';
import { dispatchHandoffOpen } from '../utils/handoffOpen';

export interface HandoffNoticeConversationLike {
  id: string;
  title?: string;
  handedOffTo?: string;
}

interface Props {
  message: Message;
  conversationId: string;
  conversations: HandoffNoticeConversationLike[];
  onNavigate: (id: string) => void;
}

export function resolveHandoffForward(
  message: Message, conversationId: string, conversations: HandoffNoticeConversationLike[],
): { id: string; title?: string } | null {
  const id = message.handoffNotice?.targetId
    || conversations.find(c => c.id === conversationId)?.handedOffTo;
  if (!id) return null;
  return { id, title: conversations.find(c => c.id === id)?.title };
}

const HandoffNotice: React.FC<Props> = ({ message, conversationId, conversations, onNavigate }) => {
  const forward = resolveHandoffForward(message, conversationId, conversations);
  return (
    <div data-testid="handoff-notice" style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', margin: '6px 0',
      borderRadius: 8, fontSize: 13,
      background: 'rgba(24, 144, 255, 0.08)', border: '1px solid rgba(24, 144, 255, 0.25)',
    }}>
      <span>⇢</span>
      <span style={{ flex: 1 }}>{message.content}</span>
      {forward ? (
        <Button size="small" type="link" data-testid="handoff-notice-forward" onClick={() => onNavigate(forward.id)}>
          Continue in {forward.title ? `“${forward.title}”` : 'the next segment'} ⇢
        </Button>
      ) : (
        <Button size="small" type="link" onClick={() => dispatchHandoffOpen(conversationId)}>
          Review handoff
        </Button>
      )}
    </div>
  );
};

export default HandoffNotice;
