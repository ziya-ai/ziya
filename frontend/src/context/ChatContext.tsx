import React, { createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo, memo, useLayoutEffect } from 'react';
import { Conversation, Message, ConversationFolder } from "../utils/types";
import { v4 as uuidv4 } from "uuid";
import { db } from '../utils/db';
import { debounce } from '../utils/debounce';
import { Modal, message } from 'antd';
import { performEmergencyRecovery } from '../utils/emergencyRecovery';
interface ChatContext {
    streamedContentMap: Map<string, string>;
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>;
    isStreaming: boolean;
    processingState: string;
    setProcessingState: Dispatch<SetStateAction<string>>;
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
    isTopToBottom: boolean;
    dbError: string | null;
    setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
    scrollToBottom: () => void;
    userHasScrolled: boolean;
    setUserHasScrolled: Dispatch<SetStateAction<boolean>>;
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
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

export function ChatProvider({ children }: ChatProviderProps) {
    const renderStart = useRef(performance.now());
    const renderCount = useRef(0);
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamedContentMap, setStreamedContentMap] = useState(() => new Map<string, string>());
    const [isStreamingAny, setIsStreamingAny] = useState(false);
    const [processingState, setProcessingState] = useState<string>('idle');
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [currentResponse, setCurrentResponse] = useState<Message | null>(null);
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>(uuidv4());
    const currentConversationRef = useRef<string>(currentConversationId);
    const [currentMessages, setCurrentMessages] = useState<Message[]>([]);
    const [streamingConversations, setStreamingConversations] = useState<Set<string>>(new Set());
    const [isTopToBottom, setIsTopToBottom] = useState(false);
    const [isInitialized, setIsInitialized] = useState(false);
    const lastSavedState = useRef<string>('');
    const [userHasScrolled, setUserHasScrolled] = useState(false);
    const [folders, setFolders] = useState<ConversationFolder[]>([]);
    const [dbError, setDbError] = useState<string | null>(null);
    const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
    const folderRef = useRef<string | null>(null);
    const [folderFileSelections, setFolderFileSelections] = useState<Map<string, string[]>>(new Map());
    const [folderPanelWidth, setFolderPanelWidth] = useState<number>(300); // Default width
    const processedModelChanges = useRef<Set<string>>(new Set());
    const contentRef = useRef<HTMLDivElement>(null);
    const saveQueue = useRef<Promise<void>>(Promise.resolve());
    const isRecovering = useRef<boolean>(false);
    const pendingSave = useRef<NodeJS.Timeout | null>(null);
    const messageUpdateCount = useRef(0);
    const conversationsRef = useRef(conversations);
    const streamingConversationsRef = useRef(streamingConversations);
    const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);

    // Monitor ChatProvider render performance
    useLayoutEffect(() => {
        renderCount.current++;
        const renderTime = performance.now() - renderStart.current;
        if (renderTime > 10 || renderCount.current % 20 === 0) {
            console.log(`📊 ChatProvider render #${renderCount.current}: ${renderTime.toFixed(2)}ms`);
        }
        renderStart.current = performance.now();
    });

    // Modified scrollToBottom function to respect user scroll
    const scrollToBottom = () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer && isTopToBottom && !userHasScrolled && isStreamingAny) {
            // Only scroll during active streaming to prevent unexpected jumps
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }
    };

    useEffect(() => {
        conversationsRef.current = conversations;
        streamingConversationsRef.current = streamingConversations;
    }, [conversations, streamingConversations]);

    // Add a resize observer effect to monitor panel width changes
    useEffect(() => {
        const resizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                const folderPanel = document.querySelector('.folder-tree-panel');
                if (folderPanel) {
                    setFolderPanelWidth(entry.contentRect.width);
                }
            }
        });

        const folderPanel = document.querySelector('.folder-tree-panel');
        if (folderPanel) {
            resizeObserver.observe(folderPanel);
        }

        return () => {
            resizeObserver.disconnect();
        };
    }, []);

    // Listen for panel width changes
    useEffect(() => {
        const handlePanelResize = (e: CustomEvent) => {
            if (e.detail && e.detail.width) {
                setFolderPanelWidth(e.detail.width);
            }
        };
        window.addEventListener('folderPanelResize', handlePanelResize as EventListener);
        return () => window.removeEventListener('folderPanelResize', handlePanelResize as EventListener);
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
    }, []);

    const removeStreamingConversation = useCallback((id: string) => {
        console.log('Removing from streaming set:', { id, currentSet: Array.from(streamingConversations) });
        setStreamingConversations(prev => {
            const next = new Set(prev);
            next.delete(id);
            return next;
        });

        setStreamedContentMap(prev => {
            const next = new Map(prev);
            next.delete(id);
            return next;
        });

        // Only set isStreamingAny to false if no conversations are streaming
        setStreamingConversations(prev => {
            setIsStreamingAny(prev.size > 1);
            return prev;
        });
    }, [streamingConversations]);

    const shouldUpdateState = (newState: Conversation[], force: boolean = false) => {

        const newStateStr = JSON.stringify(newState);

        // Check if current conversation exists in new state
        const currentConvExists = newState.some(conv => conv.id === currentConversationRef.current);
        if (!currentConvExists && conversations.some(conv => conv.id === currentConversationRef.current)) {
            console.warn('Preventing update that would remove current conversation');
            return false;
        }

        if (newStateStr === lastSavedState.current) {
            return false;
        }

        lastSavedState.current = newStateStr;
        return true;
    };

    // Enhanced backup system with corruption detection
    const createBackup = useCallback(async (conversations: Conversation[]) => {
        try {
            const activeConversations = conversations.filter(c => c.isActive !== false);
            if (activeConversations.length > 0) {
                const backupData = JSON.stringify(activeConversations);

                // Verify backup integrity before saving
                const parsed = JSON.parse(backupData);
                if (Array.isArray(parsed) && parsed.length === activeConversations.length) {
                    localStorage.setItem('ZIYA_CONVERSATION_BACKUP', backupData);
                    localStorage.setItem('ZIYA_BACKUP_TIMESTAMP', Date.now().toString());
                    console.debug(`✅ Backup created: ${activeConversations.length} conversations`);
                } else {
                    console.error('❌ Backup verification failed');
                }
            }
        } catch (e) {
            console.error('❌ Backup creation failed:', e);
        }
    }, []);

    // Queue-based save system to prevent race conditions
    const queueSave = useCallback(async (conversations: Conversation[]) => {
        saveQueue.current = saveQueue.current.then(async () => {
            await db.saveConversations(conversations);
            await createBackup(conversations);
        });
        return saveQueue.current;
    }, [createBackup]);

    const saveConversationsWithDebounce = useCallback(async (conversations: Conversation[]) => {
        if (pendingSave.current) {
            clearTimeout(pendingSave.current);
        }

        pendingSave.current = setTimeout(async () => {
            try {
                await queueSave(conversations);
            } catch (error) {
                console.error('Failed to save conversations:', error);
                setDbError(error instanceof Error ? error.message : 'Failed to save conversations');
            }
            pendingSave.current = null;
        }, 1000);
    }, [queueSave]);

    const addMessageToConversation = useCallback((message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => {
        const conversationId = targetConversationId || currentConversationId;
        if (!conversationId) return;

        const folderId = currentFolderId;
        // Calculate dynamic title length based on panel width
        // We'll use a ratio of approximately 1 character per 6 pixels of width
        const dynamicTitleLength = Math.max(30, Math.floor(folderPanelWidth / 6));

        console.log('Dynamic title length:', { folderPanelWidth, dynamicTitleLength });

        messageUpdateCount.current += 1;
        setConversations(prevConversations => {
            const existingConversation = prevConversations.find(c => c.id === conversationId);
            const isFirstMessage = existingConversation?.messages.length === 0;
            console.log('Message processing:', {
                messageRole: message.role,
                targetConversationId: conversationId,
                currentConversationId,
                isNonCurrentConversation
            });
            const shouldMarkUnread = message.role === 'assistant' && isNonCurrentConversation;
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
                    return conv;
                })
                : [...prevConversations, {
                    id: conversationId,
                    title: message.role === 'human'
                        ? message.content.slice(0, dynamicTitleLength) + (message.content.length > dynamicTitleLength ? '...' : '')
                        : 'New Conversation',
                    messages: [message],
                    folderId: folderId,
                    lastAccessedAt: Date.now(),
                    isActive: true,
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
    }, [currentConversationId, currentFolderId, folderPanelWidth, conversations]);

    // Add a function to handle model change notifications
    const handleModelChange = useCallback((event: CustomEvent) => {
        // Extract model change details
        const { previousModel, newModel, modelId, previousModelId } = event.detail;

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

        // Skip if we've already processed this exact change
        if (processedModelChanges.current.has(changeKey)) {
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
    }, [currentFolderId]);

    const startNewChat = useCallback((specificFolderId?: string | null) => {
        return new Promise<void>((resolve, reject) => {
            try {
                const newId = uuidv4();

                // Use the provided folder ID if available, otherwise use the current folder ID
                const targetFolderId = specificFolderId !== undefined ? specificFolderId : currentFolderId;

                const newConversation: Conversation = {
                    id: newId,
                    title: 'New Conversation',
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

                queueSave([...updatedConversations, newConversation])
                    .then(async () => {
                        setConversations([...updatedConversations, newConversation]);
                        setCurrentMessages([]);
                        setCurrentConversationId(newId);
                        resolve();
                    })
                    .catch(error => {
                        console.error('Failed to save new conversation:', error);
                        reject(error);
                    });
            } catch (error) {
                console.error('Failed to save new conversation:', error);
                reject(error);
            }
        });
    }, [currentConversationId, currentFolderId, conversations]);

    const loadConversation = useCallback(async (conversationId: string) => {
        setIsLoadingConversation(true);
        try {
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

            // Set the current folder ID based on the conversation's folder
            // This should not block conversation loading
            const conversation = conversations.find(c => c.id === conversationId);
            if (conversation) {
                // Set folder ID asynchronously to not block message loading
                setTimeout(() => {
                    setCurrentFolderId(conversation.folderId ?? null);
                }, 0);
            }
            // Only clear streaming content map for conversations that are no longer streaming
            setStreamedContentMap(prev => {
                const next = new Map(prev);
                // Keep streaming content for active streaming conversations
                return next;
            });

            console.log('Current conversation changed:', {
                from: currentConversationId,
                to: conversationId,
                streamingConversations: Array.from(streamingConversations),
                hasStreamingContent: Array.from(streamedContentMap.keys())
            });
        } finally {
            // Always clear loading state, even if folder operations are pending
            console.log('✅ Conversation loading complete:', conversationId);
            setIsLoadingConversation(false);
        }
        setStreamedContentMap(new Map());
    }, [currentConversationId, conversations, streamingConversations, streamedContentMap]);

    // Folder management functions
    const createFolder = useCallback(async (name: string, parentId?: string | null): Promise<string> => {
        const newFolder: ConversationFolder = {
            id: uuidv4(),
            name,
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

    // frontend/src/context/ChatContext.tsx
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
    }, [currentFolderId, setConversations, setFolders, setCurrentFolderId]); // Removed 'conversations' from deps, relies on fetching fresh from DB

    const moveConversationToFolder = useCallback(async (conversationId: string, folderId: string | null): Promise<void> => {
        try {
            // First update the conversation in memory with a new version
            const newVersion = Date.now();
            setConversations(prev => prev.map(conv =>
                conv.id === conversationId
                    ? { ...conv, folderId, _version: newVersion }
                    : conv
            ));

            // Then update in the database
            await db.moveConversationToFolder(conversationId, folderId);

            return;
        } catch (error) {
            console.error('Error moving conversation to folder:', error);
            throw error;
        }
    }, []);

    useEffect(() => {
        // Load current messages immediately when conversation changes, regardless of folder state
        if (currentConversationId) {
            const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];
            setCurrentMessages(messages);
        }
    }, [conversations, currentConversationId]);


    // Enhanced initialization with corruption detection and recovery
    const initializeWithRecovery = useCallback(async () => {
        if (isRecovering.current) return;
        isRecovering.current = true;

        try {
            await db.init();
            const savedConversations = await db.getConversations();

            // Set conversations immediately to unblock message loading
            console.log('✅ Setting conversations immediately:', savedConversations.length);
            setConversations(savedConversations);
            setIsInitialized(true);

            // Set current messages immediately if we have a current conversation
            if (currentConversationId) {
                const currentConv = savedConversations.find(c => c.id === currentConversationId);
                if (currentConv) {
                    console.log('✅ Setting current messages immediately:', currentConv.messages.length);
                    setCurrentMessages(currentConv.messages);
                }
            }

            // Handle backup/recovery operations asynchronously - don't block UI
            setTimeout(async () => {
                // Check for corruption by comparing with backup
                const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                if (backup) {
                    const backupConversations = JSON.parse(backup);

                    // If IndexedDB has significantly fewer conversations than backup, restore from backup
                    if (savedConversations.length < backupConversations.length * 0.5) {
                        console.warn(`⚠️ Potential corruption detected: IndexedDB has ${savedConversations.length} conversations, backup has ${backupConversations.length}`);

                        // Auto-recovery - update state again
                        await db.saveConversations(backupConversations);
                        setConversations(backupConversations);
                        console.log('✅ Auto-recovery completed');
                    } else {
                        await createBackup(savedConversations);
                    }
                } else {
                    await createBackup(savedConversations);
                }
            }, 0);

            console.log('✅ Conversations initialized, folder loading will continue in background');
        } catch (error) {
            console.error('Initialization failed:', error);
            // Existing error handling...
            // Existing error handling...
        } finally {
            isRecovering.current = false;
        }
    }, [createBackup]);

    useEffect(() => {
        initializeWithRecovery();

        // Enhanced backup interval - every 15 seconds instead of 30
        const backupInterval = setInterval(() => {
            if (conversations.length > 0) {
                createBackup(conversations);
            }
        }, 60000);

        const request = indexedDB.open('ZiyaDB');
        request.onerror = (event) => {
            const error = (event.target as IDBOpenDBRequest).error?.message || 'Unknown IndexedDB error';
            setDbError(error);
        };

        return () => {
            if (db.db) {
                db.db.close();
                clearInterval(backupInterval);
            }
        };
    }, [initializeWithRecovery, createBackup]);

    useEffect(() => {
        // Load current messages immediately when conversation changes, regardless of folder state
        if (currentConversationId) {
            const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];
            setCurrentMessages(messages);
        }
    }, [conversations, currentConversationId]);

    // Load folders when component mounts
    useEffect(() => {
        // Load folders independently of initialization state
        // This ensures folder loading doesn't block conversation loading
        const loadFoldersIndependently = async () => {
            try {
                console.log("Loading folders from database...");
                // Add a small delay to ensure conversations are loaded first
                await new Promise(resolve => setTimeout(resolve, 100));
                const folders = await db.getFolders();
                setFolders(folders);
                console.log("✅ Folders loaded:", folders.length);
            } catch (error) {
                console.error('Error loading folders:', error);
                // Don't let folder errors block the app
                setFolders([]);
            }
        };
        loadFoldersIndependently();
    }, []); // Remove isInitialized dependency

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
        console.log('Current conversation ref updated:', {
            id: currentConversationId,
            streamingConversations: Array.from(streamingConversations),
            hasStreamingContent: Array.from(streamedContentMap.keys()),
            activeConversations: conversations.filter(c => c.isActive).map(c => c.id),
            streamingToOther: streamingConversations.has(currentConversationId)
        });
    }, [currentConversationId]);

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
    }, [isInitialized, conversations, folders, mergeConversations, setConversations, setFolders]); // Added folders and setFolders


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
    }, []);

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
                    return { ...conv, messages: updatedMessages, _version: Date.now() };
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
    }, []);

    const value = useMemo(() => ({
        streamedContentMap,
        setStreamedContentMap,
        processingState,
        setProcessingState,
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
    }), [
        streamedContentMap,
        setStreamedContentMap,
        processingState,
        setProcessingState,
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
    ]);

    // Temporary debug command
    useEffect(() => {
        (window as any).debugChatContext = () => {
            console.log('ChatContext State:', { conversations, currentConversationId, streamedContentMap });
            console.log('Rendering Info:', Array.from(document.querySelectorAll('.diff-view')).map(el => el.id));
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
