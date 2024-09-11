import React, {createContext, Dispatch, ReactNode, SetStateAction, useContext, useEffect, useState} from 'react';
import {Conversation, Message} from "../utils/types";
import {v4 as uuidv4} from "uuid";

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
    startNewChat: () => void;
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

const LOCAL_STORAGE_CONVERSATIONS_KEY = 'ZIYA_CONVERSATIONS';

export function ChatProvider({children}: ChatProviderProps) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [question, setQuestion] = useState('');
    const [streamedContent, setStreamedContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [currentConversationId, setCurrentConversationId] = useState<string>(uuidv4());

    const addMessageToCurrentConversation = (message: Message) => {
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
                }];
            }
        });
    };

    const startNewChat = () => {
        setCurrentConversationId(uuidv4());
        setMessages([]);
    };

    useEffect(() => {
        const storedConversations = localStorage.getItem(LOCAL_STORAGE_CONVERSATIONS_KEY);
        if (storedConversations) {
            setConversations(JSON.parse(storedConversations));
        }
    }, []);

    useEffect(() => {
        localStorage.setItem(LOCAL_STORAGE_CONVERSATIONS_KEY, JSON.stringify(conversations.slice(-50)));
    }, [conversations]);

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
        setCurrentConversationId,
        addMessageToCurrentConversation,
        startNewChat
    };
    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    return useContext(chatContext)!;
}