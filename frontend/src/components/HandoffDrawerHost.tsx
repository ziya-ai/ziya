/**
 * HandoffDrawerHost — the single, always-mounted Hand off drawer.
 *
 * Every entry point (the token bar's "⇢ Hand off…", the sidebar row's
 * "Hand off…") asks for the drawer through the ziya:handoff-open document
 * event.  The listener used to live in MUIChatHistory, which antd's Tabs
 * only mounts once the sidebar's Chats tab has been opened and which a
 * project switch swaps for a spinner; while it was absent the event had no
 * listener and the click did nothing.  App mounts this unconditionally.
 */
import React, { useEffect, useState } from 'react';
import HandoffDrawer from './HandoffDrawer';
import { subscribeHandoffOpen } from '../utils/handoffOpen';

const HandoffDrawerHost: React.FC = () => {
  const [conversationId, setConversationId] = useState<string | null>(null);

  useEffect(() => subscribeHandoffOpen(id =>
    setConversationId(id.startsWith('conv-') ? id.substring(5) : id)), []);

  return (
    <HandoffDrawer
      conversationId={conversationId}
      onClose={() => setConversationId(null)}
    />
  );
};

export default HandoffDrawerHost;
