import React, {createContext, Dispatch, ReactNode, SetStateAction, useContext, useState} from 'react';
import {Message} from "../utils/types";

interface ChatContext {
    messages: Message[];
    setMessages: Dispatch<SetStateAction<Message[]>>;
    question: string;
    setQuestion: Dispatch<SetStateAction<string>>;
    streamedContent: string;
    setStreamedContent: Dispatch<SetStateAction<string>>;
    isStreaming: boolean;
    setIsStreaming: Dispatch<SetStateAction<boolean>>;
}

const chatContext = createContext<ChatContext | undefined>(undefined);

interface ChatProviderProps {
    children: ReactNode;
}

export function ChatProvider({ children }: ChatProviderProps) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [question, setQuestion] = useState('');
    const [streamedContent, setStreamedContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);

    const value: ChatContext = {
        messages,
        setMessages,
        question,
        setQuestion,
        streamedContent,
        setStreamedContent,
        isStreaming,
        setIsStreaming,
    };
    return <chatContext.Provider value={value}>{children}</chatContext.Provider>;
}

export function useChatContext(): ChatContext {
    return useContext(chatContext)!;
}