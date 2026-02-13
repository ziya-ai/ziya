import React, { createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo, useLayoutEffect } from 'react';
import { Conversation, Message, ConversationFolder } from "../utils/types";
import { v4 as uuidv4 } from "uuid";
import { db } from '../utils/db';
import { detectIncompleteResponse } from '../utils/responseUtils';
import { message } from 'antd';
import { useTheme } from './ThemeContext';
import { useConfig } from './ConfigContext';
import { useProject } from './ProjectContext';
import { useFolderContext } from './FolderContext';
import * as syncApi from '../api/conversationSyncApi';

export type ProcessingState = 'idle' | 'sending' | 'awaiting_model_response' | 'processing_tools' | 'awaiting_tool_response' | 'tool_throttling' | 'tool_limit_reached' | 'error';

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
    const { checkedKeys } = useFolderContext();
    const renderCount = useRef(0);
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamedContentMap, setStreamedContentMap] = useState(() => new Map<string, string>());
    const [reasoningContentMap, setReasoningContentMap] = useState(() => new Map<string, string>());
    const [isStreamingAny, setIsStreamingAny] = useState(false);
    const [processingStates, setProcessingStates] = useState(() => new Map<string, ConversationProcessingState>());
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>('');
    const currentConversationRef = useRef<string>(currentConversationId);
    const [currentMessages, setCurrentMessages] = useState<Message[]>([]);

    /**
     * Tag any IndexedDB conversations that lack a projectId with the given projectId.
     * Returns the full (mutated) conversation list after persisting the migration.
     * Both INIT_SYNC and PROJECT_SWITCH call this to avoid duplicating migration logic.
     */
    const migrateUntaggedConversations = useCallback(async (
        allConversations: Conversation[],
        projectId: string
    ): Promise<Conversation[]> => {
        const untagged = allConversations.filter(c => !c.projectId);
        if (untagged.length === 0) return allConversations;

        console.log(`🔄 MIGRATION: Tagging ${untagged.length} conversations without projectId → project "${projectId}"`);
        const migrated = allConversations.map(c => {
            if (!c.projectId) {
                return { ...c, projectId, _version: Date.now() };
            }
            return c;
        });

        await db.saveConversations(migrated);
        console.log(`✅ MIGRATION: Tagged ${untagged.length} conversations`);
        return migrated;
    }, []);

    // Listen for project switch - reload project-specific conversations
    useEffect(() => {
        const handleProjectSwitch = async (event: CustomEvent) => {
            const { projectId, projectPath, projectName } = event.detail;
            console.log('💬 PROJECT_SWITCH: Loading conversations for project:', projectName, projectId);

            try {
                // Mark this project as synced so the init effect doesn't re-run
                serverSyncedForProject.current = projectId;

                // 1. Load from SERVER first (source of truth across ports)
                let serverChats: syncApi.ServerChat[] = [];
                try {
                    serverChats = await syncApi.listChats(projectId);
                    console.log(`📡 PROJECT_SWITCH: Got ${serverChats.length} chats from server`);
                } catch (e) {
                    console.warn('📡 PROJECT_SWITCH: Server unavailable, falling back to IndexedDB:', e);
                }

                // 2. Load from IndexedDB (local cache)
                let allConversations = await db.getConversations();

                // 3. Migrate untagged conversations (shared helper)
                allConversations = await migrateUntaggedConversations(allConversations, projectId);

                // 4. Merge: server chats win, then add local-only conversations
                const localProjectConvs = allConversations.filter(c => c.projectId === projectId || c.isGlobal);
                const serverIdSet = new Set(serverChats.map(c => c.id));
                const localOnly = localProjectConvs.filter(c => !serverIdSet.has(c.id));

                // Also include global server chats that may belong to other projects
                const globalServerChats = serverChats.filter((sc: any) => sc.isGlobal);
                // Convert server chats to frontend Conversation shape
                const serverAsConversations = serverChats.map((sc: any) => ({
                    ...sc,
                    projectId: projectId,
                    lastAccessedAt: sc.lastAccessedAt || sc.lastActiveAt,
                    isActive: sc.isActive !== false,
                    _version: sc._version || Date.now(),
                }));

                const mergedConversations = [...serverAsConversations, ...localOnly];

                // 5. Push local-only conversations to server (async, non-blocking)
                if (localOnly.length > 0) {
                    console.log(`📡 SYNC: Pushing ${localOnly.length} local-only conversations to server`);
                    const chatsToSync = localOnly.map(c => syncApi.conversationToServerChat(c, projectId));
                    syncApi.bulkSync(projectId, chatsToSync).then(result => {
                        console.log('📡 SYNC result:', result);
                    }).catch(e => console.warn('📡 SYNC failed (non-fatal):', e));
                }

                const allFolders = await db.getFolders();
                const globalFolderIds = new Set(allFolders.filter(f => f.isGlobal).map(f => f.id));
                const projectFolders = allFolders.filter(f => f.projectId === projectId || f.isGlobal);

                console.log(`📊 PROJECT_SWITCH: ${mergedConversations.length} conversations (${serverChats.length} server, ${localOnly.length} local-only), ${projectFolders.length} folders`);

                // Update state with filtered data
                setConversations(mergedConversations);
                setFolders(projectFolders);

                // Set current conversation
                if (mergedConversations.length > 0) {
                    const mostRecent = mergedConversations.reduce((a, b) =>
                        (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                    );
                    setCurrentConversationId(mostRecent.id);
                    setCurrentMessages(mostRecent.messages);
                    console.log(`✅ PROJECT_SWITCH: Loaded conversation "${mostRecent.title}"`);
                } else {
                    // No conversations for this project - create new one
                    const newConversationId = uuidv4();
                    const newConversation: Conversation = {
                        id: newConversationId,
                        title: 'New Conversation',
                        projectId,
                        messages: [],
                        lastAccessedAt: Date.now(),
                        isActive: true,
                        _version: Date.now(),
                        hasUnreadResponse: false
                    };
                    setConversations(prev => [...prev, newConversation]);
                    setCurrentConversationId(newConversationId);
                    setCurrentMessages([]);
                    console.log('✅ PROJECT_SWITCH: No conversations found, created new one');
                }
            } catch (error) {
                console.error('❌ PROJECT_SWITCH: Failed to load project data:', error);
                // Fallback: create fresh conversation
                const newConversationId = uuidv4();
                setCurrentConversationId(newConversationId);
                setCurrentMessages([]);
            }

            // Clear streaming state
            setStreamedContentMap(new Map());
            setReasoningContentMap(new Map());
            setProcessingStates(new Map());

            // CRITICAL FIX: Restore file selections for the switched-to project
            const savedSelections = projectFileSelections.current.get(projectId);
            if (savedSelections && savedSelections.size > 0) {
                console.log(`📂 Restoring ${savedSelections.size} file selections for project ${projectName}`);
                // Dispatch event to FolderContext to restore selections
                window.dispatchEvent(new CustomEvent('restoreProjectFileSelections', {
                    detail: { projectId, selections: Array.from(savedSelections) }
                }));
            }
        };

        window.addEventListener('projectSwitched', handleProjectSwitch as EventListener);
        return () => window.removeEventListener('projectSwitched', handleProjectSwitch as EventListener);
    }, [currentProject?.id]);

    // CRITICAL: Persist currentConversationId to localStorage whenever it changes
    useEffect(() => {
        if (isEphemeralMode) return;
        localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', currentConversationId);
    }, [currentConversationId, isEphemeralMode]);

    // CRITICAL FIX: Persist the CURRENT conversation to localStorage immediately
    // This ensures the active conversation survives refresh even if IndexedDB write is pending
    useEffect(() => {
        if (isEphemeralMode) return;
    }, [currentConversationId, conversations, isEphemeralMode]);

    // Track if we've initialized ephemeral mode
    const ephemeralInitialized = useRef(false);

    // CRITICAL: Clear persisted state when ephemeral mode is detected
    useEffect(() => {
        if (isEphemeralMode && !ephemeralInitialized.current) {
            console.log('🔒 EPHEMERAL: Clearing persisted conversation state');
            ephemeralInitialized.current = true;

            // Clear all conversation-related localStorage
            try {
                localStorage.removeItem('ZIYA_CURRENT_CONVERSATION_ID');
                localStorage.removeItem('ZIYA_CONVERSATION_BACKUP');
                localStorage.removeItem('ZIYA_CURRENT_CONVERSATION_DATA');
                localStorage.removeItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY');
                localStorage.removeItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY');
            } catch (e) {
                console.warn('Failed to clear localStorage:', e);
            }

            // Force a fresh conversation ID
            setCurrentConversationId(uuidv4());
        }
    }, [isEphemeralMode]);

    const [streamingConversations, setStreamingConversations] = useState<Set<string>>(new Set());
    const [isTopToBottom, setIsTopToBottom] = useState(() => {
        const saved = localStorage.getItem('ZIYA_TOP_DOWN_MODE');
        return saved ? JSON.parse(saved) : true;
    });
    const [isInitialized, setIsInitialized] = useState(false);
    const [userHasScrolled, setUserHasScrolled] = useState(false);
    const conversationIdRestored = useRef(false);

    // Restore conversation ID from localStorage ONLY if not in ephemeral mode
    // This must run AFTER config is loaded AND after conversations are initialized
    // CRITICAL: Only run ONCE to prevent overwriting user's current conversation
    useEffect(() => {
        if (!isEphemeralMode && isInitialized && conversations.length > 0 && !conversationIdRestored.current && !currentConversationId) {
            conversationIdRestored.current = true; // Mark as restored to prevent re-running
            try {
                const savedCurrentId = localStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID');
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

    // Track which project has been server-synced to avoid duplicate syncs
    const serverSyncedForProject = useRef<string | null>(null);
    const dirtyConversationIds = useRef<Set<string>>(new Set());
    const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
    const lastManualScrollTime = useRef<number>(0);
    const manualScrollCooldownActive = useRef<boolean>(false);
    const [messageUpdateCounter, setMessageUpdateCounter] = useState(0);
    const [throttlingRecoveryData, setThrottlingRecoveryData] = useState<Map<string, { toolResults?: any[]; partialContent?: string }>>(new Map());

    // CRITICAL: Track scroll state per conversation to prevent cross-conversation interference

    // CRITICAL FIX: Preserve file selections per project across DB refreshes
    const projectFileSelections = useRef<Map<string, Set<string>>>(new Map());

    // Save current selections before project operations
    const preserveCurrentFileSelections = useCallback(() => {
        if (currentProject?.id) {
            projectFileSelections.current.set(currentProject.id, new Set(checkedKeys));
        }
    }, [currentProject?.id, checkedKeys]);

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
    }, [streamingConversations, streamedContentMap, currentConversationId, isTopToBottom]);

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
            console.log('Adding to streaming set:', { id, currentSet: Array.from(prev) });
            next.add(id);
            setStreamedContentMap(prev => new Map(prev).set(id, ''));
            setIsStreaming(true);
            setIsStreamingAny(true);
            return next;
        });
        updateProcessingState(id, 'sending');
    }, [updateProcessingState]);

    const removeStreamingConversation = useCallback((id: string) => {
        // CRITICAL: Check if this is the CURRENT conversation
        const isCurrentConv = id === currentConversationId;

        console.log('Removing from streaming set:', { id, currentSet: Array.from(streamingConversations) });
        setStreamingConversations(prev => {
            const next = new Set(prev);
            // CRITICAL: Preserve scroll for non-current conversations
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

        // Auto-reset processing state when streaming ends
        setProcessingStates(prev => {
            const next = new Map(prev);
            next.set(id, { state: 'idle', lastUpdated: Date.now() });
            return next;
        });
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

        // CRITICAL FIX: Filter out corrupted conversations before any processing
        const validConversations = conversations.filter(conv => {
            const isValid = conv &&
                conv.id &&
                typeof conv.id === 'string' &&
                conv.title !== undefined &&
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
            const isRetry = retryCount > 0;

            // Pre-save validation
            const activeCount = validatedConversations.filter(c => c.isActive).length;
            console.debug(`Saving ${validatedConversations.length} conversations (${activeCount} active)`);

            // SAFETY CHECK: Never save filtered conversations list
            // BUGFIX: Cache other-project conversations instead of reading ALL from DB on every save
            if (currentProject?.id) {
                const cache = otherProjectConvsCache.current;
                const cacheAge = Date.now() - cache.timestamp;
                let otherProjectConversations: Conversation[];
                if (cacheAge > 60000 || cache.convs.length === 0) {
                    const allDbConversations = await db.getConversations();
                    otherProjectConversations = allDbConversations.filter(c => c.projectId !== currentProject.id);
                    otherProjectConvsCache.current = { convs: otherProjectConversations, timestamp: Date.now() };
                    console.log(`💾 SAVE: Refreshed other-project cache: ${otherProjectConversations.length} conversations`);
                } else {
                    otherProjectConversations = cache.convs;
                }
                validatedConversations = [...validatedConversations, ...otherProjectConversations];
            }

            // Save all conversations - but don't throw if it fails
            try {
                await db.saveConversations(validatedConversations);
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
                    const dirtyConvs = validatedConversations.filter(
                        c => c.projectId === currentProject.id && dirty.has(c.id)
                    );
                    dirtyConversationIds.current = new Set();
                    // Async fire-and-forget — don't block the save queue
                    Promise.resolve().then(async () => {
                        try {
                            const chatsToSync = dirtyConvs.map(c =>
                                syncApi.conversationToServerChat(c, currentProject.id)
                            );
                            if (chatsToSync.length > 0) {
                                await syncApi.bulkSync(currentProject.id, chatsToSync);
                                console.log(`📡 DUAL_WRITE: Synced ${chatsToSync.length} dirty conversations to server`);
                            }
                        } catch (e) {
                            console.warn('📡 DUAL_WRITE: Server sync failed (non-fatal):', e);
                        }
                    });
                }
            }

            // Post-save validation with healing - only if not explicitly skipped
            if (!skipValidation) {
                try {
                    const savedConversations = await db.getConversations();

                    // CRITICAL FIX: Filter corrupted entries from database
                    const validSavedConversations = savedConversations.filter(c =>
                        c && c.id && typeof c.id === 'string' &&
                        c.title !== undefined && Array.isArray(c.messages)
                    );

                    if (validSavedConversations.length < savedConversations.length) {
                        console.warn(`🧹 FILTERED ${savedConversations.length - validSavedConversations.length} CORRUPTED ENTRIES FROM DB READ`);
                    }

                    const savedActiveCount = validSavedConversations.filter(c => c.isActive).length;

                    // Only trigger healing if mismatch is significant (>1 conversation difference)
                    // and we haven't exceeded max retries
                    const countDifference = Math.abs(savedActiveCount - activeCount);

                    if (countDifference > 1 && savedActiveCount !== activeCount) {
                        console.warn(`⚠️ SAVE VALIDATION MISMATCH: Expected ${activeCount} active conversations, got ${savedActiveCount} (difference: ${countDifference})`);

                        if (retryCount < maxRetries) {
                            console.log(`🔄 HEALING ATTEMPT ${retryCount + 1}/${maxRetries}: Retrying save with validated data`);

                            // CRITICAL: Re-validate all conversations before healing attempt
                            const revalidatedConversations = validatedConversations.filter(c =>
                                c.id && c.messages && Array.isArray(c.messages) && c.title
                            );

                            // If we filtered out corrupted data, log it
                            if (revalidatedConversations.length < validatedConversations.length) {
                                console.warn(`🧹 CLEANED: Removed ${validatedConversations.length - revalidatedConversations.length} corrupted conversations`);
                            }

                            console.log(`🔍 VALIDATION: Filtered ${validatedConversations.length} -> ${revalidatedConversations.length} conversations`);

                            // Wait a bit for any pending operations to complete
                            await new Promise(resolve => setTimeout(resolve, 100 * (retryCount + 1)));

                            // Merge current and saved data to ensure consistency
                            const mergedConversations = mergeConversationsForHealing(validatedConversations, savedConversations);

                            // Save directly — re-entering queueSave from inside the queue deadlocks the promise chain
                            await db.saveConversations(mergedConversations);
                            console.log(`✅ HEALING: Direct save completed (attempt ${retryCount + 1}/${maxRetries})`);
                        } else {
                            // After max retries, disable validation but continue operation
                            console.error(`🚨 HEALING FAILED: After ${maxRetries} attempts, disabling validation to prevent app failure`);
                            console.warn('🏥 EMERGENCY MODE: Trusting database state over memory to prevent corruption');

                            // When healing fails, trust the database state, not memory
                            // This prevents phantom conversations from corrupting the database
                            const trustedConversations = savedConversations.map(c => ({
                                ...c,
                                isActive: c.isActive !== false,
                                _version: Date.now()
                            }));

                            // Log the state for debugging but don't throw
                            console.debug('Final state before emergency mode:', { validatedConversations, savedConversations, activeCount, savedActiveCount });
                        }
                    }
                } catch (validationError) {
                    console.error('❌ VALIDATION ERROR during healing:', validationError);

                    if (retryCount < maxRetries && !isRecoveryAttempt) {
                        console.log(`🔄 VALIDATION RETRY ${retryCount + 1}/${maxRetries}: Retrying after validation error`);
                        await new Promise(resolve => setTimeout(resolve, 200 * (retryCount + 1)));
                        // Save directly — re-entering queueSave from inside the queue deadlocks the promise chain
                        await db.saveConversations(validatedConversations);
                        console.log(`✅ VALIDATION RETRY: Direct save completed (attempt ${retryCount + 1}/${maxRetries})`);
                    } else {
                        console.warn('🏥 EMERGENCY MODE: Skipping validation due to persistent errors');
                    }
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

        const folderId = currentFolderId;
        // Use the dynamicTitleLength from state - updated only by UI components

        // Debug logging to see when messages are added
        console.log('📝 Adding message:', { role: message.role, conversationId: targetConversationId, titleLength: dynamicTitleLength });

        // Check if this is an assistant message and if it appears incomplete
        if (message.role === 'assistant' && message.content) {
            setLastResponseIncomplete(detectIncompleteResponse(message.content));
        }

        messageUpdateCount.current += 1;
        setConversations(prevConversations => {
            const existingConversation = prevConversations.find(c => c.id === conversationId);
            const isFirstMessage = existingConversation?.messages.length === 0;

            // CRITICAL FIX: Determine if this is a non-current conversation dynamically
            // Don't trust the caller's isNonCurrentConversation - compute it from current state
            // This handles concurrent conversations and user switching mid-stream
            const actuallyNonCurrent = conversationId !== currentConversationId;

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
                            folderId: folderId,
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
                    folderId: folderId,
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
    }, [currentConversationId, currentFolderId, dynamicTitleLength, queueSave]);

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
    }, [currentConversationId, conversations]);

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
                    attemptDatabaseRecovery(),
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
                    initializeWithRecovery(),
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
            const targetFolderId = specificFolderId !== undefined ? specificFolderId : currentFolderId;

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
                    try {
                        const backupData = JSON.stringify([...updatedConversations, newConversation]);
                        localStorage.setItem('ZIYA_CONVERSATION_BACKUP', backupData);
                    } catch (backupError) {
                        console.warn('⚠️ NEW CHAT: Backup also failed:', backupError);
                    }
                }

                setConversations([...updatedConversations, newConversation]);
                setCurrentMessages([]);
                setCurrentConversationId(newId);

                try {
                    localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', newId);
                } catch (e) {
                    console.warn('Failed to persist conversation ID:', e);
                }

            } catch (saveError) {
                console.error('Failed to save new conversation, creating in memory:', saveError);
                setConversations([...updatedConversations, newConversation]);
                setCurrentMessages([]);
                setCurrentConversationId(newId);
                try {
                    localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', newId);
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
    }, [isInitialized, currentConversationId, currentFolderId, conversations, queueSave]);

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

    const loadConversation = useCallback(async (conversationId: string) => {
        setIsLoadingConversation(true);

        // Only scroll if we're actually switching conversations
        const isActualSwitch = conversationId !== currentConversationId;

        try {

            console.log('🔄 Loading conversation:', conversationId, 'isActualSwitch:', isActualSwitch);

            console.log('🔄 Loading conversation:', conversationId);

            // Don't remove streaming for the conversation we're switching away from
            // First update conversations in memory
            // Mark current conversation as read
            setConversations(prevConversations => {
                const updatedConversations = prevConversations.map(conv =>
                    conv.id === currentConversationId
                        ? { ...conv, hasUnreadResponse: false }
                        : conv);

                // Then persist to database
                queueSave(updatedConversations, { changedIds: [currentConversationId] }).catch(console.error);
                return updatedConversations;
            });

            // Set the current conversation ID after updating state
            // Remove artificial delay that might be blocking
            // await new Promise(resolve => setTimeout(resolve, 50));
            setCurrentConversationId(conversationId);

            // CRITICAL: Persist to localStorage immediately when switching conversations
            try {
                localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', conversationId);
            } catch (e) {
                console.error('Failed to persist conversation ID during switch:', e);
            }

            // Set the current folder ID based on the conversation's folder
            // This should not block conversation loading
            const conversation = conversations.find(c => c.id === conversationId);
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
                const next = new Map(prev);
                // Keep streaming content for active streaming conversations
                for (const [id, content] of prev) {
                    if (!streamingConversations.has(id)) {
                        next.delete(id);
                    }
                }
                return next;
            });
        }
    }, [currentConversationId, conversations, streamingConversations, streamedContentMap, queueSave, isTopToBottom]);

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
    }, [loadConversation, isTopToBottom]);

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
            return newFolder.id;
        } catch (error) {
            console.error('Error creating folder:', error);
            throw error;
        }
    }, []);

    const updateFolder = useCallback(async (folder: ConversationFolder): Promise<void> => {
        try {
            folder.updatedAt = Date.now();
            await db.saveFolder(folder);
            setFolders(prev => prev.map(f => f.id === folder.id ? folder : f));
        } catch (error) {
            console.error('Error updating folder:', error);
            throw error;
        }
    }, []);

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
            // First update the conversation in memory with a new version
            const newVersion = Date.now();
            console.log('🔧 CHATCONTEXT: moveConversationToFolder called:', {
                conversationId,
                folderId,
                newVersion
            });

            setConversations(prev => prev.map(conv =>
                conv.id === conversationId
                    ? { ...conv, folderId, _version: newVersion }
                    : conv
            ));

            // Then update in the database
            await db.moveConversationToFolder(conversationId, folderId);

            // Check if the state was preserved
            setTimeout(() => {
                const checkConv = conversations.find(c => c.id === conversationId);
                console.log('🔍 CHATCONTEXT: State check after database update:', {
                    conversationId,
                    actualFolderId: checkConv?.folderId,
                    expectedFolderId: folderId,
                    statePreserved: checkConv?.folderId === folderId
                });

                // If the move was overwritten, force it back to the correct state
                if (checkConv && checkConv.folderId !== folderId) {
                    console.log('🔧 FIXING OVERWRITTEN MOVE: Re-applying folder ID');
                    setConversations(prev => prev.map(conv =>
                        conv.id === conversationId ? { ...conv, folderId, _version: Date.now() } : conv
                    ));
                }
            }, 50);

            return;
        } catch (error) {
            console.error('Error moving conversation to folder:', error);
            throw error;
        }
    }, []);

    useEffect(() => {
        // Load current messages immediately when conversation changes, regardless of folder state
        // Only update if messages actually changed to prevent scroll jumps
        if (currentConversationId && conversations.length > 0) {
            const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];

            // PERFORMANCE FIX: Replace expensive JSON.stringify (18ms) with fast checks
            // Reduces comparison from O(n*m) to O(1) for most cases
            const messagesChanged =
                messages.length !== currentMessages.length ||
                messages !== currentMessages ||
                (messages.length > 0 && currentMessages.length > 0 &&
                    messages[messages.length - 1] !== currentMessages[currentMessages.length - 1]);

            if (messagesChanged) {
                // Check if this change is from the current conversation or another
                const triggeringConversation = conversations.find(c =>
                    c._version && c._version > (Date.now() - 100)
                );

                // CRITICAL FIX: Only skip update if we can CONFIRM it's a different conversation
                // AND the current conversation's messages haven't actually changed
                const isDefinitelyDifferentConversation = triggeringConversation &&
                    triggeringConversation.id !== currentConversationId &&
                    messages.length === currentMessages.length;

                if (isDefinitelyDifferentConversation) {
                    console.log('📌 Another conversation updated - preserving current conversation display');
                    return;
                }

                console.log('📝 Messages changed for conversation:', currentConversationId);
                setCurrentMessages(messages);
            }
        }
    }, [conversations, currentConversationId, messageUpdateCounter]);

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
            const savedConversations = await db.getConversations();
            console.log('✅ Setting conversations immediately:', savedConversations.length);
            setConversations(savedConversations);

            // CRITICAL FIX: If IndexedDB has conversations but state is empty, force load
            if (savedConversations.length > 0 && conversations.length === 0) {
                console.log('🔄 FORCE LOAD: IndexedDB has data but state is empty, forcing load');
                setConversations(savedConversations);
            }

            // CRITICAL: Verify the restored currentConversationId exists in loaded conversations
            const savedCurrentId = localStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID');
            if (savedCurrentId && !savedConversations.some(conv => conv.id === savedCurrentId)) {
                // Saved conversation ID not found in IndexedDB.
                // INIT_SYNC will merge from server shortly; for now use most recent local.
                if (savedConversations.length > 0) {
                    console.warn(`⚠️ ORPHANED CONVERSATION: ${savedCurrentId} not found anywhere`);
                    const mostRecent = savedConversations.reduce((a, b) =>
                        (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                    );
                    setCurrentConversationId(mostRecent.id);
                    localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', mostRecent.id);
                }
            }

            // Always mark as initialized to allow app to function
            setIsInitialized(true);

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
    }, [currentConversationId]);



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
    useEffect(() => {
        if (!isInitialized || !currentProject?.id) return;
        if (isEphemeralMode) return;

        // Skip if projectSwitched handler already synced this project
        if (serverSyncedForProject.current === currentProject.id) return;

        const projectId = currentProject.id;
        serverSyncedForProject.current = projectId;

        const syncWithServer = async () => {
            try {
                // 1. Fetch from server
                const serverChats = await syncApi.listChats(projectId);
                console.log(`📡 INIT_SYNC: Got ${serverChats.length} chats from server for project ${projectId}`);

                // 2. Load current IndexedDB state
                let allConversations = await db.getConversations();

                // 2a. Migrate untagged conversations (shared helper)
                allConversations = await migrateUntaggedConversations(allConversations, projectId);

                const localProjectConvs = allConversations.filter((c: any) => c.projectId === projectId || c.isGlobal);
                console.log(`📡 INIT_SYNC: Found ${localProjectConvs.length} local conversations for this project`);

                // 3. Three-way merge: use _version to determine winner, keep all unique
                const mergedMap = new Map<string, any>();
                
                // Start with local conversations
                localProjectConvs.forEach((conv: any) => {
                    mergedMap.set(conv.id, conv);
                });

                // Merge server conversations - only overwrite if server is newer
                serverChats.forEach((sc: any) => {
                    const local = mergedMap.get(sc.id);
                    const serverVersion = sc._version || 0;
                    const localVersion = local?._version || 0;
                    
                    if (!local || serverVersion > localVersion) {
                        // Server is newer or conversation doesn't exist locally
                        mergedMap.set(sc.id, {
                            ...sc,
                            projectId,
                            lastAccessedAt: sc.lastAccessedAt || sc.lastActiveAt,
                            isActive: sc.isActive !== false,
                            _version: serverVersion,
                        });
                    }
                });

                const mergedProjectConvs = Array.from(mergedMap.values());
                const localOnlyCount = mergedProjectConvs.filter(c => 
                    !serverChats.some(sc => sc.id === c.id)
                ).length;

                // 4. Push local-only and newer-local conversations to server (non-blocking)
                if (localOnlyCount > 0) {
                    const chatsToSync = mergedProjectConvs
                        .filter(c => !serverChats.some(sc => sc.id === c.id && (sc._version || 0) >= (c._version || 0)))
                        .map((c: any) => syncApi.conversationToServerChat(c, projectId));
                    syncApi.bulkSync(projectId, chatsToSync).catch(e =>
                        console.warn('📡 INIT_SYNC: Push to server failed (non-fatal):', e)
                    );
                }

                // 5. Update IndexedDB with merged data
                const otherProjectConvs = allConversations.filter((c: any) => c.projectId !== projectId);
                await db.saveConversations([...mergedProjectConvs, ...otherProjectConvs]);

                // 6. Update React state (project-filtered)
                setConversations(mergedProjectConvs);

                console.log(`📡 INIT_SYNC: ${mergedProjectConvs.length} conversations (${serverChats.length} from server, ${localOnlyCount} local-only, ${mergedProjectConvs.length - serverChats.length - localOnlyCount} merged)`);

                // 7. Update current conversation if it doesn't exist in merged set
                if (mergedProjectConvs.length > 0 && !mergedProjectConvs.some((c: any) => c.id === currentConversationId)) {
                    const mostRecent = mergedProjectConvs.reduce((a: any, b: any) =>
                        (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                    );
                    setCurrentConversationId(mostRecent.id);
                    setCurrentMessages(mostRecent.messages || []);
                }

                // Clean up legacy localStorage backups now that merge is complete.
                // Deferred from initializeWithRecovery to ensure data survives until
                // server sync has had a chance to preserve everything.
                try {
                    localStorage.removeItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY');
                    localStorage.removeItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY');
                    localStorage.removeItem('ZIYA_CONVERSATION_BACKUP');
                } catch (e) {
                    console.debug('Failed to clean up legacy localStorage keys:', e);
                }
            } catch (e) {
                console.warn('📡 INIT_SYNC: Server sync failed, using IndexedDB only:', e);
                // Fall back to IndexedDB only — still must migrate before filtering,
                // otherwise untagged (legacy) conversations silently vanish from the UI.
                let allConversations = await db.getConversations();
                allConversations = await migrateUntaggedConversations(allConversations, projectId);
                const projectConversations = allConversations.filter((c: any) => c.projectId === projectId || c.isGlobal);
                setConversations(projectConversations);
            }
        };

        syncWithServer();
    }, [isInitialized, currentProject?.id, isEphemeralMode]);

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
    }, [currentConversationId, conversations, currentFolderId, streamedContentMap, streamingConversations]);

    const mergeConversations = useCallback((local: Conversation[], remote: Conversation[]) => {
        const merged = new Map<string, Conversation>();

        // Add all local conversations first
        local.forEach(conv => merged.set(conv.id, conv));

        // Merge remote conversations only if newer
        remote.forEach(remoteConv => {
            const localConv = merged.get(remoteConv.id);
            if (!localConv ||
                (remoteConv._version || 0) > (localConv._version || 0)) {
                merged.set(remoteConv.id, {
                    ...remoteConv,
                    isActive: localConv?.isActive ?? true // Preserve active status
                });
            }
        });

        return Array.from(merged.values());
    }, []);

    useEffect(() => {
        let isMounted = true;

        const handleStorageChange = async () => {
            if (!isInitialized || !isMounted) return;
            try {
                const saved = await db.getConversations();
                if (saved.length > 0) {
                    // Only update if there's an actual difference
                    const currentStr = JSON.stringify(conversations);
                    const savedStr = JSON.stringify(saved);

                    if (currentStr !== savedStr) {
                        setConversations(prev =>
                            mergeConversations(prev, saved)
                        );
                    }
                }
                // Also fetch and update folders
                const savedFolders = await db.getFolders();
                // Check >= 0 to handle empty state correctly, and ensure it's an array
                if (Array.isArray(savedFolders) && savedFolders.length >= 0) {
                    const currentFoldersStr = JSON.stringify(folders); // 'folders' is the state for folders
                    const savedFoldersStr = JSON.stringify(savedFolders);
                    if (currentFoldersStr !== savedFoldersStr) {
                        setFolders(savedFolders); // 'setFolders' is the state setter for folders

                        // CRITICAL FIX: After DB refresh, restore file selections
                        // The DB refresh doesn't include checkedKeys, so we must restore from our cache
                        if (!isMounted) return;
                        preserveCurrentFileSelections();
                    }
                }
            } catch (error) {
                console.error('Error during conversation poll:', error);
            }

        };
        // setup
        window.addEventListener('storage', handleStorageChange, { passive: true });
        // cleanup
        return () => {
            isMounted = false;
            window.removeEventListener('storage', handleStorageChange);
        };
    }, [conversations, folders, mergeConversations, setConversations, setFolders, isEphemeralMode]);


    useEffect(() => {
        // Only create a new conversation ID if we're initialized and still don't have one
        // This prevents creating IDs before conversations are loaded from the database
        if (!currentConversationId && isInitialized && conversations.length === 0) {
            console.log('📝 No conversations loaded, creating initial conversation');
            setCurrentConversationId(uuidv4());
        } else if (!currentConversationId && isInitialized && conversations.length > 0) {
            // We have conversations but no current ID - use the most recent
            const mostRecent = conversations.reduce((a, b) =>
                (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
            );
            setCurrentConversationId(mostRecent.id);
            console.log('📝 Using most recent conversation:', mostRecent.id);
        }
    }, [currentConversationId, isInitialized, conversations.length]);

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

        // Don't force a full re-render via messageUpdateCounter
        // The conversation state update will propagate through React's normal rendering
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
        setConversations(prev => {
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

            // If moved away from current project, remove from visible list
            if (targetProjectId !== currentProject?.id) {
                return updated.filter(c => c.id !== conversationId || c.projectId === currentProject?.id || c.isGlobal);
            }
            return updated;
        });
    }, [currentProject?.id, queueSave]);

    const value = useMemo(() => ({
        streamedContentMap,
        reasoningContentMap,
        dynamicTitleLength,
        lastResponseIncomplete,
        setDynamicTitleLength,
        setStreamedContentMap,
        setReasoningContentMap,
        // Group conversation-specific state to reduce re-renders
        currentConversationState: {
            currentMessages,
            editingMessageIndex,
            isLoadingConversation,
            isStreaming: streamingConversations.has(currentConversationId),
            hasStreamedContent: streamedContentMap.has(currentConversationId),
        },
        // Group global state
        globalState: {
            conversations,
            folders,
            isStreamingAny,
        },
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
        toggleMessageMute,
        editingMessageIndex,
        setEditingMessageIndex,
        throttlingRecoveryData,
        setThrottlingRecoveryData,
        moveChatToGroup,
        toggleConversationGlobal,
        moveConversationToProject,
        toggleFolderGlobal,
        setChatContexts,
    }), [
        streamedContentMap,
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
    }, [conversations, currentConversationId, streamedContentMap]);

    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    const context = useContext(chatContext);
    if (!context) {
        throw new Error('useChatContext must be used within a ChatProvider');
    }
    return context;
}
