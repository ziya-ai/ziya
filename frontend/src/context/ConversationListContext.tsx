/**
 * ConversationListContext — slice context for the conversations list,
 * folder CRUD, and persistence-related state.
 *
 * Components like FolderTree, ProjectManagerModal, and
 * ConversationHealthDebug need conversations[] and folder operations
 * but do NOT need streaming maps, scroll state, or current-message
 * editing state.
 *
 * ChatProvider owns the actual state; this context is a narrow
 * pass-through — same pattern as StreamingContext.
 */
import React, { createContext, useContext, useMemo, Dispatch, SetStateAction } from 'react';
import { Conversation, ConversationFolder } from '../utils/types';

export interface ConversationListContextValue {
  conversations: Conversation[];
  setConversations: Dispatch<SetStateAction<Conversation[]>>;
  folders: ConversationFolder[];
  setFolders: Dispatch<SetStateAction<ConversationFolder[]>>;
  currentFolderId: string | null;
  setCurrentFolderId: (id: string | null) => void;
  folderFileSelections: Map<string, string[]>;
  setFolderFileSelections: Dispatch<SetStateAction<Map<string, string[]>>>;
  createFolder: (name: string, parentId?: string | null) => Promise<string>;
  updateFolder: (folder: ConversationFolder) => Promise<void>;
  deleteFolder: (id: string) => Promise<void>;
  moveConversationToFolder: (conversationId: string, folderId: string | null) => Promise<void>;
  moveChatToGroup: (chatId: string, groupId: string | null) => Promise<void>;
  toggleConversationGlobal: (conversationId: string) => Promise<void>;
  moveConversationToProject: (conversationId: string, targetProjectId: string) => Promise<void>;
  copyConversationToProject: (conversationId: string, targetProjectId: string) => Promise<void>;
  moveFolderToProject: (folderId: string, targetProjectId: string) => Promise<void>;
  toggleFolderGlobal: (folderId: string) => Promise<void>;
  dbError: string | null;
  isProjectSwitching: boolean;
  isLoadingConversation: boolean;
}

const ConversationListContext = createContext<ConversationListContextValue | undefined>(undefined);

export const ConversationListProvider: React.FC<
  ConversationListContextValue & { children: React.ReactNode }
> = ({ children, ...value }) => {
  const memoized = useMemo(
    () => ({
      conversations: value.conversations,
      setConversations: value.setConversations,
      folders: value.folders,
      setFolders: value.setFolders,
      currentFolderId: value.currentFolderId,
      setCurrentFolderId: value.setCurrentFolderId,
      folderFileSelections: value.folderFileSelections,
      setFolderFileSelections: value.setFolderFileSelections,
      createFolder: value.createFolder,
      updateFolder: value.updateFolder,
      deleteFolder: value.deleteFolder,
      moveConversationToFolder: value.moveConversationToFolder,
      moveChatToGroup: value.moveChatToGroup,
      toggleConversationGlobal: value.toggleConversationGlobal,
      moveConversationToProject: value.moveConversationToProject,
      copyConversationToProject: value.copyConversationToProject,
      moveFolderToProject: value.moveFolderToProject,
      toggleFolderGlobal: value.toggleFolderGlobal,
      dbError: value.dbError,
      isProjectSwitching: value.isProjectSwitching,
      isLoadingConversation: value.isLoadingConversation,
    }),
    [
      value.conversations,
      value.folders,
      value.currentFolderId,
      value.folderFileSelections,
      value.dbError,
      value.isProjectSwitching,
      value.isLoadingConversation,
      // Stable callbacks — won't trigger re-renders on their own
      value.setConversations, value.setFolders,
      value.setCurrentFolderId, value.setFolderFileSelections,
      value.createFolder, value.updateFolder, value.deleteFolder,
      value.moveConversationToFolder, value.moveChatToGroup,
      value.toggleConversationGlobal, value.moveConversationToProject,
      value.copyConversationToProject, value.moveFolderToProject, value.toggleFolderGlobal,
    ]
  );

  return (
    <ConversationListContext.Provider value={memoized}>
      {children}
    </ConversationListContext.Provider>
  );
};

export function useConversationList(): ConversationListContextValue {
  const ctx = useContext(ConversationListContext);
  if (!ctx) throw new Error('useConversationList must be used within ConversationListProvider');
  return ctx;
}
