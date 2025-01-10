import React, {createContext, Dispatch, ReactNode, SetStateAction, useContext, useEffect, useState, useMemo, useCallback} from 'react';
import {Conversation, Message} from "../utils/types";
import {v4 as uuidv4} from "uuid";
import { db } from '../utils/db';

interface ChatContext {
    messages: Message[];
    setMessages: Dispatch<SetStateAction<Message[]>>;
    question: string;
    setQuestion: Dispatch<SetStateAction<string>>;
    streamedContent: string;
    setStreamedContent: Dispatch<SetStateAction<string>>;
    isStreaming: boolean;
    setIsStreaming: Dispatch<SetStateAction<boolean>>;
    conversations: Conversation[];
    loadConversation: (conversationId: string) => Promise<void>;
    isLoadingConversation: boolean;
    setConversations: Dispatch<SetStateAction<Conversation[]>>;
    currentConversationId: string;
    setCurrentConversationId: Dispatch<SetStateAction<string>>;
    addMessageToCurrentConversation: (message: Message) => void;
    isTopToBottom: boolean;
    setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
    scrollToBottom: () => void;
    startNewChat: () => void;
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

async function migrateFromLocalStorage() {
    try {
        // First check if we already have data in IndexedDB
        const existingConversations = await db.getConversations();
        if (existingConversations.length > 0) {
            return; // Skip migration if we already have data
        }

        const storedConversations = localStorage.getItem('ZIYA_CONVERSATIONS');
        if (storedConversations) {
            await db.saveConversations(JSON.parse(storedConversations));
            localStorage.removeItem('ZIYA_CONVERSATIONS');
        }
    } catch (error) {
        console.error('Failed to migrate conversations:', error);
    }
}

export function ChatProvider({children}: ChatProviderProps) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [question, setQuestion] = useState('');
    const [streamedContent, setStreamedContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>(uuidv4());
    const [isTopToBottom, setIsTopToBottom] = useState<boolean>(false);
    const [isInitialized, setIsInitialized] = useState(false);

    const scrollToTop = () => {
        const bottomUpContent = document.querySelector('.bottom-up-content');
        if (bottomUpContent) {
            bottomUpContent.scrollTop = 0;
        }
    };

    const scrollToBottom = () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer && isTopToBottom) {
	    requestAnimationFrame(() => {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            });
        } else if (!isTopToBottom) {
            scrollToTop();
	}
    };

    const cleanMessage = (message: Message): Message | null => {
        if (!message || !message.content) {
            return null;
        }

        // Remove null characters and normalize whitespace
        const cleaned = message.content
            // Escape HTML tags
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            // Normalize whitespace
            .replace(/\0/g, '')
            .replace(/\r\n/g, '\n')
            .replace(/[\s\uFEFF\xA0]+/g, ' ') // Handle all types of whitespace
            .trim();

	if (!cleaned) {
	    return null;
	}

        return { ...message, content: cleaned };
    };

    const addMessageToCurrentConversation = (message: Message) => {
        const cleanedMessage = cleanMessage(message);
	if (!cleanedMessage) {
            console.warn('Attempted to add invalid message:', message);
            return;
        }

	// Calculate next sequence number
        let nextSequence = 1;
        if (messages.length > 0) {
            const maxSequence = Math.max(...messages.map(m => m.sequence));
            nextSequence = maxSequence + 1;
        }

        // Create enahnced message with sequence and timestamp
        const enhancedMessage = {
            ...cleanedMessage,
            timestamp: Date.now(),
            sequence: nextSequence
        };

	// Update messages state with new message
        setMessages(prevMessages => {
	    // check for dupe seq nums
            const sequences = new Set(prevMessages.map(m => m.sequence));
            if (sequences.has(enhancedMessage.sequence)) {
                console.warn(`Duplicate sequence number ${enhancedMessage.sequence} detected`);
                enhancedMessage.sequence = Math.max(...sequences) + 1;
            }

	    // Create new array with spread
            const updatedMessages = [...prevMessages];
            // Add new message
            updatedMessages.push(enhancedMessage);
            // Sort messages by sequence number to maintain order
	    updatedMessages.sort((a, b) => {
                return a.sequence !== b.sequence ? a.sequence - b.sequence : a.timestamp - b.timestamp;
            });

            return updatedMessages;
        });

        // Update conversations state
        setConversations(prevConversations => {
            const existingConversationIndex = prevConversations.findIndex(conv => conv.id === currentConversationId);
            if (existingConversationIndex !== -1) {
                // Update existing conversation
                const updatedConversations = [...prevConversations];
                updatedConversations[existingConversationIndex] = {
                    ...updatedConversations[existingConversationIndex],
		    messages: [...updatedConversations[existingConversationIndex].messages, enhancedMessage],
		    lastAccessedAt: Date.now()
                };
                return updatedConversations;
            } else {
                // Create new conversation
                return [...prevConversations, {
                    id: currentConversationId,
                    title: message.content.slice(0, 45),
		    messages: [enhancedMessage],
		    lastAccessedAt: Date.now(),
		    isActive: true
                }];
            }
        });
	scrollToBottom();
    };

    // helper - laod chunks instead of full for faster loading
    const loadMessagesInChunks = useCallback((messages: Message[], chunkSize: number = 10) => {
        let currentChunk = 0;
        const totalChunks = Math.ceil(messages.length / chunkSize);

        const loadNextChunk = () => {
            if (currentChunk >= totalChunks) {
                setIsLoadingConversation(false);
                return;
            }

            const start = currentChunk * chunkSize;
            const end = Math.min(start + chunkSize, messages.length);

            setMessages(prevMessages => [...prevMessages, ...messages.slice(start, end)]);
            currentChunk++;

            // Schedule next chunk with requestAnimationFrame to keep UI responsive
            requestAnimationFrame(loadNextChunk);
        };

        return loadNextChunk;
    }, []);

    const loadConversation = useCallback(async (conversationId: string) => {
        const selectedConversation = conversations.find(conv => conv.id === conversationId);
        if (selectedConversation && conversationId !== currentConversationId) {
            setIsLoadingConversation(true);
	    try {
                // Clear current messages first to reduce unmounting overhead
                setMessages([]);
		setStreamedContent(''); // Clear any existing streamed content

		setCurrentConversationId(conversationId);
                await new Promise(resolve => setTimeout(resolve, 50));

		// Start loading messages in chunks
                const loadChunks = loadMessagesInChunks(selectedConversation.messages);
		loadChunks();
            } catch (error) {
                console.error('Error loading conversation:', error);
		setIsLoadingConversation(false);
            } finally {
                setIsLoadingConversation(false);
            }
        }
    }, [conversations, currentConversationId, loadMessagesInChunks]);

    // Synchronization effect to guarantee  messages are saved to conversations
    useEffect(() => {
        if (messages.length > 0) {
            setConversations(prevConversations => {
                const conversationIndex = prevConversations.findIndex(conv => conv.id === currentConversationId);
                if (conversationIndex !== -1) {
                    const updatedConversations = [...prevConversations];
                    updatedConversations[conversationIndex] = {
                        ...updatedConversations[conversationIndex],
                        messages: messages,
                        lastAccessedAt: Date.now()
                    };
                    return updatedConversations;
                }
                return prevConversations;
            });
        }
    }, [messages, currentConversationId]);

    const startNewChat = () => {
	// Update last accessed timestamp for the current conversation
        setConversations(prevConversations =>
	    prevConversations.map(conv => {
                if (conv.id === currentConversationId) {
                    // Save current messages to the conversation before creating new chat
                    return {
                        ...conv,
                        messages: messages,  // Save current messages
                        lastAccessedAt: Date.now(),
                        isActive: false
                    };
                }
                return conv;
            }
            )
        );

	// Clear current state for new chat
	setStreamedContent(''); // Clear any existing streamed content
        setCurrentConversationId(uuidv4());
        setMessages([]);
    };

    useEffect(() => {
        const initDB = async () => {
            try {
                await db.init();
                // Attempt migration before loading conversations
                await migrateFromLocalStorage();
                const savedConversations = await db.getConversations();
                setConversations(savedConversations);
                setIsInitialized(true);
            } catch (error) {
                console.error('Failed to initialize database:', error);
            }
        };
        initDB();
    }, []);

    // Only save when initialized to prevent overwriting with empty state
    const shouldSave = isInitialized && conversations.length > 0;

    useEffect(() => {
        if (shouldSave) {
            db.saveConversations(conversations).catch(console.error);
        }
    }, [conversations, shouldSave]);

    // Memoize the context value
    const value = useMemo<ChatContext>(() => ({
        messages,
        setMessages,
        question,
        setQuestion,
        streamedContent,
        setStreamedContent,
        isStreaming,
        setIsStreaming,
        conversations,
	isLoadingConversation,
        setConversations,
        currentConversationId,
	isTopToBottom,
	loadConversation,
        setIsTopToBottom,
	scrollToBottom,
        setCurrentConversationId,
        addMessageToCurrentConversation,
        startNewChat
    }), [messages, question, streamedContent, isStreaming, conversations, currentConversationId, isTopToBottom]);

    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    return useContext(chatContext)!;
}
