import React, {createContext, Dispatch, ReactNode, SetStateAction, useContext, useEffect, useState} from 'react';
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
        setMessages(prevMessages => [...prevMessages, message]);
        setConversations(prevConversations => {
            const existingConversationIndex = prevConversations.findIndex(conv => conv.id === currentConversationId);
            if (existingConversationIndex !== -1) {
                // Update existing conversation
                const updatedConversations = [...prevConversations];
                updatedConversations[existingConversationIndex] = {
                    ...updatedConversations[existingConversationIndex],
                    messages: [...updatedConversations[existingConversationIndex].messages, message]
                };
                return updatedConversations;
            } else {
                // Create new conversation
                return [...prevConversations, {
                    id: currentConversationId,
                    title: message.content.slice(0, 45),
                    messages: [message],
		    lastAccessedAt: Date.now()
                }];
            }
        });
	scrollToBottom();
    };

    const startNewChat = () => {
	// Update last accessed timestamp for the current conversation
        setConversations(prevConversations =>
            prevConversations.map(conv =>
                conv.id === currentConversationId
                    ? {
                        ...conv,
                        lastAccessedAt: Date.now()
                      }
                    : conv
            )
        );
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

    const value: ChatContext = {
        messages,
        setMessages,
        question,
        setQuestion,
        streamedContent,
        setStreamedContent,
        isStreaming,
        setIsStreaming,
        conversations,
        setConversations,
        currentConversationId,
	isTopToBottom,
        setIsTopToBottom,
	scrollToBottom,
        setCurrentConversationId,
        addMessageToCurrentConversation,
        startNewChat
    };
    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    return useContext(chatContext)!;
}
