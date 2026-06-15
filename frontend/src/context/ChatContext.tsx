import React, { createContext, ReactNode, useContext, useState, useEffect, useLayoutEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo } from 'react';
import { StreamingProvider } from './StreamingContext';
import { ScrollProvider } from './ScrollContext';
import { ConversationListProvider } from './ConversationListContext';
import { ActiveChatProvider } from './ActiveChatContext';
import { Conversation, Message, ConversationFolder } from "../utils/types";
import { v4 as uuidv4 } from "uuid";
import { db } from '../utils/db';
import * as syncMerge from '../utils/syncMerge';
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
import { gcEmptyConversations, purgeExpiredConversations } from '../utils/retentionPurge';
import { useDelegateStreaming } from '../hooks/useDelegateStreaming';
import { folderIsEffectivelyGlobal, conversationIsEffectivelyGlobal } from '../utils/folderUtil';

const TERMINAL_PLAN_STATUSES = new Set(['completed', 'completed_partial', 'cancelled']);

// Titles that are placeholders rather than meaningful (auto- or user-set)
// names.  Used by the sync/save merge guards to avoid downgrading a resolved
// title back to a placeholder when in-memory _version is newer.
const PLACEHOLDER_TITLES = new Set(['New Conversation', 'New Ephemeral Chat', 'Loading...', 'Untitled', '']);

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
    // Conversations whose bound task card has a run in flight.
    // Distinct from streamingConversations so the conversation list
    // can render a different affordance (gear) for "task running"
    // vs the dots-spinner used for chat streaming.
    runningTaskConversations: Set<string>;
    addRunningTaskConversation: (id: string) => void;
    removeRunningTaskConversation: (id: string) => void;
    setCurrentConversationId: (id: string) => void;
    addMessageToConversation: (message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => void;
    currentMessages: Message[];
    loadConversation: (id: string) => void;
    startNewChat: (specificFolderId?: string | null) => Promise<void>;
    // Create a new conversation that lives only in React state for the
    // current UX session. Never written to IndexedDB or pushed to the
    // server. See promoteEphemeralToRetained for converting later.
    startNewEphemeralChat: (specificFolderId?: string | null) => Promise<void>;
    promoteEphemeralToRetained: (conversationId: string) => Promise<void>;
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
    copyConversationToProject: (conversationId: string, targetProjectId: string) => Promise<void>;
    moveFolderToProject: (folderId: string, targetProjectId: string) => Promise<void>;
    toggleFolderGlobal: (folderId: string) => Promise<void>;
    forkConversation: (conversationId: string) => Promise<string | null>;
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
    const [runningTaskConversations, setRunningTaskConversations] = useState<Set<string>>(() => new Set());
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [isProjectSwitching, setIsProjectSwitching] = useState(false);
    const [isInitialized, setIsInitialized] = useState(false);
    const [userHasScrolled, setUserHasScrolled] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>('');
    const currentMessagesRef = useRef<Message[]>([]);
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
    const otherProjectConvsCache = useRef<{ convs: any[], timestamp: number }>({ convs: [], timestamp: 0 });  // BUGFIX: Cache other-project convos to avoid reading ALL from DB on every save
    const saveDebounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingSaveConversations = useRef<Conversation[] | null>(null);
    const pendingSaveChangedIds = useRef<Set<string>>(new Set());
    const dualWriteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingDirtyIdsRef = useRef<Set<string>>(new Set());

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
    // Epoch counter — bumped on every project-switch effect firing.  Stale
    // syncs (from a previously-mounted effect, or from an in-flight periodic
    // poll that started before the user switched) detect the mismatch at
    // every commit/write site and bail without overwriting state.
    const syncEpochRef = useRef<number>(0);
    // Re-entrancy guard for periodic polling.  Project-switch syncs use
    // syncEpochRef to displace stale syncs at write time, but periodic
    // ticks on a stable project don't bump the epoch — so without this
    // flag, multiple 30 s ticks can run concurrently when one is slow
    // (large hydration sets, slow IDB, slow server).  Concurrent ticks
    // race on shared refs and on setConversations, which has been
    // observed to drop just-created conversations from React state.
    const periodicSyncInFlightRef = useRef<boolean>(false);
    // Track which project has been server-synced to avoid duplicate syncs
    const serverSyncedForProject = useRef<string | null>(null);
    // Conversations confirmed present on the server (used to distinguish imports from server-deletions)
    const knownServerConversationIds = useRef<Set<string>>(new Set());
    // Tab-scoped record of IDs we've already issued a server-side
    // empty-shell delete for.  Without this, overlapping sync cycles
    // re-stage the same IDs and produce a flurry of 404 deletes after
    // the first successful one.
    const serverGcAttemptedIds = useRef<Set<string>>(new Set());
    const dirtyConversationIds = useRef<Set<string>>(new Set());
    const recentlyFetchedFullIds = useRef<Set<string>>(new Set());
    // Throttle for user-visible persistence-failure toasts.  Without this, a
    // sustained IDB or server-push outage during streaming would show a toast
    // per SSE chunk — unusable.
    const lastSaveErrorNotifyRef = useRef<number>(0);
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

    // Conversation has a task run in flight.  Drives a distinct
    // gear affordance in the conversation list — chat streaming uses
    // the dots-spinner; this uses a different icon so the user can
    // tell at a glance whether they're waiting on the model directly
    // or on a task card.  Both states can be true at once (rare:
    // user types in the chat while a task is also running) and the
    // list should show both indicators.
    const addRunningTaskConversation = useCallback((id: string) => {
        if (!id) return;
        setRunningTaskConversations(prev => {
            if (prev.has(id)) return prev;
            const next = new Set(prev);
            next.add(id);
            return next;
        });
    }, []);

    const removeRunningTaskConversation = useCallback((id: string) => {
        if (!id) return;
        setRunningTaskConversations(prev => {
            if (!prev.has(id)) return prev;
            const next = new Set(prev);
            next.delete(id);
            return next;
        });
    }, []);

    const getProcessingState = useCallback((conversationId: string): ProcessingState => {
        return processingStates.get(conversationId)?.state || 'idle';
    }, [processingStates]);

    // Surface sustained persistence failures to the user without spamming.
    // queueSave's slow path and the server dual-write both swallow errors by
    // design (so the save queue doesn't break on transient issues), but that
    // hides real quota-exceeded / IDB-unavailable conditions from the user
    // until they refresh and discover data loss.  Throttled to one toast per
    // 30 seconds so streaming-chunk-sized bursts don't flood the UI.
    const SAVE_ERROR_NOTIFY_THROTTLE_MS = 30_000;
    const notifyPersistenceFailure = useCallback((context: string, err: unknown) => {
        console.error(`❌ ${context}:`, err);
        const now = Date.now();
        if (now - lastSaveErrorNotifyRef.current > SAVE_ERROR_NOTIFY_THROTTLE_MS) {
            lastSaveErrorNotifyRef.current = now;
            message.warning('Some changes may not be saved — storage may be full or unavailable.');
        }
    }, []);

    // Queue-based save system to prevent race conditions
    const queueSave = useCallback(async (conversations: Conversation[], options: {
        skipValidation?: boolean;
        retryCount?: number;
        isRecoveryAttempt?: boolean;
        changedIds?: string[];
        _bypassDebounce?: boolean;
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

        // EPHEMERAL GUARD: Strip ephemeral conversations from the input
        // and the changedIds set. Ephemerals exist only in React state for
        // the current UX session — they must never reach IndexedDB or the
        // server's bulkSync endpoint. Done here (single chokepoint) rather
        // than at every call site so future code paths can't accidentally
        // leak them.
        const ephemeralIds = new Set(
            conversations.filter(c => (c as any).isEphemeral).map(c => c.id)
        );
        if (ephemeralIds.size > 0) {
            conversations = conversations.filter(c => !ephemeralIds.has(c.id));
            if (options.changedIds) {
                options.changedIds = options.changedIds.filter(id => !ephemeralIds.has(id));
                if (options.changedIds.length === 0) {
                    return Promise.resolve();
                }
            }
        }

        // Skip saves during the first 5 seconds after init to let all data settle.
        // Shells → server sync → lazy-load → folders all trigger setConversations
        // in rapid succession; saving each intermediate state is wasted I/O.
        const initTime = (window as any).__ziyaInitTime || 0;
        if (initTime > 0 && Date.now() - initTime < 5000) {
            console.debug('📝 INIT_GUARD: Skipping save during startup settle window');
            return Promise.resolve();
        }

        // DEBOUNCE: Coalesce rapid-fire saves when specific conversations changed.
        // During streaming, addMessageToConversation fires queueSave on every SSE
        // chunk. Without debouncing, each save reads ALL conversations from IndexedDB
        // (due to shell guard), merges, and writes them all back — potentially
        // hundreds of full IDB write cycles per second. This coalesces into one
        // save per 300ms window, accumulating changedIds across calls.
        const SAVE_DEBOUNCE_MS = 300;
        if (options.changedIds && options.changedIds.length > 0
            && !options._bypassDebounce
            && !options.isRecoveryAttempt) {
            // Accumulate changed IDs and keep the latest conversations snapshot
            options.changedIds.forEach(id => pendingSaveChangedIds.current.add(id));
            pendingSaveConversations.current = conversations;

            if (saveDebounceTimer.current) {
                clearTimeout(saveDebounceTimer.current);
            }

            return new Promise<void>((resolve) => {
                saveDebounceTimer.current = setTimeout(() => {
                    saveDebounceTimer.current = null;
                    const pendingConvs = pendingSaveConversations.current;
                    const pendingIds = Array.from(pendingSaveChangedIds.current);
                    pendingSaveConversations.current = null;
                    pendingSaveChangedIds.current = new Set();

                    if (pendingConvs) {
                        queueSave(pendingConvs, { ...options, changedIds: pendingIds, _bypassDebounce: true })
                            .then(resolve, resolve);
                    } else {
                        resolve();
                    }
                }, SAVE_DEBOUNCE_MS);
            });
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
        // Declare here so the fast path below can reference them without TDZ.
        const changedIdSet = new Set(options.changedIds || []);
        const { skipValidation = false, isRecoveryAttempt = false } = options;

        // FAST PATH: bypass saveQueue entirely for per-message saves.
        // Chaining onto saveQueue even with an early return still adds
        // a .then() callback that must resolve — with 940 rapid calls
        // this creates a Promise chain that pegs the CPU for minutes.
        const _fastPathIds = new Set(options.changedIds || []);
        if (_fastPathIds.size > 0 && !options.isRecoveryAttempt && !options.skipValidation) {
            if (!(window as any).__fastPathTraced) {
                (window as any).__fastPathTraced = true;
                console.trace('📋 FAST_PATH caller:', options.changedIds);
                setTimeout(() => { (window as any).__fastPathTraced = false; }, 100);
            }
            const toWrite = validatedConversations.filter(c => _fastPathIds.has(c.id));
            if (toWrite.length > 0) {
                const nonShells = toWrite.filter(c => !(c as any)._isShell);
                if (nonShells.length > 0) await db.saveConversations(nonShells);
                if (options.changedIds && options.changedIds.length > 0) {
                    projectSync.post('conversations-changed', { ids: options.changedIds });
                }
                // Dual-write to server.  Without this, FAST_PATH-routed saves
                // (every per-message save during a chat, plus folder moves,
                // mute/display toggles, etc.) only ever reach IndexedDB and
                // same-browser BroadcastChannel listeners.  Cross-browser /
                // cross-machine instances never see those writes — even after
                // a reload — because the server never received them.  The
                // slow path further down has equivalent logic; we mirror it
                // here with the same 2s debounce so streaming bursts coalesce
                // into a single bulkSync call.
                if (currentProject?.id && options.changedIds && options.changedIds.length > 0) {
                    options.changedIds.forEach(id => {
                        pendingDirtyIdsRef.current.add(id);
                    });
                    if (dualWriteTimerRef.current) clearTimeout(dualWriteTimerRef.current);
                    const capturedProjectId = currentProject.id;
                    dualWriteTimerRef.current = setTimeout(async () => {
                        dualWriteTimerRef.current = null;
                        const batchIds = new Set(pendingDirtyIdsRef.current);
                        pendingDirtyIdsRef.current.clear();
                        // Read live state at fire time, not the snapshot
                        // captured when the timer was set — important because
                        // additional messages may have appended during the 2s.
                        const dirtyConvs = conversationsRef.current.filter(
                        (c: any) => batchIds.has(c.id) && c.isActive !== false && !c._isShell
                            // Don't push empty "New Conversation" shells to the
                            // server.  They have no meaningful content and their
                            // presence on the server triggers the isEmptyShell
                            // skip logic on subsequent sync cycles, which can
                            // race with shell-cache timing and drop the
                            // conversation from mergedMap.  The conversation
                            // will be pushed on the next dual-write after the
                            // user sends their first message (which bumps
                            // messages.length > 0).
                            && !(c.title === 'New Conversation' && (!c.messages || c.messages.length === 0))
                        ) as Conversation[];
                        if (dirtyConvs.length === 0) {
                            console.debug('📡 DUAL_WRITE(fast): no dirty convs to push (all filtered out)');
                            return;
                        }
                        try {
                            const byProject = new Map<string, any[]>();
                            dirtyConvs.forEach(c => {
                                const pid = c.projectId || capturedProjectId;
                                if (!byProject.has(pid)) byProject.set(pid, []);
                                byProject.get(pid)!.push(c);
                            });
                            for (const [pid, convs] of byProject) {
                                const chatsToSync = convs.map(c =>
                                    syncApi.conversationToServerChat(c, pid)
                                );
                                const result = await syncApi.bulkSync(pid, chatsToSync);
                                console.debug(
                                    `📡 DUAL_WRITE(fast): pushed ${chatsToSync.length} conversation(s) to project ${pid.substring(0, 8)} ` +
                                    `→ created=${result.created} updated=${result.updated} skipped=${result.skipped}` +
                                    (result.errors?.length ? ` errors=${result.errors.length}` : '')
                                );
                            }
                        } catch (e) {
                            notifyPersistenceFailure('Server sync failed (fast path)', e);
                        }
                    }, 2000);
                }
            }
            return Promise.resolve();
        }


        saveQueue.current = saveQueue.current.then(async () => {
            console.trace('📋 saveQueue.then called, changedIds:', options.changedIds?.length, 'bypassDebounce:', options._bypassDebounce);
            const { retryCount = 0 } = options;
            const maxRetries = 3;

            // Pre-save validation
            const activeCount = validatedConversations.filter(c => c.isActive).length;
            console.debug(`Saving ${validatedConversations.length} conversations (${activeCount} active)`);

            // changedIdSet, skipValidation, isRecoveryAttempt already declared above

            // Use cached other-project conversations when possible, BUT
            // never substitute React state for IndexedDB when state contains
            // shell conversations (messages stripped to first+last only).
            // Writing shells back to IndexedDB would destroy full message data.
            const CACHE_TTL_MS = 300_000; // 5 minutes — reduces full IDB reads during idle polling
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

            // 1. Load everything from DB (preserves other tabs' writes).
            //    Skip conversations that came from the otherProjectConvsCache
            //    with stripped messages (messages: []) — writing them back to
            //    IDB would destroy their full message history.
            allDbConversations.forEach(c => {
                // Cache-sourced entries have messages: [] and belong to other
                // projects.  Never let them overwrite IDB's full data.
                const isCacheStripped = Array.isArray(c.messages) && c.messages.length === 0
                    && c.projectId && c.projectId !== currentProject?.id && !c.isGlobal;
                if (isCacheStripped) return; // Skip — IDB already has full data
                mergedMap.set(c.id, c);
            });

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

            // Guard: never regress the active conversation's message count.
            // The active conversation is authoritative in React state; if the
            // merged version has fewer messages (e.g. from a stale dual-write
            // round-trip), preserve the React state version.
            const liveActiveId = currentConversationRef?.current;
            if (liveActiveId) {
                const liveConv = conversationsRef.current.find((c: any) => c.id === liveActiveId);
                const mergedIdx = finalConversations.findIndex(c => c.id === liveActiveId);
                if (liveConv && mergedIdx >= 0) {
                    const liveMsgCount = liveConv.messages?.length || 0;
                    const mergedMsgCount = finalConversations[mergedIdx].messages?.length || 0;
                    const liveVerIsNewer = (liveConv._version || 0) > (finalConversations[mergedIdx]._version || 0);
                    if (liveMsgCount > mergedMsgCount || (liveVerIsNewer && liveMsgCount > 0)) {
                        // Spread liveConv to keep its (authoritative) messages,
                        // but don't let a placeholder title in React state
                        // overwrite a real title the merge already resolved.
                        // _version bumps on every message append, so liveConv
                        // can be "newer" while still holding "New Conversation".
                        const mergedTitle = finalConversations[mergedIdx].title;
                        const liveTitleIsPlaceholder = !liveConv.title || PLACEHOLDER_TITLES.has(liveConv.title);
                        const keepTitle = (liveTitleIsPlaceholder && mergedTitle && !PLACEHOLDER_TITLES.has(mergedTitle))
                            ? mergedTitle : liveConv.title;
                        finalConversations[mergedIdx] = { ...liveConv, title: keepTitle, _version: Date.now() };
                    }
                }
            }

            // Save all conversations - but don't throw if it fails
            try {
                // Only write the conversations that actually changed to avoid
                // cloning all 674 conversations (with full message arrays) on
                // every save — the old behaviour caused OOM with large histories.
                if (changedIdSet.size > 0) {
                    const changedOnly = finalConversations.filter(c => changedIdSet.has(c.id));
                    await db.saveConversations(changedOnly);
                } else {
                    await db.saveConversations(finalConversations);
                }
                // Notify other same-project tabs about the change
                if (options.changedIds && options.changedIds.length > 0) {
                    projectSync.post('conversations-changed', { ids: options.changedIds });
                }
            } catch (saveError) {
                // Log but don't throw - let the app continue functioning
                // Surface to user (throttled) so they aren't blindsided by
                // data loss after a sustained quota/IDB failure.
                notifyPersistenceFailure('Database save failed', saveError);
                return; // Exit early, skip validation
            }

            // DUAL-WRITE: Also sync changed conversations to server (non-blocking)
            if (currentProject?.id) {
                const dirty = dirtyConversationIds.current;
                if (dirty.size > 0) {
                    // Accumulate dirty IDs and debounce the server push.
                    // During streaming, queueSave fires on every chunk — batching
                    // prevents dozens of redundant bulk-sync requests per second.
                    dirty.forEach(id => pendingDirtyIdsRef.current.add(id));
                    dirtyConversationIds.current = new Set();

                    if (dualWriteTimerRef.current) clearTimeout(dualWriteTimerRef.current);
                    const capturedProjectId = currentProject.id;
                    dualWriteTimerRef.current = setTimeout(async () => {
                        dualWriteTimerRef.current = null;
                        const batchIds = new Set(pendingDirtyIdsRef.current);
                        pendingDirtyIdsRef.current.clear();
                        // Read the CURRENT React state at fire time, not the
                        // stale snapshot from when the timer was set.  The old
                        // code captured `finalConversations` at set-time, which
                        // could be many messages behind by the time the 2s
                        // debounce expires — causing the server to receive a
                        // conversation with only the first message.
                        const dirtyConvs = conversationsRef.current.filter(
                        (c: any) => batchIds.has(c.id) && c.isActive !== false && !c._isShell
                            // Mirror of the FAST_PATH guard: don't push empty
                            // "New Conversation" shells — they trigger the
                            // isEmptyShell skip race on subsequent syncs.
                            && !(c.title === 'New Conversation' && (!c.messages || c.messages.length === 0))
                        ) as Conversation[];
                        if (dirtyConvs.length === 0) return;
                        try {
                            const byProject = new Map<string, any[]>();
                            dirtyConvs.forEach(c => {
                                const pid = c.projectId || capturedProjectId;
                                if (!byProject.has(pid)) byProject.set(pid, []);
                                byProject.get(pid)!.push(c);
                            });

                            for (const [pid, convs] of byProject) {
                                const chatsToSync = convs.map(c =>
                                    syncApi.conversationToServerChat(c, pid)
                                );
                                await syncApi.bulkSync(pid, chatsToSync);
                                console.debug(`📡 DUAL_WRITE: Synced ${chatsToSync.length} conversations to project ${pid}`);
                            }
                        } catch (e) {
                            // Non-fatal: IDB is still authoritative and next sync
                            // cycle will retry.  But surface to user (throttled) so
                            // persistent server outages don't go unnoticed.
                            notifyPersistenceFailure('Server sync failed', e);
                        }
                    }, 2000); // 2-second debounce batches streaming writes
                }
            }
        });
        return saveQueue.current;
    }, [isEphemeralMode, currentProject?.id]); // conversationsRef and currentConversationRef are refs — stable identity, no dep needed

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

        // Stable ID assignment: every message gets a uuid on the way in.
        // This is what lets task-card bindings and any future
        // message-anchored features reference specific messages.  Existing
        // messages that already carry an id (e.g. model-change system
        // messages constructed earlier in this file) keep theirs.
        if (!message.id) {
            message = { ...message, id: uuidv4() };
        }

        // Temporary: unconditional trace to identify spam caller
        if (!(window as any).__addMsgTraced) {
            (window as any).__addMsgTraced = true;
            console.trace('📝 addMessageToConversation first call stack');
            setTimeout(() => { (window as any).__addMsgTraced = false; }, 100);
        }

        // If adding message to non-current conversation, don't trigger any scroll
        if (conversationId !== currentConversationId) {
            console.debug('📝 Adding message to non-current conversation - scroll preservation mode');
        }

        // Diagnostic: trace who is calling addMessageToConversation during idle.
        // TODO: Remove this console.trace once the idle-spam caller is identified.

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
                    (db.getConversation(conversationId)).then(full => {
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

            // DUPLICATE GUARD: If a message with the same role + content already
            // exists in the conversation, skip it.  This breaks feedback loops
            // that replay the entire message history through addMessageToConversation.
            // Intentionally ignores _timestamp — replayed messages may carry a
            // fresh timestamp even though the content is identical.
            if (existingConversation && existingConversation.messages.length > 0) {
                const msgs = existingConversation.messages;
                const isDuplicate = msgs.some(
                    m => m.role === message.role && m.content === message.content
                );
                if (isDuplicate) {
                    // Trace only once to avoid flooding the console further
                    if (!(window as any).__dupGuardTraced) {
                        (window as any).__dupGuardTraced = true;
                        console.warn('🛑 DUPLICATE_GUARD: Blocked duplicate message:', {
                            role: message.role,
                            convId: conversationId.substring(0, 8),
                            existingMsgCount: msgs.length,
                        });
                        console.trace('🛑 DUPLICATE_GUARD: Call stack (logged once)');
                    }
                    return prevConversations;
                }
            }

            // CRITICAL FIX: Determine if this is a non-current conversation dynamically
            // Don't trust the caller's isNonCurrentConversation - compute it from current state
            // This handles concurrent conversations and user switching mid-stream
            // Use the REF (not closed-over state) so we always get the live value,
            // even when this callback was captured before the user switched conversations.
            const actuallyNonCurrent = conversationId !== currentConversationRef.current;

            const shouldMarkUnread = message.role === 'assistant' && actuallyNonCurrent;
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
                setCurrentConversationId(newId);

                try {
                    setTabState('ZIYA_CURRENT_CONVERSATION_ID', newId);
                } catch (e) {
                    console.warn('Failed to persist conversation ID:', e);
                }

            } catch (saveError) {
                console.error('Failed to save new conversation, creating in memory:', saveError);
                setConversations([...updatedConversations, newConversation]);
                setCurrentConversationId(newId);
                try {
                    setTabState('ZIYA_CURRENT_CONVERSATION_ID', newId);
                } catch (e) { /* ignore */ }

                setTimeout(async () => {
                    try {
                        // Use live state, not the snapshot from 2s ago.
                        // Any messages added or edits made during the
                        // retry window would otherwise be rolled back
                        // when this save lands.
                        await queueSave(conversationsRef.current, {
                            skipValidation: true,
                            changedIds: [newConversation.id],
                        });
                    } catch (e) {
                        console.warn('Background save retry failed:', e);
                    }
                }, 2000);
            }
        } catch (error) {
            console.error('Failed to create new conversation:', error);
        }
    }, [isInitialized, currentConversationId, currentFolderId, conversations, folders, queueSave, currentProject?.id]);

    // Create a session-only conversation. Lives in React state, never
    // persisted to IndexedDB and never pushed to the server. Lost on
    // page reload or project switch (project switches reload conversations
    // from IDB). Use promoteEphemeralToRetained to convert later.
    const startNewEphemeralChat = useCallback(async (specificFolderId?: string | null) => {
        try {
            const newId = uuidv4();
            let targetFolderId = specificFolderId !== undefined ? specificFolderId : currentFolderId;
            if (targetFolderId) {
                const folder = folders.find(f => f.id === targetFolderId);
                if (folder?.taskPlan) targetFolderId = null;
            }

            const newConversation: Conversation = {
                id: newId,
                title: 'New Ephemeral Chat',
                projectId: currentProject?.id,
                messages: [],
                folderId: targetFolderId,
                lastAccessedAt: Date.now(),
                isActive: true,
                isEphemeral: true,
                _version: Date.now(),
                hasUnreadResponse: false,
            };

            // Clear unread flag on the previously active conversation,
            // mirroring startNewChat — but route through state only,
            // never queueSave (the ephemeral guard would strip it anyway,
            // but skipping the call avoids the debounce timer churn).
            setConversations(prev => [
                ...prev.map(conv =>
                    conv.id === currentConversationId
                        ? { ...conv, hasUnreadResponse: false }
                        : conv,
                ),
                newConversation,
            ]);
            setCurrentConversationId(newId);
            try { setTabState('ZIYA_CURRENT_CONVERSATION_ID', newId); } catch { /* ignore */ }
        } catch (error) {
            console.error('Failed to create ephemeral conversation:', error);
        }
    }, [currentConversationId, currentFolderId, folders, currentProject?.id]);

    // Convert an ephemeral conversation into a normally-persisted one.
    // Clears isEphemeral, bumps _version, and routes through queueSave
    // so it lands in IndexedDB and on the next dual-write cycle.
    const promoteEphemeralToRetained = useCallback(async (conversationId: string) => {
        // Strip MUIChatHistory's "conv-" tree-node prefix if a caller
        // forgot to (matches forkConversation's tolerance).
        if (conversationId.startsWith('conv-')) {
            conversationId = conversationId.substring(5);
        }
        let promoted = false;
        setConversations(prev => prev.map(conv => {
            if (conv.id !== conversationId || !conv.isEphemeral) return conv;
            promoted = true;
            const { isEphemeral, ...rest } = conv;
            return { ...rest, _version: Date.now(), lastAccessedAt: Date.now() };
        }));
        if (!promoted) return;
        // Read live state at fire time so we capture any messages added
        // between the setConversations call and this save.
        try {
            await queueSave(conversationsRef.current, { changedIds: [conversationId] });
            message.success('Conversation retained — it will now persist and sync.');
        } catch (e) {
            console.warn('promoteEphemeralToRetained: queueSave failed:', e);
        }
    }, [queueSave]);

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
            console.log('🔄 Loading conversation:', conversationId);
            // Switch the UI immediately — selection must not wait on network.
            // The lazy-load below can include a server fetch (getChat) which,
            // during a project switch, queues behind the file scan for many
            // seconds.  Setting the ID first makes the click take effect
            // instantly: the shell's summary content renders now, and the
            // full messages hydrate in via setConversations when the
            // lazy-load resolves.
            setCurrentConversationId(conversationId);
            try {
                setTabState('ZIYA_CURRENT_CONVERSATION_ID', conversationId);
                if (currentProject?.id) {
                    saveProjectConversationId(currentProject.id, conversationId);
                }
            } catch (e) {
                console.error('Failed to persist conversation ID during switch:', e);
            }

            // Lazy-load messages for conversations that only have summary
            // metadata (e.g. after SERVER_SYNC with empty/corrupt IDB) or
            // shell data (first+last messages only from startup fast-path),
            // or zombie records where a shell was persisted as a complete
            // conversation (2 messages, _isShell: false, no _fullMessageCount).
            // Ephemeral conversations live only in React state — they're
            // never in IDB or on the server, so any lazy-load attempt is a
            // wasted round-trip that 404s.
            const isEphemeralConv = (convEntry as any)?.isEphemeral === true;
            const isZombieRecord = convEntry?.messages?.length <= 2
                && !convEntry._isShell
                && convEntry.title !== 'New Conversation'
                && convEntry.title !== '';
            const needsLazyLoad = !isEphemeralConv && convEntry && (
                (!convEntry.messages || convEntry.messages.length === 0) ||
                convEntry._isShell ||
                isZombieRecord
            );

            if (needsLazyLoad) {
                // Try IDB first for shell conversations (IDB has full data)
                let loaded = false;
                if (convEntry._isShell || isZombieRecord) {
                    try {
                        // Use single-record read if available, fall back to
                        // full read only as last resort.  The full read
                        // deserializes the entire conversations blob which
                        // causes OOM with 200+ conversations.
                        const fullConv = typeof db.getConversation === 'function'
                            ? await db.getConversation(conversationId)
                            : (await db.getConversations()).find(c => c.id === conversationId);
                        // Accept IDB data when it has real content.  The shell
                        // in state has content: ''; the IDB record has real
                        // text.  Count-based comparison can't distinguish a
                        // shell-pair (2 empty msgs) from a real short chat
                        // (2 real msgs), so we compare total content length.
                        const localContentLen = (convEntry.messages || []).reduce(
                            (n: number, m: any) => n + (typeof m?.content === 'string' ? m.content.length : 0), 0);
                        const idbContentLen = (fullConv?.messages || []).reduce(
                            (n: number, m: any) => n + (typeof m?.content === 'string' ? m.content.length : 0), 0);
                        const idbMsgs = fullConv?.messages ?? [];
                        const idbUsable = !!fullConv && idbMsgs.length > 0
                            && !(fullConv as any)._isShell
                            && (
                                idbMsgs.length > (convEntry.messages?.length || 0)
                                || idbContentLen > localContentLen
                            );
                        if (idbUsable && fullConv) {
                            setConversations(prev => prev.map(c =>
                                c.id === conversationId
                                    ? { ...c, messages: fullConv!.messages, _isShell: false, _fullMessageCount: undefined }
                                    : c
                            ));
                            loaded = true;
                            console.log(`✅ Lazy-loaded ${fullConv.messages.length} messages from IDB`);
                        }
                        if (!loaded) console.log(`⚠️ IDB has ${fullConv?.messages?.length ?? 0} messages — trying server`);
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
                            // Accept server payload when it carries more total
                            // content than what we have in state — covers real
                            // short (2-message) conversations that the previous
                            // `length > 2` gate rejected, while still preventing
                            // a thinner server payload from overwriting a fuller
                            // local record.
                            const localLen = (convEntry.messages || []).reduce(
                                (n: number, m: any) => n + (typeof m?.content === 'string' ? m.content.length : 0), 0);
                            const srvLen = (serverChat?.messages || []).reduce(
                                (n: number, m: any) => n + (typeof m?.content === 'string' ? m.content.length : 0), 0);
                            const srvMsgs = serverChat?.messages ?? [];
                            if (serverChat
                                && srvMsgs.length >= (convEntry.messages?.length || 0)
                                && srvLen > localLen) {
                                setConversations(prev => prev.map(c =>
                                    c.id === conversationId
                                        ? { ...c, messages: srvMsgs, _isShell: false, _fullMessageCount: undefined, _version: Date.now() }
                                        : c
                                ));
                                console.log(`✅ Lazy-loaded ${srvMsgs.length} messages from server`);
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

            // (conversation ID was already set at the top of this function)

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
                        const srvMsgs = serverChat?.messages ?? [];
                        if (!serverChat || srvMsgs.length === 0) return;
                        React.startTransition(() => {
                            setConversations(prev => prev.map(c => {
                                if (c.id !== conversationId) return c;
                                // Never replace with fewer messages — prevents
                                // partial server data from destroying local history
                                const localCount = c.messages?.length || 0;
                                const serverCount = srvMsgs.length;
                                if (serverCount < localCount && localCount > 2) {
                                    console.warn(`🛡️ FETCH_GUARD: Keeping ${localCount} local messages (server had ${serverCount})`);
                                    return { ...c, delegateMeta: serverChat.delegateMeta ?? c.delegateMeta };
                                }
                                return { ...c, messages: srvMsgs, delegateMeta: serverChat.delegateMeta ?? c.delegateMeta };
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

            // Retry scrolling with backoff — lazy-load may still be in progress
            const tryScroll = (attempt: number) => {
                const chatContainer = document.querySelector('.chat-container') as HTMLElement;
                const allIndexed = chatContainer?.querySelectorAll('[data-message-index]');
                console.log('🔍 tryScroll attempt', attempt, 'container:', !!chatContainer, 'indexed msgs:', allIndexed?.length, 'looking for index:', messageIndex, 'searchTerm:', (window as any).__ziyaSearchHighlight);
                if (!chatContainer) {
                    if (attempt < 10) setTimeout(() => tryScroll(attempt + 1), 150 * (attempt + 1));
                    return;
                }
                const targetMessage = chatContainer.querySelector(
                    `[data-message-index="${messageIndex}"]`
                ) as HTMLElement | null;
                if (targetMessage) {
                    targetMessage.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    targetMessage.style.transition = 'background-color 0.5s ease';
                    targetMessage.style.backgroundColor = isDarkMode ? 'rgba(24, 144, 255, 0.2)' : 'rgba(24, 144, 255, 0.1)';
                    setTimeout(() => { targetMessage.style.backgroundColor = ''; }, 2000);
                    // Highlight search term within the target message
                    const searchTerm = (window as any).__ziyaSearchHighlight;
                    if (searchTerm) {
                        // textContent extraction is safe, but when the captured
                        // match is spliced into a <mark>$1</mark> replacement
                        // and assigned via innerHTML, any literal HTML chars in
                        // the message (e.g. <img src=x onerror=…>) become live
                        // markup and execute. Escape both the haystack and the
                        // search term before matching, so the regex operates on
                        // HTML-safe text and the reinsert stays literal.
                        const escapeHtml = (s: string) => s
                            .replace(/&/g, '&amp;')
                            .replace(/</g, '&lt;')
                            .replace(/>/g, '&gt;')
                            .replace(/"/g, '&quot;');
                        const escapedTerm = escapeHtml(searchTerm)
                            .replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                        const walk = (node: Node) => {
                            if (node.nodeType === Node.TEXT_NODE) {
                                const text = node.textContent || '';
                                if (text.toLowerCase().includes(searchTerm.toLowerCase())) {
                                    const span = document.createElement('span');
                                    span.innerHTML = escapeHtml(text).replace(
                                        new RegExp(`(${escapedTerm})`, 'gi'),
                                        (m) => `<mark style="background:${isDarkMode ? '#b8860b' : '#fff176'};color:${isDarkMode ? '#fff' : '#000'};border-radius:2px;padding:0 1px">${m}</mark>`);
                                    node.parentNode?.replaceChild(span, node);
                                }
                            } else if (node.nodeType === Node.ELEMENT_NODE && !['SCRIPT', 'STYLE', 'CODE', 'PRE'].includes((node as Element).tagName)) {
                                Array.from(node.childNodes).forEach(walk);
                            }
                        };
                        Array.from(targetMessage.childNodes).forEach(walk);
                        setTimeout(() => { (window as any).__ziyaSearchHighlight = null; }, 10000);
                    }
                } else if (attempt < 20) {
                    setTimeout(() => tryScroll(attempt + 1), 200);
                }
            };
            setTimeout(() => tryScroll(0), 100);
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
                // Also delete the contained conversations from the server.
                // queueSave above marked them isActive: false locally, but its
                // dual-write filter excludes inactive conversations from the
                // bulkSync push — so without an explicit deleteChat call here,
                // other browsers / machines never learn about the deletion and
                // the conversations resurface on those clients (and on a
                // server-sourced reload of any tab).  Best-effort: 404s are
                // benign (already deleted by another instance).
                affectedIds.forEach(convId => {
                    syncApi.deleteChat(deleteFromProject, convId).then(ok => {
                        if (!ok) {
                            console.debug(`📡 Folder delete: server delete returned non-ok for ${convId.substring(0, 8)}`);
                        }
                    }).catch(e =>
                        console.warn(`📡 Folder delete: server delete failed for ${convId.substring(0, 8)}:`, e)
                    );
                });
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

            // Immediate state update so drag-drop feels instant.  Sidebar
            // entries may be shells — overlay metadata only.
            setConversations(prev => prev.map(conv =>
                conv.id === conversationId
                    ? { ...conv, folderId, _version: newVersion, lastAccessedAt: newVersion }
                    : conv
            ));

            if (isEphemeralMode) return;

            // Unified mutation path: hydrate the full record from IDB,
            // patch, persist, broadcast cross-tab, and push to the server
            // immediately.  The legacy queueSave route relied on the 2s
            // dual-write debounce AND filtered out shells — sidebar entries
            // for non-active conversations are shells, so folder moves on
            // them never reached the server until the next periodic sync's
            // push side, leaving a window where a server-side _version bump
            // would revert the move (same mechanism as the rename bug).
            const { mutateConversationMeta } = await import('../utils/conversationMutations');
            const result = await mutateConversationMeta(
                conversationId,
                { folderId, lastAccessedAt: newVersion },
                { projectId: currentProject?.id }
            );
            if (!result.ok) {
                // Not in IDB yet (brand-new conversation) — fall back to
                // the legacy save pipeline so the move still persists.
                queueSave(conversationsRef.current, { changedIds: [conversationId] }).catch(console.error);
            }
        } catch (error) {
            console.error('Error moving conversation to folder:', error);
            throw error;
        }
    }, [queueSave, isEphemeralMode, currentProject?.id]);

    // Derive currentMessages synchronously — no useState/useEffect needed.
    // The previous useEffect called setCurrentMessages on every render,
    // which scheduled a new React commit even when returning prev,
    // creating a 33 commits/sec render loop.
    const currentMessages = useMemo(() => {
        if (!currentConversationId || conversations.length === 0) return currentMessagesRef.current;
        const conv = conversations.find(c => c.id === currentConversationId);
        if (!conv) {
            // Conversation is not present in state — filtered/purged by sync,
            // a lazy-load race, or stranded after a server-side empty-shell
            // cleanup. Returning the stale ref would leak the previously-loaded
            // conversation's messages, and a subsequent send would package them
            // under the orphaned conversation id (wrong context, wrong target).
            if (currentMessagesRef.current.length !== 0) currentMessagesRef.current = [];
            return currentMessagesRef.current;
        }
        if (!conv.messages) return currentMessagesRef.current;
        const messages = conv.messages;
        const prev = currentMessagesRef.current;
        // Fast path: identical array reference
        if (messages === prev) return prev;
        // Length change is definitive
        if (messages.length !== prev.length) { currentMessagesRef.current = messages; return messages; }
        if (messages.length === 0) return prev;
        // Compare by id (preferred) or content+role (fallback for legacy messages)
        const lastNew = messages[messages.length - 1];
        const lastOld = prev[prev.length - 1];
        const lastSame = (lastNew.id && lastOld.id) ? lastNew.id === lastOld.id
            : lastNew.content === lastOld.content && lastNew.role === lastOld.role;
        const firstSame = (messages[0].id && prev[0].id) ? messages[0].id === prev[0].id
            : messages[0].content === prev[0].content && messages[0].role === prev[0].role;
        if (lastSame && firstSame) {
            // Length, first id, and last id all match.  The id-equality
            // sampling above is too weak for in-place edits: when the user
            // edits a message, EditSection.handleSubmit rebuilds the
            // message object via spread (preserving id but replacing
            // content).  Editing the LAST message also keeps length
            // unchanged (slice(0, last+1) is a no-op on length), so all
            // three sampled values match while the content is genuinely
            // different.  Returning prev here leaves the UI showing the
            // unedited text until something else forces an update —
            // typically when the assistant response arrives and appends a
            // new message, increasing length.
            //
            // EditSection, mute toggles, and any other in-place message
            // update all create new message objects (spread), so reference
            // inequality at any index reliably indicates a real change.
            // This scan only runs when length+endpoints match, so it's
            // bypassed entirely on the streaming hot path where conv.messages
            // reference is unchanged and the \`messages === prev\` fast path
            // returns above.
            let changed = false;
            for (let i = 0; i < messages.length; i++) {
                if (messages[i] !== prev[i]) { changed = true; break; }
            }
            if (!changed) return prev;
        }
        currentMessagesRef.current = messages;
        return messages;
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
            // Scope the initial shell load to the current project. IndexedDB
            // holds shells for every project the user has ever opened in this
            // browser; dumping all of them into state on refresh produced a
            // thousands-long sidebar for unrooted projects (and a brief flash
            // for rooted ones) until the project-scoped server sync ran and
            // replaced them. We still include conversations with no projectId
            // — legacy/untagged entries that the migration step will assign.
            // ProjectContext restores its state asynchronously, so currentProject
            // is typically still undefined when this init effect fires. Read the
            // persisted project id directly from localStorage (same key that
            // ProjectContext uses: LAST_PROJECT_KEY = 'ZIYA_LAST_PROJECT_ID') so
            // the filter engages on the very first render after refresh.
            const startupPid = currentProject?.id || (() => { try { return localStorage.getItem('ZIYA_LAST_PROJECT_ID') || undefined; } catch { return undefined; } })();
            const scopedShells = startupPid
                ? savedConversations.filter(c => !c.projectId || c.projectId === startupPid || (c as any).isGlobal)
                : savedConversations;
            console.log('✅ Setting conversation shells immediately:', scopedShells.length, `(of ${savedConversations.length} total across all projects)`);
            setConversations(scopedShells);

            // Detect corrupt IDB: if shells returned very few valid
            // conversations, check the server to see if we're missing data.
            // This handles the case where the IDB conversations store has a
            // corrupt record (e.g. a single entry with id: undefined) while
            // the server still has the full history.
            const validShells = savedConversations.filter(c => c.id && c.title);
            if (validShells.length <= 1 && startupPid && isServerReachable) {
                try {
                    const serverSummaries = await syncApi.listChats(startupPid, false);
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
                            // Immediately populate from server rather than waiting for the
                            // next syncWithServer cycle — which depends on currentProject being
                            // set, a condition that may not hold yet at this point in startup.
                            const recovered = serverSummaries.map(s => ({ ...s, projectId: startupPid }));
                            setConversations(recovered);
                            console.log(`🔧 IDB_REPAIR: Set ${recovered.length} conversations directly from server.`);
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
                setConversations(scopedShells);
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

            // GC must operate on the scoped shells — GCing the unfiltered 852
            // and then setConversations(gcKept) would undo the project filter
            // above and dump every project's chats back into state.
            const { kept: gcKept, purgedIds: gcPurged } = gcEmptyConversations(
                scopedShells, protectedAtStartup, STARTUP_GC_MAX_AGE_MS
            );
            if (gcPurged.length > 0) {
                console.log(`🗑️ Startup GC: removing ${gcPurged.length} stale empty conversation(s)`);
                setConversations(gcKept);
                queueSave(gcKept, { changedIds: gcPurged }).catch(e => console.warn('Startup GC save failed:', e));
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
                let idbLoaded = false;
                try {
                    const fullConv = typeof db.getConversation === 'function'
                        ? await db.getConversation(activeId)
                        : (await db.getConversations()).find(c => c.id === activeId);
                    const fcMsgs = fullConv?.messages ?? [];
                    const idbIsShell = !!fullConv && (
                        (fullConv as any)._isShell ||
                        ((fullConv as any)._fullMessageCount && fcMsgs.length < (fullConv as any)._fullMessageCount)
                    );
                    if (fullConv && fcMsgs.length > 0 && !idbIsShell) {
                        setConversations(prev => prev.map(c =>
                            c.id === activeId ? { ...c, messages: fcMsgs, _isShell: false, _fullMessageCount: undefined } : c
                        ));
                        console.log(`✅ Lazy-loaded ${fcMsgs.length} messages for active conversation`);
                        idbLoaded = true;
                    }
                } catch (err) {
                    console.warn('⚠️ Failed to lazy-load active conversation messages:', err);
                }
                // Fall back to server when IDB has no usable messages (corrupted shell record)
                if (!idbLoaded && startupPid && isServerReachable) {
                    try {
                        const serverChat = await syncApi.getChat(startupPid, activeId);
                        const srvMsgs = serverChat?.messages ?? [];
                        if (srvMsgs.length > 0) {
                            setConversations(prev => prev.map(c =>
                                c.id === activeId
                                    ? { ...c, messages: srvMsgs, _isShell: false, _fullMessageCount: undefined, _version: Date.now() }
                                    : c
                            ));
                            console.log(`✅ Lazy-loaded ${srvMsgs.length} messages for active conversation (server fallback)`);
                        }
                    } catch (err) {
                        console.warn('⚠️ Server fallback for active conversation failed:', err);
                    }
                }
            }

        } catch (error) {
            console.error('❌ INIT: Database initialization failed:', error);
            console.warn('⚠️ INIT: IndexedDB failed. Loading conversations directly from server.');

            // Don't wait for the 30s sync cycle — fetch immediately so the
            // conversation list isn't blank for users with a corrupted IDB.
            try {
                const startupPid = currentProject?.id ||
                    (() => { try { return localStorage.getItem('ZIYA_LAST_PROJECT_ID') || undefined; } catch { return undefined; } })();
                if (startupPid && isServerReachable) {
                    const serverChats = await syncApi.listChats(startupPid, false);
                    if (serverChats.length > 0) {
                        console.log(`✅ INIT: Loaded ${serverChats.length} conversations from server (IDB unavailable)`);
                        // Stamp projectId so project-switch filtering works correctly.
                        setConversations(serverChats.map((c: any) => ({ ...c, projectId: c.projectId || startupPid })));
                    }
                }
            } catch (serverFallbackErr) {
                console.warn('⚠️ INIT: Server fallback also failed, starting fresh:', serverFallbackErr);
            }

            // Set initialized AFTER the server fetch so conversations.length > 0
            // when the effect fires, preventing a blank placeholder from being created.
            setIsInitialized(true);
        } finally {
            isRecovering.current = false;
            // Schedule retention purge AFTER initialization, off the critical
            // path.  Previously this ran inside db.init() and its getConversations()
            // call held the ziya-db-read Web Lock, starving getConversationShells()
            // and producing multi-minute startup hangs on large DBs.
            const schedulePurge = () => {
                purgeExpiredConversations(db).catch(err => {
                    console.warn('Retention purge failed (non-fatal):', err);
                });
            };
            if (typeof (window as any).requestIdleCallback === 'function') {
                (window as any).requestIdleCallback(schedulePurge, { timeout: 10000 });
            } else {
                setTimeout(schedulePurge, 5000);
            }
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

    // Fix 3: Synchronous clear on project switch.
    //
    // When currentProject.id changes, drop the previous project's folders
    // and conversations from React state IMMEDIATELY — before any async
    // loader has a chance to commit new data, before any memoized tree
    // builder runs, before the next paint.
    //
    // Previously the pattern was "keep showing the old project's data
    // until the new project's data finishes loading, then swap."  In
    // practice the new project's load takes long enough (multi-second
    // hydration loop, server round-trips, IDB scans) that the user sees
    // a fully-populated sidebar belonging to the wrong project for tens
    // of seconds.  Worse, every memo/cache layer that runs during that
    // window (tree builder, sort hash) snapshots the wrong project's
    // data and may reuse it on subsequent renders.  Root cause of the
    // "open new project shows hundreds of stale folders and swarm
    // containers" symptom.
    //
    // useLayoutEffect (rather than useEffect) so the clear is applied
    // in the same synchronous render pass that observes the new project
    // id — no commit-then-paint window where the previous project's
    // data is on screen with the new project active.
    const lastProjectIdRef = useRef<string | undefined>(undefined);
    useLayoutEffect(() => {
        const newPid = currentProject?.id;
        const prevPid = lastProjectIdRef.current;
        if (prevPid === undefined) {
            lastProjectIdRef.current = newPid;
            return;
        }
        if (prevPid === newPid) return;
        console.log(`🧹 PROJECT_CLEAR: switching ${prevPid?.substring(0, 8) ?? 'none'} → ${newPid?.substring(0, 8) ?? 'none'}, clearing folders/conversations`);
        setFolders([]);
        setConversations([]);
        lastProjectIdRef.current = newPid;
    }, [currentProject?.id]);

    // Load folders independently of initialization state
    // This ensures folder loading doesn't block conversation loading
    useEffect(() => {
        if (!isInitialized) return;

        const projectId = currentProject?.id;
        if (!projectId) return;

        const loadFoldersIndependently = async () => {
            try {
                console.log("Loading folders from database...");
                await new Promise(resolve => setTimeout(resolve, 100));

                // Load from IDB first (may be unavailable — that's OK, we fall back to server)
                let folders: ConversationFolder[] = [];
                try {
                    folders = await db.getFolders();

                    // Migrate folders without projectId
                    const folderNeedsMigration = folders.some(f => !f.projectId);
                    if (folderNeedsMigration && projectId) {
                        console.log('🔄 MIGRATION: Assigning folders without projectId to current project');
                        const migrated: ConversationFolder[] = [];
                        folders = folders.map(f => {
                            if (f.projectId) return f;
                            const next = { ...f, projectId, updatedAt: Date.now() };
                            migrated.push(next);
                            return next;
                        });
                        await Promise.all(migrated.map(f => db.saveFolder(f)));
                        console.log('✅ MIGRATION: All folders now have projectId');
                    }
                } catch (idbErr) {
                    // IDB unavailable — continue with empty local set; server merge below recovers folders.
                    console.warn('📡 INIT_FOLDERS: IDB unavailable, loading folders from server only');
                }

                // Render IDB folders immediately — do NOT await the server fetch.
                // The server call can be blocked behind a long-running hydration
                // request (~23s observed), and previously the entire folder render
                // waited on it.  Filter, repair, and commit IDB folders now;
                // background-merge server data after.
                const renderFolders = (src: ConversationFolder[], label: string) => {
                    const projectFolders = projectId
                        ? src.filter(f => f.projectId === projectId || folderIsEffectivelyGlobal(f, src))
                        : src;
                    // Repair circular parentId references — clear the parentId of
                    // any folder whose ancestor chain forms a cycle.  Without this,
                    // treeDataRaw re-detects the same cycles on every render, which
                    // floods the console and wastes CPU.
                    for (const f of projectFolders) {
                        if (!f.parentId) continue;
                        const visited = new Set<string>([f.id]);
                        let cur: string | null | undefined = f.parentId;
                        while (cur) {
                            if (visited.has(cur)) {
                                console.warn(`🔧 CYCLE_REPAIR: Clearing parentId of "${f.name}" (${f.id}) to break cycle`);
                                f.parentId = null;
                                db.saveFolder(f).catch(e => console.warn('Cycle repair persist failed:', e));
                                break;
                            }
                            visited.add(cur);
                            cur = projectFolders.find(pf => pf.id === cur)?.parentId;
                        }
                    }
                    setFolders(projectFolders);
                    console.log(`✅ Folders loaded for project ${projectId} (${label}): ${projectFolders.length} of ${src.length} total`);
                };

                // First commit: IDB-only folders.
                renderFolders(folders, 'idb');

                // Background server merge — does not block the render above.
                (async () => {
                    try {
                        const { listServerFolders } = await import('../api/folderSyncApi');
                        const serverFolders = await listServerFolders(projectId);
                        // Bail if the user has switched projects since we started.
                        if (currentProject?.id !== projectId) return;
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
                            const merged = Array.from(localMap.values());
                            Promise.all(merged.map(f => db.saveFolder(f))).catch(e =>
                                console.warn('📡 INIT_FOLDERS: Failed to persist server folders:', e)
                            );
                            // Re-render with server enrichment (e.g. TaskPlan folders that
                            // exist on the server but haven't reached IDB yet, plus globals).
                            if (currentProject?.id === projectId) renderFolders(merged, 'idb+server');
                        }
                    } catch (e) {
                        console.warn('📡 INIT_FOLDERS: Server folder fetch failed:', e);
                    }
                })();
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
        const tEffect = performance.now();
        console.log(`⏱️ SWITCH[${projectId.substring(0, 8)}]: project-switch effect fired`);
        const logSwitchTotal = (label: string) => console.log(`⏱️ SWITCH[${projectId.substring(0, 8)}]: ${label} at +${(performance.now() - tEffect).toFixed(0)}ms`);

        // Bump the epoch as the very first thing this effect does.  Every
        // sync invocation captures it locally; if a later project switch
        // bumps it again, in-flight syncs see the mismatch and bail.
        // This replaces the old "drop new sync if previous in-flight" guard
        // which made the new project's sync wait up to 30s for the next
        // interval tick — a long stall on large projects.
        const myEpoch = ++syncEpochRef.current;
        const isStale = () => syncEpochRef.current !== myEpoch;

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

            if (isActualProjectSwitch) {
                setIsProjectSwitching(true);
                console.log('🔄 PROJECT_SWITCH: Set isProjectSwitching = true for', projectId);
            }
        }

        recentlyFetchedFullIds.current.clear();

        // On an actual project switch, immediately load the new project's
        // conversations from local IndexedDB and show them in the sidebar before
        // the server sync completes.  Also run on initial browser session load
        // (serverSyncedForProject === null) so the sidebar is never blank while
        // waiting for the first server round-trip — the same guarantee we already
        // give for explicit project switches.
        // The server sync still runs in the background and will merge any
        // newer data from the server once it finishes.
        const isInitialLoad = serverSyncedForProject.current === null;
        if (isActualProjectSwitch || isInitialLoad) {
            const preloadForSwitch = async () => {
                try {
                    let shells: Conversation[];
                    try {
                        shells = await db.getConversationShells();
                    } catch {
                        shells = [];
                    }
                    const projectShells = shells.filter(
                        (c: any) => (c.projectId === projectId || conversationIsEffectivelyGlobal(c, folders)) && c.isActive !== false
                    );

                    // If the user has switched to yet another project while
                    // db.getConversationShells() was running, bail — committing
                    // this project's shells now would clobber the newer
                    // project's preload.
                    if (isStale()) return;
                    logSwitchTotal(`preload commit (${projectShells.length} shells)`);

                    // Replace stale conversations with the new project's local data.
                    // Use startTransition so the paint isn't blocked.
                    React.startTransition(() => {
                        setConversations(projectShells);
                    });

                    // Select the appropriate conversation for the new project.
                    const savedId = loadProjectConversationId(projectId);
                    const savedExists = savedId && projectShells.some(c => c.id === savedId);
                    let activeId: string | null = null;
                    if (savedExists) {
                        setCurrentConversationId(savedId!);
                        setTabState('ZIYA_CURRENT_CONVERSATION_ID', savedId!);
                        activeId = savedId!;
                    } else if (projectShells.length > 0) {
                        const mostRecent = projectShells.reduce((a, b) =>
                            (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                        );
                        setCurrentConversationId(mostRecent.id);
                        setTabState('ZIYA_CURRENT_CONVERSATION_ID', mostRecent.id);
                        activeId = mostRecent.id;
                    }

                    // Hydrate the active conversation's body from IDB right away.
                    // Without this, the chat pane shows empty until either the
                    // user clicks the conversation again (forcing loadConversation)
                    // or the periodic sync's finally-block rehydration runs —
                    // a 30-second window of "I see the title selected but the
                    // body is blank" after every switch into a large project.
                    // Single-record IDB read is cheap (~1–5ms) and cross-project
                    // safe: db.getConversation reads only the requested record.
                    if (activeId) {
                        try {
                            const fullConv = typeof db.getConversation === 'function'
                                ? await db.getConversation(activeId)
                                : null;
                            const fcMsgs = fullConv?.messages ?? [];
                            const idbIsShell = !!fullConv && (
                                (fullConv as any)._isShell ||
                                ((fullConv as any)._fullMessageCount && fcMsgs.length < (fullConv as any)._fullMessageCount)
                            );
                            if (fullConv && fcMsgs.length > 0 && !idbIsShell && !isStale()) {
                                React.startTransition(() => {
                                    setConversations(prev => prev.map(c =>
                                        c.id === activeId ? { ...c, messages: fcMsgs, _isShell: false, _fullMessageCount: undefined } : c
                                    ));
                                });
                            }
                        } catch (err) {
                            console.warn('⚠️ PRELOAD: Active conversation hydration failed:', err);
                        }
                    }
                } finally {
                    // Unblock the sidebar tree immediately — the server sync
                    // will continue in the background and refine the data.
                    // Only clear if we're still the current epoch; a stale
                    // preload should not flip the spinner off underneath the
                    // active project's switch.
                    if (!isStale()) setIsProjectSwitching(false);
                }
            };
            preloadForSwitch();
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

            // Only save the migrated conversations (not shells) to avoid clobbering full data
            const migratedNonShells = migrated.filter(c => !(c as any)._isShell);
            if (migratedNonShells.length > 0) await db.saveConversations(migratedNonShells);
            console.log('✅ MIGRATION: Conversations tagged with projectId');
            return migrated;
        };

        const syncWithServer = async () => {
            // Re-entrancy guard for periodic polling.  An actual project
            // switch always proceeds (the epoch counter handles staleness
            // at write sites); periodic ticks bail when a previous tick
            // is still in flight.  This eliminates the multi-sync race
            // that drops just-created conversations during long hydration
            // cycles on large projects.
            if (!isActualProjectSwitch && periodicSyncInFlightRef.current) {
                console.debug('📡 SERVER_SYNC: skipping periodic tick — previous sync still in flight');
                return;
            }
            const isPeriodicTick = !isActualProjectSwitch;
            if (isPeriodicTick) periodicSyncInFlightRef.current = true;
            // No "drop if another sync in flight" guard here: a project switch
            // mid-sync used to wait up to 30s for the next interval tick.  We
            // now let concurrent syncs run and use the epoch check to discard
            // stale results at every commit site.  setIsProjectSwitching(false)
            // still runs unconditionally in the finally so the UI unblocks.
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

                    // 2. Load current IndexedDB state
                    let allConversations: Conversation[];
                    try {
                        // Use shells (metadata only) for sync — we only need versions/ids
                        // Loading full message arrays for all 695 conversations causes OOM
                        allConversations = await db.getConversationShells();
                    } catch (dbErr) {
                        console.warn('📡 SERVER_SYNC: IndexedDB unavailable, using server as sole source:', dbErr);
                        allConversations = [];
                    }

                    // 2a. Migrate untagged conversations (only on first sync)
                    if (serverSyncedForProject.current !== projectId) {
                        allConversations = await migrateUntaggedConversations(allConversations, projectId);
                    }
                    serverSyncedForProject.current = projectId;

                    const localProjectConvs = allConversations.filter((c: any) => c.projectId === projectId || conversationIsEffectivelyGlobal(c, folders));

                    // 2b. Detect conversations that need full fetch:
                    //     - Server-only (new from another instance)
                    //     - Server version newer than local (updated from another instance)
                    const localMap = new Map(localProjectConvs.map((c: any) => [c.id, c]));
                    const needFullFetch: string[] = [];

                    // The conversation the user is actively viewing/editing is
                    // authoritative in React state.  Full-fetching it from the
                    // server during periodic polling overwrites IDB with stale
                    // data, creating a window where a crash loses unsaved messages.
                    // Only skip during polling — initial project switches
                    // (isActualProjectSwitch) need to fetch everything.
                    const activeConvId = isActualProjectSwitch ? null : currentConversationRef.current;

                    for (const sc of serverChats) {
                        const local = localMap.get(sc.id);
                        // Fetch-decision core extracted to utils/syncMerge.ts
                        // (pure + unit-testable).  Behavior unchanged: see
                        // shouldFetchFull for the version/count comparison
                        // rules and their rationale comments.
                        if (syncMerge.shouldFetchFull(sc as any, local as any, {
                            isActiveConv: sc.id === activeConvId,
                            alreadyFetchedThisSession: recentlyFetchedFullIds.current.has(sc.id),
                        })) {
                            needFullFetch.push(sc.id);
                        }
                    }

                    // DEFERRED HYDRATION: Don't block the project-switch commit on
                    // full-fetches.  Build mergedMap from local + summary data now
                    // (marking server-only entries as shells), commit immediately
                    // so the sidebar populates within ~1s, then hydrate in the
                    // background as fetches complete.  needFullFetch IDs are
                    // recorded in pendingHydration; the actual fetches kick off
                    // after the first commit lands.
                    const fullFetchMap = new Map<string, any>();
                    const pendingHydration = needFullFetch.slice();
                    // Server-side empty-shell IDs we'll DELETE after the commit.
                    // These are stale "New Conversation" entries the server still
                    // has on disk that no client wants — they accumulate when an
                    // empty chat is created and never receives a message before
                    // the user navigates away.  We only delete entries older than
                    // STALE_SHELL_AGE_MS to avoid racing with other tabs that may
                    // have just created a chat.
                    const staleEmptyShellIds: string[] = [];
                    const STALE_SHELL_AGE_MS = 60 * 60 * 1000;  // 1 hour
                    const STALE_SHELL_DELETE_CAP = 25;           // per sync cycle

                    // 3. Three-way merge: use _version to determine winner, keep all unique
                    const mergedMap = new Map<string, any>();
                    // Start with local conversations
                    localProjectConvs.forEach((conv: any) => {
                        mergedMap.set(conv.id, conv);
                    });

                    // Merge server conversations
                    // Merge-decision core extracted to utils/syncMerge.ts
                    // (pure + unit-testable).  Behavior unchanged: see
                    // mergeServerChat for the empty-shell / adopt-full /
                    // shell-placeholder / summary-overlay rules and their
                    // rationale comments.  Orchestration policies stay
                    // here: the per-cycle GC staging cap and the tab-scoped
                    // attempted-id dedup (both involve refs/limits that
                    // belong to this sync cycle, not the decision).
                    serverChats.forEach((sc: any) => {
                        const decision = syncMerge.mergeServerChat(
                            sc,
                            mergedMap.get(sc.id),
                            fullFetchMap.get(sc.id),
                            {
                                projectId,
                                isActiveConv: sc.id === currentConversationRef.current,
                                now: Date.now(),
                                staleShellAgeMs: STALE_SHELL_AGE_MS,
                            }
                        );
                        if (decision.action === 'skip-empty-shell') {
                            console.debug(`📡 SERVER_SYNC: skipping empty-shell ${sc.id?.substring(0, 8)} title="${sc.title}"`);
                            if (decision.staleDeleteEligible
                                && !serverGcAttemptedIds.current.has(sc.id)
                                && staleEmptyShellIds.length < STALE_SHELL_DELETE_CAP) {
                                staleEmptyShellIds.push(sc.id);
                                serverGcAttemptedIds.current.add(sc.id);
                            }
                        } else if (decision.action === 'set') {
                            mergedMap.set(sc.id, decision.record);
                        }
                    });

                    const mergedProjectConvs = Array.from(mergedMap.values());

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

                    // Prune recentlyFetchedFullIds to prevent unbounded growth during idle polling
                    if (recentlyFetchedFullIds.current.size > KNOWN_SERVER_IDS_MAX) {
                        // Delete oldest half instead of clearing entirely to avoid re-fetching everything
                        const ids = Array.from(recentlyFetchedFullIds.current);
                        ids.slice(0, Math.floor(ids.length / 2)).forEach(id => recentlyFetchedFullIds.current.delete(id));
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
                        if (conv.isActive !== false && !serverIdSet.has(conv.id)) {
                            // Never splice the conversation the user is actively
                            // viewing — even if it aged out of the grace period
                            // (e.g. composing a long message for >60s).  Removing
                            // it strands currentConversationId on a ghost id and
                            // forces the RECOVERY effect to switch chats underneath
                            // the user mid-compose.  A genuine cross-instance delete
                            // of the active chat is rare and self-heals on the next
                            // sync once the user navigates away.
                            if (conv.id === currentConversationRef.current) {
                                continue;
                            }
                            // Honor the grace period documented above: a conversation
                            // touched within the last 60s may simply not have
                            // round-tripped to the server yet (dual-write is debounced
                            // 2s and async; empty "New Conversation" shells are never
                            // pushed at all).  Splicing it here drops it from
                            // mergedResult and trips the RECOVERY effect.
                            const lastTouch = conv.lastAccessedAt || conv._version || 0;
                            if (lastTouch && now - lastTouch < SYNC_GRACE_PERIOD_MS) {
                                continue;
                            }
                            // Only remove if previously seen on server — never remove freshly imported conversations.
                            if (knownServerConversationIds.current.has(conv.id)) {
                                console.log(`📡 SERVER_SYNC: "${conv.title}" (${conv.id.substring(0, 8)}) removed from server — removing locally`);
                                deletedIds.push(conv.id);
                                mergedProjectConvs.splice(i, 1);
                            }
                        }
                    }

                    // 4. Push local-only / newer-local conversations to server (non-blocking)
                    // Phase 1: Identify candidates that need pushing.
                    // mergedProjectConvs are shells (messages stripped), so use
                    // _fullMessageCount for the message-count comparison and
                    // hydrate from IDB before actually sending.
                    const pushCandidateIds = mergedProjectConvs
                        .filter(c => {
                            // Don't push conversations we just marked as deleted
                            if (deletedIds.includes(c.id)) return false;
                            // Don't push inactive (deleted) conversations back to server
                            if (c.isActive === false) return false;
                            // Use _fullMessageCount (real IDB count) when available,
                            // since merged entries are shells with stripped messages.
                            const localMsgCount = (c as any)._fullMessageCount
                                || (Array.isArray(c.messages) ? c.messages.length : 0);
                            const sc = serverChats.find(sc => sc.id === c.id);
                            if (!sc) {
                                // Local-only conversation — only push if it has
                                // actual content.  Empty local-only records
                                // (e.g. task-card scratch shells, conversations
                                // created but never used) would otherwise be
                                // flagged every sync cycle, hydrated, found
                                // empty, and warned about indefinitely.
                                return localMsgCount > 0;
                            }
                            // Message-count divergence check.  If local has strictly
                            // more messages than server, push regardless of _version.
                            // Without this, a once-synced conversation whose _version
                            // matched the server can accumulate local messages via
                            // code paths that don't bump _version (or via stale
                            // server state from an earlier shell-push incident), and
                            // the version-only filter will block the correction
                            // indefinitely — producing permanent local/server drift.
                            // The April-2026 Rahul-Patil conversation hit this:
                            // server stuck at 2 messages, local at 46, same _version.
                            // Shell guards above still prevent pushing truncated
                            // shells, so widening here is safe.
                            const serverMsgCount = typeof (sc as any).messageCount === 'number'
                                ? (sc as any).messageCount
                                : Array.isArray((sc as any).messages) ? (sc as any).messages.length : 0;
                            if (localMsgCount > serverMsgCount) {
                                console.log(`📡 SERVER_SYNC: msg-count divergence for ${c.id.substring(0, 8)} (local=${localMsgCount} server=${serverMsgCount}) — pushing`);
                                return true;
                            }
                            // No-op push guard: if both sides are empty there is
                            // nothing meaningful to send.  This commonly fires on
                            // freshly-created delegate chats (DelegateLaunchButton
                            // stamps lastAccessedAt=Date.now() at insert time, which
                            // outruns the server's lastActiveAt; the version-only
                            // check below would then flag every empty delegate chat
                            // for push every sync cycle, hydrate it with 0 messages
                            // and warn).  Server already has the empty record, so a
                            // metadata-only push would just churn _version and
                            // produce another round-trip.
                            if (localMsgCount === 0 && serverMsgCount === 0) {
                                return false;
                            }
                            // Only push if local has a _version AND it's strictly greater than server's
                            // If server has no _version (old data), compare lastActiveAt instead
                            const serverVer = (sc as any)._version || sc.lastActiveAt || 0;
                            const localVer = (c as any)._version || c.lastAccessedAt || 0;
                            return localVer > serverVer;
                        })
                        .map(c => c.id);

                    // Phase 2: Hydrate candidates from IDB (full message arrays).
                    // Shells must never be sent to the server — they'd overwrite
                    // real data with empty content.  Load full records and guard
                    // against any that come back truncated or missing.
                    if (pushCandidateIds.length > 0) {
                        const fullConvResults = await Promise.allSettled(
                            pushCandidateIds.map(id => db.getConversation(id))
                        );
                        const chatsToSync: any[] = [];
                        fullConvResults.forEach((result, i) => {
                            if (result.status !== 'fulfilled' || !result.value) return;
                            const full = result.value;
                            const fullMsgCount = Array.isArray(full.messages) ? full.messages.length : 0;
                            // Guard: if the hydrated record is itself truncated
                            // (e.g. IDB was corrupted), don't push garbage.
                            if (fullMsgCount === 0) {
                                console.warn(`📡 SERVER_SYNC: Skipping push for ${full.id.substring(0, 8)} — hydrated with 0 messages`);
                                return;
                            }
                            if ((full as any)._isShell) {
                                console.warn(`📡 SERVER_SYNC: Skipping push for ${full.id.substring(0, 8)} — still marked as shell after hydration`);
                                return;
                            }
                            chatsToSync.push(syncApi.conversationToServerChat(full, projectId));
                        });
                        if (chatsToSync.length > 0) {
                            syncApi.bulkSync(projectId, chatsToSync).then(result => {
                                if (result.errors && result.errors.length > 0) {
                                    console.error('📡 SERVER_SYNC: Push partial failure —', result.errors.length, 'errors:', result.errors);
                                }
                                if (result.created > 0 || result.updated > 0) {
                                    console.log(`📡 SERVER_SYNC: Push complete — created=${result.created} updated=${result.updated} skipped=${result.skipped}`);
                                }
                            }).catch(e =>
                                console.error('📡 SERVER_SYNC: Push to server failed:', e)
                            );
                        }
                    }

                    // 5. Update IndexedDB with merged data
                    // Only write if something actually changed to avoid churning IndexedDB on idle polls
                    const localVersionMap = new Map(localProjectConvs.map((l: any) => [l.id, l._version || 0]));
                    const changedConvs = mergedProjectConvs.filter(mc => {
                        const localVer = localVersionMap.get(mc.id);
                        // New conversation (not in local) or version bumped
                        return localVer === undefined || (mc._version || 0) > localVer;
                    });
                    const conversationsChanged = changedConvs.length > 0;

                    if (conversationsChanged) {
                        // Protect the active conversation's messages in the IDB write.
                        // React state is authoritative for the conversation the user is
                        // actively editing.  Without this, the IDB write (built from
                        // stale IDB + stale server data) regresses the active
                        // conversation's message history, and a subsequent crash loses
                        // all messages that existed only in React state.
                        const liveActiveId = currentConversationRef.current;
                        if (liveActiveId) {
                            const liveConv = conversationsRef.current.find(c => c.id === liveActiveId);
                            const mergedConv = mergedProjectConvs.find(c => c.id === liveActiveId);
                            if (liveConv && mergedConv) {
                                const liveMsgCount = liveConv.messages?.length || 0;
                                const mergedMsgCount = mergedConv.messages?.length || 0;
                                const liveVersionIsNewer = (liveConv._version || 0) > (mergedConv._version || 0);
                                if (liveMsgCount > mergedMsgCount || (liveVersionIsNewer && liveMsgCount > 0)) {
                                    mergedConv.messages = liveConv.messages;
                                    mergedConv._version = Math.max(liveConv._version || 0, mergedConv._version || 0);
                                }
                            }
                        }
                        // Only write current-project conversations — other-project records
                        // already exist in IDB with full message data.  Only write the
                        // conversations that actually changed — writing all 674 every 30s
                        // causes OOM from cloning every message array.
                        const nonShells = changedConvs.filter(c => !(c as any)._isShell);
                        if (nonShells.length > 0) {
                            // Bail before touching IDB if the user has already
                            // moved on — a stale write for a different project
                            // would persist its own merged-but-now-irrelevant
                            // state onto disk and confuse the next session.
                            if (isStale()) return;
                            const tWrite = performance.now();
                            try {
                                await db.saveConversations(nonShells);
                                console.log(`⏱️ SYNC[${projectId.substring(0, 8)}]: saveConversations=${(performance.now() - tWrite).toFixed(0)}ms (${nonShells.length} records)`);
                            } catch (saveErr) {
                                // IDB may be permanently unavailable (e.g. corrupt backing store).
                                // Log and continue so the state update below still runs.
                                console.warn('📡 SERVER_SYNC: IDB write failed (non-fatal):', saveErr);
                            }
                        }
                    }

                    // 6. Update React state — only if something actually changed
                    // Wrap in startTransition so this potentially large state
                    // update (hundreds of conversations) doesn't block user
                    // interaction or paint frames during project switches.
                    // PERF: Build a lookup map from prev to preserve object
                    // references for unchanged conversations (avoids re-render cascade).
                    // PERF: Compute the merged result outside setConversations and
                    // call it with a plain value.  Functional updaters capture
                    // mergedProjectConvs/serverIdSet in their closure; those closures
                    // get retained by React's pending Update queue when the reducer
                    // bails out, leaking ~2MB per 30s sync cycle on long histories.
                    let mergedResult: Conversation[] | null = null;
                    try {
                        const prev = conversationsRef.current;
                        const prevMap = new Map<string, Conversation>(prev.map(c => [c.id, c] as [string, Conversation]));
                        // Don't let a stale sync cycle resurrect a conversation that
                        // was deleted in this tab between when this sync started and now.
                        const prevIds = new Set(prev.map((p: any) => p.id));
                        const mergedIds = new Set(mergedProjectConvs.map((mc: any) => mc.id));
                        // The conversation the user is actively viewing must never be
                        // dropped from state by a sync.  Capture once for the filter.
                        const activeConvId = currentConversationRef.current;
                        const safeConvs: any[] = mergedProjectConvs.filter((mc: any) =>
                            mc.id === activeConvId ||
                            prevIds.has(mc.id) ||
                            serverIdSet.has(mc.id) ||
                            // Preserve IDB-resident conversations that belong to this project
                            // but have never been pushed to the server (e.g. empty new chats).
                            // Without this third condition, once they fall out of React state
                            // they can never re-enter — safeConvs drops them and the
                            // preservation loop skips them because mergedIds already has them.
                            (mc.isActive !== false &&
                                mc.projectId === projectId &&
                                !knownServerConversationIds.current.has(mc.id))
                        );

                        // Preserve in-memory-only conversations that haven't been
                        // persisted to IDB yet and aren't on the server — e.g. a
                        // just-forked conversation whose background db.saveConversation
                        // hasn't completed before this sync cycle loaded IDB shells.
                        // Without this, the next sync after a fork drops the fork
                        // from React state.
                        // Guard: only keep active, in-this-project entries, and skip
                        // any id the server previously knew about (those are real
                        // deletions handled by the block above).
                        //
                        // Also cap by lastAccessedAt: prev-only entries by construction
                        // come from code paths that haven't yet round-tripped IDB.  In
                        // practice that means the entry was created/touched very
                        // recently.  An old prev-only entry is an anomaly (e.g.
                        // knownServerConversationIds was pruned above 5000 entries
                        // between a sibling-tab delete and now) and resurrecting it
                        // would fight the delete.  The 5-minute window is much larger
                        // than any legitimate IDB write latency and comfortably covers
                        // the 2s dual-write debounce + sync cycle.
                        const PRESERVATION_MAX_AGE_MS = 5 * 60 * 1000;
                        const nowTs = Date.now();
                        for (const p of prev) {
                            if (mergedIds.has((p as any).id)) continue;
                            if ((p as any).isActive === false) continue;
                            if (knownServerConversationIds.current.has((p as any).id)) continue;
                            const pid = (p as any).projectId;
                            if (pid && pid !== projectId && !(p as any).isGlobal) continue;
                            const lastActivity = (p as any).lastAccessedAt || (p as any)._version || 0;
                            if (lastActivity === 0 || nowTs - lastActivity > PRESERVATION_MAX_AGE_MS) continue;
                            safeConvs.push(p);
                        }

                        // Preserve in-memory messages that were lazy-loaded during
                        // this session but not yet persisted to IDB.
                        for (const mc of safeConvs) {
                            const mcMsgCount = mc.messages?.length || 0;
                            const inMemory = prevMap.get(mc.id);
                            const inMemoryCount = inMemory?.messages?.length || 0;
                            const isActive = mc.id === currentConversationRef.current;
                            const inMemoryIsNewer = (inMemory?._version || 0) > (mc._version || 0);
                            if (inMemoryCount > mcMsgCount
                                || (isActive && inMemoryCount > 0 && inMemoryCount >= mcMsgCount)
                                || (inMemoryIsNewer && inMemoryCount > 0)) {
                                mc.messages = inMemory.messages;
                                // We just restored REAL messages from React state.
                                // mc came from db.getConversationShells() and carries
                                // _isShell: true. Leaving the shell marker on means
                                // the FAST_PATH dual-write filter (!c._isShell) will
                                // drop every subsequent push, silently stranding all
                                // local edits on this browser.  Clear shell flags
                                // since we now have full data.
                                delete (mc as any)._isShell;
                                delete (mc as any)._fullMessageCount;
                                if (inMemoryIsNewer) mc._version = inMemory._version;
                            }
                            // Preserve locally-mutated fields when the in-memory
                            // _version is newer than what the server returned.
                            // A higher local _version means we made a change that
                            // hasn't round-tripped through the server yet (e.g.
                            // a drag-drop folder move whose 2s debounced push is
                            // still in flight, a rename, or a global toggle).
                            // Without this, the next sync tick overwrites those
                            // fields with the stale server values and the change
                            // visibly reverts a few seconds after the user acts.
                            if (inMemory && inMemoryIsNewer) {
                                mc.folderId = inMemory.folderId;
                                mc.isGlobal = inMemory.isGlobal;
                                // Title preservation must NOT downgrade a real
                                // (auto- or user-set) merged title back to a
                                // placeholder.  addMessageToConversation bumps
                                // _version on every append while the in-memory
                                // title may still be the "New Conversation"
                                // placeholder, so inMemoryIsNewer alone would
                                // clobber a title the sync just resolved.  Only
                                // adopt the in-memory title when it is itself a
                                // meaningful value (a genuine in-flight rename).
                                if (inMemory.title && !PLACEHOLDER_TITLES.has(inMemory.title)) {
                                    mc.title = inMemory.title;
                                }
                            }
                            // Preserve user-read state across syncs.
                            // hasUnreadResponse is cleared on conversation view
                            // without a _version bump (loadConversation avoids
                            // the 35s-freeze cost of touching every record),
                            // so version-based merging can't keep it cleared
                            // when a full fetch returns the stale value.
                            // Guard on message count so a genuinely-new unread
                            // signal from a sibling tab isn't suppressed.
                            if (inMemory && inMemory.hasUnreadResponse === false
                                && mc.hasUnreadResponse
                                && mcMsgCount <= inMemoryCount) {
                                mc.hasUnreadResponse = false;
                            }
                        }

                        // PERF: Reuse prev object references for conversations whose
                        // _version and message count haven't changed.  This prevents
                        // downstream useMemo/useEffect from detecting spurious changes.
                        let anyReferenceChanged = prev.length !== safeConvs.length;
                        const result = safeConvs.map((mc: any) => {
                            const existing = prevMap.get(mc.id);
                            if (existing &&
                                (mc._version || 0) <= (existing._version || 0) &&
                                (mc.messages?.length || 0) <= (existing.messages?.length || 0) &&
                                mc.title === existing.title &&
                                mc.folderId === existing.folderId &&
                                mc.isGlobal === existing.isGlobal &&
                                mc.delegateMeta?.status === existing.delegateMeta?.status &&
                                mc.hasUnreadResponse === existing.hasUnreadResponse) {
                                return existing;
                            }
                            anyReferenceChanged = true;
                            return mc;
                        });
                        if (anyReferenceChanged) mergedResult = result;
                    } catch (mergeErr) {
                        console.error('📡 SERVER_SYNC: Merge failed, state unchanged:', mergeErr);
                        mergedResult = null;
                    }

                    if (mergedResult !== null) {
                        // The headline guard for the stale-sync clobber bug:
                        // mergedResult was built from `projectId` captured in
                        // closure.  If the user has since switched away,
                        // committing it now would replace the new project's
                        // sidebar with the old project's conversations — the
                        // exact "wrong project's chats visible" symptom.
                        if (isStale()) return;
                        logSwitchTotal(`server-sync commit (${mergedResult.length} conversations)`);
                        const localCount = mergedResult.filter((c: any) => c.projectId === projectId && !c.isGlobal).length;
                        const globalCount = mergedResult.filter((c: any) => c.isGlobal).length;
                        const otherCount = mergedResult.length - localCount - globalCount;
                        console.log(`📡 SERVER_SYNC: commit breakdown for ${projectId.substring(0, 8)}: ${localCount} local, ${globalCount} global, ${otherCount} other`);
                        React.startTransition(() => {
                            setConversations(prev => {
                                // Late preservation: catch in-memory conversations created
                                // between when this sync built its merged data (against a
                                // possibly-stale conversationsRef.current snapshot) and now.
                                // Without this, a brand-new conversation created via
                                // startNewChat is briefly dropped on the next sync commit
                                // when its IDB write hadn't landed before the sync read
                                // local shells AND the sync's prev-only preservation read
                                // ran before React committed startNewChat's setConversations.
                                const mergedIdSet = new Set(mergedResult!.map((c: any) => c.id));
                                const PRESERVATION_MAX_AGE_MS = 5 * 60 * 1000;
                                const nowTs = Date.now();
                                const missed = prev.filter((p: any) =>
                                    !mergedIdSet.has(p.id)
                                    && p.isActive !== false
                                    && (!p.projectId || p.projectId === projectId || (p as any).isGlobal)
                                    && !knownServerConversationIds.current.has(p.id)
                                    && (nowTs - (p.lastAccessedAt || p._version || 0)) < PRESERVATION_MAX_AGE_MS
                                );
                                // Active-conversation safety net: if the user's currently
                                // viewed conversation is in prev but didn't make it into
                                // mergedResult, rescue it unconditionally — bypassing the
                                // knownServerConversationIds and age guards above.  The
                                // active conversation is the one the user is interacting
                                // with right now; dropping it from state strands
                                // currentConversationId pointing at a ghost id and forces
                                // the recovery effect to switch the user to a different
                                // chat (the symptom: typed text and the new-chat banner
                                // vanish ~30s after creation).  We log enough state to
                                // diagnose the underlying drop cause when this fires.
                                const activeId = currentConversationRef.current;
                                if (activeId && !mergedIdSet.has(activeId)) {
                                    const fromPrev = prev.find((p: any) => p.id === activeId);
                                    if (fromPrev && !missed.some((m: any) => m.id === activeId)) {
                                        console.warn(
                                            `🛟 ACTIVE_CONV_RESCUE: ${activeId.substring(0, 8)} `
                                            + `dropped by sync — restoring. `
                                            + `inPrev=true, inServerIds=${knownServerConversationIds.current.has(activeId)}, `
                                            + `lastAccessedAt=${(fromPrev as any).lastAccessedAt}, `
                                            + `isActive=${(fromPrev as any).isActive}, `
                                            + `projectId=${(fromPrev as any).projectId}`
                                        );
                                        missed.push(fromPrev);
                                    }
                                }
                                if (missed.length === 0) return mergedResult!;
                                console.debug(`📡 SERVER_SYNC: late preservation rescued ${missed.length} prev-only conversation(s)`);
                                return [...mergedResult!, ...missed];
                            });
                        });
                    }

                    // DEFERRED HYDRATION: now that the sidebar is rendered, fetch
                    // full message bodies for shell entries in the background and
                    // fold each one in as it lands.  Each fetch is independent —
                    // a slow chat (cross-project getChat fallback) doesn't block
                    // the rest from arriving.  recentlyFetchedFullIds is updated
                    // for both success and "server returns 0 messages" outcomes,
                    // so we never retry IDs that fundamentally have no body.
                    // Network errors leave the entry unmarked for next-cycle retry.
                    //
                    // Filter against mergedMap: IDs skipped during the merge
                    // (empty-shell "New Conversation" stubs) won't be in the
                    // merged set, so hydrating them is pointless and burns
                    // server time.  Without this filter they retried forever
                    // because each retry returned 0 messages → nFailed++ →
                    // never added to recentlyFetchedFullIds → next sync
                    // re-fetches.
                    const hydrationTargets = pendingHydration.filter(id => mergedMap.has(id));
                    if (hydrationTargets.length > 0) {
                        const tHydrateStart = performance.now();
                        // Use the bulk endpoint to avoid the lock-contention storm
                        // that made 56 parallel /chats/{id} requests take 23s.
                        // Chunk at 50 to stay under the server's 200-id limit
                        // and keep payloads reasonable for proxies / browsers.
                        const BULK_CHUNK = 50;
                        const chunks: string[][] = [];
                        for (let i = 0; i < hydrationTargets.length; i += BULK_CHUNK) {
                            chunks.push(hydrationTargets.slice(i, i + BULK_CHUNK));
                        }
                        console.log(`⏱️ SYNC[${projectId.substring(0, 8)}]: bulk-hydrating ${hydrationTargets.length} shells in ${chunks.length} chunk(s)${pendingHydration.length !== hydrationTargets.length ? ` (skipped ${pendingHydration.length - hydrationTargets.length} empty-shells)` : ''}`);
                        let nDone = 0;
                        let nFailed = 0;
                        let nEmpty = 0;
                        const processBulk = async () => {
                            // Run chunks in parallel — each chunk is one HTTP
                            // request so the lock-contention issue doesn't apply
                            // (server pays decryption setup once per chunk).
                            await Promise.all(chunks.map(async (chunk) => {
                                if (isStale()) return;
                                const result = await syncApi.bulkGetChats(projectId, chunk);
                                if (isStale()) return;
                                if (!result) {
                                    // Whole chunk failed — count all as failed,
                                    // don't mark as fetched so next sync retries.
                                    nFailed += chunk.length;
                                    return;
                                }
                                // Confirmed-missing IDs (server returned them in
                                // the missing list rather than as null/error).
                                for (const id of result.missing) {
                                    recentlyFetchedFullIds.current.add(id);
                                    nEmpty++;
                                }
                                // Apply each returned chat.
                                React.startTransition(() => {
                                    const byId = new Map(result.chats.map(c => [c.id, c]));
                                    setConversations(prev => prev.map(c => {
                                        const full = byId.get(c.id);
                                        if (!full) return c;
                                        // Don't overwrite if a newer in-memory version exists
                                        // (user is actively editing while hydration lands).
                                        if ((c._version || 0) > ((full as any)._version || 0)) return c;
                                        return {
                                            ...c,
                                            ...full,
                                            _isShell: false,
                                            _fullMessageCount: undefined,
                                            projectId: (full as any).projectId || projectId,
                                            folderId: (full as any).groupId || (full as any).folderId || c.folderId || null,
                                            delegateMeta: (full as any).delegateMeta || c.delegateMeta || null,
                                            isActive: (full as any).isActive !== false,
                                            _version: (full as any)._version || Date.now(),
                                        };
                                    }));
                                });
                                // Mark each returned chat as fetched.
                                for (const c of result.chats) {
                                    recentlyFetchedFullIds.current.add(c.id);
                                    nDone++;
                                }
                            }));
                            console.log(`⏱️ SYNC[${projectId.substring(0, 8)}]: bulk hydration complete in ${(performance.now() - tHydrateStart).toFixed(0)}ms (${nDone} ok, ${nEmpty} empty, ${nFailed} failed)`);
                        };
                        processBulk();
                    }

                    // Server-side garbage collection of stale empty-shell chats.
                    // The merge loop staged staleEmptyShellIds; issue DELETEs in
                    // the background.  Capped at STALE_SHELL_DELETE_CAP per cycle
                    // to avoid storming the server on first sync of a project
                    // with a large backlog of accumulated empties.  Subsequent
                    // cycles will pick up the rest.  Errors are non-fatal: the
                    // chat will be re-skipped next cycle and we'll retry the
                    // delete then.  Stale guard prevents racing with another
                    // tab that just created a conversation.
                    if (staleEmptyShellIds.length > 0) {
                        console.log(`🗑️ SERVER_GC[${projectId.substring(0, 8)}]: deleting ${staleEmptyShellIds.length} stale server-side empty-shells`);
                        staleEmptyShellIds.forEach(id => {
                            syncApi.deleteChat(projectId, id).then(ok => {
                                if (!ok) {
                                    console.debug(`🗑️ SERVER_GC: delete returned non-ok for ${id.substring(0, 8)}`);
                                }
                            }).catch(err => {
                                console.debug(`🗑️ SERVER_GC: delete failed for ${id.substring(0, 8)}:`, err);
                            });
                        });
                    }

                    // 7. Update current conversation if it doesn't exist in merged set
                    // For periodic polls: NEVER change currentConversationId — it's
                    // a per-tab view concern that only changes on explicit user actions.
                    //
                    // For actual project switches: relocate to a conversation that
                    // belongs to the new project.  The old conversation from the
                    // previous project is irrelevant and confusing if left visible.
                    if (isActualProjectSwitch) {
                        // Stale syncs must not change the active conversation
                        // — that would yank the user from a chat the user
                        // actually clicked into for the current project.
                        if (isStale()) return;
                        const currentId = currentConversationRef.current;
                        const currentConv = mergedProjectConvs.find(c => c.id === currentId);
                        const belongsToNewProject = currentConv &&
                            (currentConv.isGlobal || currentConv.projectId === projectId);

                        // The preload already selected a conversation for this project.
                        // Only override if what's currently selected still doesn't belong
                        // to the new project (can happen if the preload found no shells).
                        if (!belongsToNewProject || !currentId) {
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
                            f => f.projectId === projectId || folderIsEffectivelyGlobal(f, localFolders)
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
                        const allMergedFolders = Array.from(folderMap.values());
                        const mergedFolders = allMergedFolders.filter(f => f.projectId === projectId || folderIsEffectivelyGlobal(f, allMergedFolders));

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
                            if (isStale()) return;
                            await Promise.all(mergedFolders.map(f => db.saveFolder(f)));
                        }

                        // Skip setFolders entirely when state matches — the previous
                        // functional update form enqueued `mergedFolders` in a closure
                        // every sync cycle, which React's hook queue retained even on
                        // bailout.  With a 30s sync that leaked ~100 folder objects per
                        // tick.  Reuse the foldersChanged flag computed just above.
                        if (foldersChanged) {
                            // Same epoch guard as the conversations commit:
                            // stale folder data must not overwrite the new
                            // project's hierarchy.
                            if (isStale()) return;
                            setFolders(mergedFolders);
                        }
                    } catch (e) {
                        console.warn('📡 SERVER_SYNC: Folder sync failed:', e);
                    }

                } catch (syncError) {
                    console.error('📡 SERVER_SYNC: Error syncing with server:', syncError);
                }
            } finally {
                // Re-hydrate the active conversation if it's still a shell after
                // sync.  This catches the race where initializeWithRecovery's
                // lazy-load was clobbered by the sync's setConversations.
                const activeId = currentConversationRef.current;
                // Stale syncs skip rehydration: their `activeId` is the
                // previous project's active conversation; rehydrating it now
                // would push messages into a chat the user can't see anyway.
                if (activeId && !isStale()) {
                    const activeConv = conversationsRef.current.find(c => c.id === activeId);
                    if (activeConv && ((activeConv as any)._isShell || (activeConv.messages?.length || 0) <= 2)) {
                        try {
                            const fullConv = typeof db.getConversation === 'function'
                                ? await db.getConversation(activeId)
                                : null;
                            const fcMsgs = fullConv?.messages ?? [];
                            if (fullConv && fcMsgs.length > (activeConv.messages?.length || 0)) {
                                if (isStale()) return;
                                React.startTransition(() => {
                                    setConversations(prev => prev.map(c =>
                                        c.id === activeId
                                            ? { ...c, messages: fcMsgs, _isShell: false, _fullMessageCount: undefined }
                                            : c
                                    ));
                                });
                                console.log(`✅ POST_SYNC: Re-hydrated active conversation with ${fcMsgs.length} messages`);
                            }
                        } catch (e) {
                            console.warn('⚠️ POST_SYNC: Active conversation re-hydration failed:', e);
                        }
                    }
                }
                // Always clear the switching spinner.  Even a stale sync
                // exit should release the UI — by the time we get here, a
                // newer epoch has its own setIsProjectSwitching flow.
                setIsProjectSwitching(false);
                if (isPeriodicTick) periodicSyncInFlightRef.current = false;
            }
        };
        syncWithServer();

        // Poll every 30 seconds to pick up changes from other browser instances
        const intervalId = setInterval(syncWithServer, 30_000);

        return () => clearInterval(intervalId);
    }, [isInitialized, currentProject?.id, isEphemeralMode]);

    // GC empty "New Conversation" nodes older than 1 hour
    useEffect(() => {
        if (!isInitialized || isEphemeralMode) return;

        const GC_INTERVAL_MS = 5 * 60 * 1000; // every 5 minutes

        const runGc = () => {
            // Skip GC when tab is hidden to avoid IndexedDB I/O and state updates in background
            if (document.hidden) return;

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
        // Timestamp when the active conversation was last set — used by the
        // RECOVERY effect's grace period to avoid switching away from a
        // just-created conversation during sync-merge races.
        if (currentConversationId) {
            (window as any).__ziyaLastConvSetAt = Date.now();
        }
    }, [currentConversationId, conversations, currentFolderId]);

    const mergeConversations = useCallback((local: Conversation[], remote: Conversation[]) => {
        const merged = new Map<string, Conversation>();

        // Add all local conversations first
        local.forEach(conv => {
            if (!conv?.id) return; // Skip null/undefined/corrupt entries
            merged.set(conv.id, {
                ...conv,
                title: conv.title || 'Untitled',
                messages: conv.messages || [],
            });
        });

        // Merge remote conversations only if newer
        remote.forEach(remoteConv => {
            if (!remoteConv?.id) return; // Skip null/undefined/corrupt entries
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
                // Cold path (cross-tab broadcast): fetch fresh folders so
                // folder-inherited globalness resolves correctly — the closure
                // `folders` here is mount-stale (not in this effect's deps).
                const allDbFolders = await db.getFolders();
                const projectConvs = (await db.getConversations())
                    .filter(c => c.projectId === currentProject?.id || conversationIsEffectivelyGlobal(c, allDbFolders));
                setConversations(prev => {
                    try { return mergeConversations(prev, projectConvs); }
                    catch (e) { console.error('📡 Cross-tab merge failed:', e); return prev; }
                });
            } catch (err) {
                console.error('📡 Sync: Failed to reload conversations:', err);
            }
        };

        const handleFoldersChanged = async () => {
            if (!isInitialized) return;
            try {
                const allFolders = await db.getFolders();
                const folderMap = new Map<string, ConversationFolder>(allFolders.map(f => [f.id, f] as [string, ConversationFolder]));
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
                const allMergedFolders = Array.from(folderMap.values());
                const projectFolders = allMergedFolders.filter(
                    f => f.projectId === currentProject?.id || folderIsEffectivelyGlobal(f, allMergedFolders)
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

    // Recovery: if currentConversationId points at a conversation that no
    // longer exists in state (purged by a stale sync, deleted in another
    // tab, or stranded by an empty-shell server cleanup), switch to the
    // most-recently-accessed conversation in the current project rather
    // than letting the user keep typing into a ghost id.  Without this,
    // the next send routes to an orphan and the response lands in a
    // brand-new conversation with no visible history.
    //
    // Guarded against transient states where the conversation is
    // legitimately not in state yet:
    //   - isProjectSwitching: server sync hasn't committed yet
    //   - isLoadingConversation: lazy-load in flight
    //   - streaming: currentMessages must stay stable
    //   - empty list: handled by the effect above
    useEffect(() => {
        if (!isInitialized || !currentConversationId) return;
        if (isProjectSwitching || isLoadingConversation) return;
        if (conversations.length === 0) return;
        if (streamingConversationsRef.current.has(currentConversationId)) return;
        if (conversations.some(c => c.id === currentConversationId)) return;

        // Grace period: don't switch away from a conversation that was set as
        // active very recently (within 60s).  This covers the race where a
        // sync cycle's setConversations briefly drops a just-created
        // conversation before the ACTIVE_CONV_RESCUE (inside the updater)
        // re-adds it — React.startTransition can defer the commit, and the
        // RECOVERY effect fires on the intermediate state where the
        // conversation is absent.  The 60s window is generous enough to
        // cover any IDB write + server round-trip + sync-cycle timing.
        const convSetAt = (window as any).__ziyaLastConvSetAt;
        if (convSetAt && Date.now() - convSetAt < 60_000) {
            console.debug(`🛡️ RECOVERY_GRACE: Suppressing switch — conversation ${currentConversationId.substring(0, 8)} set ${((Date.now() - convSetAt) / 1000).toFixed(0)}s ago`);
            return;
        }

        // Active conversation has gone missing.  Pick a replacement from
        // the current project (or globals), preferring most-recently-accessed.
        const pid = currentProject?.id;
        const candidates = conversations.filter(c =>
            c.isActive !== false &&
            (!pid || c.projectId === pid || conversationIsEffectivelyGlobal(c, folders))
        );
        if (candidates.length === 0) {
            // Nothing to fall back to; the empty-list effect above will
            // create a fresh conversation on the next render.
            return;
        }
        const mostRecent = candidates.reduce((a, b) =>
            (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
        );
        console.warn(
            `🛟 RECOVERY: currentConversationId ${currentConversationId.substring(0, 8)} ` +
            `not in state — switching to "${mostRecent.title}" (${mostRecent.id.substring(0, 8)})`
        );
        setCurrentConversationId(mostRecent.id);
        try { setTabState('ZIYA_CURRENT_CONVERSATION_ID', mostRecent.id); } catch { /* ignore */ }
    }, [conversations, currentConversationId, isInitialized, isProjectSwitching, isLoadingConversation, currentProject?.id, folders]);

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
        // Capture the prior folder state so we can roll back if the
        // server rejects the toggle.  Optimistic update first, then
        // server confirm; on failure restore the original.
        const prevState = folders.find(f => f.id === folderId);
        if (!prevState) return;
        const wasGlobal = prevState.isGlobal === true;
        const newIsGlobal = !wasGlobal;
        const ownerProjectId = prevState.projectId || currentProject?.id;
        if (!ownerProjectId) {
            console.warn('toggleFolderGlobal: no owner project id available');
            return;
        }

        // Optimistic local update.
        const optimistic: ConversationFolder = {
            ...prevState,
            isGlobal: newIsGlobal,
            projectId: wasGlobal ? currentProject?.id : prevState.projectId,
            updatedAt: Date.now()
        };
        setFolders(prev => prev.map(f => f.id === folderId ? optimistic : f));

        // Server is authoritative for the isGlobal flag.  Hit the dedicated
        // endpoint with the folder's owner project, not the currently-viewed
        // project (which may differ for globals surfaced from other projects).
        const result = await folderSyncApi.setFolderGlobal(ownerProjectId, folderId, newIsGlobal);
        if (!result) {
            console.error('toggleFolderGlobal: server rejected, rolling back');
            setFolders(prev => prev.map(f => f.id === folderId ? prevState : f));
            message.error('Failed to update global flag — server unavailable');
            return;
        }
        // Persist confirmed state to IDB so it survives reloads.  Use the
        // server's response (authoritative) rather than our optimistic copy.
        await db.saveFolder(result);
        projectSync.post('folders-changed');
        console.log(`📁 Folder "${prevState.name}" is now ${result.isGlobal ? 'global' : 'project-scoped'}`);
    }, [folders, currentProject?.id]);

    // Fork a conversation: create a copy with a new id, select it, and persist.
    // Goes through the same architecture as other mutations (optimistic state
    // update + IDB write + projectSync broadcast + dirty-tracking for server
    // dual-write) so siblings tabs see the fork and the server receives it.
    // Returns the new conversation id on success, or null on failure.
    // On persistence failure, rolls back the optimistic state change and
    // surfaces an error to the user — fork is never silently lost.
    const forkConversation = useCallback(async (conversationId: string): Promise<string | null> => {
        if (conversationId.startsWith('conv-')) {
            conversationId = conversationId.substring(5);
        }

        let source = conversationsRef.current.find(c => c.id === conversationId);
        if (!source) {
            message.error('Cannot fork: conversation not found');
            return null;
        }

        // If the source is a shell (messages stripped for sidebar memory),
        // hydrate from IDB so the fork carries full history.  Read lock is
        // separate from the write lock, safe during active streaming.
        if ((source as any)._isShell) {
            try {
                const full = await db.getConversation(conversationId);
                if (full && !((full as any)._isShell) && full.messages.length > 0) {
                    source = full;
                }
            } catch (err) {
                console.warn('Fork: failed to hydrate source from IDB:', err);
            }
        }

        const newId = uuidv4();
        const forked: Conversation & { _isShell?: boolean; _fullMessageCount?: number } = {
            ...source,
            id: newId,
            title: `Fork: ${source.title}`,
            lastAccessedAt: Date.now(),
            _version: Date.now(),
            hasUnreadResponse: false,
            isActive: true,
        };
        // Strip transient shell metadata — the fork is a full conversation.
        delete forked._isShell;
        delete forked._fullMessageCount;

        // Optimistic state update + navigation. Don't await anything on the
        // click path — the Dropdown overlay is torn down during streaming
        // re-renders if we yield here.
        setConversations(prev => [...prev, forked]);
        setCurrentConversationId(newId);

        // Persist and broadcast.  Direct db.saveConversation gives us a
        // reliable per-operation success signal; queueSave debounces and
        // swallows its internal errors, which we don't want for a
        // user-initiated action that needs rollback-on-failure semantics.
        try {
            await db.saveConversation(forked);
            dirtyConversationIds.current.add(newId);
            projectSync.post('conversations-changed', { ids: [newId] });
            // Push the fork to the server immediately.  Without this, the
            // fork lives only in IDB until the user types into it (the next
            // FAST_PATH dual-write picks it up).  Until then, other browsers
            // and machines never see it — same class as the message-sync
            // bug, just one level up.  Best-effort: failures don't roll back
            // the local fork.
            if (currentProject?.id) {
                try {
                    const serverChat = syncApi.conversationToServerChat(forked, currentProject.id);
                    await syncApi.bulkSync(currentProject.id, [serverChat]);
                } catch (e) {
                    console.warn('Fork: server push failed (non-fatal — will retry next sync):', e);
                }
            }
            message.success('Conversation forked successfully');
            return newId;
        } catch (err) {
            console.error('Fork: failed to persist forked conversation:', err);
            // Roll back optimistic state so React, IDB, and the server
            // all agree the fork never happened.
            setConversations(prev => prev.filter(c => c.id !== newId));
            setCurrentConversationId(conversationId);
            message.error('Failed to fork conversation — storage unavailable');
            return null;
        }
    }, [setCurrentConversationId, currentProject?.id]);

    const toggleConversationGlobal = useCallback(async (conversationId: string) => {
        // Capture the current state before any mutation so we can roll back
        // on server failure.  The optimistic update gives the user immediate
        // visual feedback; the server call confirms it durably.  If the
        // server rejects, we restore the original state and surface an error.
        const prevState = conversationsRef.current.find(c => c.id === conversationId);
        if (!prevState) {
            console.warn(`toggleConversationGlobal: ${conversationId.substring(0, 8)} not in state`);
            return;
        }
        const wasGlobal = prevState.isGlobal === true;
        const newIsGlobal = !wasGlobal;

        // Optimistic local update.
        setConversations(prev => {
            const updated = prev.map(conv => {
                if (conv.id === conversationId) {
                    return {
                        ...conv,
                        lastAccessedAt: Date.now(),
                        isGlobal: newIsGlobal,
                        // When un-globaling, pin to current project
                        projectId: wasGlobal ? currentProject?.id : conv.projectId,
                        _version: Date.now()
                    };
                }
                return conv;
            });
            return updated;
        });

        // Server is authoritative for the isGlobal flag.  Hit the dedicated
        // endpoint with the chat's owner project — that's the project whose
        // on-disk file holds this chat — rather than the currently-viewed
        // project, which may differ for global chats surfaced from other
        // projects.
        const ownerProjectId = prevState.projectId || currentProject?.id;
        if (!ownerProjectId) {
            console.warn('toggleConversationGlobal: no owner project id available');
            return;
        }
        const result = await syncApi.setChatGlobal(ownerProjectId, conversationId, newIsGlobal);
        if (!result) {
            // Server failure — roll back the optimistic update so React,
            // IDB, and the server all agree the toggle never happened.
            console.error('toggleConversationGlobal: server rejected, rolling back');
            setConversations(prev => prev.map(c =>
                c.id === conversationId ? prevState : c
            ));
            message.error('Failed to update global flag — server unavailable');
            return;
        }
        // Persist the confirmed state to IDB (server already has it).
        // queueSave is debounced, so this won't fight the next periodic sync.
        queueSave(conversationsRef.current, { changedIds: [conversationId] }).catch(console.error);
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
                        lastAccessedAt: Date.now(),
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

        // If the moved conversation was the active one, switch to the most
        // recent remaining conversation rather than creating a new empty one.
        if (currentConversationId === conversationId && targetProjectId !== sourceProjectId) {
            const remaining = conversationsRef.current.filter(
                c => c.id !== conversationId && c.isActive !== false &&
                    (c.projectId === sourceProjectId || c.isGlobal)
            );
            if (remaining.length > 0) {
                const mostRecent = remaining.reduce((a, b) =>
                    (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a);
                setCurrentConversationId(mostRecent.id);
                setTabState('ZIYA_CURRENT_CONVERSATION_ID', mostRecent.id);
            } else {
                startNewChat();
            }
        }

        // Server-side move: push to target first, THEN delete from source.
        // This ordering ensures the conversation exists on the target before
        // we remove it from the source, preventing data loss on partial failure.
        if (sourceProjectId && sourceProjectId !== targetProjectId && capturedConv) {
            const serverChat = syncApi.conversationToServerChat(
                { ...capturedConv, projectId: targetProjectId, lastAccessedAt: Date.now(), isGlobal: false, _version: Date.now() },
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

    const copyConversationToProject = useCallback(async (conversationId: string, targetProjectId: string) => {
        const sourceProjectId = currentProject?.id;

        // Read fresh state via updater to avoid stale closure
        let capturedConv: Conversation | undefined;
        setConversations(prev => {
            capturedConv = prev.find(c => c.id === conversationId);
            return prev; // No mutation — just reading
        });

        if (!capturedConv) {
            console.error(`📡 Copy: conversation ${conversationId} not found in state`);
            return;
        }

        // Create a duplicate with a new ID for the target project
        const newId = uuidv4();
        const copiedConversation: Conversation = {
            ...capturedConv,
            id: newId,
            projectId: targetProjectId,
            isGlobal: false,
            folderId: null, // Don't carry folder assignment across projects
            _version: Date.now(),
            lastAccessedAt: Date.now(),
        };

        // Persist the copy to IndexedDB (it belongs to another project so
        // it won't appear in the current filtered list, but needs to be in IDB
        // so it shows up when the user switches to the target project).
        await db.saveConversation(copiedConversation);
        console.log(`📡 Copy: saved copy ${newId.substring(0, 8)} to IndexedDB`);

        // Push the copy to the target project on the server
        if (targetProjectId) {
            try {
                const serverChat = syncApi.conversationToServerChat(copiedConversation, targetProjectId);
                await syncApi.bulkSync(targetProjectId, [serverChat]);
                console.log(`📡 Copy: pushed conversation copy to project ${targetProjectId}`);
            } catch (e) {
                console.warn('📡 Copy: server sync failed (non-fatal):', e);
            }
        }
    }, [currentProject?.id]);

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
                    }).catch(() => { });
                }
                folderSyncApi.deleteServerFolder(sourceProjectId, folderId).catch(() => { });
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
        runningTaskConversations,
        addRunningTaskConversation,
        removeRunningTaskConversation,
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
        copyConversationToProject,
        moveFolderToProject,
        toggleFolderGlobal,
        forkConversation,
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
        forkConversation,
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
                    copyConversationToProject={copyConversationToProject}
                    moveFolderToProject={moveFolderToProject}
                    forkConversation={forkConversation}
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
                        startNewEphemeralChat={startNewEphemeralChat}
                        promoteEphemeralToRetained={promoteEphemeralToRetained}
                        editingMessageIndex={editingMessageIndex}
                        setEditingMessageIndex={setEditingMessageIndex}
                        isStreaming={isStreaming}
                        setIsStreaming={setIsStreaming}
                        streamingConversations={streamingConversations}
                        runningTaskConversations={runningTaskConversations}
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
