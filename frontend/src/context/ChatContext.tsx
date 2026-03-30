import React, { createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo } from 'react';
import { StreamingProvider } from './StreamingContext';
import { ScrollProvider } from './ScrollContext';
import { ConversationListProvider } from './ConversationListContext';
import { ActiveChatProvider } from './ActiveChatContext';
import { Conversation, Message, ConversationFolder } from "../utils/types";
import { v4 as uuidv4 } from "uuid";
import { db } from '../utils/db';
import { detectIncompleteResponse } from '../utils/responseUtils';
import { message } from 'antd';
import { useTheme } from './ThemeContext';
import { useConfig } from './ConfigContext';
import { useProject } from './ProjectContext';
import { projectSync } from '../utils/projectSync';
import { getTabState, setTabState } from '../utils/tabState';
import * as syncApi from '../api/conversationSyncApi';
import { useServerStatus } from './ServerStatusContext';
import * as folderSyncApi from '../api/folderSyncApi';
import { useDelegatePolling } from '../hooks/useDelegatePolling';
import { gcEmptyConversations } from '../utils/retentionPurge';
import { useDelegateStreaming } from '../hooks/useDelegateStreaming';

const TERMINAL_PLAN_STATUSES = new Set(['completed', 'completed_partial', 'cancelled']);

/** Return true when the server folder should replace the local copy. */
function serverFolderWins(local: ConversationFolder | undefined, server: ConversationFolder): boolean {
    if (!local) return true;
    if ((server.updatedAt || 0) > (local.updatedAt || 0)) return true;
    // Server says terminal but local still says active → server wins (status can't regress)
    if (server.taskPlan && TERMINAL_PLAN_STATUSES.has(server.taskPlan.status)
        && local.taskPlan && !TERMINAL_PLAN_STATUSES.has(local.taskPlan.status)) return true;
    return false;
}

export type ProcessingState = 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'awaiting_tool_response' | 'tool_throttling' | 'tool_limit_reached' | 'model_thinking' | 'error';

interface ConversationProcessingState {
    state: ProcessingState;
    toolExecutionCount?: number;
    throttlingDelay?: number;
    lastToolName?: string;
    lastUpdated: number;
}

interface ChatContext {
    streamedContentMap: Map<string, string>;
    reasoningContentMap: Map<string, string>;
    dynamicTitleLength: number;
    lastResponseIncomplete: boolean;
    setDynamicTitleLength: (length: number) => void;
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>;
    setReasoningContentMap: Dispatch<SetStateAction<Map<string, string>>>;
    isStreaming: boolean;
    getProcessingState: (conversationId: string) => ProcessingState;
    updateProcessingState: (conversationId: string, state: ProcessingState) => void;
    isStreamingAny: boolean;
    setIsStreaming: Dispatch<SetStateAction<boolean>>;
    setConversations: Dispatch<SetStateAction<Conversation[]>>;
    conversations: Conversation[];
    isLoadingConversation: boolean;
    isProjectSwitching: boolean;
    currentConversationId: string;
    streamingConversations: Set<string>;
    addStreamingConversation: (id: string) => void;
    removeStreamingConversation: (id: string) => void;
    setCurrentConversationId: (id: string) => void;
    addMessageToConversation: (message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => void;
    currentMessages: Message[];
    loadConversation: (id: string) => void;
    startNewChat: (specificFolderId?: string | null) => Promise<void>;
    loadConversationAndScrollToMessage: (conversationId: string, messageIndex: number) => Promise<void>;
    isTopToBottom: boolean;
    dbError: string | null;
    setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
    scrollToBottom: () => void;
    userHasScrolled: boolean;
    setUserHasScrolled: Dispatch<SetStateAction<boolean>>;
    recordManualScroll: () => void;
    folders: ConversationFolder[];
    setFolders: Dispatch<SetStateAction<ConversationFolder[]>>;
    currentFolderId: string | null;
    setCurrentFolderId: (id: string | null) => void;
    createFolder: (name: string, parentId?: string | null) => Promise<string>;
    moveConversationToFolder: (conversationId: string, folderId: string | null) => Promise<void>;
    updateFolder: (folder: ConversationFolder) => Promise<void>;
    folderFileSelections: Map<string, string[]>;
    setFolderFileSelections: Dispatch<SetStateAction<Map<string, string[]>>>;
    deleteFolder: (id: string) => Promise<void>;
    setDisplayMode: (conversationId: string, mode: 'raw' | 'pretty') => void;
    toggleMessageMute: (conversationId: string, messageIndex: number) => void;
    editingMessageIndex: number | null;
    setEditingMessageIndex: (index: number | null) => void;
    throttlingRecoveryData: Map<string, { toolResults?: any[]; partialContent?: string }>;
    setThrottlingRecoveryData: (data: Map<string, any>) => void;
    moveChatToGroup: (chatId: string, groupId: string | null) => Promise<void>;
    toggleConversationGlobal: (conversationId: string) => Promise<void>;
    moveConversationToProject: (conversationId: string, targetProjectId: string) => Promise<void>;
    moveFolderToProject: (folderId: string, targetProjectId: string) => Promise<void>;
    toggleFolderGlobal: (folderId: string) => Promise<void>;
    setChatContexts: (chatId: string, contextIds: string[], skillIds: string[], additionalFiles: string[], additionalPrompt: string | null) => Promise<void>;
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

export function ChatProvider({ children }: ChatProviderProps) {
    const renderStart = useRef(performance.now());
    const { isDarkMode } = useTheme();
    const { isEphemeralMode } = useConfig();
    const { currentProject } = useProject();
    const { isServerReachable } = useServerStatus();
    const renderCount = useRef(0);
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamedContentMap, setStreamedContentMap] = useState(() => new Map<string, string>());
    const [reasoningContentMap, setReasoningContentMap] = useState(() => new Map<string, string>());
    const [isTopToBottom, setIsTopToBottom] = useState<boolean>(() => {
        try { return JSON.parse(localStorage.getItem('ZIYA_TOP_DOWN_MODE') || 'true'); } catch { return true; }
    });
    const [isStreamingAny, setIsStreamingAny] = useState(false);
    const [processingStates, setProcessingStates] = useState(() => new Map<string, ConversationProcessingState>());
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [streamingConversations, setStreamingConversations] = useState<Set<string>>(() => new Set());
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [isProjectSwitching, setIsProjectSwitching] = useState(false);
    const [isInitialized, setIsInitialized] = useState(false);
    const [userHasScrolled, setUserHasScrolled] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>('');
    const [currentMessages, setCurrentMessages] = useState<Message[]>([]);
    const currentConversationRef = useRef<string>('');
    const conversationIdRestored = useRef(false);

    // ── Per-project conversation ID persistence ─────────────────────
    // Store the last-active conversation per project so switching back
    // to a project restores the user's previous position.
    const _projectConvKey = (pid: string) => `ZIYA_PROJECT_CONV_${pid}`;

    const saveProjectConversationId = useCallback((projectId: string, conversationId: string) => {
        if (!projectId || !conversationId) return;
        try { localStorage.setItem(_projectConvKey(projectId), conversationId); }
        catch { /* quota exceeded — non-fatal */ }
    }, []);

    const loadProjectConversationId = useCallback((projectId: string): string | null => {
        if (!projectId) return null;
        try { return localStorage.getItem(_projectConvKey(projectId)); }
        catch { return null; }
    }, []);

    // Restore conversation ID from localStorage ONLY if not in ephemeral mode
    // This must run AFTER config is loaded AND after conversations are initialized
    // CRITICAL: Only run ONCE to prevent overwriting user's current conversation
    useEffect(() => {
        if (!isEphemeralMode && isInitialized && conversations.length > 0 && !conversationIdRestored.current && !currentConversationId) {
            conversationIdRestored.current = true; // Mark as restored to prevent re-running
            try {
                const savedCurrentId = getTabState('ZIYA_CURRENT_CONVERSATION_ID');
                if (savedCurrentId) {
                    // CRITICAL: Only restore if the conversation actually exists
                    const conversationExists = conversations.some(c => c.id === savedCurrentId);
                    if (conversationExists) {
                        console.log('🔄 RESTORED: Last active conversation ID:', savedCurrentId);
                        setCurrentConversationId(savedCurrentId);
                    } else {
                        console.warn('⚠️ Saved conversation ID not found in loaded conversations:', savedCurrentId);
                        // Use the most recent conversation instead
                        const mostRecent = conversations.reduce((a, b) =>
                            (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                        );
                        setCurrentConversationId(mostRecent.id);
                    }
                } else if (conversations.length > 0) {
                    // No saved ID, use most recent
                    const mostRecent = conversations.reduce((a, b) =>
                        (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                    );
                    setCurrentConversationId(mostRecent.id);
                }
            } catch (e) {
                console.warn('Failed to restore current conversation ID:', e);
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isEphemeralMode, isInitialized, conversations.length]);

    // Persist top-down mode preference
    useEffect(() => {
        localStorage.setItem('ZIYA_TOP_DOWN_MODE', JSON.stringify(isTopToBottom));
    }, [isTopToBottom]);
    const initializationStarted = useRef(false);
    const [folders, setFolders] = useState<ConversationFolder[]>([]);
    const [dbError, setDbError] = useState<string | null>(null);
    const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
    const folderRef = useRef<string | null>(null);
    const [folderFileSelections, setFolderFileSelections] = useState<Map<string, string[]>>(new Map());
    const [dynamicTitleLength, setDynamicTitleLength] = useState<number>(50); // Default reasonable length
    const processedModelChanges = useRef<Set<string>>(new Set());
    const saveQueue = useRef<Promise<void>>(Promise.resolve());
    const otherProjectConvsCache = useRef<{convs: any[], timestamp: number}>({convs: [], timestamp: 0});  // BUGFIX: Cache other-project convos to avoid reading ALL from DB on every save

    const KNOWN_SERVER_IDS_MAX = 5000; // Cap to prevent unbounded growth during idle polling

    const [lastResponseIncomplete, setLastResponseIncomplete] = useState<boolean>(false);
    const isRecovering = useRef<boolean>(false);
    const lastRecoveryAttempt = useRef<number>(0);
    const RECOVERY_COOLDOWN = 5000; // 5 seconds between recovery attempts
    const recoveryInProgress = useRef<boolean>(false);
    const consecutiveRecoveries = useRef<number>(0);
    const MAX_CONSECUTIVE_RECOVERIES = 3;
    const messageUpdateCount = useRef(0);
    const conversationsRef = useRef(conversations);
    const streamingConversationsRef = useRef(streamingConversations);

    const removedStreamingIds = useRef<Set<string>>(new Set());
    // Track which project has been server-synced to avoid duplicate syncs
    const serverSyncedForProject = useRef<string | null>(null);
    // Conversations confirmed present on the server (used to distinguish imports from server-deletions)
    const knownServerConversationIds = useRef<Set<string>>(new Set());
    const dirtyConversationIds = useRef<Set<string>>(new Set());
    const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
    const lastManualScrollTime = useRef<number>(0);
    const manualScrollCooldownActive = useRef<boolean>(false);
    const [throttlingRecoveryData, setThrottlingRecoveryData] = useState<Map<string, { toolResults?: any[]; partialContent?: string }>>(new Map());

    // CRITICAL: Track scroll state per conversation to prevent cross-conversation interference

    const conversationScrollStates = useRef<Map<string, {
        userScrolledAway: boolean;
        lastManualScrollTime: number;
        isAtEnd: boolean;
    }>>(new Map());

    // Improved scrollToBottom function with better user scroll respect
    const scrollToBottom = useCallback(() => {
        const chatContainer = document.querySelector('.chat-container');
        if (!chatContainer) return;

        // Get or create scroll state for current conversation
        if (!conversationScrollStates.current.has(currentConversationId)) {
            conversationScrollStates.current.set(currentConversationId, {
                userScrolledAway: false,
                lastManualScrollTime: 0,
                isAtEnd: true
            });
        }

        const scrollState = conversationScrollStates.current.get(currentConversationId)!;

        // STEP 1: Only proceed if the current conversation is the one that's streaming
        const currentConversationStreaming = streamingConversations.has(currentConversationId);

        if (!currentConversationStreaming) {
            return; // Absolutely no scroll changes if not streaming current conversation
        }

        // STEP 2: Check for actual content (not just spinner)
        const streamedContent = streamedContentMap.get(currentConversationId) || '';
        const hasContent = streamedContent.trim().length > 0;

        if (!hasContent) {
            console.log('📜 Autoscroll blocked - no actual content yet (spinner phase)');
            return;
        }

        // STEP 3: Respect if user has scrolled away from end
        const now = Date.now();
        if (scrollState.userScrolledAway) {
            const timeSinceScroll = now - scrollState.lastManualScrollTime;
            const COOLDOWN = 5000;

            if (timeSinceScroll < COOLDOWN) {
                return; // User scrolled away, respect their choice
            }

            // Cooldown expired - check if user returned to end
            const scrollTop = chatContainer.scrollTop;
            const scrollHeight = chatContainer.scrollHeight;
            const clientHeight = chatContainer.clientHeight;

            const isAtEnd = isTopToBottom ?
                (scrollHeight - scrollTop - clientHeight) < 50 :
                scrollTop < 50;

            if (!isAtEnd) {
                return; // User still away from end
            }

            // User returned to end
            scrollState.userScrolledAway = false;
            scrollState.isAtEnd = true;
        }

        // STEP 4: Check if user is currently at the end
        const scrollTop = chatContainer.scrollTop;
        const scrollHeight = chatContainer.scrollHeight;
        const clientHeight = chatContainer.clientHeight;

        const isCurrentlyAtEnd = isTopToBottom ?
            (scrollHeight - scrollTop - clientHeight) < 50 :
            scrollTop < 50;

        if (!isCurrentlyAtEnd) {
            return; // User not at end, don't scroll
        }

        // STEP 5: Maintain position at end
        const targetScroll = isTopToBottom ?
            scrollHeight - clientHeight :
            0;

        chatContainer.scrollTop = targetScroll;
        scrollState.isAtEnd = true;
    }, [streamingConversations, currentConversationId, isTopToBottom]);
    // Function to record manual scroll events
    const recordManualScroll = useCallback(() => {
        // Update scroll state for CURRENT conversation only
        if (!conversationScrollStates.current.has(currentConversationId)) {
            conversationScrollStates.current.set(currentConversationId, {
                userScrolledAway: true,
                lastManualScrollTime: Date.now(),
                isAtEnd: false
            });
        } else {
            const scrollState = conversationScrollStates.current.get(currentConversationId)!;
            scrollState.userScrolledAway = true;
            scrollState.lastManualScrollTime = Date.now();
            scrollState.isAtEnd = false;
        }

        // Keep global state for backward compatibility
        lastManualScrollTime.current = Date.now();
        manualScrollCooldownActive.current = true;
        setUserHasScrolled(true);
    }, [currentConversationId]);

    // Clean up scroll state when conversations are deleted
    useEffect(() => {
        const activeIds = new Set(conversations.map(c => c.id));
        conversationScrollStates.current.forEach((_, id) => {
            if (!activeIds.has(id)) {
                conversationScrollStates.current.delete(id);
            }
        });
    }, [conversations]);

    useEffect(() => {
        conversationsRef.current = conversations;
        streamingConversationsRef.current = streamingConversations;
    }, [conversations, streamingConversations]);

    const updateProcessingState = useCallback((conversationId: string, state: ProcessingState) => {
        setProcessingStates(prev => {
            const next = new Map(prev);
            next.set(conversationId, {
                state,
                lastUpdated: Date.now()
            });
            return next;
        });
    }, []);

    const addStreamingConversation = useCallback((id: string) => {
        setStreamingConversations(prev => {
            const next = new Set(prev);
            // Update local streaming state
            console.log('Adding to streaming set:', { id, currentSet: Array.from(prev) });
            next.add(id);
            setStreamedContentMap(prev => new Map(prev).set(id, ''));
            setIsStreaming(true);
            setIsStreamingAny(true);
            return next;
        });
        updateProcessingState(id, 'sending');
        // Relay to other same-project tabs so they can show streaming UI
        projectSync.post('streaming-state', { conversationId: id, state: 'sending' });
    }, [updateProcessingState]);

    const removeStreamingConversation = useCallback((id: string) => {
        // CRITICAL: Check if this is the CURRENT conversation
        const isCurrentConv = id === currentConversationId;
        
        // Guard against broadcast loops: each streaming-ended broadcast triggers
        // listeners which call this function again. Use a ref to dedup.
        if (removedStreamingIds.current.has(id)) {
            return;
        }
        removedStreamingIds.current.add(id);
        setTimeout(() => removedStreamingIds.current.delete(id), 2000);

        // Check ref to prevent processing if already removed
        const wasStreaming = streamingConversationsRef.current.has(id);
        if (!wasStreaming) {
            return;  // Already removed or never was streaming - skip
        }

        setStreamingConversations(prev => {
            const next = new Set(prev);
            // Preserve scroll for non-current conversations
            if (!isCurrentConv) {
                console.log('📌 Background conversation finished - NO scroll changes:', id.substring(0, 8));
            }

            next.delete(id);

            // Update global streaming state based on remaining conversations
            const stillStreaming = next.size > 0;
            setIsStreaming(stillStreaming);
            setIsStreamingAny(stillStreaming);

            return next;
        });

        setStreamedContentMap(prev => {
            const next = new Map(prev);
            next.delete(id);
            return next;
        });

        setReasoningContentMap(prev => {
            const next = new Map(prev);
            next.delete(id);
            return next;
        });

        // Auto-reset processing state when streaming ends
        setProcessingStates(prev => {
            const next = new Map(prev);
            next.delete(id);
            return next;
        });
        // Relay to other same-project tabs so they can stop showing streaming UI
        projectSync.post('streaming-ended', { conversationId: id });
    }, [currentConversationId]);

    const getProcessingState = useCallback((conversationId: string): ProcessingState => {
        return processingStates.get(conversationId)?.state || 'idle';
    }, [processingStates]);

    // Queue-based save system to prevent race conditions
    const queueSave = useCallback(async (conversations: Conversation[], options: {
        skipValidation?: boolean;
        retryCount?: number;
        isRecoveryAttempt?: boolean;
        changedIds?: string[];
    } = {}) => {
        // Skip all persistence in ephemeral mode
        if (isEphemeralMode) {
            console.debug('📝 EPHEMERAL: Skipping save (ephemeral mode)');
            return Promise.resolve();
        }

        // GUARD: Never persist shell data — shells have only first+last messages
        // and would destroy full conversation history if written to IndexedDB.
        const hasShellData = conversations.some(c => (c as any)._isShell);
        if (hasShellData && (!options.changedIds || options.changedIds.length === 0)) {
            console.warn('📝 SHELL_GUARD: Blocking save — React state contains shell conversations (messages stripped). Waiting for full data to load.');
            return Promise.resolve();
        }

        // Skip saves during the first 5 seconds after init to let all data settle.
        // Shells → server sync → lazy-load → folders all trigger setConversations
        // in rapid succession; saving each intermediate state is wasted I/O.
        const initTime = (window as any).__ziyaInitTime || 0;
        if (initTime > 0 && Date.now() - initTime < 5000) {
            console.debug('📝 INIT_GUARD: Skipping save during startup settle window');
            return Promise.resolve();
        }

        // CRITICAL FIX: Filter out corrupted conversations before any processing
        const validConversations = conversations.filter(conv => {
            const isValid = conv &&
                conv.id &&
                typeof conv.id === 'string' &&
                conv.title != null && conv.title !== '' &&
                Array.isArray(conv.messages);

            if (!isValid) {
                console.warn('🧹 FILTERED CORRUPTED CONVERSATION:', { id: conv?.id, title: conv?.title, hasMessages: Array.isArray(conv?.messages) });
            }

            return isValid;
        });

        // VALIDATION: Ensure all conversations have explicit isActive values
        let validatedConversations = validConversations.map(conv => ({
            ...conv,
            isActive: conv.isActive !== false ? true : false, // Normalize to explicit boolean
            _version: conv._version || Date.now() // Ensure version is set
        }));

        // Track which conversations are dirty for server sync
        if (options.changedIds) {
            options.changedIds.forEach(id => dirtyConversationIds.current.add(id));
        }

        saveQueue.current = saveQueue.current.then(async () => {
            const { skipValidation = false, retryCount = 0, isRecoveryAttempt = false } = options;
            const maxRetries = 3;

            // Pre-save validation
            const activeCount = validatedConversations.filter(c => c.isActive).length;
            console.debug(`Saving ${validatedConversations.length} conversations (${activeCount} active)`);

            const changedIdSet = new Set(options.changedIds || []);

            // Use cached other-project conversations when possible, BUT
            // never substitute React state for IndexedDB when state contains
            // shell conversations (messages stripped to first+last only).
            // Writing shells back to IndexedDB would destroy full message data.
            const CACHE_TTL_MS = 60_000;
            let allDbConversations: Conversation[];
            const reactStateHasShells = validatedConversations.some(c => (c as any)._isShell);
            if (reactStateHasShells) {
                // CRITICAL: React state has shell data — must read full data from IndexedDB
                console.debug('📝 SHELL_GUARD: Fast-path disabled — React state has shells, reading full data from IndexedDB');
                allDbConversations = await db.getConversations();
                const pid = currentProject?.id;
                if (pid) {
                    otherProjectConvsCache.current = {
                        // Strip message bodies — only metadata is needed for merge logic.
                        // Full messages were the #1 memory leak during idle polling.
                        convs: allDbConversations
                            .filter(c => c.projectId !== pid)
                            .filter(c => !c.isGlobal)
                            .map(c => ({ ...c, messages: [] })),
                        timestamp: Date.now()
                    };
                }
            } else if (changedIdSet.size > 0 &&
                otherProjectConvsCache.current.timestamp > 0 &&
                Date.now() - otherProjectConvsCache.current.timestamp < CACHE_TTL_MS) {
                allDbConversations = [...otherProjectConvsCache.current.convs, ...validatedConversations];
                console.debug(`Save (fast path): reused ${otherProjectConvsCache.current.convs.length} cached other-project convos`);
            } else {
                allDbConversations = await db.getConversations();
                const pid = currentProject?.id;
                if (pid) {
                    otherProjectConvsCache.current = {
                        // Strip message bodies — only metadata is needed for merge logic.
                        convs: allDbConversations
                            .filter(c => c.projectId !== pid)
                            .filter(c => !c.isGlobal)
                            .map(c => ({ ...c, messages: [] })),
                        timestamp: Date.now()
                    };
                }
            }
            
            // Build merged list: start with DB as base, overlay our changes
            const mergedMap = new Map<string, Conversation>();
            
            // 1. Load everything from DB (preserves other tabs' writes)
            allDbConversations.forEach(c => mergedMap.set(c.id, c));
            
            // 2. Overlay ONLY the conversations this tab actually changed
            //    (identified by changedIds). For these, our version wins.
            if (changedIdSet.size > 0) {
                validatedConversations
                    .filter(c => changedIdSet.has(c.id))
                    .forEach(c => mergedMap.set(c.id, c));
            } else {
                // No changedIds specified — legacy call, overlay all from this tab's memory.
                // Use _version to pick the newer copy of each conversation.
                validatedConversations.forEach(c => {
                    const existing = mergedMap.get(c.id);
                    if (!existing || (c._version || 0) >= (existing._version || 0)) {
                        mergedMap.set(c.id, c);
                    }
                });
            }
            
            const finalConversations = Array.from(mergedMap.values());

            // Save all conversations - but don't throw if it fails
            try {
                await db.saveConversations(finalConversations);
                // Notify other same-project tabs about the change
                if (options.changedIds && options.changedIds.length > 0) {
                    projectSync.post('conversations-changed', { ids: options.changedIds });
                }
            } catch (saveError) {
                // Log but don't throw - let the app continue functioning
                console.error('❌ Database save failed:', saveError);
                // If quota exceeded, we could try to prune old data here
                // For now, just continue - the data is in React state
                return; // Exit early, skip validation
            }

            // DUAL-WRITE: Also sync changed conversations to server (non-blocking)
            if (currentProject?.id) {
                const dirty = dirtyConversationIds.current;
                if (dirty.size > 0) {
                    // Include ALL dirty conversations, not just current project's.
                    // Moved conversations have a different projectId but still need syncing.
                    const dirtyConvs = finalConversations.filter(
                        c => dirty.has(c.id) && c.isActive !== false
                    );
                    dirtyConversationIds.current = new Set();
                    Promise.resolve().then(async () => {
                        try {
                            // Group dirty conversations by their projectId
                            // so each gets synced to the correct server directory
                            const byProject = new Map<string, any[]>();
                            dirtyConvs.forEach(c => {
                                const pid = c.projectId || currentProject.id;
                                if (!byProject.has(pid)) byProject.set(pid, []);
                                byProject.get(pid)!.push(c);
                            });
                            
                            for (const [pid, convs] of byProject) {
                                const chatsToSync = convs.map(c =>
                                    syncApi.conversationToServerChat(c, pid)
                                );
                                await syncApi.bulkSync(pid, chatsToSync);
                                console.log(`📡 DUAL_WRITE: Synced ${chatsToSync.length} conversations to project ${pid}`);
                            }
                        } catch (e) {
                            console.warn('📡 DUAL_WRITE: Server sync failed (non-fatal):', e);
                        }
                    });
                }
            }
        });
        return saveQueue.current;
    }, [isEphemeralMode, currentProject?.id]);

    // Helper function to merge conversations during healing
    const mergeConversationsForHealing = useCallback((expected: Conversation[], actual: Conversation[]) => {
        const merged = new Map<string, Conversation>();

        // Start with actual conversations from database
        actual.forEach(conv => merged.set(conv.id, conv));

        // Add or update with expected conversations, preserving database versions when possible
        expected.forEach(expectedConv => {
            const actualConv = merged.get(expectedConv.id);

            // Validate conversation has essential data before adding
            const isValidConversation = (
                expectedConv.id &&
                expectedConv.messages &&
                Array.isArray(expectedConv.messages) &&
                expectedConv.title
            );

            if (!isValidConversation) {
                console.warn(`🚫 HEALING: Skipping invalid conversation ${expectedConv.id?.substring(0, 8)}`);
                return;
            }

            if (!actualConv) {
                // New conversation - add it
                // CRITICAL: Additional validation - don't add if it's a duplicate of an existing conversation
                const isDuplicate = Array.from(merged.values()).some(existingConv =>
                    existingConv.title === expectedConv.title &&
                    existingConv.messages.length === expectedConv.messages.length &&
                    Math.abs((existingConv.lastAccessedAt || 0) - (expectedConv.lastAccessedAt || 0)) < 5000
                );

                if (!isDuplicate) {
                    merged.set(expectedConv.id, expectedConv);
                    console.log(`🔄 HEALING: Adding validated conversation ${expectedConv.id.substring(0, 8)}`);
                } else {
                    console.log(`🚫 HEALING: Skipping duplicate conversation ${expectedConv.id.substring(0, 8)}`);
                }
            } else {
                // Existing conversation - merge with preference for newer version
                const expectedVersion = expectedConv._version || 0;
                const actualVersion = actualConv._version || 0;

                // Only merge if expected version is actually newer
                if (expectedVersion <= actualVersion) {
                    return; // Skip merge if database version is newer or equal
                }

                const mergedConv = {
                    ...actualConv,
                    ...expectedConv,
                    _version: Math.max(actualConv._version || 0, expectedConv._version || 0),
                    // Preserve important state from actual (database) version
                    isActive: expectedConv.isActive !== undefined ? expectedConv.isActive : actualConv.isActive
                };
                merged.set(expectedConv.id, mergedConv);
            }
        });

        return Array.from(merged.values());
    }, []);

    const addMessageToConversation = useCallback((message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => {
        // CRITICAL: Always use targetConversationId - never fall back to currentConversationId
        // This prevents responses from being routed to the wrong conversation when user switches mid-stream
        const conversationId = targetConversationId;
        if (!conversationId) {
            console.error('❌ addMessageToConversation called without targetConversationId');
            return;
        }

        // If adding message to non-current conversation, don't trigger any scroll
        if (conversationId !== currentConversationId) {
            console.log('📝 Adding message to non-current conversation - scroll preservation mode');
        }

        // dynamicTitleLength from state - updated only by UI components
        // Debug logging to see when messages are added
        console.log('📝 Adding message:', { role: message.role, conversationId: targetConversationId, titleLength: dynamicTitleLength });

        // Check if this is an assistant message and if it appears incomplete
        if (message.role === 'assistant' && message.content) {
            setLastResponseIncomplete(detectIncompleteResponse(message.content));
        }

        messageUpdateCount.current += 1;
        setConversations(prevConversations => {
            const existingConversation = prevConversations.find(c => c.id === conversationId);

            // SHELL_GUARD: If the conversation is still a shell (only first+last
            // messages loaded), appending a new message would destroy all
            // intermediate history.  Attempt a synchronous recovery from IDB.
            if (existingConversation && (existingConversation as any)._isShell) {
                const fullCount = (existingConversation as any)._fullMessageCount || 0;
                if (fullCount > existingConversation.messages.length) {
                    console.error(
                        `🚨 SHELL_GUARD: addMessage called on shell conversation ${conversationId.substring(0, 8)} ` +
                        `(has ${existingConversation.messages.length} messages, full count ${fullCount}). ` +
                        `Queueing lazy-load before message append.`
                    );
                    // Fire-and-forget: load full messages then re-add this message
                    db.getConversations().then(allFull => {
                        const full = allFull.find(c => c.id === conversationId);
                        if (full?.messages?.length > existingConversation.messages.length) {
                            setConversations(prev => prev.map(c =>
                                c.id === conversationId
                                    ? { ...c, messages: [...full.messages, message], _isShell: false, _fullMessageCount: undefined, _version: Date.now() }
                                    : c
                            ));
                        }
                    }).catch(e => console.error('Shell recovery failed:', e));
                    // Return unchanged for now — the async recovery will apply
                    return prevConversations;
                }
            }

            const isFirstMessage = existingConversation?.messages.length === 0;

            // CRITICAL FIX: Determine if this is a non-current conversation dynamically
            // Don't trust the caller's isNonCurrentConversation - compute it from current state
            // This handles concurrent conversations and user switching mid-stream
            // Use the REF (not closed-over state) so we always get the live value,
            // even when this callback was captured before the user switched conversations.
            const actuallyNonCurrent = conversationId !== currentConversationRef.current;

            console.log('Message processing:', {
                messageRole: message.role,
                targetConversationId: conversationId,
                currentConversationId,
                isNonCurrentConversation: actuallyNonCurrent
            });
            const shouldMarkUnread = message.role === 'assistant' && actuallyNonCurrent;
            console.log('Message add check:', {
                willMarkUnread: shouldMarkUnread,
                reason: shouldMarkUnread ? 'AI message to non-current conversation' : 'Not marking unread'
            });
            const updatedConversations = existingConversation
                ? prevConversations.map(conv => {
                    if (conv.id === conversationId) {
                        return {
                            ...conv,
                            messages: [...conv.messages, message],
                            hasUnreadResponse: shouldMarkUnread,
                            lastAccessedAt: Date.now(),
                            _version: Date.now(),
                            // Preserve existing folderId — never overwrite on message add.
                            // (Fixes bug where viewing a swarm delegate re-rooted new conversations.)
                            title: isFirstMessage && message.role === 'human' ? message.content.slice(0, dynamicTitleLength) + (message.content.length > dynamicTitleLength ? '...' : '') : conv.title
                        };
                    }
                    // BUGFIX: Return same reference for unchanged conversations to prevent unnecessary re-renders
                    return conv;
                })
                : [...prevConversations, {
                    id: conversationId,
                    title: message.role === 'human'
                        ? message.content.slice(0, dynamicTitleLength) + (message.content.length > dynamicTitleLength ? '...' : '')
                        : 'New Conversation',
                    projectId: currentProject?.id,
                    messages: [message],
                    // Use currentFolderId for brand-new inline conversations, but
                    // never auto-place inside TaskPlan (swarm) folders.
                    folderId: (currentFolderId && folders.find(f => f.id === currentFolderId)?.taskPlan) ? null : currentFolderId,
                    lastAccessedAt: Date.now(),
                    isActive: true, // Explicitly set to true
                    _version: Date.now(),
                    hasUnreadResponse: false
                }];

            console.log('After update:', {
                updatedConversation: updatedConversations.find(c => c.id === conversationId),
                allConversations: updatedConversations.map(c => ({
                    id: c.id,
                    hasUnreadResponse: c.hasUnreadResponse,
                    isCurrent: c.id === currentConversationId
                }))
            });

            queueSave(updatedConversations, { changedIds: [conversationId] }).catch(console.error);

            return updatedConversations;
        });
    }, [currentConversationId, currentFolderId, dynamicTitleLength, queueSave, currentProject?.id, folders]);

    // T28: Poll for delegate status changes when TaskPlan folders are active
    useDelegatePolling(currentProject?.id, folders, setConversations, setFolders);

    // T28b: Live WebSocket streaming for delegate conversations
    useDelegateStreaming({
        conversationId: currentConversationId,
        conversations,
        streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
        setStreamedContentMap,
        addMessageToConversation,
    });

    // Add a function to handle model change notifications
    const handleModelChange = useCallback((event: CustomEvent) => {
        const { previousModel, newModel, modelId, previousModelId } = event.detail;
        if (!previousModel || !newModel) return; // Skip invalid model changes

        console.log('ChatContext received modelChanged event:', {
            previousModel,
            newModel,
            modelId,
            currentConversationId
        });

        // Create a unique key for this model change to prevent duplicates
        const changeKey = `${previousModel}->${newModel}`;

        // Skip if we've already processed this exact change
        if (processedModelChanges.current.has(changeKey)) {
            console.log('Skipping duplicate model change:', changeKey);
            return;
        }

        // Only add model change message if we have a valid conversation
        if (currentConversationId) {
            // Explicitly type the modelChangeMessage as Message
            const modelChangeMessage: Message = {
                id: uuidv4(),
                role: 'system' as const,
                content: `Model changed from ${previousModel} to ${newModel}`,
                _timestamp: Date.now(),
                modelChange: {
                    from: previousModel,
                    to: newModel,
                    changeKey: changeKey
                }
            };

            console.log('Adding model change system message:', modelChangeMessage);

            // Add the message to the conversation
            const existingConversation = conversations.find(c => c.id === currentConversationId);
            console.log('Current conversation state:', {
                conversationId: currentConversationId,
                hasConversation: !!existingConversation,
                messageCount: existingConversation?.messages.length || 0
            });

            setConversations((prevConversations) => {
                const updatedConversations = prevConversations.map(conv => {
                    if (conv.id === currentConversationId) {
                        return { ...conv, messages: [...conv.messages, modelChangeMessage] };
                    }
                    return conv;
                });

                // Log the updated conversation
                const updatedConv = updatedConversations.find(c => c.id === currentConversationId);
                console.log('Updated conversation with model change message:', {
                    messageCount: updatedConv?.messages.length || 0
                });
                return updatedConversations;
            });

            // Mark this change as processed
            processedModelChanges.current.add(changeKey);
        }
    }, [currentConversationId]);

    // Refs to break forward-reference TDZ: startNewChat calls attemptDatabaseRecovery
    // and initializeWithRecovery, which are declared later in the file. Accessing a
    // const before its declaration crashes with "Cannot access before initialization".
    const attemptDatabaseRecoveryRef = useRef<() => Promise<void>>(() => Promise.resolve());
    const initializeWithRecoveryRef = useRef<() => Promise<void>>(() => Promise.resolve());

    const startNewChat = useCallback(async (specificFolderId?: string | null) => {
        // Only attempt recovery if not in cooldown period
        const now = Date.now();
        const timeSinceLastRecovery = now - lastRecoveryAttempt.current;

        // Skip recovery if:
        // 1. Already recovering
        // 2. Within cooldown period
        // 3. Conversations exist and appear healthy
        const shouldSkipRecovery = (
            recoveryInProgress.current ||
            timeSinceLastRecovery < RECOVERY_COOLDOWN ||
            (conversations.length > 0 && conversations.length === conversationsRef.current.length)
        );

        try {
            if (!shouldSkipRecovery) {
                await Promise.race([
                    attemptDatabaseRecoveryRef.current(),
                    new Promise((_, rej) => setTimeout(() => rej(new Error('Recovery timeout')), 5000))
                ]);
            }
        } catch (recoveryError) {
            console.warn('Database recovery attempt failed or timed out, continuing anyway:', recoveryError);
        }

        if (!isInitialized) {
            console.warn('⚠️ NEW CHAT: Context not initialized, attempting initialization...');
            try {
                await Promise.race([
                    initializeWithRecoveryRef.current(),
                    new Promise((_, rej) => setTimeout(() => rej(new Error('Init timeout')), 5000))
                ]);
                // Give initialization a moment to complete
                await new Promise(resolve => setTimeout(resolve, 200));

                if (!isInitialized) {
                    console.warn('⚠️ NEW CHAT: Proceeding with degraded mode (no IndexedDB persistence)');
                    setIsInitialized(true);
                }
            } catch (initError) {
                console.error('❌ NEW CHAT: Initialization failed, proceeding in localStorage-only mode:', initError);
                setIsInitialized(true);
            }
        }
        try {
            const newId = uuidv4();

            // Use the provided folder ID if available, otherwise use the current folder ID
            let targetFolderId = specificFolderId !== undefined ? specificFolderId : currentFolderId;

            // Prevent creating regular conversations inside TaskPlan folders —
            // those folders are managed exclusively by the delegate system.
            if (targetFolderId) {
                const folder = folders.find(f => f.id === targetFolderId);
                if (folder?.taskPlan) targetFolderId = null;
            }

            const newConversation: Conversation = {
                id: newId,
                title: 'New Conversation',
                projectId: currentProject?.id,
                messages: [],
                folderId: targetFolderId,
                lastAccessedAt: Date.now(),
                isActive: true,
                _version: Date.now(),
                hasUnreadResponse: false
            };

            // Clear unread flag from current conversation before creating new one
            const updatedConversations = conversations.map(conv =>
                conv.id === currentConversationId
                    ? { ...conv, hasUnreadResponse: false }
                    : conv
            );

            try {
                try {
                    await queueSave([...updatedConversations, newConversation], { changedIds: [newConversation.id] });
                } catch (saveError) {
                    console.warn('⚠️ NEW CHAT: Save failed, continuing anyway:', saveError);
                    // Server dual-write will pick up the new conversation
                    // on the next bulkSync cycle. No localStorage fallback needed.
                }

                setConversations([...updatedConversations, newConversation]);
                setCurrentMessages([]);
                setCurrentConversationId(newId);

                try {
                    setTabState('ZIYA_CURRENT_CONVERSATION_ID', newId);
                } catch (e) {
                    console.warn('Failed to persist conversation ID:', e);
                }

            } catch (saveError) {
                console.error('Failed to save new conversation, creating in memory:', saveError);
                setConversations([...updatedConversations, newConversation]);
                setCurrentMessages([]);
                setCurrentConversationId(newId);
                try {
                    setTabState('ZIYA_CURRENT_CONVERSATION_ID', newId);
                } catch (e) { /* ignore */ }

                setTimeout(async () => {
                    try {
                        await queueSave([...updatedConversations, newConversation], { skipValidation: true, changedIds: [newConversation.id] });
                    } catch (e) {
                        console.warn('Background save retry failed:', e);
                    }
                }, 2000);
            }
        } catch (error) {
            console.error('Failed to create new conversation:', error);
        }
    }, [isInitialized, currentConversationId, currentFolderId, conversations, folders, queueSave, currentProject?.id]);
    // Recovery function to fix database sync issues
    const attemptDatabaseRecovery = useCallback(async () => {
        // Circuit breaker: Stop recovery if too many consecutive attempts
        if (consecutiveRecoveries.current >= MAX_CONSECUTIVE_RECOVERIES) {
            console.warn('🚨 RECOVERY: Circuit breaker activated - too many consecutive recovery attempts');
            console.warn('🔧 RECOVERY: Manual intervention required - clear IndexedDB or localStorage');
            consecutiveRecoveries.current = 0; // Reset for future attempts
            return;
        }

        // Prevent concurrent recovery attempts
        if (recoveryInProgress.current) {
            console.log('🔄 RECOVERY: Already in progress, skipping');
            return;
        }

        recoveryInProgress.current = true;
        lastRecoveryAttempt.current = Date.now();

        try {
            console.log('🔄 RECOVERY: Attempting database recovery');

            // CRITICAL FIX: Get ALL conversations from database, not just current project's filtered state
            // The 'conversations' state is filtered by project, but the database has ALL projects
            const dbConversations = await db.getConversations();

            // For recovery purposes, we should compare the FULL database with itself
            // Memory state is project-filtered and should NOT be used for recovery
            // If we used the filtered state, we'd delete other projects' conversations!
            console.log('🔄 RECOVERY: Using database as source of truth, not filtered memory state');
            console.log(`📊 RECOVERY: Current project has ${conversations.length} conversations (filtered view)`);
            console.log(`📊 RECOVERY: Database has ${dbConversations.length} total conversations (all projects)`);

            // Recovery should not use filtered memory state - skip recovery during project view
            const memoryActive = conversations.filter(c => c.isActive !== false).length;
            const dbActive = dbConversations.filter(c => c.isActive !== false).length;

            // Only recover if there's a significant difference AND we can identify the cause
            // Don't recover for minor differences (1-2 conversations) as they may be transient
            const difference = Math.abs(memoryActive - dbActive);

            if (difference === 0) {
                console.log('✅ RECOVERY: States are in sync, no recovery needed');
                return;
            }

            // Don't blindly trust memory when it has significantly more conversations
            // This can happen due to phantom conversations from failed saves
            if (memoryActive > dbActive) {
                // If the difference is HUGE (>50%), memory is likely corrupted
                const percentDifference = ((memoryActive - dbActive) / dbActive) * 100;

                if (percentDifference > 50 && dbActive > 0) {
                    console.warn(`⚠️ RECOVERY: Memory has ${percentDifference.toFixed(0)}% more conversations than DB`);

                    // CRITICAL FIX: Check if current conversation is in DB before nuking memory
                    const currentConvInDB = dbConversations.find(c => c.id === currentConversationId);
                    const currentConvInMemory = conversations.find(c => c.id === currentConversationId);

                    if (!currentConvInDB && currentConvInMemory && currentConvInMemory.messages.length > 0) {
                        console.error(`🚨 RECOVERY BLOCKED: Current conversation ${currentConversationId.substring(0, 8)} not in DB but has ${currentConvInMemory.messages.length} messages!`);
                        console.log('🔄 RECOVERY: Saving current conversation to DB instead of deleting it');

                        // Save the current conversation to DB instead of deleting it
                        await db.saveConversations([...dbConversations, currentConvInMemory]);
                        console.log('✅ RECOVERY: Protected current conversation from deletion');
                        return;
                    }

                    console.log(`🔄 RECOVERY: Trusting database (${dbActive}) over memory (${memoryActive})`);

                    // Reload memory from database
                    setConversations(dbConversations);

                    // CRITICAL FIX: Never delete backups - they're the last line of defense!
                    // The backup will be naturally refreshed on next save cycle

                    console.log('✅ RECOVERY: Memory synced from database');
                } else {
                    // CRITICAL FIX: Never trust filtered memory state for saving
                    // Memory state is filtered by current project, saving it would delete other projects!
                    console.warn(`⚠️ RECOVERY BLOCKED: Memory state is project-filtered (${memoryActive} conversations)`);
                    console.warn(`⚠️ RECOVERY BLOCKED: Saving would delete ${dbActive - memoryActive} conversations from other projects`);
                    console.log('🔄 RECOVERY: Reloading memory from database instead');

                    // Reload current project's conversations from database
                    const projectConversations = dbConversations.filter(c => c.projectId === currentProject?.id);
                    setConversations(projectConversations);
                    console.log('✅ RECOVERY: Memory reloaded from database (project-filtered)');
                }
                return;
            }

            // If DB has MORE conversations, merge carefully
            if (dbActive > memoryActive && difference > 2) {
                console.log(`🔄 RECOVERY: Syncing conversation states (memory: ${memoryActive}, db: ${dbActive})`);

                // CRITICAL: Only merge conversations for the CURRENT project
                // Never use filtered memory state to overwrite the entire database
                console.log('🔄 RECOVERY: Trusting database, reloading filtered view');
                const projectConversations = dbConversations.filter(c => c.projectId === currentProject?.id);
                setConversations(projectConversations);
                console.log('✅ RECOVERY: Database sync completed');
                consecutiveRecoveries.current = 0; // Reset on successful recovery
            } else {
                consecutiveRecoveries.current++;
            }
        } catch (error) {
            console.warn('Database recovery failed:', error);
            consecutiveRecoveries.current++;
        } finally {
            recoveryInProgress.current = false;
        }
    }, [conversations, mergeConversationsForHealing]);

    // Sync ref after attemptDatabaseRecovery is initialized (avoids TDZ in startNewChat)
    attemptDatabaseRecoveryRef.current = attemptDatabaseRecovery;

    const loadConversation = useCallback(async (conversationId: string) => {
        setIsLoadingConversation(true);

        const convEntry = conversationsRef.current.find(c => c.id === conversationId);

        const isDelegate = conversationsRef.current.find(c => c.id === conversationId)?.delegateMeta;
        // Only scroll if we're actually switching conversations
        const isActualSwitch = conversationId !== currentConversationId;

        try {

            console.log('🔄 Loading conversation:', conversationId, 'isActualSwitch:', isActualSwitch);

            console.log('🔄 Loading conversation:', conversationId);

            // Lazy-load messages for conversations that only have summary
            // metadata (e.g. after SERVER_SYNC with empty/corrupt IDB) or
            // shell data (first+last messages only from startup fast-path).
            const needsLazyLoad = convEntry && (
                (!convEntry.messages || convEntry.messages.length === 0) ||
                convEntry._isShell
            );

            if (needsLazyLoad) {
                // Try IDB first for shell conversations (IDB has full data)
                let loaded = false;
                if (convEntry._isShell) {
                    try {
                        const allFull = await db.getConversations();
                        const fullConv = allFull.find(c => c.id === conversationId);
                        if (fullConv?.messages?.length > 0 &&
                            fullConv.messages.length >= (convEntry.messages?.length || 0)) {
                            setConversations(prev => prev.map(c =>
                                c.id === conversationId
                                    ? { ...c, messages: fullConv.messages, _isShell: false, _fullMessageCount: undefined }
                                    : c
                            ));
                            loaded = true;
                            console.log(`✅ Lazy-loaded ${fullConv.messages.length} messages from IDB`);
                        }
                    } catch (err) {
                        console.warn('⚠️ IDB lazy-load failed:', err);
                    }
                }
                // Fall back to server fetch for summary-only conversations
                if (!loaded) {
                    const pid = convEntry.projectId || currentProject?.id;
                    if (pid) {
                        try {
                            const serverChat = await syncApi.getChat(pid, conversationId);
                            // Only accept server messages if they have MORE than what
                            // we already have locally (prevents partial data overwrite)
                            if (serverChat?.messages?.length > 0 &&
                                serverChat.messages.length >= (convEntry.messages?.length || 0)) {
                                setConversations(prev => prev.map(c =>
                                    c.id === conversationId
                                        ? { ...c, messages: serverChat.messages, _isShell: false, _fullMessageCount: undefined, _version: Date.now() }
                                        : c
                                ));
                                console.log(`✅ Lazy-loaded ${serverChat.messages.length} messages from server`);
                            }
                        } catch (err) {
                            console.warn('⚠️ Server lazy-load failed:', err);
                        }
                    }
                }
            }

            // Mark current conversation as read — cosmetic flag only.
            // Do NOT trigger queueSave here; persisting 456 conversations
            // to IndexedDB just to flip hasUnreadResponse was the main
            // source of 35-second freezes on conversation switch.
            // The flag will be persisted on the next substantive save.
            setConversations(prev =>
                prev.map(conv =>
                    conv.id === conversationId
                        ? { ...conv, hasUnreadResponse: false }
                        : conv)
            );

            // Set the current conversation ID after updating state
            // Remove artificial delay that might be blocking
            // await new Promise(resolve => setTimeout(resolve, 50));
            setCurrentConversationId(conversationId);

            // CRITICAL: Persist to localStorage immediately when switching conversations
            try {
                setTabState('ZIYA_CURRENT_CONVERSATION_ID', conversationId);
                if (currentProject?.id) {
                    saveProjectConversationId(currentProject.id, conversationId);
                }
            } catch (e) {
                console.error('Failed to persist conversation ID during switch:', e);
            }

            // Delegate conversations are created server-side. Their messages
            // may not be in IndexedDB yet. Fetch fresh data on demand.
            const conv = conversationsRef.current.find(c => c.id === conversationId);
            const delegateStatus = (conv?.delegateMeta as any)?.status;
            const isTerminalDelegate = delegateStatus === 'crystal'
                || delegateStatus === 'failed'
                || delegateStatus === 'interrupted';
            // Skip server fetch for RUNNING delegates entirely — the
            // WebSocket stream (useDelegateStreaming) provides live content
            // and delegate polling handles status updates.  Fetching
            // getChat here returns a potentially huge message array that
            // triggers an expensive synchronous re-render cascade (the
            // original cause of multi-minute UI freezes when clicking
            // active swarm members).
            //
            // For QUEUED delegates (not yet terminal, not yet running),
            // still fetch since they have no WebSocket stream yet.
            const isActivelyStreaming = delegateStatus === 'running'
                || delegateStatus === 'compacting';
            if (conv?.delegateMeta && !isTerminalDelegate && !isActivelyStreaming) {
                const pid = conv.projectId || currentProject?.id;
                if (pid) {
                    // Fire-and-forget: don't block conversation load
                    syncApi.getChat(pid, conversationId).then(serverChat => {
                        if (!serverChat?.messages?.length) return;
                        React.startTransition(() => {
                            setConversations(prev => prev.map(c => {
                                if (c.id !== conversationId) return c;
                                // Never replace with fewer messages — prevents
                                // partial server data from destroying local history
                                const localCount = c.messages?.length || 0;
                                const serverCount = serverChat.messages?.length || 0;
                                if (serverCount < localCount && localCount > 2) {
                                    console.warn(`🛡️ FETCH_GUARD: Keeping ${localCount} local messages (server had ${serverCount})`);
                                    return { ...c, delegateMeta: serverChat.delegateMeta ?? c.delegateMeta };
                                }
                                return { ...c, messages: serverChat.messages, delegateMeta: serverChat.delegateMeta ?? c.delegateMeta };
                            }));
                        });
                    }).catch(err => {
                        console.warn('Background delegate fetch failed:', err);
                    });
                }
            }

            // Set the current folder ID based on the conversation's folder
            // This should not block conversation loading
            const conversation = conversationsRef.current.find(c => c.id === conversationId);
            if (conversation) {
                // Set folder ID asynchronously to not block message loading
                setTimeout(() => {
                    setCurrentFolderId(conversation.folderId ?? null);
                }, 0);
            }

            console.log('Current conversation changed:', {
                from: currentConversationId,
                to: conversationId,
                streamingConversations: Array.from(streamingConversations)
            });
        } finally {
            // Always clear loading state, even if folder operations are pending

            // Reset scroll state for newly loaded conversation
            conversationScrollStates.current.set(conversationId, {
                userScrolledAway: false,
                lastManualScrollTime: 0,
                isAtEnd: true
            });

            setIsLoadingConversation(false);

            // Only scroll if we actually switched conversations
            if (isActualSwitch) {
                // Scroll to appropriate position after conversation loads with multiple attempts
                const scrollToPosition = () => {
                    const chatContainer = document.querySelector('.chat-container') as HTMLElement;
                    if (chatContainer) {
                        if (isTopToBottom) {
                            // For top-down, scroll to the very bottom
                            chatContainer.scrollTop = chatContainer.scrollHeight;
                        } else {
                            // For bottom-up, scroll to the very top
                            chatContainer.scrollTop = 0;
                        }
                    }
                };

                // Execute scroll positioning
                scrollToPosition();
            } else {
                console.log('📌 Not switching - preserving scroll position');
            }

            // Only clear streamed content for conversations that are NOT actively streaming
            setStreamedContentMap(prev => {
                const streaming = streamingConversationsRef.current;
                let anyRemoved = false;
                for (const [id, content] of prev) {
                    if (!streaming.has(id)) { anyRemoved = true; break; }
                }
                if (!anyRemoved) return prev;
                const next = new Map(prev);
                for (const [id] of prev) {
                    if (!streaming.has(id)) next.delete(id);
                }
                return next;
            });
        }
    }, [currentConversationId, streamingConversations, isTopToBottom, currentProject?.id]);

    // Load conversation and scroll to specific message
    const loadConversationAndScrollToMessage = useCallback(async (conversationId: string, messageIndex: number) => {
        try {
            // Load the conversation first
            await loadConversation(conversationId);

            // Wait for the conversation to be loaded and rendered
            await new Promise(resolve => setTimeout(resolve, 100));

            // Find the message element and scroll to it
            const scrollToMessage = () => {
                const chatContainer = document.querySelector('.chat-container') as HTMLElement;
                if (!chatContainer) return false;

                // Find all message elements
                const messageElements = chatContainer.querySelectorAll('.message');

                if (messageIndex < messageElements.length) {
                    const targetMessage = messageElements[messageIndex] as HTMLElement;

                    // Scroll to the message with smooth behavior
                    targetMessage.scrollIntoView({ behavior: 'smooth', block: 'center' });

                    // Add highlight effect
                    targetMessage.style.transition = 'background-color 0.5s ease';
                    targetMessage.style.backgroundColor = isDarkMode ? 'rgba(24, 144, 255, 0.2)' : 'rgba(24, 144, 255, 0.1)';

                    setTimeout(() => {
                        targetMessage.style.backgroundColor = '';
                    }, 2000);

                    return true;
                }
                return false;
            };

            // Try scrolling multiple times with delays to ensure rendering is complete
            setTimeout(scrollToMessage, 200);
            setTimeout(scrollToMessage, 500);
        } catch (error) {
            console.error('Error loading conversation and scrolling to message:', error);
            throw error;
        }
    }, [loadConversation, isDarkMode]);

    // frontend/src/context/ChatContext.tsx
    // Folder management functions
    const createFolder = useCallback(async (name: string, parentId?: string | null): Promise<string> => {
        const newFolder: ConversationFolder = {
            id: uuidv4(),
            name,
            projectId: currentProject?.id,
            parentId: parentId || null,
            useGlobalContext: true,
            useGlobalModel: true,
            createdAt: Date.now(),
            updatedAt: Date.now()
        };

        try {
            await db.saveFolder(newFolder);
            setFolders(prev => [...prev, newFolder]);
            projectSync.post('folders-changed');
            if (newFolder.projectId || currentProject?.id) {
                folderSyncApi.bulkSyncFolders(newFolder.projectId || currentProject!.id, [newFolder]).catch(e =>
                    console.warn('📡 Folder server sync failed:', e));
            }
            return newFolder.id;
        } catch (error) {
            console.error('Error creating folder:', error);
            throw error;
        }
    }, [currentProject?.id]);

    const updateFolder = useCallback(async (folder: ConversationFolder): Promise<void> => {
        try {
            folder.updatedAt = Date.now();
            await db.saveFolder(folder);
            setFolders(prev => prev.map(f => f.id === folder.id ? folder : f));
            projectSync.post('folders-changed');
            // Push to the folder's own project (which may differ from currentProject
            // if the folder was just moved to another project)
            const targetProjectId = folder.projectId || currentProject?.id;
            if (targetProjectId) {
                folderSyncApi.bulkSyncFolders(targetProjectId, [folder]).catch(e =>
                    console.warn('📡 Folder server sync failed:', e));
                // If moved away from current project, also delete from source
                if (currentProject?.id && targetProjectId !== currentProject.id) {
                    folderSyncApi.deleteServerFolder(currentProject.id, folder.id).catch(e =>
                        console.warn('📡 Folder source delete failed:', e));
                }
            }
        } catch (error) {
            console.error('Error updating folder:', error);
            throw error;
        }
    }, [currentProject?.id]);

    const deleteFolder = useCallback(async (id: string): Promise<void> => {
        try {
            // Get the most up-to-date list of conversations from the DB or state.
            // To be safest, especially if other operations might be happening,
            // it's better to work with the data that will be persisted.
            const currentConversationsFromDB = await db.getConversations();
            const updatedConversationsForDB = currentConversationsFromDB.map(conv =>
                conv.folderId === id ? { ...conv, isActive: false, _version: Date.now() } : conv
            );

            // Save the entire updated list to the database once
            const affectedIds = updatedConversationsForDB.filter(c => c.folderId === id).map(c => c.id);
            await queueSave(updatedConversationsForDB, { changedIds: affectedIds });

            // Now update the React state based on the successfully persisted changes
            setConversations(prevConvs => prevConvs.map(conv =>
                conv.folderId === id ? { ...conv, isActive: false, _version: Date.now() } : conv
            ));

            // Delete the folder metadata from the database
            await db.deleteFolder(id);

            // Update folders state in React
            setFolders(prevFolders => prevFolders.filter(f => f.id !== id));
            projectSync.post('folders-changed');
            // Delete from the folder's own project on server
            const folderToDelete = folders.find(f => f.id === id);
            const deleteFromProject = folderToDelete?.projectId || currentProject?.id;
            if (deleteFromProject) {
                folderSyncApi.deleteServerFolder(deleteFromProject, id).catch(e =>
                    console.warn('📡 Folder server delete failed:', e));
            }

            // If the currently active folder is the one being deleted, reset it
            if (currentFolderId === id) {
                setCurrentFolderId(null);
            }

            const numAffectedConversations = updatedConversationsForDB.filter(c => c.folderId === id && !currentConversationsFromDB.find(oc => oc.id === c.id)?.isActive).length;
            console.log(`Folder deleted and ${numAffectedConversations} conversations marked inactive.`);

        } catch (error) {
            console.error('Error deleting folder:', error);
            message.error('Failed to delete folder. Please try again.');
            // Potentially re-fetch state from DB to ensure consistency if partial failure
            const freshConversations = await db.getConversations();
            setConversations(freshConversations);
            const freshFolders = await db.getFolders();
            setFolders(freshFolders);
        }
    }, [currentFolderId, setConversations, setFolders, setCurrentFolderId, queueSave]);

    const moveConversationToFolder = useCallback(async (conversationId: string, folderId: string | null): Promise<void> => {
        try {
            const newVersion = Date.now();
            console.log('🔧 CHATCONTEXT: moveConversationToFolder called:', {
                conversationId,
                folderId,
                newVersion
            });

            // Use the single save pipeline to avoid races with other concurrent
            // writes.  queueSave serialises DB writes and posts to
            // BroadcastChannel so other same-project tabs see the move.
            setConversations(prev => {
                const updated = prev.map(conv =>
                    conv.id === conversationId
                        ? { ...conv, folderId, _version: newVersion, lastAccessedAt: newVersion }
                        : conv
                );
                queueSave(updated, { changedIds: [conversationId] }).catch(console.error);
                return updated;
            });
        } catch (error) {
            console.error('Error moving conversation to folder:', error);
            throw error;
        }
    }, [queueSave]);

    useEffect(() => {
        // Load current messages immediately when conversation changes, regardless of folder state
        // Only update if messages actually changed to prevent scroll jumps
        if (currentConversationId && conversations.length > 0) {
            const messages = conversations.find(c => c.id === currentConversationId)?.messages ?? null;
            if (messages === null) return;

            // PERFORMANCE FIX: Replace expensive JSON.stringify (18ms) with fast checks
            // Reduces comparison from O(n*m) to O(1) for most cases
            setCurrentMessages(prev => {
                const messagesChanged =
                    messages.length !== prev.length ||
                    messages !== prev ||
                    (messages.length > 0 && prev.length > 0 &&
                        messages[messages.length - 1] !== prev[prev.length - 1]);

                if (!messagesChanged) return prev;
                console.log('📝 Messages changed for conversation:', currentConversationId);
                return messages;
            });
        }
    }, [conversations, currentConversationId]);

    // Enhanced initialization with corruption detection and recovery
    const initializeWithRecovery = useCallback(async () => {
        if (isRecovering.current || initializationStarted.current) return;

        // CRITICAL: Check ephemeral mode before any database operations
        if (isEphemeralMode) {
            console.log('🔒 EPHEMERAL MODE: Starting fresh, no persistence');
            initializationStarted.current = true;
            const newId = uuidv4();
            setCurrentConversationId(newId);
            setConversations([{
                id: newId,
                title: 'New Conversation',
                messages: [],
                lastAccessedAt: Date.now(),
                isActive: true,
                _version: Date.now(),
                hasUnreadResponse: false
            }]);
            
            // Set the new conversation as current if we don't have one yet
            if (!currentConversationId) {
                setCurrentConversationId(newId);
                setCurrentMessages([]);
                console.log('✅ EPHEMERAL MODE: Set initial conversation');
            }
            
            setIsInitialized(true);
            isRecovering.current = false;
            return;
        }

        initializationStarted.current = true;
        isRecovering.current = true;

        // Track if database is fully functional
        let isDatabaseHealthy = true;

        try {
            await db.init();
            // Fast path: load shells (metadata only, no message bodies)
            // GC pass runs below after shells are loaded.
            // Messages for the active conversation are loaded below.
            const savedConversations = await db.getConversationShells();
            console.log('✅ Setting conversation shells immediately:', savedConversations.length);
            setConversations(savedConversations);

            // Detect corrupt IDB: if shells returned very few valid
            // conversations, check the server to see if we're missing data.
            // This handles the case where the IDB conversations store has a
            // corrupt record (e.g. a single entry with id: undefined) while
            // the server still has the full history.
            const validShells = savedConversations.filter(c => c.id && c.title);
            if (validShells.length <= 1 && currentProject?.id && isServerReachable) {
                try {
                    const serverSummaries = await syncApi.listChats(currentProject.id, false);
                    if (serverSummaries.length > 10 && validShells.length <= 1) {
                        console.warn(`🔧 IDB_REPAIR: IDB has ${validShells.length} valid conversations but server has ${serverSummaries.length}. Clearing corrupt IDB store.`);
                        // Clear the corrupt store so SERVER_SYNC can repopulate cleanly
                        try {
                            const health = await db.checkDatabaseHealth();
                            if (!health.isHealthy || health.errors.length > 0) {
                                console.warn('🔧 IDB_REPAIR: Health check failed:', health.errors);
                                await db.repairDatabase();
                                console.log('🔧 IDB_REPAIR: Database repaired. SERVER_SYNC will repopulate.');
                            } else {
                                // Store is structurally OK but data is corrupt/empty
                                // Force a save of empty array to clear the corrupt record
                                await db.saveConversations([]);
                                console.log('🔧 IDB_REPAIR: Cleared corrupt conversations record.');
                            }
                        } catch (repairErr) {
                            console.warn('🔧 IDB_REPAIR: Repair failed (non-fatal):', repairErr);
                        }
                    }
                } catch (e) {
                    console.debug('🔧 IDB_REPAIR: Server check failed (non-fatal):', e);
                }
            }

            if (savedConversations.length > 0 && conversations.length === 0) {
                console.log('🔄 FORCE LOAD: shells loaded, state was empty');
                setConversations(savedConversations);
            }

            // Immediate startup GC: purge stale empty "New Conversation" entries
            // that accumulated from previous sessions.  Uses a shorter threshold
            // (5 min) than the periodic GC (1 hour) because at startup we know
            // no human is mid-thought in an empty conversation from a prior run.
            const STARTUP_GC_MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes
            const protectedAtStartup = new Set<string>();
            // Protect whichever conversation we're about to restore
            const savedCurrentId = getTabState('ZIYA_CURRENT_CONVERSATION_ID');
            if (savedCurrentId) protectedAtStartup.add(savedCurrentId);

            const { kept: gcKept, purgedIds: gcPurged } = gcEmptyConversations(
                savedConversations, protectedAtStartup, STARTUP_GC_MAX_AGE_MS
            );
            if (gcPurged.length > 0) {
                console.log(`🗑️ Startup GC: removing ${gcPurged.length} stale empty conversation(s)`);
                setConversations(gcKept);
                db.saveConversations(gcKept).catch(e => console.warn('Startup GC save failed:', e));
            }

            // CRITICAL: Verify the restored currentConversationId exists in loaded conversations
            if (savedCurrentId && !savedConversations.some(conv => conv.id === savedCurrentId)) {
                // Saved conversation ID not found in IndexedDB.
                // INIT_SYNC will merge from server shortly; for now use most recent local.
                if (savedConversations.length > 0) {
                    console.warn(`⚠️ ORPHANED CONVERSATION: ${savedCurrentId} not found anywhere`);
                    const mostRecent = savedConversations.reduce((a, b) =>
                        (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                    );
                    setCurrentConversationId(mostRecent.id);
                    setTabState('ZIYA_CURRENT_CONVERSATION_ID', mostRecent.id);
                }
            }

            // Always mark as initialized to allow app to function
            setIsInitialized(true);
            (window as any).__ziyaInitTime = Date.now();

            // Lazy-load full messages for the active conversation
            const activeId = getTabState('ZIYA_CURRENT_CONVERSATION_ID') || savedConversations[0]?.id;
            if (activeId) {
                try {
                    const allFull = await db.getConversations();
                    const fullConv = allFull.find(c => c.id === activeId);
                    if (fullConv && fullConv.messages.length > 0) {
                        setConversations(prev => prev.map(c =>
                            c.id === activeId ? { ...c, messages: fullConv.messages, _isShell: false, _fullMessageCount: undefined } : c
                        ));
                        console.log(`✅ Lazy-loaded ${fullConv.messages.length} messages for active conversation`);
                    }
                } catch (err) {
                    console.warn('⚠️ Failed to lazy-load active conversation messages:', err);
                }
            }

        } catch (error) {
            console.error('❌ INIT: Database initialization failed:', error);
            isDatabaseHealthy = false;

            // CRITICAL FIX: Set initialized flag even on failure
            // The app should be able to create new conversations even if DB initialization failed
            setIsInitialized(true);

            // IndexedDB failed. INIT_SYNC will try the server next.
            // If server also fails, the user starts fresh.
            console.warn('⚠️ INIT: IndexedDB failed. Server sync will attempt recovery.');
        } finally {
            isRecovering.current = false;
        }
    }, [currentConversationId, isEphemeralMode]);

    // Sync ref after initializeWithRecovery is initialized (avoids TDZ in startNewChat)
    initializeWithRecoveryRef.current = initializeWithRecovery;

    useEffect(() => {
        // Warn user before leaving if a response is still streaming.
        // No content backup needed — server and IndexedDB have the data.
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (streamingConversations.size > 0) {
                const msg = 'A response is still being generated. Leaving now may lose the in-progress response.';
                e.preventDefault();
                e.returnValue = msg;
                return msg;
            }
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [streamingConversations]);

    useEffect(() => {
        // Only initialize once
        if (isInitialized) {
            return;
        }

        initializeWithRecovery();

        const request = indexedDB.open('ZiyaDB');
        request.onerror = (event) => {
            const error = (event.target as IDBOpenDBRequest).error?.message || 'Unknown IndexedDB error';
            setDbError(error);
        };
    }, [initializeWithRecovery, isEphemeralMode]);

    // Load folders independently of initialization state
    // This ensures folder loading doesn't block conversation loading
    useEffect(() => {
        if (!isInitialized) return;

        const projectId = currentProject?.id;

        const loadFoldersIndependently = async () => {
            try {
                console.log("Loading folders from database...");
                // Add a small delay to ensure conversations are loaded first
                await new Promise(resolve => setTimeout(resolve, 100));
                let folders = await db.getFolders();

                // Migrate folders without projectId
                const folderNeedsMigration = folders.some(f => !f.projectId);
                if (folderNeedsMigration && projectId) {
                    console.log('🔄 MIGRATION: Assigning folders without projectId to current project');
                    folders = folders.map(f => {
                        if (!f.projectId) {
                            return { ...f, projectId, updatedAt: Date.now() };
                        }
                        return f;
                    });
                    await Promise.all(folders.map(f => db.saveFolder(f)));
                    console.log('✅ MIGRATION: All folders now have projectId');
                }

                // Merge with server folders to pick up TaskPlan folders that
                // may not be in IndexedDB yet (e.g. after page refresh)
                try {
                    const { listServerFolders } = await import('../api/folderSyncApi');
                    const serverFolders = await listServerFolders(projectId);
                    const localMap = new Map(folders.map(f => [f.id, f]));
                    let changed = false;
                    for (const sf of serverFolders) {
                        const local = localMap.get(sf.id);
                        if (serverFolderWins(local, sf)) {
                            localMap.set(sf.id, { ...sf, projectId: sf.projectId || projectId });
                            changed = true;
                        }
                    }
                    if (changed) {
                        folders = Array.from(localMap.values());
                        // Persist to IndexedDB (non-blocking)
                        Promise.all(folders.map(f => db.saveFolder(f))).catch(e =>
                            console.warn('📡 INIT_FOLDERS: Failed to persist server folders:', e)
                        );
                    }
                } catch (e) {
                    console.warn('📡 INIT_FOLDERS: Server folder fetch failed:', e);
                }

                // Filter to current project only
                const projectFolders = projectId
                    ? folders.filter(f => f.projectId === projectId || f.isGlobal)
                    : folders;  // Show all only if no project loaded

                setFolders(projectFolders);
                console.log(`✅ Folders loaded for project ${projectId}: ${projectFolders.length} of ${folders.length} total`);
            } catch (error) {
                console.error('Error loading folders:', error);
                // Don't let folder errors block the app
                setFolders([]);
            }
        };
        loadFoldersIndependently();
    }, [isInitialized, currentProject?.id]); // Reload when project changes

    // Sync conversations with server on initial load (and when project becomes available)
    // This complements the projectSwitched handler which only fires on user-initiated switches.
    // Also runs periodically to pick up changes from other browser instances (different ports).
    useEffect(() => {
        if (!isInitialized || !currentProject?.id) return;
        if (isEphemeralMode) return;

        const projectId = currentProject.id;

        // Detect whether this is an actual project switch (vs a periodic poll
        // or a dependency re-fire).  We need this flag below to decide whether
        // to relocate the active conversation.
        const isActualProjectSwitch = serverSyncedForProject.current !== null
            && serverSyncedForProject.current !== projectId;

        // Only show the switching spinner when the PROJECT actually changes,
        // not when isServerReachable or other deps change.
        if (serverSyncedForProject.current !== projectId) {
            // Save the outgoing project's active conversation so we can
            // restore it if the user switches back later.
            if (isActualProjectSwitch && currentConversationRef.current) {
                saveProjectConversationId(serverSyncedForProject.current!, currentConversationRef.current);
            }

            setIsProjectSwitching(true);
            console.log('🔄 PROJECT_SWITCH: Set isProjectSwitching = true for', projectId);
        }

        // Migrate conversations without a projectId to the current project
        const migrateUntaggedConversations = async (conversations: Conversation[], projectId: string): Promise<Conversation[]> => {
            const untagged = conversations.filter(c => !c.projectId && c.isActive !== false);
            if (untagged.length === 0) return conversations;

            console.log(`🔄 MIGRATION: Tagging ${untagged.length} conversations with project ${projectId}`);
            const migrated = conversations.map(c => {
                if (!c.projectId && c.isActive !== false) {
                    return { ...c, projectId, _version: Date.now() };
                }
                return c;
            });

            await db.saveConversations(migrated);
            console.log('✅ MIGRATION: Conversations tagged with projectId');
            return migrated;
        };

        const syncWithServer = async () => {
            // Outer try-finally guarantees setIsProjectSwitching(false) runs
            // even when an early-return guard exits before the inner try block.
            // Without this, the switching flag stays true and the chat-selection
            // UI deadlocks until the next 30s poll succeeds.
            try {
                // Skip sync entirely when server is known to be unreachable.
                if (!isServerReachable) {
                    return;
                }
                // Skip polling when any conversation is actively streaming.
                // This tab's own state is authoritative during streaming; the
                // server poll would race with addMessageToConversation /
                // queueSave and clobber in-progress conversation data.
                if (streamingConversationsRef.current.size > 0) return;
                // Skip polling when tab is not visible
                if (document.hidden) {
                    return;
                }

                try {
                // 1. Fetch from server
                // Use summaries (no messages) for polling — only fetch full data on version mismatch
                const serverChats = await syncApi.listChats(projectId, false);
                console.debug(`📡 SERVER_SYNC: Got ${serverChats.length} chat summaries from server`);

                // 2. Load current IndexedDB state
                let allConversations: Conversation[];
                try {
                    allConversations = await db.getConversations();
                } catch (dbErr) {
                    console.warn('📡 SERVER_SYNC: IndexedDB unavailable, using server as sole source:', dbErr);
                    // IDB is broken (stale connections, blocked upgrade, etc.)
                    // Fall through with empty local state so server data still gets applied.
                    allConversations = [];
                }

                // 2a. Migrate untagged conversations (only on first sync)
                if (serverSyncedForProject.current !== projectId) {
                    allConversations = await migrateUntaggedConversations(allConversations, projectId);
                }
                serverSyncedForProject.current = projectId;

                const localProjectConvs = allConversations.filter((c: any) => c.projectId === projectId || c.isGlobal);

                // 2b. Detect conversations that need full fetch:
                //     - Server-only (new from another instance)
                //     - Server version newer than local (updated from another instance)
                const localMap = new Map(localProjectConvs.map((c: any) => [c.id, c]));
                const needFullFetch: string[] = [];

                for (const sc of serverChats) {
                    const local = localMap.get(sc.id);
                    if (!local) {
                        needFullFetch.push(sc.id);
                    } else {
                        // Always fetch full data if server has delegate metadata
                        // or folder assignment that local is missing
                        const serverHasDelegateMeta = sc.delegateMeta && !local.delegateMeta;
                        const serverHasFolder = (sc.groupId || sc.folderId) && !local.folderId;
                        if (serverHasDelegateMeta || serverHasFolder) {
                            needFullFetch.push(sc.id);
                            continue;
                        }
                        const serverVer = (sc as any)._version || sc.lastActiveAt || 0;
                        const localVer = (local as any)._version || local.lastAccessedAt || 0;
                        if (serverVer > localVer) {
                            needFullFetch.push(sc.id);
                        }
                    }
                }

                // Fetch full data only for conversations that are new or updated
                const fullFetchMap = new Map<string, any>();
                if (needFullFetch.length > 0) {
                    console.log(`📡 SERVER_SYNC: Fetching full data for ${needFullFetch.length} new/updated conversation(s)`);
                    const results = await Promise.allSettled(
                        needFullFetch.map(id => syncApi.getChat(projectId, id))
                    );
                    results.forEach((result, i) => {
                        if (result.status === 'fulfilled' && result.value) {
                            fullFetchMap.set(needFullFetch[i], result.value);
                        }
                    });
                }

                // 3. Three-way merge: use _version to determine winner, keep all unique
                const mergedMap = new Map<string, any>();

                // Start with local conversations
                localProjectConvs.forEach((conv: any) => {
                    mergedMap.set(conv.id, conv);
                });

                // Merge server conversations
                serverChats.forEach((sc: any) => {
                    const local = mergedMap.get(sc.id);
                    const serverVersion = sc._version || 0;
                    const localVersion = local?._version || 0;
                    
                    if (!local) {
                        // Server-only conversation — use full-fetched data if available
                        const full = fullFetchMap.get(sc.id);

                        // Skip empty "New Conversation" shells from the server.
                        // These are stale empties that the GC purged locally;
                        // re-importing them defeats the cleanup.
                        const isEmptyShell = sc.title === 'New Conversation'
                            && (!full?.messages || full.messages.length === 0);
                        if (isEmptyShell) return;

                        if (full) {
                            mergedMap.set(sc.id, {
                                ...full,
                                _isShell: false,
                                _fullMessageCount: undefined,
                                projectId: full.projectId || projectId,
                                folderId: full.groupId || full.folderId || sc.groupId || sc.folderId || null,
                                delegateMeta: full.delegateMeta || null,
                                lastAccessedAt: full.lastAccessedAt || full.lastActiveAt,
                                isActive: full.isActive !== false,
                                _version: full._version || Date.now(),
                            });
                        }
                        // Full fetch failed — add as metadata-only with _version: 0
                        // so it appears in the sidebar immediately, and the low version
                        // ensures a full fetch is retried on the next sync cycle.
                        if (!full && !isEmptyShell) {
                            mergedMap.set(sc.id, {
                                id: sc.id,
                                title: sc.title || 'Loading...',
                                messages: [],
                                projectId: sc.projectId || projectId,
                                folderId: sc.groupId || sc.folderId || null,
                                lastAccessedAt: sc.lastActiveAt || 0,
                                isActive: true,
                                _version: 0,
                            });
                        }
                    } else if (serverVersion > localVersion) {
                        // Server is newer — use full-fetched data if available,
                        // otherwise update metadata only from summary
                        const full = fullFetchMap.get(sc.id);
                        if (full) {
                            // Message-count guard: if the server has fewer
                            // messages than local, keep local messages but
                            // update metadata from server.  This prevents
                            // partial syncs from destroying conversation history.
                            const localMsgCount = local.messages?.length || 0;
                            const serverMsgCount = full.messages?.length || 0;
                            if (serverMsgCount < localMsgCount && localMsgCount > 2) {
                                console.warn(`🛡️ SYNC_GUARD: Keeping ${localMsgCount} local messages for ${sc.id?.substring(0, 8)} (server had ${serverMsgCount})`);
                                full.messages = local.messages;
                            }
                            mergedMap.set(sc.id, {
                                ...full,
                                _isShell: false,
                                _fullMessageCount: undefined,
                                projectId: full.projectId || projectId,
                                folderId: full.groupId || full.folderId || null,
                                delegateMeta: full.delegateMeta || null,
                                lastAccessedAt: full.lastAccessedAt || full.lastActiveAt,
                                isActive: full.isActive !== false,
                                _version: full._version || Date.now(),
                            });
                        } else {
                            // Summary-only update (full fetch wasn't needed or failed)
                            mergedMap.set(sc.id, {
                                ...local,
                                title: sc.title || local.title,
                                projectId: sc.projectId || local.projectId || projectId,
                                folderId: sc.groupId || sc.folderId || local.folderId || null,
                                lastActiveAt: sc.lastActiveAt || local.lastActiveAt,
                                _version: serverVersion,
                            });
                        }
                    }
                });

                const mergedProjectConvs = Array.from(mergedMap.values());

                // Preserve in-memory messages that were lazy-loaded during
                // this session but may not have been persisted to IDB yet.
                // Without this, the setConversations(mergedProjectConvs) call
                // below would overwrite lazy-loaded data with empty messages,
                // causing the "No messages" bug to recur after each sync cycle.
                setConversations(prev => {
                    // This is read inside the updater below, not used here.
                    // We capture it now to close over the correct prev.
                    return prev;
                });

                // Record all IDs the server currently knows about.
                serverChats.forEach((sc: any) => knownServerConversationIds.current.add(sc.id));

                // Prune to prevent unbounded growth during idle polling.
                // Keep the most recent IDs (those the server just told us about)
                // and drop the oldest accumulated entries.
                if (knownServerConversationIds.current.size > KNOWN_SERVER_IDS_MAX) {
                    const currentServerIds = new Set(serverChats.map((sc: any) => sc.id));
                    knownServerConversationIds.current = currentServerIds;
                    console.debug(`🧹 Pruned knownServerConversationIds: was > ${KNOWN_SERVER_IDS_MAX}, reset to ${currentServerIds.size}`);
                }

                // 3b. Detect locally-present conversations that the server no longer has.
                // This means another instance deleted them — mark inactive locally.
                // CRITICAL: Only consider conversations that are old enough to have been
                // synced. Recently-created or recently-modified conversations may simply
                // not have reached the server yet (bulk-sync is async/non-blocking).
                const SYNC_GRACE_PERIOD_MS = 60_000; // 60 seconds
                const now = Date.now();
                const serverIdSet = new Set(serverChats.map((sc: any) => sc.id));
                const deletedIds: string[] = [];
                for (let i = mergedProjectConvs.length - 1; i >= 0; i--) {
                    const conv = mergedProjectConvs[i];
                    if (conv.isActive !== false && conv.messages?.length > 0 && !serverIdSet.has(conv.id)) {
                        // Only remove if previously seen on server — never remove freshly imported conversations.
                        if (knownServerConversationIds.current.has(conv.id)) {
                            console.log(`📡 SERVER_SYNC: "${conv.title}" (${conv.id.substring(0, 8)}) removed from server — removing locally`);
                            deletedIds.push(conv.id);
                            mergedProjectConvs.splice(i, 1);
                        }
                    }
                }

                // 4. Push local-only / newer-local conversations to server (non-blocking)
                const chatsToSync = mergedProjectConvs
                    .filter(c => {
                        // Don't push conversations we just marked as deleted
                        if (deletedIds.includes(c.id)) return false;
                        // Don't push inactive (deleted) conversations back to server
                        if (c.isActive === false) return false;
                        const sc = serverChats.find(sc => sc.id === c.id);
                        if (!sc) return true; // local-only, needs push
                        // Only push if local has a _version AND it's strictly greater than server's
                        // If server has no _version (old data), compare lastActiveAt instead
                        const serverVer = (sc as any)._version || sc.lastActiveAt || 0;
                        const localVer = (c as any)._version || c.lastAccessedAt || 0;
                        return localVer > serverVer;
                    })
                    .map((c: any) => syncApi.conversationToServerChat(c, projectId));
                if (chatsToSync.length > 0) {
                    syncApi.bulkSync(projectId, chatsToSync).catch(e =>
                        console.warn('📡 SERVER_SYNC: Push to server failed (non-fatal):', e)
                    );
                }

                // 5. Update IndexedDB with merged data
                // Only write if something actually changed to avoid churning IndexedDB on idle polls
                const conversationsChanged = mergedProjectConvs.length !== localProjectConvs.length ||
                    mergedProjectConvs.some(mc => {
                        const local = localProjectConvs.find((l: any) => l.id === mc.id);
                        return !local || (mc._version || 0) > (local._version || 0);
                    });
                if (conversationsChanged) {
                    // Exclude global conversations from otherProjectConvs — they are
                    // already included in mergedProjectConvs via the localProjectConvs
                    // filter (c.isGlobal), so including them here creates duplicates.
                    const otherProjectConvs = allConversations.filter((c: any) =>
                        c.projectId !== projectId && !c.isGlobal
                    );
                    await db.saveConversations([...mergedProjectConvs, ...otherProjectConvs]);
                }

                // 6. Update React state — only if something actually changed
                setConversations(prev => {
                    // Don't let a stale sync cycle resurrect a conversation that
                    // was deleted in this tab between when this sync started and now.
                    // If an id is in mergedProjectConvs but absent from both prev
                    // (current React state) and the server list, it was just deleted locally.
                    const prevIds = new Set(prev.map((p: any) => p.id));
                    const safeConvs = mergedProjectConvs.filter((mc: any) =>
                        prevIds.has(mc.id) || serverIdSet.has(mc.id)
                    );

                    // Preserve in-memory messages that were lazy-loaded during
                    // this session but not yet persisted to IDB.  Without this,
                    // mergedProjectConvs (built from IDB+server) would overwrite
                    // lazy-loaded messages with empty arrays.
                    for (const mc of safeConvs) {
                        const mcMsgCount = mc.messages?.length || 0;
                        {
                            const inMemory = prev.find((p: any) => p.id === mc.id);
                            const inMemoryCount = inMemory?.messages?.length || 0;
                            // Preserve in-memory messages when they have MORE content
                            // than the merged version (prevents partial/stale data from overwriting)
                            if (inMemoryCount > mcMsgCount) {
                                console.warn(`🛡️ SYNC_GUARD: Preserving ${inMemoryCount} in-memory messages for ${mc.id?.substring(0, 8)} (merged had ${mcMsgCount})`);
                                mc.messages = inMemory.messages;
                            }
                        }
                    }

                    if (prev.length === safeConvs.length) {
                        const changed = safeConvs.some((mc: any) => {
                            const existing = prev.find(p => p.id === mc.id);
                            return !existing || (mc._version || 0) > (existing._version || 0);
                        });
                        if (!changed) return prev; // No-op, avoid re-render
                    }
                    return safeConvs;
                });

                // 7. Update current conversation if it doesn't exist in merged set
                // For periodic polls: NEVER change currentConversationId — it's
                // a per-tab view concern that only changes on explicit user actions.
                //
                // For actual project switches: relocate to a conversation that
                // belongs to the new project.  The old conversation from the
                // previous project is irrelevant and confusing if left visible.
                if (isActualProjectSwitch) {
                    const currentId = currentConversationRef.current;
                    // Check if the current conversation belongs to the new project
                    // (or is global, which is visible everywhere).
                    const currentConv = mergedProjectConvs.find(c => c.id === currentId);
                    const belongsToNewProject = currentConv &&
                        (currentConv.isGlobal || currentConv.projectId === projectId);

                    if (!belongsToNewProject) {
                        // Try to restore the saved conversation for this project
                        const savedId = loadProjectConversationId(projectId);
                        const savedExists = savedId && mergedProjectConvs.some(c => c.id === savedId);

                        if (savedExists) {
                            console.log(`🔄 PROJECT_SWITCH: Restoring saved conversation ${savedId} for project ${projectId}`);
                            setCurrentConversationId(savedId!);
                            setTabState('ZIYA_CURRENT_CONVERSATION_ID', savedId!);
                        } else if (mergedProjectConvs.length > 0) {
                            // Fall back to most recently accessed conversation in the new project
                            const mostRecent = mergedProjectConvs.reduce((a, b) =>
                                (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                            );
                            console.log(`🔄 PROJECT_SWITCH: Selecting most recent conversation "${mostRecent.title}" for project ${projectId}`);
                            setCurrentConversationId(mostRecent.id);
                            setTabState('ZIYA_CURRENT_CONVERSATION_ID', mostRecent.id);
                        }
                        // else: no conversations in this project — user will create one
                    }
                }

                // 8. Sync folders with server (same merge pattern)
                try {
                    const serverFolders = await folderSyncApi.listServerFolders(projectId);
                    const localFolders = await db.getFolders();
                    const localProjectFolders = localFolders.filter(
                        f => f.projectId === projectId || f.isGlobal
                    );

                    const folderMap = new Map<string, ConversationFolder>();
                    localProjectFolders.forEach(f => folderMap.set(f.id, f));
                    serverFolders.forEach(sf => {
                        const effectiveProjectId = sf.projectId || projectId;

                        // Skip server folders that were moved to another project.
                        // Check the full (unfiltered) local set — if IndexedDB already
                        // has this folder under a different project, the server copy is stale.
                        const fullLocalEntry = localFolders.find(f => f.id === sf.id);
                        if (fullLocalEntry && fullLocalEntry.projectId && fullLocalEntry.projectId !== projectId && !fullLocalEntry.isGlobal) {
                            return; // stale server entry for a moved folder — skip
                        }
                        const local = folderMap.get(sf.id);
                        if (serverFolderWins(local, sf)) {
                            folderMap.set(sf.id, { ...sf, projectId: effectiveProjectId });
                        }
                    });
                    const mergedFolders = Array.from(folderMap.values()).filter(f => f.projectId === projectId || f.isGlobal);

                    const localOnly = mergedFolders.filter(
                        f => !serverFolders.some(sf => sf.id === f.id)
                    );
                    if (localOnly.length > 0) {
                        folderSyncApi.bulkSyncFolders(projectId, localOnly).catch(e =>
                            console.warn('📡 SERVER_SYNC: Folder push failed:', e)
                        );
                    }

                    // Only write if something actually changed
                    const foldersChanged = mergedFolders.length !== localProjectFolders.length ||
                        mergedFolders.some(mf => {
                            const local = localProjectFolders.find(l => l.id === mf.id);
                            return !local || (mf.updatedAt || 0) > (local.updatedAt || 0);
                        });
                    if (foldersChanged) {
                        await Promise.all(mergedFolders.map(f => db.saveFolder(f)));
                    }

                    setFolders(prev => {
                        const changed = mergedFolders.some(mf => {
                            const existing = prev.find(p => p.id === mf.id);
                            return !existing || (mf.updatedAt || 0) > (existing.updatedAt || 0);
                        });
                        return (prev.length !== mergedFolders.length || changed) ? mergedFolders : prev;
                    });
                } catch (e) {
                    console.warn('📡 SERVER_SYNC: Folder sync failed:', e);
                }

            } catch (syncError) {
                console.error('📡 SERVER_SYNC: Error syncing with server:', syncError);
            }
            } finally {
                setIsProjectSwitching(false);
            }
        };
        syncWithServer();

        // Poll every 30 seconds to pick up changes from other browser instances
        const intervalId = setInterval(syncWithServer, 30_000);

        return () => clearInterval(intervalId);
    }, [isInitialized, currentProject?.id, isEphemeralMode, isServerReachable]);

    // GC empty "New Conversation" nodes older than 1 hour
    useEffect(() => {
        if (!isInitialized || isEphemeralMode) return;

        const GC_INTERVAL_MS = 5 * 60 * 1000; // every 5 minutes

        const runGc = () => {
            const protectedIds = new Set<string>(streamingConversationsRef.current);
            if (currentConversationRef.current) protectedIds.add(currentConversationRef.current);
            const { kept, purgedIds } = gcEmptyConversations(
                conversationsRef.current,
                protectedIds,
            );

            if (purgedIds.length > 0) {
                console.log(`🗑️ GC: removing ${purgedIds.length} empty conversation(s):`, purgedIds);
                setConversations(kept);
                queueSave(kept, { changedIds: purgedIds });
            }
        };

        // Run first GC cycle immediately (catches anything the startup GC
        // missed, e.g. conversations created during the init settle window).
        runGc();
        const intervalId = setInterval(runGc, GC_INTERVAL_MS);
        return () => clearInterval(intervalId);
    }, [isInitialized, isEphemeralMode, queueSave]);

    // Listen for model change events
    useEffect(() => {
        window.addEventListener('modelChanged', handleModelChange as EventListener);

        return () => {
            // Reset processed changes when component unmounts
            processedModelChanges.current.clear();
            window.removeEventListener('modelChanged', handleModelChange as EventListener);
        };
    }, [handleModelChange]);

    useEffect(() => {
        currentConversationRef.current = currentConversationId;
        folderRef.current = currentFolderId;
    }, [currentConversationId, conversations, currentFolderId]);

    const mergeConversations = useCallback((local: Conversation[], remote: Conversation[]) => {
        const merged = new Map<string, Conversation>();

        // Add all local conversations first
        local.forEach(conv => merged.set(conv.id, conv));

        // Merge remote conversations only if newer
        remote.forEach(remoteConv => {
            const localConv = merged.get(remoteConv.id);
            if (!localConv) {
                merged.set(remoteConv.id, {
                    ...remoteConv,
                    isActive: true
                });
                return;
            }
            // Message count guard: never accept a remote version that has
            // fewer messages, even if its _version is newer.
            const localMsgCount = localConv.messages?.length || 0;
            const remoteMsgCount = remoteConv.messages?.length || 0;
            if ((remoteConv._version || 0) > (localConv._version || 0)
                && (remoteMsgCount >= localMsgCount || localMsgCount <= 2)) {
                merged.set(remoteConv.id, {
                    ...remoteConv,
                    isActive: localConv?.isActive ?? true // Preserve active status
                });
            }
        });

        return Array.from(merged.values());
    }, []);

    // Cross-tab sync via BroadcastChannel (replaces localStorage 'storage' event).
    // Same-project tabs see each other's conversation/folder changes.
    // Different-project tabs are on different channels — fully isolated.
    useEffect(() => {
        if (!currentProject?.id) return;
        projectSync.join(currentProject.id);

        // Batch cross-tab streaming updates with requestAnimationFrame.
        // Multiple BroadcastChannel messages can arrive faster than React
        // can re-render; coalescing into one setState per frame avoids
        // creating intermediate Map objects and re-rendering 10+ consumers.
        let pendingChunk: { conversationId: string; content: string; reasoning?: string } | null = null;
        let chunkRafId: number | null = null;

        const handleConversationsChanged = async (msg: any) => {
            if (!isInitialized) return;
            try {
                const projectConvs = (await db.getConversations())
                    .filter(c => c.projectId === currentProject?.id || c.isGlobal);
                setConversations(prev => mergeConversations(prev, projectConvs));
            } catch (err) {
                console.error('📡 Sync: Failed to reload conversations:', err);
            }
        };

        const handleFoldersChanged = async () => {
            if (!isInitialized) return;
            try {
                const allFolders = await db.getFolders();
                const folderMap = new Map(allFolders.map(f => [f.id, f]));
                // Merge server folders so TaskPlan folders that haven't
                // been persisted to IndexedDB yet aren't lost, and
                // stale IndexedDB entries get updated with server status.
                try {
                    const serverFolders = await folderSyncApi.listServerFolders(currentProject?.id);
                    let changed = false;
                    for (const sf of serverFolders) {
                        const local = folderMap.get(sf.id);
                        if (serverFolderWins(local, sf)) {
                            folderMap.set(sf.id, { ...sf, projectId: sf.projectId || currentProject?.id });
                            changed = true;
                        }
                    }
                    if (changed) {
                        const merged = Array.from(folderMap.values());
                        Promise.all(merged.map(f => db.saveFolder(f))).catch(
                            e => console.warn('📡 handleFoldersChanged: Failed to persist folders:', e));
                    }
                } catch (e) {
                    // Server unavailable — use IndexedDB only
                }
                const projectFolders = Array.from(folderMap.values()).filter(
                    f => f.projectId === currentProject?.id || f.isGlobal
                );
                setFolders(projectFolders);
            } catch (err) {
                console.error('📡 Sync: Failed to reload folders:', err);
            }
        };

        const handleStreamingChunk = (msg: any) => {
            const { conversationId, content, reasoning } = msg;
            if (!content && !reasoning) return;

            // Ensure this conversation is marked as streaming in case we missed
            // the initial streaming-state event (e.g., tab opened mid-stream).
            // Set.has is O(1); new Set is created only when actually needed.
            setStreamingConversations(prev =>
                prev.has(conversationId) ? prev : new Set(prev).add(conversationId));

            // Stash the latest payload; the rAF callback will pick it up.
            // If another message arrives before the frame fires, we overwrite —
            // chatApi sends the full accumulated string, not deltas.
            pendingChunk = { conversationId, content, reasoning };

            if (chunkRafId === null) {
                chunkRafId = requestAnimationFrame(() => {
                    chunkRafId = null;
                    if (!pendingChunk) return;
                    const { conversationId: cid, content: c, reasoning: r } = pendingChunk;
                    pendingChunk = null;

                    if (c) {
                        setStreamedContentMap(prev => {
                            if (prev.get(cid) === c) return prev; // no-op: same content
                            const next = new Map(prev);
                            next.set(cid, c);
                            return next;
                        });
                    }
                    if (r) {
                        setReasoningContentMap(prev => {
                            if (prev.get(cid) === r) return prev;
                            const next = new Map(prev);
                            next.set(cid, r);
                            return next;
                        });
                    }
                });
            }
        };

        const handleStreamingState = (msg: any) => {
            const { conversationId, state } = msg;
            updateProcessingState(conversationId, state);
            if (state !== 'idle') {
                setStreamingConversations(prev => new Set(prev).add(conversationId));
            }
        };

        const handleStreamingEnded = (msg: any) => {
            removeStreamingConversation(msg.conversationId);
        };

        projectSync.on('conversations-changed', handleConversationsChanged);
        projectSync.on('conversation-created', handleConversationsChanged);
        projectSync.on('conversation-deleted', handleConversationsChanged);

        // T28: When another tab detects delegate status changes, update our
        // conversation state directly from the broadcast payload.
        // The polling tab includes { planId, delegates: { did: status } }
        // so receiving tabs can merge without a server/IndexedDB round-trip.
        const handleDelegateStatusChanged = async (msg: any) => {
            if (!isInitialized) return;
            const { planId, delegates } = msg;
            if (!planId || !delegates) return;
            setConversations(prev => prev.map(c => {
                if (!c.delegateMeta || c.delegateMeta.plan_id !== planId) return c;
                const did = c.delegateMeta.delegate_id;
                if (!did || !delegates[did]) return c;
                const newStatus = delegates[did];
                if (c.delegateMeta.status === newStatus) return c;
                return { ...c, delegateMeta: { ...c.delegateMeta, status: newStatus } };
            }));
        };
        projectSync.on('delegate-status-changed', handleDelegateStatusChanged);

        projectSync.on('folders-changed', handleFoldersChanged);
        projectSync.on('streaming-chunk', handleStreamingChunk);
        projectSync.on('streaming-state', handleStreamingState);
        projectSync.on('streaming-ended', handleStreamingEnded);

        return () => {
            // Cancel any pending rAF to prevent setState after unmount
            if (chunkRafId !== null) {
                cancelAnimationFrame(chunkRafId);
                chunkRafId = null;
            }
            pendingChunk = null;
            projectSync.off('conversations-changed', handleConversationsChanged);
            projectSync.off('conversation-created', handleConversationsChanged);
            projectSync.off('conversation-deleted', handleConversationsChanged);
            projectSync.off('delegate-status-changed', handleDelegateStatusChanged);
            projectSync.off('folders-changed', handleFoldersChanged);
            projectSync.off('streaming-chunk', handleStreamingChunk);
            projectSync.off('streaming-state', handleStreamingState);
            projectSync.off('streaming-ended', handleStreamingEnded);
        };
    }, [currentProject?.id, isInitialized, mergeConversations, updateProcessingState, removeStreamingConversation]);


    useEffect(() => {
        // Only create a new conversation ID if we're initialized and still don't have one
        // This prevents creating IDs before conversations are loaded from the database
        if (!currentConversationId && isInitialized && conversations.length === 0) {
            console.log('📝 No conversations loaded, creating initial conversation');
            setCurrentConversationId(uuidv4());
        }
        // REMOVED: The else-if branch that auto-selected mostRecent whenever
        // conversations.length changed.  This was triggered by sync adding
        // conversations from other windows, causing forced focus switches.
        // The initial conversation is set by initializeWithRecovery or
        // handleProjectSwitch; ongoing selection is user-driven only.
    }, [currentConversationId, isInitialized]);

    const setDisplayMode = useCallback((conversationId: string, mode: 'raw' | 'pretty') => {
        setConversations(prev => {
            const updated = prev.map(conv => {
                if (conv.id === conversationId) {
                    return {
                        ...conv,
                        displayMode: mode,
                        _version: Date.now()
                    };
                }
                return conv;
            });
            queueSave(updated, { changedIds: [conversationId] }).catch(console.error);
            return updated;
        });
    }, [queueSave]);

    const toggleMessageMute = useCallback((conversationId: string, messageIndex: number) => {
        setConversations(prev => {
            const updated = prev.map(conv => {
                if (conv.id === conversationId) {
                    const updatedMessages = [...conv.messages];
                    if (updatedMessages[messageIndex]) {
                        updatedMessages[messageIndex] = {
                            ...updatedMessages[messageIndex],
                            muted: !updatedMessages[messageIndex].muted
                        };
                    }
                    // Don't update _version for mute changes to avoid triggering scroll resets
                    return { ...conv, messages: updatedMessages };
                }
                return conv;
            });
            queueSave(updated, { changedIds: [conversationId] }).catch(console.error);

            // Dispatch event to notify token counter of mute state change
            window.dispatchEvent(new CustomEvent('messagesMutedChanged', {
                detail: { conversationId, messageIndex }
            }));
            return updated;
        });

    }, [queueSave]);

    // New functions for session management integration
    const moveChatToGroup = useCallback(async (chatId: string, groupId: string | null) => {
        // TODO: Get project context here when needed
        // For now, just update local state

        // Update locally first
        setConversations(prev => prev.map(conv =>
            conv.id === chatId ? { ...conv, folderId: groupId, _version: Date.now() } : conv
        ));

        // TODO: When Phase 4 is implemented, sync to server
        // For now, just update local state
    }, []);

    const setChatContexts = useCallback(async (
        chatId: string,
        contextIds: string[],
        skillIds: string[],
        additionalFiles: string[],
        additionalPrompt: string | null
    ) => {
        // TODO: Get project context here when needed
        // For now, just log

        // Update chat with new context configuration
        // This will be used when we persist to server in Phase 3
        console.log('Setting chat contexts:', { chatId, contextIds, skillIds, additionalFiles });

        // TODO: When Phase 3 is implemented, persist to server
    }, []);

    const toggleFolderGlobal = useCallback(async (folderId: string) => {
        const folder = folders.find(f => f.id === folderId);
        if (!folder) return;

        const wasGlobal = folder.isGlobal;
        const updatedFolder: ConversationFolder = {
            ...folder,
            isGlobal: !wasGlobal,
            // When un-globaling, pin to current project
            projectId: wasGlobal ? currentProject?.id : folder.projectId,
            updatedAt: Date.now()
        };

        await updateFolder(updatedFolder);
        console.log(`📁 Folder "${folder.name}" is now ${updatedFolder.isGlobal ? 'global' : 'project-scoped'}`);
    }, [folders, currentProject?.id, updateFolder]);

    const toggleConversationGlobal = useCallback(async (conversationId: string) => {
        setConversations(prev => {
            const updated = prev.map(conv => {
                if (conv.id === conversationId) {
                    const wasGlobal = conv.isGlobal;
                    return {
                        ...conv,
                        isGlobal: !wasGlobal,
                        // When un-globaling, pin to current project
                        projectId: wasGlobal ? currentProject?.id : conv.projectId,
                        _version: Date.now()
                    };
                }
                return conv;
            });
            queueSave(updated, { changedIds: [conversationId] }).catch(console.error);
            return updated;
        });
    }, [currentProject?.id, queueSave]);
    const moveConversationToProject = useCallback(async (conversationId: string, targetProjectId: string) => {
        const sourceProjectId = currentProject?.id;

        // Capture the conversation data from the updater's `prev` (guaranteed
        // fresh) instead of the stale `conversations` closure.  The closure
        // value may be outdated because `conversations` is NOT (and should not
        // be) in the dependency array — it changes every render.
        let capturedConv: Conversation | undefined;

        setConversations(prev => {
            capturedConv = prev.find(c => c.id === conversationId);

            const updated = prev.map(conv => {
                if (conv.id === conversationId) {
                    return {
                        ...conv,
                        projectId: targetProjectId,
                        isGlobal: false,
                        _version: Date.now()
                    };
                }
                return conv;
            });
            queueSave(updated, { changedIds: [conversationId] }).catch(console.error);

            // Remove from the visible list when moved to a different project
            if (targetProjectId !== sourceProjectId) {
                return updated.filter(c => c.id !== conversationId);
            }
            return updated;
        });

        // If the moved conversation was the active one, switch away so the
        // user isn't left with a stale currentConversationId pointing at a
        // conversation that no longer exists in this project's visible list.
        if (currentConversationId === conversationId && targetProjectId !== sourceProjectId) {
            startNewChat();
        }

        // Server-side move: push to target first, THEN delete from source.
        // This ordering ensures the conversation exists on the target before
        // we remove it from the source, preventing data loss on partial failure.
        if (sourceProjectId && sourceProjectId !== targetProjectId && capturedConv) {
            const serverChat = syncApi.conversationToServerChat(
                { ...capturedConv, projectId: targetProjectId, isGlobal: false, _version: Date.now() },
                targetProjectId
            );
            try {
                // 1. Push to target project on server
                await syncApi.bulkSync(targetProjectId, [serverChat]);
                console.log(`📡 Move: pushed conversation to target project ${targetProjectId}`);

                // 2. Delete from source project on server (best-effort, ignore 404)
                const headers: Record<string, string> = { 'Content-Type': 'application/json' };
                const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
                if (projectPath) headers['X-Project-Root'] = projectPath;

                const deleteRes = await fetch(
                    `/api/v1/projects/${sourceProjectId}/chats/${conversationId}`,
                    { method: 'DELETE', headers }
                );
                if (deleteRes.ok) {
                    console.log(`📡 Move: deleted conversation from source project ${sourceProjectId}`);
                } else {
                    console.log(`📡 Move: source delete returned ${deleteRes.status} (non-fatal)`);
                }
            } catch (e) {
                console.warn('📡 Move: server-side move failed (non-fatal):', e);
            }
        } else if (sourceProjectId && sourceProjectId !== targetProjectId && !capturedConv) {
            console.error(`📡 Move: conversation ${conversationId} not found in state — server-side move skipped!`);
        }
    }, [currentProject?.id, queueSave, currentConversationId, startNewChat]);

    const moveFolderToProject = useCallback(async (folderId: string, targetProjectId: string) => {
        const sourceProjectId = currentProject?.id;
        const folder = folders.find(f => f.id === folderId);
        if (!folder) {
            console.warn('📁 moveFolderToProject: folder not found:', folderId);
            return;
        }

        console.log(`📁 Moving folder "${folder.name}" from project ${sourceProjectId} to ${targetProjectId}`);

        // 1. Update the folder's projectId
        const updatedFolder: ConversationFolder = {
            ...folder,
            projectId: targetProjectId,
            isGlobal: false,
            updatedAt: Date.now()
        };
        await updateFolder(updatedFolder);

        // 2. Move all conversations in this folder to the target project
        const conversationsInFolder = conversations.filter(c => c.folderId === folderId);
        console.log(`📁 Moving ${conversationsInFolder.length} conversations with folder`);

        const movedIds: string[] = [];
        setConversations(prev => {
            const updated = prev.map(conv => {
                if (conv.folderId === folderId) {
                    movedIds.push(conv.id);
                    return { ...conv, projectId: targetProjectId, _version: Date.now() };
                }
                return conv;
            });
            queueSave(updated, { changedIds: movedIds }).catch(console.error);

            // Remove moved items from visible list
            if (targetProjectId !== sourceProjectId) {
                return updated.filter(c =>
                    !movedIds.includes(c.id) || c.projectId === sourceProjectId || c.isGlobal
                );
            }
            return updated;
        });

        // 3. Remove folder from visible list if moved away
        if (targetProjectId !== sourceProjectId) {
            setFolders(prev => prev.filter(f => f.id !== folderId));
        }

        // 4. Server sync: push conversations to target, then clean up source
        if (sourceProjectId && sourceProjectId !== targetProjectId) {
            try {
                const chatsToSync = conversationsInFolder.map(c =>
                    syncApi.conversationToServerChat(
                        { ...c, projectId: targetProjectId, _version: Date.now() },
                        targetProjectId
                    )
                );
                if (chatsToSync.length > 0) {
                    await syncApi.bulkSync(targetProjectId, chatsToSync);
                    console.log(`📡 FolderMove: pushed ${chatsToSync.length} conversations to target`);
                }
                // Best-effort delete from source
                const headers: Record<string, string> = {};
                const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
                if (projectPath) headers['X-Project-Root'] = projectPath;
                for (const conv of conversationsInFolder) {
                    fetch(`/api/v1/projects/${sourceProjectId}/chats/${conv.id}`, {
                        method: 'DELETE', headers
                    }).catch(() => {});
                }
                folderSyncApi.deleteServerFolder(sourceProjectId, folderId).catch(() => {});
            } catch (e) {
                console.warn('📡 FolderMove: server sync failed (non-fatal):', e);
            }
        }
    }, [currentProject?.id, folders, conversations, updateFolder, queueSave]);

    const value = useMemo(() => ({
        streamedContentMap,
        reasoningContentMap,
        dynamicTitleLength,
        lastResponseIncomplete,
        setDynamicTitleLength,
        setStreamedContentMap,
        setReasoningContentMap,
        getProcessingState,
        updateProcessingState,
        isStreaming,
        isStreamingAny,
        streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
        setConversations,
        setIsStreaming,
        conversations,
        currentConversationId,
        currentMessages,
        setCurrentConversationId,
        addMessageToConversation,
        loadConversationAndScrollToMessage,
        loadConversation,
        startNewChat,
        isTopToBottom,
        setIsTopToBottom,
        scrollToBottom,
        userHasScrolled,
        setUserHasScrolled,
        recordManualScroll,
        folders,
        setFolders,
        currentFolderId,
        setCurrentFolderId,
        folderFileSelections,
        setFolderFileSelections,
        createFolder,
        updateFolder,
        deleteFolder,
        setDisplayMode,
        moveConversationToFolder,
        dbError,
        isLoadingConversation,
        isProjectSwitching,
        toggleMessageMute,
        editingMessageIndex,
        setEditingMessageIndex,
        throttlingRecoveryData,
        setThrottlingRecoveryData,
        moveChatToGroup,
        toggleConversationGlobal,
        moveConversationToProject,
        moveFolderToProject,
        toggleFolderGlobal,
        setChatContexts,
    }), [
        currentMessages,
        editingMessageIndex,
        dynamicTitleLength,
        lastResponseIncomplete,
        setDynamicTitleLength,
        setStreamedContentMap,
        getProcessingState,
        updateProcessingState,
        isStreaming,
        isStreamingAny,
        streamingConversations,
        addStreamingConversation,
        removeStreamingConversation,
        setConversations,
        setIsStreaming,
        conversations,
        currentConversationId,
        currentMessages,
        setCurrentConversationId,
        addMessageToConversation,
        loadConversation,
        startNewChat,
        isTopToBottom,
        setIsTopToBottom,
        scrollToBottom,
        userHasScrolled,
        setUserHasScrolled,
        recordManualScroll,
        folders,
        setFolders,
        currentFolderId,
        setCurrentFolderId,
        folderFileSelections,
        setFolderFileSelections,
        createFolder,
        updateFolder,
        deleteFolder,
        setDisplayMode,
        moveConversationToFolder,
        dbError,
        isLoadingConversation,
        toggleMessageMute,
        editingMessageIndex,
        setEditingMessageIndex,
        throttlingRecoveryData,
        setThrottlingRecoveryData,
        moveChatToGroup,
        toggleConversationGlobal,
        moveConversationToProject,
        moveFolderToProject,
        toggleFolderGlobal,
        setChatContexts,
    ]);

    // Temporary debug command
    useEffect(() => {
        // Enhanced debug helper to diagnose history corruption
        (window as any).debugChatContext = () => {
            console.log('=== CHAT CONTEXT DEBUG ===');
            console.log('Current Conversation ID:', currentConversationId);
            console.log('Total conversations in memory:', conversations.length);
            console.log('Active conversations in memory:', conversations.filter(c => c.isActive !== false).length);
            console.log('Inactive conversations in memory:', conversations.filter(c => c.isActive === false).length);

            const currentConv = conversations.find(c => c.id === currentConversationId);
            console.log('Current conversation in memory:', currentConv ? {
                id: currentConv.id,
                title: currentConv.title,
                isActive: currentConv.isActive,
                folderId: currentConv.folderId,
                messageCount: currentConv.messages.length
            } : 'NOT FOUND IN MEMORY');

            console.log('\n=== CONVERSATION LIST ===');
            conversations.forEach((conv, idx) => {
                console.log(`${idx + 1}. ${conv.id.substring(0, 8)} - "${conv.title.substring(0, 30)}" - isActive: ${conv.isActive}, messages: ${conv.messages.length}`);
            });

            return { conversations, currentConversationId, currentConv };
        };

        // Debug helper to check IndexedDB directly
        (window as any).debugIndexedDB = async () => {
            console.log('=== INDEXEDDB DEBUG ===');
            try {
                const dbConversations = await db.getConversations();
                console.log('Total conversations in IndexedDB:', dbConversations.length);
                console.log('Active in IndexedDB:', dbConversations.filter(c => c.isActive !== false).length);
                console.log('Inactive in IndexedDB:', dbConversations.filter(c => c.isActive === false).length);

                const currentInDB = dbConversations.find(c => c.id === currentConversationId);
                console.log('Current conversation in IndexedDB:', currentInDB ? {
                    id: currentInDB.id,
                    title: currentInDB.title,
                    isActive: currentInDB.isActive,
                    folderId: currentInDB.folderId,
                    messageCount: currentInDB.messages.length
                } : 'NOT FOUND IN INDEXEDDB');

                console.log('\n=== INDEXEDDB CONVERSATION LIST ===');
                dbConversations.forEach((conv, idx) => {
                    console.log(`${idx + 1}. ${conv.id.substring(0, 8)} - "${conv.title.substring(0, 30)}" - isActive: ${conv.isActive}, messages: ${conv.messages.length}`);
                });

                return { dbConversations, currentInDB };
            } catch (error) {
                console.error('Error reading IndexedDB:', error);
            }
        };
    }, [conversations, currentConversationId]);

    return (
        <chatContext.Provider value={value}>
            <ScrollProvider
                scrollToBottom={scrollToBottom}
                userHasScrolled={userHasScrolled}
                setUserHasScrolled={setUserHasScrolled}
                recordManualScroll={recordManualScroll}
                isTopToBottom={isTopToBottom}
                setIsTopToBottom={setIsTopToBottom}
            >
            <ConversationListProvider
                conversations={conversations}
                setConversations={setConversations}
                folders={folders}
                setFolders={setFolders}
                currentFolderId={currentFolderId}
                setCurrentFolderId={setCurrentFolderId}
                folderFileSelections={folderFileSelections}
                setFolderFileSelections={setFolderFileSelections}
                createFolder={createFolder}
                updateFolder={updateFolder}
                deleteFolder={deleteFolder}
                moveConversationToFolder={moveConversationToFolder}
                moveChatToGroup={moveChatToGroup}
                toggleConversationGlobal={toggleConversationGlobal}
                moveConversationToProject={moveConversationToProject}
                moveFolderToProject={moveFolderToProject}
                toggleFolderGlobal={toggleFolderGlobal}
                dbError={dbError}
                isProjectSwitching={isProjectSwitching}
                isLoadingConversation={isLoadingConversation}
            >
            <ActiveChatProvider
                currentConversationId={currentConversationId}
                currentMessages={currentMessages}
                currentDisplayMode={conversations.find(c => c.id === currentConversationId)?.displayMode ?? 'pretty'}
                setCurrentConversationId={setCurrentConversationId}
                addMessageToConversation={addMessageToConversation}
                loadConversation={loadConversation}
                loadConversationAndScrollToMessage={loadConversationAndScrollToMessage}
                startNewChat={startNewChat}
                editingMessageIndex={editingMessageIndex}
                setEditingMessageIndex={setEditingMessageIndex}
                isStreaming={isStreaming}
                setIsStreaming={setIsStreaming}
                streamingConversations={streamingConversations}
                addStreamingConversation={addStreamingConversation}
                removeStreamingConversation={removeStreamingConversation}
                streamedContentMap={streamedContentMap}
                setStreamedContentMap={setStreamedContentMap}
                reasoningContentMap={reasoningContentMap}
                setReasoningContentMap={setReasoningContentMap}
                getProcessingState={getProcessingState}
                updateProcessingState={updateProcessingState}
                dynamicTitleLength={dynamicTitleLength}
                setDynamicTitleLength={setDynamicTitleLength}
                lastResponseIncomplete={lastResponseIncomplete}
                setDisplayMode={setDisplayMode}
                toggleMessageMute={toggleMessageMute}
                setChatContexts={setChatContexts}
                throttlingRecoveryData={throttlingRecoveryData}
                setThrottlingRecoveryData={setThrottlingRecoveryData}
            >
            <StreamingProvider
                isStreaming={isStreaming}
                isStreamingAny={isStreamingAny}
                currentConversationId={currentConversationId}
                streamingConversations={streamingConversations}
            >
                {children}
            </StreamingProvider>
            </ActiveChatProvider>
            </ConversationListProvider>
            </ScrollProvider>
        </chatContext.Provider>
    );
}

export function useChatContext(): ChatContext {
    const context = useContext(chatContext);
    if (!context) {
        throw new Error('useChatContext must be used within a ChatProvider');
    }
    return context;
}
