import React, {createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction} from 'react';
import {Conversation, Message} from "../utils/types";
import {SetStreamedContentFunction} from "../apis/chatApi";
import {v4 as uuidv4} from "uuid";
import { db } from '../utils/db';

interface ChatContext {
    question: string;
    setQuestion: (q: string) => void;
    streamedContent: string;
    setStreamedContent: SetStreamedContentFunction;
    isStreaming: boolean;
    setIsStreaming: (s: boolean) => void;
    setConversations: Dispatch<SetStateAction<Conversation[]>>;
    conversations: Conversation[];
    isLoadingConversation: boolean;
    currentConversationId: string;
    setCurrentConversationId: (id: string) => void;
    addMessageToCurrentConversation: (message: Message) => void;
    currentMessages: Message[];
    loadConversation: (id: string) => void;
    startNewChat: () => void;
    isTopToBottom: boolean;
    setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
    scrollToBottom: () => void;
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

export function ChatProvider({children}: ChatProviderProps) {
    const [question, setQuestion] = useState('');
    const [streamedContent, setStreamedContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [currentResponse, setCurrentResponse] = useState<Message | null>(null);
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [currentConversationId, setCurrentConversationId] = useState<string>(uuidv4());
    const [currentMessages, setCurrentMessages] = useState<Message[]>([]);
    const [isTopToBottom, setIsTopToBottom] = useState(false);

    const scrollToBottom = () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer && isTopToBottom) {
            requestAnimationFrame(() => {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            });
        }
    };

    const addMessageToCurrentConversation = (message: Message) => {
	
	// If this is a human message and we're already processing a response, don't add it
        if (message.role === 'human' && isStreaming) {
            console.warn('Attempted to add human message while streaming');
            return;
        }
 
	console.log('Adding message:', {
            role: message.role,
            content: message.content.substring(0, 50)
        });

	setConversations(prevConversations => {
            const conversation = prevConversations.find(c => c.id === currentConversationId);
            if (conversation) {
                // Update existing conversation
		const updatedConversations = prevConversations.map(c =>
                    c.id === currentConversationId
                        ? {...c, messages: [...c.messages, message]} 
                        : c
                );
		console.log('Updated conversation:', updatedConversations.find(c => c.id === currentConversationId)?.messages);
                return updatedConversations;
            } else {
                // Create new conversation
                const newConversation: Conversation = {
                    id: currentConversationId,
                    title: message.content.slice(0, 45),
                    messages: [message],
                    lastAccessedAt: Date.now(),
                    isActive: true
                };
                const newConversations = [...prevConversations, newConversation];
		console.log('Created new conversation with messages:', newConversation.messages);
		return newConversations;
            }
        });

        db.saveConversations(conversations).catch(console.error);
    };

    const startNewChat = () => {
	const newId = uuidv4();
        setConversations(prevConversations =>
            prevConversations.map(conv => ({
                ...conv,
                isActive: false
            }))
        );
	setCurrentConversationId(newId);
        setStreamedContent('');
        setCurrentMessages([]);
    };

     const loadConversation = async (id: string) => {
        setIsLoadingConversation(true);
        try {
            setCurrentConversationId(id);
            await new Promise(resolve => setTimeout(resolve, 50)); // Brief delay for UI
        } finally {
            setIsLoadingConversation(false);
        }
        setStreamedContent('');
    };

    // Update currentMessages whenever conversations change
    useEffect(() => {
        const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];
        setCurrentMessages(messages);
        console.log('Updated current messages:', messages);
    }, [conversations, currentConversationId]);

    // Load conversations from storage on mount
    useEffect(() => {
        const loadSavedConversations = async () => {
            try {
                await db.init();
                const saved = await db.getConversations();
                if (saved.length > 0) {
                    setConversations(saved);
                }
            } catch (error) {
                console.error('Failed to load conversations:', error);
            }
        };
        loadSavedConversations();
    }, []);

    // Initialize with a new conversation ID if none exists
    useEffect(() => {
        if (!currentConversationId) {
            setCurrentConversationId(uuidv4());
        }
    }, [currentConversationId]);

    const value = {
        question,
        setQuestion,
        streamedContent,
        setStreamedContent,
        isStreaming,
	setConversations,
        setIsStreaming,
        conversations,
        currentConversationId,
	currentMessages,
        setCurrentConversationId,
        addMessageToCurrentConversation,
        loadConversation,
        startNewChat,
        isTopToBottom,
        setIsTopToBottom,
        scrollToBottom,
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
