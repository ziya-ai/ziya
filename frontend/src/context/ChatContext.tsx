import React, {createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback} from 'react';
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
    addMessageToConversation: (message: Message) => void;
    currentMessages: Message[];
    loadConversation: (id: string) => void;
    startNewChat: () => void;
    isTopToBottom: boolean;
    dbError: string | null;
    setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
    scrollToBottom: () => void;
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
            next.add(id);
	    setStreamedContentMap(prev => new Map(prev).set(id, ''));
            setIsStreaming(true);
            return next;
        });
    };

    const removeStreamingConversation = (id: string) => {
        setStreamingConversations(prev => {
            const next = new Set(prev);
            next.delete(id);
            return next;
	    setStreamedContentMap(prev => {
                const next = new Map(prev);
                next.delete(id);
                return next;
            });
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

    const addMessageToConversation = (message: Message) => {
        if (!currentConversationId) return;

        messageUpdateCount.current += 1;
        setConversations(prevConversations => {
            const existingConversation = prevConversations.find(c => c.id === currentConversationId);
            const isFirstMessage = existingConversation?.messages.length === 0;

            const updatedConversations = existingConversation
                ? prevConversations.map(conv =>
                    conv.id === currentConversationId
                        ? {
                            ...conv,
                            messages: [...conv.messages, message],
                            lastAccessedAt: Date.now(),
                            _version: Date.now(),
                            hasUnreadResponse: message.role === 'assistant',
                            title: isFirstMessage && message.role === 'human' ? message.content.slice(0, 45) + '...' : conv.title
                        }
                        : conv
                )
                : [...prevConversations, {
                    id: currentConversationId,
                    title: message.role === 'human'
                        ? message.content.slice(0, 45) + (message.content.length > 45 ? '...' : '')
                        : 'New Conversation',
                    messages: [message],
                    lastAccessedAt: Date.now(),
                    isActive: true,
                    _version: Date.now(),
                    hasUnreadResponse: false
                }];

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

                setConversations(prevConversations => [...prevConversations, newConversation]);

                db.saveConversations([...conversations, newConversation])
                    .then(() => {
                        setStreamingConversations(new Set());
			setStreamedContentMap(new Map());
                        setCurrentMessages([]);
                    })
                    .catch(error => {
                        console.error('Failed to save new conversation:', error);
                    });

                setCurrentConversationId(newId);
                resolve();
            } catch (error) {
                console.error('Failed to create new conversation:', error);
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
                    conv.id === conversationId
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

    const value = {
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
        dbError,
        isLoadingConversation
    };

    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    const context = useContext(chatContext);
    if (!context) {
        throw new Error('useChatContext must be used within a ChatProvider');
    }
    return context;
}
