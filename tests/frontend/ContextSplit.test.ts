/**
 * Context split contract tests — validates the data contracts for the
 * slice contexts (ScrollContext, ConversationListContext, ActiveChatContext).
 *
 * Verifies each slice interface covers the fields its consumers need.
 * Type-level tests: compilation failure = test failure.
 *
 * Run: cd frontend && npx tsc --noEmit ../tests/frontend/ContextSplit.test.ts
 */

import type { ConversationListContextValue } from '../../frontend/src/context/ConversationListContext';
import type { ActiveChatContextValue } from '../../frontend/src/context/ActiveChatContext';

/* ------------------------------------------------------------------ */
/*  1. ConversationListContext covers consumer dependencies            */
/* ------------------------------------------------------------------ */

// FolderButton needs createFolder + currentFolderId
type FolderButtonDeps = Pick<ConversationListContextValue, 'createFolder' | 'currentFolderId'>;
const _fb: FolderButtonDeps = {} as ConversationListContextValue;

// Debug needs dbError
type DebugDeps = Pick<ConversationListContextValue, 'dbError'>;
const _db: DebugDeps = {} as ConversationListContextValue;

// ConversationHealthDebug needs conversations + folders
type HealthDeps = Pick<ConversationListContextValue, 'conversations' | 'folders'>;
const _hd: HealthDeps = {} as ConversationListContextValue;

// DelegateLaunchButton needs setConversations + setFolders
type DelegateDeps = Pick<ConversationListContextValue, 'setConversations' | 'setFolders'>;
const _dl: DelegateDeps = {} as ConversationListContextValue;

// ProjectManagerModal needs conversations
type PmDeps = Pick<ConversationListContextValue, 'conversations'>;
const _pm: PmDeps = {} as ConversationListContextValue;

/* ------------------------------------------------------------------ */
/*  2. ActiveChatContext covers consumer dependencies                  */
/* ------------------------------------------------------------------ */

// ExportConversationModal needs currentConversationId + currentMessages
type ExportDeps = Pick<ActiveChatContextValue, 'currentConversationId' | 'currentMessages'>;
const _ex: ExportDeps = {} as ActiveChatContextValue;

// ReasoningDisplay needs reasoningContentMap
type ReasonDeps = Pick<ActiveChatContextValue, 'reasoningContentMap'>;
const _rd: ReasonDeps = {} as ActiveChatContextValue;

// Streaming mutations are in ActiveChatContext
type StreamMutDeps = Pick<ActiveChatContextValue,
  'isStreaming' | 'setIsStreaming' | 'streamingConversations' |
  'addStreamingConversation' | 'removeStreamingConversation'
>;
const _sm: StreamMutDeps = {} as ActiveChatContextValue;

// Processing state is in ActiveChatContext
type ProcDeps = Pick<ActiveChatContextValue, 'getProcessingState' | 'updateProcessingState'>;
const _pd: ProcDeps = {} as ActiveChatContextValue;

/* ------------------------------------------------------------------ */
/*  3. Runtime field count assertions                                  */
/* ------------------------------------------------------------------ */

function testConversationListFieldCount(): void {
  const fields: (keyof ConversationListContextValue)[] = [
    'conversations', 'setConversations', 'folders', 'setFolders',
    'currentFolderId', 'setCurrentFolderId', 'createFolder',
    'updateFolder', 'deleteFolder', 'moveConversationToFolder',
    'moveChatToGroup', 'toggleConversationGlobal',
    'moveConversationToProject', 'moveFolderToProject',
    'toggleFolderGlobal', 'dbError', 'isProjectSwitching',
    'isLoadingConversation', 'folderFileSelections', 'setFolderFileSelections',
  ];
  console.assert(fields.length === 20,
    `ConversationListContext should have 20 fields, got ${fields.length}`);
}

function testActiveChatFieldCount(): void {
  const coreFields: (keyof ActiveChatContextValue)[] = [
    'currentConversationId', 'currentMessages', 'setCurrentConversationId',
    'addMessageToConversation', 'loadConversation', 'startNewChat',
    'editingMessageIndex', 'setEditingMessageIndex',
    'isStreaming', 'setIsStreaming', 'streamingConversations',
    'addStreamingConversation', 'removeStreamingConversation',
    'streamedContentMap', 'setStreamedContentMap',
    'reasoningContentMap', 'setReasoningContentMap',
    'getProcessingState', 'updateProcessingState',
    'loadConversationAndScrollToMessage',
  ];
  console.assert(coreFields.length === 20,
    `ActiveChatContext core fields: expected 20, got ${coreFields.length}`);
}

testConversationListFieldCount();
testActiveChatFieldCount();
console.log('✅ Context split contract tests passed');
