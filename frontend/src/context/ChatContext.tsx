import React, {createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback, useMemo} from 'react';
import {Conversation, Message} from "../utils/types";
import {v4 as uuidv4} from "uuid";
import { db } from '../utils/db';
import { debounce } from '../utils/debounce';

interface ChatContext {
    question: string;
    setQuestion: (q: string) => void;
    streamedContentMap: Map<string, string>;
    setStreamedContentMap: Dispatch<SetStateAction<Map<string, string>>>;
    isStreaming: boolean;
    setIsStreaming: (s: boolean) => void;
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
    startNewChat: () => void;
    isTopToBottom: boolean;
    dbError: string | null;
    setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
    scrollToBottom: () => void;
    setDisplayMode: (conversationId: string, mode: 'raw' | 'pretty') => void;
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

export function ChatProvider({children}: ChatProviderProps) {
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
    const pendingSave = useRef<NodeJS.Timeout | null>(null);
    const messageUpdateCount = useRef(0);
    const [dbError, setDbError] = useState<string | null>(null);

    const scrollToBottom = () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer && isTopToBottom) {
            requestAnimationFrame(() => {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            });
        }
    };

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
                            title: isFirstMessage && message.role === 'human' ? message.content.slice(0, 45) + '...' : conv.title
                        };
                    }
                    return conv;
                })
                : [...prevConversations, {
                    id: conversationId,
                    title: message.role === 'human'
                        ? message.content.slice(0, 45) + (message.content.length > 45 ? '...' : '')
                        : 'New Conversation',
                    messages: [message],
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

    const startNewChat = () => {
        return new Promise<void>((resolve, reject) => {
            try {
                const newId = uuidv4();
                const newConversation: Conversation = {
                    id: newId,
                    title: 'New Conversation',
                    messages: [],
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
                const saved = await db.getConversations();
                setConversations(saved);
                setIsInitialized(true);
            } catch (error) {
                console.error('Failed to initialize:', error);
            }
        };
        initialize();

        const request = indexedDB.open('ZiyaDB');
        request.onerror = (event) => {
            const error = (event.target as IDBOpenDBRequest).error?.message || 'Unknown IndexedDB error';
            setDbError(error);
        };

        return () => {
            if (db.db) {
                db.db.close();
            }
        };
    }, []);

    useEffect(() => {
        currentConversationRef.current = currentConversationId;
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
                    setConversations(saved);
                }
            } catch (error) {
                console.error('Error syncing conversations:', error);
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
        setDisplayMode,
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
        isLoadingConversation,
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
