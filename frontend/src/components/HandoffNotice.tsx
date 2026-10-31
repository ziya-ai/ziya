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
 * A 'forked' notice is the same row for a plain fork: left on the source at
 * the fork point, it links to the fork by its own targetId only (a fork never
 * sets handedOffTo) and offers no "Review handoff" fallback.
 *
 * The message is role 'system' + muted and is never sent to the model.
 */
import React from 'react';
import { Button } from 'antd';
import type { Message } from '../utils/types';
import { dispatchHandoffOpen } from '../utils/handoffOpen';
import ForkGlyph from './ForkGlyph';

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
  const isFork = message.handoffNotice?.kind === 'forked';
  // The record's handedOffTo names a continuation, not a fork: a fork notice
  // without a targetId must not borrow it.
  const id = message.handoffNotice?.targetId
    || (isFork ? undefined : conversations.find(c => c.id === conversationId)?.handedOffTo);
  if (!id) return null;
  return { id, title: conversations.find(c => c.id === id)?.title };
}

/**
 * A 'drafted' notice says only that a handoff document exists.  Once the
 * handoff is committed, the 'handed_off' notice carries the same forward
 * link, so showing both repeats the same row twice.  The drafted notice is
 * then hidden from the transcript (it stays in the message list).  A source
 * handed off before 'handed_off' notices existed has only the drafted one,
 * which then remains the forward link.
 */
export function isSupersededHandoffNotice(message: Message, messages: Message[]): boolean {
  if (message.role !== 'system' || message.handoffNotice?.kind !== 'drafted') return false;
  return messages.some(m => m.role === 'system' && m.handoffNotice?.kind === 'handed_off');
}

/**
 * Button-label form of a conversation title.  The button cannot wrap (antd
 * buttons are nowrap), so an untruncated long title made it as wide as the
 * row and squeezed the notice text to one word per line.  The full title is
 * already in the notice text and in the button's tooltip.
 */
export const FORWARD_LABEL_MAX = 48;
export function forwardLabelTitle(title: string): string {
  const t = title.trim();
  return t.length <= FORWARD_LABEL_MAX ? t : `${t.slice(0, FORWARD_LABEL_MAX - 1).trimEnd()}…`;
}

const HandoffNotice: React.FC<Props> = ({ message, conversationId, conversations, onNavigate }) => {
  const forward = resolveHandoffForward(message, conversationId, conversations);
  const isFork = message.handoffNotice?.kind === 'forked';
  const label = forward?.title ? `“${forwardLabelTitle(forward.title)}”` : null;
  return (
    <div data-testid="handoff-notice" data-kind={message.handoffNotice?.kind} style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', margin: '6px 0',
      borderRadius: 8, fontSize: 13,
      background: 'rgba(24, 144, 255, 0.08)', border: '1px solid rgba(24, 144, 255, 0.25)',
    }}>
      {isFork
        ? <ForkGlyph title="Forked here" style={{ marginRight: 0, opacity: 1 }} />
        : <span>⇢</span>}
      <span data-testid="handoff-notice-text"
        style={{ flex: '1 1 0', minWidth: 0, overflowWrap: 'anywhere' }}>{message.content}</span>
      {forward ? (
        <Button size="small" type="link"
          data-testid={isFork ? 'fork-notice-forward' : 'handoff-notice-forward'}
          title={forward.title}
          style={{ flexShrink: 0, maxWidth: '45%', overflow: 'hidden', textOverflow: 'ellipsis' }}
          onClick={() => onNavigate(forward.id)}>
          {isFork
            ? `Open ${label ?? 'the fork'}`
            : `Continue in ${label ?? 'the next segment'} ⇢`}
        </Button>
      ) : isFork ? null : (
        <Button size="small" type="link" onClick={() => dispatchHandoffOpen(conversationId)}>
          Review handoff
        </Button>
      )}
    </div>
  );
};

export default HandoffNotice;
