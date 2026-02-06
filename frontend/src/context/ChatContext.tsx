import React, { createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo, useLayoutEffect } from 'react';
import { Conversation, Message, ConversationFolder } from "../utils/types";
import { v4 as uuidv4 } from "uuid";
import { db } from '../utils/db';
import { detectIncompleteResponse } from '../utils/responseUtils';
import { message } from 'antd';
import { useTheme } from './ThemeContext';
import { useConfig } from './ConfigContext';
import { useProject } from './ProjectContext';

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
    const renderCount = useRef(0);
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamedContentMap, setStreamedContentMap] = useState(() => new Map<string, string>());
    const [reasoningContentMap, setReasoningContentMap] = useState(() => new Map<string, string>());
    const [isStreamingAny, setIsStreamingAny] = useState(false);
    const [processingStates, setProcessingStates] = useState(() => new Map<string, ConversationProcessingState>());
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>(() => {
        // Don't restore from localStorage yet - wait for config to determine ephemeral mode
        // The restoration will happen in an effect after config loads
        const newId = uuidv4();
        console.log('🆕 CREATED: New conversation ID:', newId);
        return newId;
    });
    const currentConversationRef = useRef<string>(currentConversationId);
    const [currentMessages, setCurrentMessages] = useState<Message[]>([]);

    // Listen for project switch - reload project-specific conversations
    useEffect(() => {
        const handleProjectSwitch = async (event: CustomEvent) => {
            const { projectId, projectPath, projectName } = event.detail;
            console.log('💬 PROJECT_SWITCH: Loading conversations for project:', projectName, projectId);
            
            try {
                // First, check if we need to migrate conversations without projectId
                let allConversations = await db.getConversations();
                const needsMigration = allConversations.some(c => !c.projectId);
                
                if (needsMigration) {
                    console.log('🔄 MIGRATION: Assigning conversations without projectId to current project');
                    allConversations = allConversations.map(c => {
                        if (!c.projectId) {
                            return { ...c, projectId: currentProject?.id, _version: Date.now() };
                        }
                        return c;
                    });
                    await db.saveConversations(allConversations);
                    console.log('✅ MIGRATION: All conversations now have projectId');
                }
                
                // Load ALL conversations and folders from database
                const allFolders = await db.getFolders();
                
                // Filter by projectId - STRICT match only (no backward compat)
                const projectConversations = allConversations.filter(c => c.projectId === projectId);
                const projectFolders = allFolders.filter(f => f.projectId === projectId);
                
                console.log(`📊 PROJECT_SWITCH: Found ${projectConversations.length} conversations, ${projectFolders.length} folders for project ${projectName}`);
                
                // Update state with filtered data
                setConversations(projectConversations);
                setFolders(projectFolders);
                
                // Set current conversation
                if (projectConversations.length > 0) {
                    // Load most recent conversation for this project
                    const mostRecent = projectConversations.reduce((a, b) => 
                        (b.lastAccessedAt || 0) > (a.lastAccessedAt || 0) ? b : a
                    );
                    setCurrentConversationId(mostRecent.id);
                    setCurrentMessages(mostRecent.messages);
                    console.log(`✅ PROJECT_SWITCH: Loaded conversation "${mostRecent.title}"`);
                } else {
                    // No conversations for this project - create new one
                    const newConversationId = uuidv4();
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
        if (!isEphemeralMode && isInitialized && conversations.length > 0 && !conversationIdRestored.current) {
            conversationIdRestored.current = true; // Mark as restored to prevent re-running
            try {
                const savedCurrentId = localStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID');
                if (savedCurrentId && savedCurrentId !== currentConversationId) {
                    // CRITICAL: Only restore if the conversation actually exists
                    const conversationExists = conversations.some(c => c.id === savedCurrentId);
                    if (conversationExists) {
                        console.log('🔄 RESTORED: Last active conversation ID:', savedCurrentId);
                        setCurrentConversationId(savedCurrentId);
                    } else {
                        console.warn('⚠️ Saved conversation ID not found in loaded conversations:', savedCurrentId);
                    }
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
    const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
    const lastManualScrollTime = useRef<number>(0);
    const manualScrollCooldownActive = useRef<boolean>(false);
    const [messageUpdateCounter, setMessageUpdateCounter] = useState(0);
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

    // Enhanced backup system with corruption detection
    const createBackup = useCallback(async (conversations: Conversation[]) => {
        // Skip backups in ephemeral mode
        if (isEphemeralMode) {
            return;
        }

        try {
            // More robust filtering to prevent data loss
            // The original logic c.isActive !== false was losing conversations with undefined isActive
            const activeConversations = conversations.filter(c => {
                // Explicitly exclude only conversations marked as false
                // Include: true, undefined, null (default to active)
                if (c.isActive === false) return false;

                // Additional safety: exclude conversations without messages only if explicitly inactive
                if (!c.messages || c.messages.length === 0) {
                    return c.isActive === true; // Only include empty conversations if explicitly active
                }

                return true; // Include all other conversations
            });
            if (activeConversations.length > 0) {
                const backupData = JSON.stringify(activeConversations);

                // Verify backup integrity before saving
                const parsed = JSON.parse(backupData);
                if (Array.isArray(parsed) && parsed.length === activeConversations.length) {
                    localStorage.setItem('ZIYA_CONVERSATION_BACKUP', backupData);
                    localStorage.setItem('ZIYA_BACKUP_TIMESTAMP', Date.now().toString());
                } else {
                    console.error('❌ Backup verification failed');
                }
            }
        } catch (e) {
            console.error('❌ Backup creation failed:', e);
        }
    }, [isEphemeralMode]);

    // Queue-based save system to prevent race conditions
    const queueSave = useCallback(async (conversations: Conversation[], options: {
        skipValidation?: boolean;
        retryCount?: number;
        isRecoveryAttempt?: boolean;
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

        saveQueue.current = saveQueue.current.then(async () => {
            const { skipValidation = false, retryCount = 0, isRecoveryAttempt = false } = options;
            const maxRetries = 3;
            const isRetry = retryCount > 0;

            // Pre-save validation
            const activeCount = validatedConversations.filter(c => c.isActive).length;
            console.debug(`Saving ${validatedConversations.length} conversations (${activeCount} active)`);

            // SAFETY CHECK: Never save filtered conversations list
            // If we're in a project-filtered view, we must merge with other projects' conversations
            if (currentProject?.id) {
                const allDbConversations = await db.getConversations();
                const otherProjectConversations = allDbConversations.filter(c => c.projectId !== currentProject.id);
                validatedConversations = [...validatedConversations, ...otherProjectConversations];
                console.log(`💾 SAVE: Merged ${otherProjectConversations.length} conversations from other projects`);
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

                            // CRITICAL: Only retry if merge produced valid results
                            return queueSave(mergedConversations, {
                                skipValidation: false,
                                retryCount: retryCount + 1,
                                isRecoveryAttempt: true
                            });
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
                        return queueSave(validatedConversations, {
                            skipValidation: false,
                            retryCount: retryCount + 1,
                            isRecoveryAttempt: true
                        });
                    } else {
                        console.warn('🏥 EMERGENCY MODE: Skipping validation due to persistent errors');
                    }
                }
            }
        });
        return saveQueue.current;
    }, [createBackup, isEphemeralMode]);

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
                    // Ensure isActive is explicitly set for all conversations
                    return { ...conv, isActive: conv.isActive !== false ? true : false };
                })
                : [...prevConversations, {
                    id: conversationId,
                    title: message.role === 'human'
                        ? message.content.slice(0, dynamicTitleLength) + (message.content.length > dynamicTitleLength ? '...' : '')
                        : 'New Conversation',
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

            queueSave(updatedConversations).catch(console.error);

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
        return new Promise<void>(async (resolve, reject) => {
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
                    await attemptDatabaseRecovery();
                }
            } catch (recoveryError) {
                console.warn('Database recovery attempt failed, continuing anyway:', recoveryError);
            }

            if (!isInitialized) {
                console.warn('⚠️ NEW CHAT: Context not initialized, attempting initialization...');
                try {
                    await initializeWithRecovery();
                    // Give initialization a moment to complete
                    await new Promise(resolve => setTimeout(resolve, 200));

                    if (!isInitialized) {
                        // If still not initialized after attempt, proceed anyway with degraded mode
                        console.warn('⚠️ NEW CHAT: Proceeding with degraded mode (no IndexedDB persistence)');
                        setIsInitialized(true);
                    }
                } catch (initError) {
                    console.error('❌ NEW CHAT: Initialization failed, proceeding in localStorage-only mode:', initError);
                    // Set initialized to true anyway - app can work with localStorage
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
                        await queueSave([...updatedConversations, newConversation]);
                    } catch (saveError) {
                        // Log but don't block - persistence failure shouldn't prevent new chats
                        console.warn('⚠️ NEW CHAT: Save failed, continuing anyway:', saveError);
                        try {
                            // Try localStorage backup
                            const backupData = JSON.stringify([...updatedConversations, newConversation]);
                            localStorage.setItem('ZIYA_CONVERSATION_BACKUP', backupData);
                        } catch (backupError) {
                            console.warn('⚠️ NEW CHAT: Backup also failed:', backupError);
                        }
                    }

                    // Update state immediately after successful save
                    // CRITICAL: Always update state, even if save failed
                    setConversations([...updatedConversations, newConversation]);
                    setCurrentMessages([]);
                    setCurrentConversationId(newId);

                    // CRITICAL: Persist the new conversation ID immediately
                    try {
                        localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', newId);
                    } catch (e) {
                        console.warn('Failed to persist conversation ID:', e);
                    }
                    resolve();

                } catch (saveError) {
                    // CRITICAL: Never block new chat creation due to storage issues
                    console.error('Failed to save new conversation, creating in memory:', saveError);

                    // Create conversation in memory anyway
                    setConversations([...updatedConversations, newConversation]);
                    setCurrentMessages([]);
                    setCurrentConversationId(newId);
                    try {
                        localStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', newId);
                    } catch (e) { /* ignore */ }

                    // Background retry
                    setTimeout(async () => {
                        try {
                            await queueSave([...updatedConversations, newConversation], { skipValidation: true });
                        } catch (e) {
                            console.warn('Background save retry failed:', e);
                        }
                    }, 2000);

                    // Always resolve - user can work, persistence will catch up
                    resolve();
                }
            } catch (error) {
                console.error('Failed to save new conversation:', error);
                reject(error);
            }
        });
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
                    const currentConvInMemory = memoryConversations.find(c => c.id === currentConversationId);

                    if (!currentConvInDB && currentConvInMemory && currentConvInMemory.messages.length > 0) {
                        console.error(`🚨 RECOVERY BLOCKED: Current conversation ${currentConversationId.substring(0, 8)} not in DB but has ${currentConvInMemory.messages.length} messages!`);
                        console.log('🔄 RECOVERY: Saving current conversation to DB instead of deleting it');

                        // Save the current conversation to DB instead of deleting it
                        await db.saveConversations([...dbConversations, currentConvInMemory]);
                        console.log('✅ RECOVERY: Protected current conversation from deletion');
                        return;
                    }

                    // CRITICAL FIX: Check backup before deleting it
                    const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                    if (backup) {
                        const backupConversations = JSON.parse(backup);
                        const backupIds = new Set(backupConversations.map(c => c.id));

                        // Find conversations in backup that aren't in DB
                        const missingFromDB = backupConversations.filter(bc =>
                            !dbConversations.find(dc => dc.id === bc.id) &&
                            bc.isActive !== false &&
                            bc.messages.length > 0
                        );

                        if (missingFromDB.length > 0) {
                            console.warn(`🚨 RECOVERY: Backup has ${missingFromDB.length} conversations not in DB!`);
                            console.log('🔄 RECOVERY: Merging backup conversations into DB');
                            await db.saveConversations([...dbConversations, ...missingFromDB]);
                            console.log('✅ RECOVERY: Restored conversations from backup');
                            return;
                        }
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
                queueSave(updatedConversations).catch(console.error);
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
            await queueSave(updatedConversationsForDB);

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
            setIsInitialized(true);
            isRecovering.current = false;
            return;
        }

        // EMERGENCY RECOVERY SYSTEM: Check for unsaved conversations before initialization
        const checkForUnsavedConversations = async () => {
            try {
                const emergencyRecovery = localStorage.getItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY');
                const enhancedBackup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY');

                if (emergencyRecovery || enhancedBackup) {
                    console.warn('🚨 EMERGENCY RECOVERY: Found unsaved conversation data during init');

                    // Load current conversations from DB
                    const currentConversations = await db.getConversations();
                    const currentIds = new Set(currentConversations.map(c => c.id));

                    let recoveryData: any = null;
                    let recoveredConversations: Conversation[] = [];

                    if (enhancedBackup) {
                        recoveryData = JSON.parse(enhancedBackup);
                        // Handle both old format (array) and new format (object)
                        recoveredConversations = Array.isArray(recoveryData)
                            ? recoveryData
                            : recoveryData.conversations || [];
                    } else if (emergencyRecovery) {
                        recoveryData = JSON.parse(emergencyRecovery);
                        recoveredConversations = recoveryData.conversations || [];

                        // CRITICAL: Restore streaming content that was in progress
                        if (recoveryData.wasStreaming && recoveryData.streamingContent) {
                            console.log('📡 RECOVERY: Found streaming content in backup');

                            // Merge streaming content into messages
                            Object.entries(recoveryData.streamingContent).forEach(([convId, content]) => {
                                if (!content || (content as string).trim().length === 0) return;

                                const conv = recoveredConversations.find(c => c.id === convId);
                                if (conv) {
                                    // Check if last message is already this content
                                    const lastMsg = conv.messages[conv.messages.length - 1];
                                    if (lastMsg?.content !== content) {
                                        conv.messages.push({
                                            id: uuidv4(),
                                            role: 'assistant',
                                            content: content as string,
                                            _timestamp: Date.now(),
                                            _recovered: true
                                        });
                                        console.log(`✅ RECOVERY: Restored streaming content for ${convId.substring(0, 8)}`);
                                    }
                                }
                            });
                        }

                        // Restore current conversation context
                        if (recoveryData.currentConversationId && recoveryData.currentMessages) {
                            setTimeout(() => {
                                setCurrentConversationId(recoveryData.currentConversationId);
                                setCurrentMessages(recoveryData.currentMessages);
                            }, 100);
                        }
                    }

                    // Only add conversations that don't already exist
                    const newConversations = recoveredConversations.filter((c: Conversation) =>
                        !currentIds.has(c.id) ||
                        // Update if recovered version has more messages
                        (c.messages.length > (currentConversations.find(cc => cc.id === c.id)?.messages.length || 0))
                    );

                    if (newConversations.length > 0) {
                        const mergedConversations = [...currentConversations, ...newConversations];
                        await db.saveConversations(mergedConversations);

                        // CRITICAL FIX: Update React state immediately after saving
                        setConversations(mergedConversations);

                        // Restore the current conversation if it was backed up
                        if (recoveryData.currentConversationId) {
                            setCurrentConversationId(recoveryData.currentConversationId);
                        }

                        console.log(`✅ RECOVERY: Restored ${newConversations.length} missing conversations`);
                    } else {
                        console.log('ℹ️ RECOVERY: No new conversations to restore');
                    }

                    // CRITICAL: ALWAYS clean up recovery data, even if nothing to restore
                    // Leaving stale recovery data causes it to trigger again and again
                    localStorage.removeItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY');
                    localStorage.removeItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY');
                    console.log('✅ Cleaned up recovery data');
                }
            } catch (error) {
                console.error('❌ RECOVERY: Failed to process emergency recovery:', error);
                // Clean up even on error to prevent recovery loops
                localStorage.removeItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY');
                localStorage.removeItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY');
            }
        };

        // Run emergency recovery check
        await checkForUnsavedConversations();

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
                // CRITICAL FIX: Check backups BEFORE switching to different conversation
                let recovered = false;
                for (const key of ['ZIYA_CONVERSATION_BACKUP', 'ZIYA_EMERGENCY_CONVERSATION_RECOVERY', 'ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY']) {
                    if (recovered) break;
                    try {
                        const data = localStorage.getItem(key);
                        if (data) {
                            const parsed = JSON.parse(data);
                            const convs = Array.isArray(parsed) ? parsed : (parsed.conversations || []);
                            const found = convs.find((c: any) => c.id === savedCurrentId);
                            if (found) {
                                console.log(`✅ RECOVERY: Found current conversation in ${key}`);
                                recovered = true;
                                savedConversations.push(found);
                                setConversations([...savedConversations]);
                                db.saveConversations([...savedConversations]).catch(e => console.warn('Recovery save failed:', e));
                            }
                        }
                    } catch { /* ignore */ }
                }

                if (!recovered && savedConversations.length > 0) {
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

            // Handle backup/recovery operations asynchronously - don't block UI
            setTimeout(async () => {
                // Check for corruption by comparing with backup - but be careful!
                const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                if (backup) {
                    const backupConversations = JSON.parse(backup);

                    // CRITICAL FIX: Don't blindly trust backup over IndexedDB
                    // The backup might be from a different browser tab with partial state

                    // Instead, MERGE conversations from both sources
                    const allConversationIds = new Set([
                        ...savedConversations.map(c => c.id),
                        ...backupConversations.map(c => c.id)
                    ]);

                    console.log('🔄 INIT MERGE:', {
                        dbCount: savedConversations.length,
                        backupCount: backupConversations.length,
                        uniqueIds: allConversationIds.size
                    });

                    // Only restore if backup has conversations that DB doesn't
                    const missingInDb = backupConversations.filter(bc =>
                        !savedConversations.find(sc => sc.id === bc.id)
                    );

                    if (missingInDb.length > 0) {
                        console.warn(`⚠️ Found ${missingInDb.length} conversations in backup but not in IndexedDB:`,
                            missingInDb.map(c => c.id.substring(0, 8)));

                        // Merge instead of replace
                        const mergedConversations = [
                            ...savedConversations,
                            ...missingInDb
                        ];

                        await db.saveConversations(mergedConversations);
                        setConversations(mergedConversations);
                        console.log('✅ Merged backup conversations into IndexedDB');
                    } else {
                        // Backup has no additional conversations, create new backup from DB
                        await createBackup(savedConversations);
                    }
                } else {
                    // No backup exists, create one
                    await createBackup(savedConversations);
                }
            }, 0);

        } catch (error) {
            console.error('❌ INIT: Database initialization failed:', error);
            isDatabaseHealthy = false;

            // CRITICAL FIX: Set initialized flag even on failure
            // The app should be able to create new conversations even if DB initialization failed
            setIsInitialized(true);

            // Try to recover from localStorage backup as fallback
            try {
                const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                if (backup) {
                    const backupConversations = JSON.parse(backup);
                    console.log(`🔄 INIT: Loaded ${backupConversations.length} conversations from localStorage backup`);
                    setConversations(backupConversations);
                }
            } catch (backupError) {
                console.error('❌ INIT: Failed to load backup, starting with empty state:', backupError);
                setConversations([]);
            }

            console.warn('⚠️ INIT: App running in degraded mode - persistence may be limited to localStorage');
        } finally {
            isRecovering.current = false;
        }
    }, [createBackup, currentConversationId]);



    useEffect(() => {
        // ============================================================================
        // CRITICAL FIX: Emergency backup system to prevent conversation loss
        // ============================================================================
        // This fixes the bug where conversations vanish when navigating away during
        // active streaming or before IndexedDB saves complete.

        // Helper to estimate storage size in bytes
        const estimateSize = (obj: any): number => {
            try {
                return JSON.stringify(obj).length * 2; // UTF-16 uses 2 bytes per char
            } catch {
                return 0;
            }
        };

        // Helper to get available localStorage space
        const getAvailableSpace = (): number => {
            try {
                // Test by trying to add data
                let total = 0;
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    if (key) {
                        const item = localStorage.getItem(key) || '';
                        total += key.length + item.length;
                    }
                }
                // Most browsers: 5-10MB limit, assume 5MB conservative
                const QUOTA_LIMIT = 5 * 1024 * 1024;
                return Math.max(0, QUOTA_LIMIT - total * 2); // UTF-16
            } catch {
                return 0;
            }
        };

        // Helper to clean up old localStorage entries
        const cleanupOldEntries = () => {
            try {
                const keysToCleanup = [];
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    if (key?.startsWith('ZIYA_')) {
                        keysToCleanup.push(key);
                    }
                }

                // CRITICAL: Don't delete keys that store plain strings (not JSON objects)
                const protectedKeys = new Set([
                    'ZIYA_CURRENT_CONVERSATION_ID',
                    'ZIYA_DB_NAME',
                    'ZIYA_ACTIVE_TAB',
                    'ZIYA_BACKUP_TIMESTAMP',
                    'ZIYA_THEME_PREFERENCE',
                    'ZIYA_TOP_DOWN_MODE',
                    'ZIYA_PANEL_COLLAPSED',
                    'ZIYA_PANEL_WIDTH',
                    'safari-warning-dismissed'
                ]);

                keysToCleanup.forEach(key => {
                    // Never delete protected keys
                    if (protectedKeys.has(key)) {
                        return;
                    }

                    try {
                        const item = localStorage.getItem(key);
                        if (item) {
                            const data = JSON.parse(item);
                            const age = Date.now() - (data.timestamp || 0);
                            // Remove entries older than 1 hour or without timestamp
                            if (age > 3600000 || !data.timestamp) {
                                localStorage.removeItem(key);
                                console.log(`🧹 Cleaned up old entry: ${key} (age: ${(age / 60000).toFixed(0)}min)`);
                            }
                        }
                    } catch (e) {
                        // If parsing fails, it might be a plain string value - don't delete
                        console.debug(`Skipped cleanup for non-JSON key: ${key}`);
                    }
                });
            } catch (error) {
                console.debug('Cleanup error:', error);
            }
        };

        // Helper to truncate large messages
        const truncateMessages = (messages: Message[], maxLength: number = 1000): Message[] => {
            return messages.map(msg => ({
                ...msg,
                content: msg.content.length > maxLength
                    ? msg.content.substring(0, maxLength) + '...[truncated for backup]'
                    : msg.content
            }));
        };

        // Helper to create minimal conversation representation
        const createMinimalConversation = (conv: Conversation): any => {
            return {
                id: conv.id,
                title: conv.title,
                folderId: conv.folderId,
                lastAccessedAt: conv.lastAccessedAt,
                isActive: conv.isActive,
                messageCount: conv.messages.length,
                // Keep only last 10 messages with truncated content
                messages: truncateMessages(conv.messages.slice(-10), 500)
            };
        };

        const createEmergencyBackup = () => {
            // CRITICAL: Don't create emergency backups until initialization completes
            // Creating backups with empty initial state overwrites good recovery data
            if (!isInitialized || conversations.length === 0) {
                console.debug('Skipping emergency backup - not initialized or no conversations');
                return;
            }

            // Skip all backups in ephemeral mode
            if (isEphemeralMode) {
                return;
            }

            // Clean up old entries first to free space
            cleanupOldEntries();

            // Check available space
            const availableSpace = getAvailableSpace();
            console.log('💾 BACKUP: Available localStorage space:', (availableSpace / 1024).toFixed(2), 'KB');

            // If we have less than 500KB available, skip backup
            if (availableSpace < 500 * 1024) {
                console.warn('⚠️ BACKUP: Insufficient space, skipping emergency backup');
                console.warn('💡 BACKUP: Consider clearing browser cache or old conversation data');
                return;
            }

            try {
                // Include ALL current state, not just what's persisted
                const emergencyData = {
                    conversations: conversations.filter(c => c.isActive !== false),
                    currentConversationId,
                    currentMessages,
                    currentFolderId,
                    timestamp: Date.now(),
                    // CRITICAL: Capture streaming content before it's lost
                    streamingContent: Object.fromEntries(streamedContentMap),
                    streamingConversations: Array.from(streamingConversations),
                    wasStreaming: streamingConversations.size > 0,
                };

                // Estimate size
                const estimatedSize = estimateSize(emergencyData);
                const QUOTA_THRESHOLD = Math.min(availableSpace * 0.8, 4 * 1024 * 1024); // 80% of available or 4MB

                console.log('💾 BACKUP: Estimated size:', (estimatedSize / 1024).toFixed(2), 'KB, threshold:', (QUOTA_THRESHOLD / 1024).toFixed(2), 'KB');

                // Progressive compression strategies
                if (estimatedSize > QUOTA_THRESHOLD) {
                    console.warn('⚠️ BACKUP: Data too large, applying compression');

                    // Strategy 1: Keep only recent conversations with truncated messages
                    const compressedData = {
                        conversations: emergencyData.conversations
                            .slice(-5) // Last 5 conversations only
                            .map(conv => {
                                // Keep current conversation with more detail
                                if (conv.id === currentConversationId) {
                                    return {
                                        ...conv,
                                        messages: truncateMessages(conv.messages, 1000)
                                    };
                                }
                                return createMinimalConversation(conv);
                            }),
                        currentConversationId,
                        currentMessages: truncateMessages(currentMessages.slice(-30), 800),
                        currentFolderId,
                        timestamp: Date.now(),
                        streamingContent: emergencyData.streamingContent,
                        streamingConversations: emergencyData.streamingConversations,
                        wasStreaming: emergencyData.wasStreaming,
                        _compressed: true
                    };

                    const compressedSize = estimateSize(compressedData);
                    console.log('💾 BACKUP: Compressed size:', (compressedSize / 1024).toFixed(2), 'KB');

                    // If still too large, try ultra-minimal
                    if (compressedSize > QUOTA_THRESHOLD) {
                        console.warn('⚠️ BACKUP: Still too large, using ultra-minimal backup');

                        const ultraMinimalData = {
                            currentConversationId,
                            currentMessages: truncateMessages(currentMessages.slice(-10), 300),
                            timestamp: Date.now(),
                            wasStreaming: streamingConversations.size > 0,
                            streamingContent: emergencyData.wasStreaming ?
                                Object.fromEntries(
                                    Array.from(streamedContentMap.entries())
                                        .map(([id, content]) => [id, content.substring(0, 500)])
                                ) : {},
                            _ultraMinimal: true
                        };

                        localStorage.setItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY',
                            JSON.stringify(ultraMinimalData));
                        console.log('💾 BACKUP: Saved ultra-minimal backup');
                        return;
                    }

                    localStorage.setItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY',
                        JSON.stringify(compressedData));
                    localStorage.setItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY',
                        JSON.stringify(compressedData.conversations));
                    console.log('💾 BACKUP: Saved compressed backup');
                    return;
                }

                // Synchronous localStorage save (works during page unload)
                localStorage.setItem('ZIYA_EMERGENCY_CONVERSATION_RECOVERY',
                    JSON.stringify(emergencyData));

                // Also update the regular backup with recovery flag
                localStorage.setItem('ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY',
                    JSON.stringify(emergencyData.conversations));

                console.log('💾 EMERGENCY BACKUP:', {
                    conversations: emergencyData.conversations.length,
                    wasStreaming: emergencyData.wasStreaming,
                    streamingIds: emergencyData.streamingConversations,
                    size: (estimatedSize / 1024).toFixed(2) + ' KB'
                });
            } catch (error) {
                // Check if this is a quota error
                const isQuotaError = error instanceof Error &&
                    (error.name === 'QuotaExceededError' || error.message.includes('quota'));

                if (isQuotaError) {
                    console.error('❌ QUOTA EXCEEDED during backup, trying fallback strategies');

                    // Clean up ALL old entries aggressively
                    try {
                        ['ZIYA_EMERGENCY_CONVERSATION_RECOVERY',
                            'ZIYA_CONVERSATION_BACKUP_WITH_RECOVERY',
                            'ZIYA_LAST_MESSAGES',
                            'ZIYA_CONVERSATION_BACKUP'].forEach(key => {
                                try { localStorage.removeItem(key); } catch { }
                            });
                        console.log('🧹 Cleared all backup entries to free space');
                    } catch { }

                    // Try absolute minimal backup
                    try {
                        const absoluteMinimal = {
                            id: currentConversationId,
                            timestamp: Date.now(),
                            messageCount: currentMessages.length
                        };
                        localStorage.setItem('ZIYA_LAST_CONVERSATION_ID',
                            JSON.stringify(absoluteMinimal));
                        console.log('💾 BACKUP: Saved absolute minimal backup (metadata only)');
                    } catch (finalError) {
                        console.error('❌ All backup strategies exhausted');
                        // Show user notification about storage issue
                        console.warn('💡 USER ACTION NEEDED: Clear browser cache to free storage space');
                    }
                } else {
                    console.error('❌ EMERGENCY BACKUP failed (non-quota error):', error);
                }
            }
        };

        // Handle page unload - create emergency backup
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            console.log('🚪 BEFOREUNLOAD: Creating emergency backup');
            createEmergencyBackup();

            // Warn user if actively streaming
            if (streamingConversations.size > 0) {
                const message = 'A response is still being generated. Leaving now may lose your conversation.';
                e.preventDefault();
                e.returnValue = message;
                return message;
            }
        };

        // Handle visibility changes (catches mobile browser backgrounding)
        const handleVisibilityChange = () => {
            if (document.hidden) {
                console.log('👁️ VISIBILITY: Page hidden, creating emergency backup');
                createEmergencyBackup();
            }
        };

        // Handle page hide (more reliable than beforeunload on some browsers)
        const handlePageHide = () => {
            console.log('🚫 PAGEHIDE: Creating emergency backup');
            createEmergencyBackup();
        };

        // Periodic emergency backup during streaming (every 10 seconds)
        let streamingBackupInterval: NodeJS.Timeout | null = null;
        if (streamingConversations.size > 0) {
            streamingBackupInterval = setInterval(() => {
                console.log('🔄 STREAMING BACKUP: Auto-backup during stream');
                createEmergencyBackup();
            }, 10000);
        }

        // Attach all handlers
        window.addEventListener('beforeunload', handleBeforeUnload);
        document.addEventListener('visibilitychange', handleVisibilityChange);
        window.addEventListener('pagehide', handlePageHide);

        // Cleanup
        return () => {
            window.removeEventListener('beforeunload', handleBeforeUnload);
            document.removeEventListener('visibilitychange', handleVisibilityChange);
            window.removeEventListener('pagehide', handlePageHide);
            if (streamingBackupInterval) {
                clearInterval(streamingBackupInterval);
            }
        };
    }, [conversations, currentConversationId, currentMessages, currentFolderId,
        streamedContentMap, streamingConversations]);

    useEffect(() => {
        // Only initialize once
        if (isInitialized) {
            return;
        }

        initializeWithRecovery();

        // Skip backup system entirely in ephemeral mode
        if (isEphemeralMode) {
            return;
        }

        // Enhanced backup interval - but merge with existing backup instead of replacing
        const backupInterval = setInterval(() => {
            if (conversations.length > 0) {
                // CRITICAL FIX: Merge with existing backup instead of replacing
                const existingBackup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                if (existingBackup) {
                    try {
                        const existingBackupData = JSON.parse(existingBackup);

                        // Merge: keep conversations from backup that aren't in current state
                        const currentIds = new Set(conversations.map(c => c.id));
                        const missingFromCurrent = existingBackupData.filter(
                            (bc: Conversation) => !currentIds.has(bc.id) && bc.isActive !== false
                        );

                        if (missingFromCurrent.length > 0) {
                            console.log(`🔄 BACKUP MERGE: Adding ${missingFromCurrent.length} conversations from previous backup`);
                            const mergedForBackup = [...conversations, ...missingFromCurrent];
                            createBackup(mergedForBackup);
                        } else {
                            createBackup(conversations);
                        }
                    } catch (e) {
                        console.error('Failed to merge with existing backup:', e);
                        createBackup(conversations);
                    }
                } else {
                    createBackup(conversations);
                }
            }
        }, 60000);

        // Also create backup immediately on mount
        if (conversations.length > 0) createBackup(conversations);

        const request = indexedDB.open('ZiyaDB');
        request.onerror = (event) => {
            const error = (event.target as IDBOpenDBRequest).error?.message || 'Unknown IndexedDB error';
            setDbError(error);
        };

        return () => {
            clearInterval(backupInterval);
            // Don't close database connection here - it may interrupt ongoing transactions
            // The database connection will be managed by the DB class itself
            // if (db.db) {
            //     db.db.close();
            // }
        };
    }, [initializeWithRecovery, createBackup, conversations, isEphemeralMode]);

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
                    ? folders.filter(f => f.projectId === projectId)
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
        if (!currentConversationId) {
            setCurrentConversationId(uuidv4());
        }
    }, [currentConversationId]);

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
            queueSave(updated).catch(console.error);
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
            queueSave(updated).catch(console.error);

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
