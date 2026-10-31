/**
 * useHandoffDraftRequest — ask the conversation's own model to write the
 * handoff draft as a BACKGROUND, NON-PERSISTED turn.
 *
 * Nothing is placed in the composer and nothing streams into the transcript.
 * The request carries the same history / checked files / skills / model pin
 * a normal send would (derived exactly as useSendPayload does), so the draft
 * is written from the full live context.  The MODEL is the exception: by
 * default the turn is pinned to the `medium` tier (the standard model, see
 * utils/handoffDraftModel.ts); the caller may pass another tier or null for
 * the conversation's own model.  The model saves the draft server-side with
 * handoff_write; when the stream ends we re-read the record and drop a LOCAL
 * notice into the conversation (role 'system', muted — never sent to the
 * model) with a link back to the drawer.
 */
import { useCallback, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { useActiveChat } from '../context/ActiveChatContext';
import { useProject } from '../context/ProjectContext';
import { useFolderContext } from '../context/FolderContext';
import { useResolvedModelPin } from './useResolvedModelPin';
import { runBackgroundTurn } from '../apis/chatApi';
import { convertKeysToStrings } from '../utils/types';
import type { Message } from '../utils/types';
import * as handoffApi from '../api/handoffApi';
import type { HandoffDocument } from '../api/handoffApi';
import { DEFAULT_DRAFT_TIER, resolveDraftTierModel, type DraftTierChoice } from '../utils/handoffDraftModel';

/** Instruction for the drafting turn.  The tool writes to the same record
 *  the drawer reads; the model must not create the continuation itself. */
export const DRAFT_REQUEST_PROMPT =
  "Write the handoff draft for this conversation with handoff_write(mode='replace'): " +
  'objective, current state, decisions (with turn or messageIndex references), gotchas, ' +
  'and references worth retrieving later. Do not create a new conversation and do not ' +
  'write to any file — the user commits the handoff from the UI. Reply with one sentence ' +
  'confirming the draft was saved.';

export interface DraftRequestOptions {
  /** Tier to draft on; undefined = DEFAULT_DRAFT_TIER, null = the conversation's own model. */
  tier?: DraftTierChoice;
}

export function useHandoffDraftRequest():
  (conversationId: string, opts?: DraftRequestOptions) => Promise<HandoffDocument | null> {
  const activeChat = useActiveChat();
  const { checkedKeys } = useFolderContext();
  const project = useProject();
  const { resolveFor } = useResolvedModelPin();
  const ref = useRef({ activeChat, checkedKeys, project, resolveFor });
  ref.current = { activeChat, checkedKeys, project, resolveFor };

  return useCallback(async (conversationId: string, opts: DraftRequestOptions = {}) => {
    const { activeChat: ac, checkedKeys: ck, project: pj, resolveFor: rmp } = ref.current;
    // Only the ACTIVE conversation's messages are in memory here; the drawer
    // opens on the active one, and the caller disables the action otherwise.
    if (conversationId !== ac.currentConversationId) {
      throw new Error('Open the conversation before asking for a draft.');
    }
    const messages = ac.currentMessages.filter(m => !m.muted);
    // Pin the drafting turn to the chosen tier's model.  The conversation's
    // own pin is the fallback: the explicit "conversation model" choice, or a
    // tier table that could not be read.
    const tier = opts.tier === undefined ? DEFAULT_DRAFT_TIER : opts.tier;
    const tierModel = await resolveDraftTierModel(tier);
    const resolvedModelPin = tierModel
      ? { model: tierModel, scope: 'conversation' as const, persistent: false }
      : rmp(conversationId);
    await runBackgroundTurn(
      messages, DRAFT_REQUEST_PROMPT, convertKeysToStrings(ck || []), conversationId,
      {
        currentProject: pj.currentProject ?? null,
        activeSkillPrompts: pj.activeSkillPrompts || undefined,
        resolvedModelPin,
      },
    );
    const state = await handoffApi.getHandoffState(conversationId);
    if (!state.draft?.document) return null;

    const notice: Message = {
      id: uuidv4(),
      role: 'system',
      muted: true,
      content: 'The model drafted a handoff document for this conversation.',
      _timestamp: Date.now(),
      handoffNotice: { kind: 'drafted' },
    };
    ac.addMessageToConversation(notice, conversationId, false);
    return state.draft;
  }, []);
}
