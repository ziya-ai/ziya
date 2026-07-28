/**
 * useBranchFromBead — shared "split from here" branch flow.
 *
 * Forking a conversation at a bead's seam involves more than the API call:
 * the fork endpoint writes the new chat server-side, but the frontend
 * conversation list knows nothing about it until the next periodic sync, so
 * a lineage-stamped shell must be inserted into React state immediately
 * (the _isShell marker makes loadConversation hydrate messages from the
 * server).  This hook owns the whole sequence — fork, shell insert, success
 * toast, navigation, and pre-filling the composer with a pickup message so
 * the new conversation opens ready to continue rather than as a bare
 * truncated transcript with no obvious next step.
 *
 * Used by: BeadTree ("split"), BacklogBrowser ("Branch"), SeamRibbon
 * ("Branch from here").  Previously BeadTree and BacklogBrowser each
 * carried a byte-similar copy of the shell-insert logic.
 */
import { useCallback } from 'react';
import { message } from 'antd';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import * as beadApi from '../api/beadApi';
import type { ForkBeadResponse } from '../api/beadApi';
import { dispatchComposerInject } from '../utils/composerInject';

export function useBranchFromBead(): (
  conversationId: string, beadId: string,
) => Promise<ForkBeadResponse> {
  const { loadConversation } = useActiveChat();
  const { conversations, setConversations } = useConversationList();

  return useCallback(async (conversationId: string, beadId: string) => {
    const result = await beadApi.forkFromBead(conversationId, beadId);
    // Insert a shell now — the branch would otherwise be invisible until
    // the ~30s server sync, and loadConversation would navigate to an id
    // not in state (the "banner but no branch" symptom).  projectId /
    // folderId are copied from the parent so the branch lands in scope;
    // branchedFrom/At/Label drive sidebar nesting and the LineageBar.
    const parent = conversations.find(c => c.id === conversationId);
    const now = Date.now();
    const branchShell: any = {
      id: result.new_chat_id,
      title: result.branchedFromLabel || 'Branch',
      messages: [],
      projectId: (parent as any)?.projectId,
      folderId: (parent as any)?.folderId ?? null,
      lastAccessedAt: now,
      isActive: true,
      _version: now,
      _isShell: true,
      hasUnreadResponse: false,
      branchedFrom: result.branchedFrom,
      branchedAtMessageIndex: result.branchedAtMessageIndex,
      branchedFromLabel: result.branchedFromLabel,
    };
    setConversations(prev =>
      prev.some(c => c.id === branchShell.id) ? prev : [...prev, branchShell]
    );
    message.success(
      `Branched: ${result.branchedFromLabel || 'thread'} — original preserved`
    );
    loadConversation(result.new_chat_id);
    // Give the user a running start: the branched chat already carries all
    // context up to the seam, so a one-line pickup message is enough for
    // the model to continue.  SendChatContainer stashes this until the
    // navigation completes, then loads it into the composer (not auto-sent).
    dispatchComposerInject(
      result.new_chat_id,
      `Let's continue this thread: ${result.branchedFromLabel || 'the branched thread'}`,
    );
    return result;
  }, [conversations, setConversations, loadConversation]);
}
