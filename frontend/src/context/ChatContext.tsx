import React, { createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo } from 'react';
import { Conversation, Message, ConversationFolder } from "../utils/types";
import { v4 as uuidv4 } from "uuid";
import { db } from '../utils/db';
import { debounce } from '../utils/debounce';
import { Modal, message } from 'antd';
import { performEmergencyRecovery } from '../utils/emergencyRecovery';
interface ChatContext {
    question: string;
    setQuestion: (q: string) => void;
    streamedContentMap: Map<string, string>;
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>;
    isStreaming: boolean;
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
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

export function ChatProvider({ children }: ChatProviderProps) {
    const [question, setQuestion] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamedContentMap, setStreamedContentMap] = useState(() => new Map<string, string>());
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
    const [folders, setFolders] = useState<ConversationFolder[]>([]);
    const [dbError, setDbError] = useState<string | null>(null);
    const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
    const folderRef = useRef<string | null>(null);
    const [folderFileSelections, setFolderFileSelections] = useState<Map<string, string[]>>(new Map());
    const [folderPanelWidth, setFolderPanelWidth] = useState<number>(300); // Default width
    const processedModelChanges = useRef<Set<string>>(new Set());
    const contentRef = useRef<HTMLDivElement>(null);
    const pendingSave = useRef<NodeJS.Timeout | null>(null);
    const messageUpdateCount = useRef(0);

    const scrollToBottom = () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer && isTopToBottom) {
            requestAnimationFrame(() => {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            });
        }
    };

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

    const addStreamingConversation = (id: string) => {
        setStreamingConversations(prev => {
            const next = new Set(prev);
            console.log('Adding to streaming set:', { id, currentSet: Array.from(prev) });
            next.add(id);
            setStreamedContentMap(prev => new Map(prev).set(id, ''));
            setIsStreaming(true);
            return next;
        });
    };

    const removeStreamingConversation = (id: string) => {
        console.log('Removing from streaming set:', { id, currentSet: Array.from(streamingConversations) });
        setStreamingConversations(prev => {
            const next = new Set(prev);
            next.delete(id);
            setStreamedContentMap(prev => {
                const next = new Map(prev);
                next.delete(id);
                return next;
            });
            return next;
        });
    };

    const shouldUpdateState = (newState: Conversation[], force: boolean = false) => {
        if (force) return true;

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

    const saveConversationsWithDebounce = useCallback(async (conversations: Conversation[]) => {
        if (pendingSave.current) {
            clearTimeout(pendingSave.current);
        }

        pendingSave.current = setTimeout(async () => {
            try {
                await db.saveConversations(conversations);
                console.log('Debounced saved conversations to database');
            } catch (error) {
                console.error('Failed to save conversations:', error);
                setDbError(error instanceof Error ? error.message : 'Failed to save conversations');
            }
            pendingSave.current = null;
        }, 1000);
    }, []);

    const addMessageToConversation = (message: Message, targetConversationId: string, isNonCurrentConversation?: boolean) => {
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

            db.saveConversations(updatedConversations).catch(error => {
                console.error('Failed to save conversations:', error);
                setDbError(error instanceof Error ? error.message : 'Failed to save conversation');
            });

            return updatedConversations;
        });
    };

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

    const startNewChat = (specificFolderId?: string | null) => {
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

                db.saveConversations([...updatedConversations, newConversation])
                    .then(() => {
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
    };

    const loadConversation = async (conversationId: string) => {
        setIsLoadingConversation(true);
        try {
            // First update conversations in memory
            removeStreamingConversation(currentConversationId);
            // Mark current conversation as read
            setConversations(prevConversations => {
                const updatedConversations = prevConversations.map(conv =>
                    conv.id === currentConversationId
                        ? { ...conv, hasUnreadResponse: false }
                        : conv);

                // Then persist to database
                db.saveConversations(updatedConversations).catch(error => {
                    console.error('Failed to save conversation state:', error);
                });

                return updatedConversations;
            });

            // Set the current conversation ID after updating state
            await new Promise(resolve => setTimeout(resolve, 50));
            setCurrentConversationId(conversationId);

            // Set the current folder ID based on the conversation's folder
            const conversation = conversations.find(c => c.id === conversationId);
            if (conversation && conversation.folderId !== undefined) {
                setCurrentFolderId(conversation.folderId);
            }
            console.log('Current conversation changed:', {
                from: currentConversationId,
                to: conversationId,
                streamingConversations: Array.from(streamingConversations),
                hasStreamingContent: Array.from(streamedContentMap.keys())
            });
        } finally {
            setIsLoadingConversation(false);
        }
        setStreamedContentMap(new Map());
    };

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

    const deleteFolder = useCallback(async (id: string): Promise<void> => {
        try {
            // Find all conversations in this folder
            const conversationsInFolder = conversations.filter(c => c.folderId === id);

            // Mark all conversations in this folder as inactive
            setConversations(prev => prev.map(c =>
                c.folderId === id ? { ...c, isActive: false, _version: Date.now() } : c
            ));

            // Update conversations in database to mark them as inactive
            for (const conv of conversationsInFolder) {
                const updatedConv = { ...conv, isActive: false, _version: Date.now() };
                await db.saveConversations([updatedConv]);
            }

            // Log the deletion
            console.log(`Deleted folder ${id} with ${conversationsInFolder.length} conversations`);

            // Delete the folder
            await db.deleteFolder(id);

            // Update folders state
            setFolders(prev => prev.filter(f => f.id !== id));

            // If current folder is deleted, set current folder to null
            if (currentFolderId === id) {
                setCurrentFolderId(null);
            }
        } catch (error) {
            console.error('Error deleting folder:', error);
            throw error;
        }
    }, [conversations, currentFolderId]);

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
        if (isInitialized) {
            const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];
            setCurrentMessages(messages);
        }
    }, [conversations, currentConversationId, isInitialized]);

    useEffect(() => {
        const initialize = async () => {
            try {
                await db.init();
                const savedFolders = await db.getFolders();
                const saved = await db.getConversations();
                setConversations(saved);
                setIsInitialized(true);
            } catch (error) {
                console.error('Failed to initialize:', error);

                // Check if we have a backup in localStorage
                try {
                    const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                    if (backup) {
                        const backupConversations = JSON.parse(backup);
                        if (Array.isArray(backupConversations) && backupConversations.length > 0) {
                            console.log('Found backup conversations in localStorage:', backupConversations.length);
                            setConversations(backupConversations);
                            setIsInitialized(true);

                            // Try to repair in the background
                            performEmergencyRecovery().then(() => {
                                message.success('Database repaired successfully');
                                // Save the recovered conversations
                                db.saveConversations(backupConversations).catch(console.error);
                            }).catch(console.error);

                            // Return early since we've restored from backup
                            return;
                        }
                    }
                } catch (e) {
                    console.error('Error checking for backup:', e);
                }

                // Show user-friendly recovery dialog
                Modal.confirm({
                    title: 'Database Issue Detected',
                    content: 'We found an issue with your data storage. Would you like to repair it automatically?',
                    okText: 'Repair Now',
                    cancelText: 'Cancel',
                    onOk: async () => {
                        try {
                            const result = await performEmergencyRecovery();
                            if (result.success) {
                                message.success('Recovery completed. Reloading page...');
                                setTimeout(() => window.location.reload(), 1500);
                            } else {
                                message.error(`Recovery failed: ${result.message}`);
                            }
                        } catch (recoveryError) {
                            message.error('Recovery failed. Please try again.');
                        }
                    }
                });
            }
        };
        // Set up periodic backup of conversations to localStorage
        const backupConversations = () => {
            if (conversations.length > 0) {
                try {
                    // Only backup active conversations
                    const activeConversations = conversations.filter(c => c.isActive !== false);
                    if (activeConversations.length > 0) {
                        localStorage.setItem('ZIYA_CONVERSATION_BACKUP',
                            JSON.stringify(activeConversations));
                        console.debug('Backed up', activeConversations.length, 'conversations to localStorage');
                    }
                } catch (e) {
                    console.error('Error backing up conversations:', e);
                }
            }
        };

        // Set up periodic backup every 30 seconds
        const backupInterval = setInterval(backupConversations, 30000);


        initialize();

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
    }, []);

    // Load folders when component mounts
    useEffect(() => {
        if (isInitialized) {
            console.log("Loading folders from database...");
            db.getFolders().then(setFolders).catch(console.error);
        }
    }, [isInitialized]);

    // Listen for model change events
    useEffect(() => {
        window.addEventListener('modelChanged', handleModelChange as EventListener);

        return () => {
            window.removeEventListener('modelChanged', handleModelChange as EventListener);
        };

        // Reset processed changes when component unmounts
        return () => { processedModelChanges.current.clear(); };
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


    useEffect(() => {
        const handleStorageChange = async () => {
            try {
                const saved = await db.getConversations();
                if (saved.length > 0) {
                    setConversations(prev => {
                        // Don't update conversations that are being edited
                        return prev.map(conv => {
                            const savedConv = saved.find(s => s.id === conv.id);
                            // Keep our version if we're editing or if our version is newer
                            return (conv._editInProgress || (conv._version || 0) > (savedConv?._version || 0))
                                ? conv : (savedConv || conv);
                        });
                    });
                }
            } catch (error) {
                console.error('Error during conversation poll:', error);
            }

        };
        window.addEventListener('storage', handleStorageChange);
        return () => window.removeEventListener('storage', handleStorageChange);
    }, []);

    useEffect(() => {
        if (!currentConversationId) {
            setCurrentConversationId(uuidv4());
        }
    }, [currentConversationId]);

    const setDisplayMode = (conversationId: string, mode: 'raw' | 'pretty') => {
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
            db.saveConversations(updated).catch(console.error);
            return updated;
        });
    };

    const value = useMemo(() => ({
        question,
        setQuestion,
        streamedContentMap,
        setStreamedContentMap,
        isStreaming,
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
        isLoadingConversation
    }), [
        question,
        streamedContentMap,
        isStreaming,
        streamingConversations,
        conversations,
        currentConversationId,
        currentMessages,
        isTopToBottom,
        dbError,
        currentFolderId,
        createFolder,
        updateFolder,
        deleteFolder,
        moveConversationToFolder,
        isLoadingConversation,
        // Include setDisplayMode in the dependency array
        setQuestion,
        setStreamedContentMap,
        addStreamingConversation,
        removeStreamingConversation,
        setConversations,
        setIsStreaming,
        setCurrentConversationId
    ]);

    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    const context = useContext(chatContext);
    if (!context) {
        throw new Error('useChatContext must be used within a ChatProvider');
    }
    return context;
}
