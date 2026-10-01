/**
 * Follows ziya:// links (frontend/src/utils/ziyaLinks.ts) clicked anywhere
 * in the page: a project, a conversation, or one message of a conversation.
 *
 * Renders nothing.  A single document-level click listener (capture phase,
 * so it runs before the browser tries to open an unknown scheme) covers
 * every render path — marked link tokens, raw-HTML links, tool widgets,
 * lazily-mounted messages — without threading a handler through
 * MarkdownRenderer.  The navigation itself is the path the sidebar search
 * already uses: switch project if needed, then loadConversationAndScrollToMessage.
 *
 * A chat link carries no project id (conversations can move between
 * projects), so the owning project comes from the conversations already in
 * state, else from GET /api/v1/chats/{id}/locate.
 */
import React, { useEffect, useRef } from 'react';
import { message } from 'antd';
import { useProject } from '../context/ProjectContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import { ziyaLinkFromEvent, type ZiyaLinkTarget } from '../utils/ziyaLinks';

interface LocateResponse {
  conversationId: string;
  projectId: string;
  projectName?: string | null;
  title?: string;
  messageCount?: number;
}

export async function locateChat(conversationId: string): Promise<LocateResponse | null> {
  const res = await fetch(`/api/v1/chats/${encodeURIComponent(conversationId)}/locate`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`locate failed: ${res.status}`);
  return res.json();
}

// Same settle delay the sidebar search uses between switchProject and the
// conversation load, so PROJECT_CLEAR has run before we point at the new id.
const PROJECT_SWITCH_SETTLE_MS = 300;

const ZiyaLinkNavigator: React.FC = () => {
  const { currentProject, projects, switchProject } = useProject();
  const { loadConversation, loadConversationAndScrollToMessage } = useActiveChat();
  const { conversations } = useConversationList();

  // Latest values for the stable listener; re-registering the document
  // listener on every conversations change would be wasteful.
  const stateRef = useRef({ currentProject, projects, switchProject, loadConversation, loadConversationAndScrollToMessage, conversations });
  stateRef.current = { currentProject, projects, switchProject, loadConversation, loadConversationAndScrollToMessage, conversations };
  const busyRef = useRef(false);

  useEffect(() => {
    const navigate = async (link: ZiyaLinkTarget) => {
      const s = stateRef.current;
      if (link.kind === 'project') {
        if (link.projectId === s.currentProject?.id) return;
        if (!s.projects.some(p => p.id === link.projectId)) {
          message.error('That project is not registered on this server');
          return;
        }
        await s.switchProject(link.projectId);
        return;
      }
      // Owning project: local state first (covers the current project and
      // global conversations), then the server index.
      const local = s.conversations.find(c => c.id === link.conversationId) as any;
      let projectId: string | undefined = local?.projectId;
      let messageCount: number | undefined;
      if (local) {
        // A shell holds only its first and last messages; its real length is
        // _fullMessageCount.  Unknown → skip the bounds check rather than
        // reject a valid index.
        messageCount = local._isShell ? local._fullMessageCount : local.messages?.length;
      } else {
        const located = await locateChat(link.conversationId);
        if (!located) {
          message.error('That conversation no longer exists');
          return;
        }
        projectId = located.projectId;
        messageCount = located.messageCount;
      }
      if (projectId && projectId !== s.currentProject?.id) {
        await s.switchProject(projectId);
        await new Promise(r => setTimeout(r, PROJECT_SWITCH_SETTLE_MS));
      }
      if (link.messageIndex != null && (messageCount == null || link.messageIndex < messageCount)) {
        await s.loadConversationAndScrollToMessage(link.conversationId, link.messageIndex);
      } else {
        if (link.messageIndex != null) {
          message.warning(`That conversation has only ${messageCount} messages; opening it at the top`);
        }
        await s.loadConversation(link.conversationId);
      }
    };

    const onClick = (e: MouseEvent) => {
      // Modifier clicks (new tab, etc.) mean nothing for an in-app scheme;
      // still swallow them so the browser does not show "unknown protocol".
      const hit = ziyaLinkFromEvent(e.target);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      if (busyRef.current) return;
      busyRef.current = true;
      navigate(hit.link)
        .catch(err => {
          console.error('ziya:// navigation failed:', err);
          message.error('Could not open that link');
        })
        .finally(() => { busyRef.current = false; });
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  return null;
};

export default ZiyaLinkNavigator;
