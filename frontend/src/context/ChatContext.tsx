import React, {createContext, ReactNode, useContext, useState, useEffect, Dispatch, SetStateAction, useRef, useCallback} from 'react';
import {Conversation, Message} from "../utils/types";
import {SetStreamedContentFunction} from "../apis/chatApi";
import {v4 as uuidv4} from "uuid";
import { db } from '../utils/db';
import { debounce } from '../utils/debounce';

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
    streamingConversationId: string | null;
    setStreamingConversationId: Dispatch<SetStateAction<string | null>>;
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
    const currentConversationRef = useRef<string>(currentConversationId);
    const [currentMessages, setCurrentMessages] = useState<Message[]>([]);
    const [streamingConversationId, setStreamingConversationId] = useState<string | null>(null);
    const [isTopToBottom, setIsTopToBottom] = useState(false);
    const [isInitialized, setIsInitialized] = useState(false);
    const lastSavedState = useRef<string>('');

    const scrollToBottom = () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer && isTopToBottom) {
            requestAnimationFrame(() => {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            });
        }
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

        const shouldUpdate = newStateStr !== lastSavedState.current;
        lastSavedState.current = newStateStr;
        return shouldUpdate;
    };

    // Add debounce for database updates
    const debouncedSaveToDb = useCallback(
        debounce(async (updatedConversations: Conversation[]) => {
            try {
                await db.saveConversations(updatedConversations);
                console.log('Persisted conversations to database:',
                    `Total: ${updatedConversations.length}`);
            } catch (error) {
                console.error('Error persisting conversations:', error);
            }
        }, 1000),
        []
    );

    const addMessageToCurrentConversation = (message: Message) => {
        if (!currentConversationId) return;

	// If this is a human message and we're already processing a response, don't add it
        if (message.role === 'human' && isStreaming) {
            console.warn('Attempted to add human message while streaming');
            return;
        }
 
	console.log('Adding message:', {
            role: message.role,
            content: message.content.substring(0, 50)
        });

	const timestamp = Date.now();
	console.log(`Adding message to conversation ${currentConversationId}:`, message);

        const updateConversations = (prevConversations: Conversation[]): Conversation[] => {
	    // Find existing conversation
           const existingConversation = prevConversations.find(c => c.id === currentConversationId);
           
           if (!existingConversation) {
                // Create new conversation
                const newConversation: Conversation = {
                    id: currentConversationId,
		    title: message.role === 'human'
                       ? message.content.slice(0, 45) + (message.content.length > 45 ? '...' : '')
                       : prevConversations.find(c => c.id === currentConversationId)?.title || 'New Conversation',
                    messages: [message],
                    lastAccessedAt: timestamp,
                    isActive: true,
		    _version: Date.now()
                };
		return [...prevConversations, newConversation];
            }

	   // Update existing conversation
           const updatedConversations = prevConversations.map(conv =>
               conv.id === currentConversationId
                   ? {
                       ...conv,
                       messages: [...conv.messages, message],
                       lastAccessedAt: timestamp,
		       _version: Date.now()
                     }
                   : conv
           );

           return updatedConversations;
	};

	// Update state first
        setConversations(prevConversations => {
	    // Get the updated conversations array
            const updatedConversations: Conversation[] = updateConversations(prevConversations);

            // Persist changes after state update
            if (shouldUpdateState(updatedConversations)) {
                console.log('Persisting updated conversations:', `Total: ${updatedConversations.length}`);
                db.saveConversations(updatedConversations).catch(console.error);
            }
	    return updatedConversations;

        });

    };

    // Add storage event listener to detect changes from other tabs or windows
    useEffect(() => {
       let pollInterval: NodeJS.Timeout;
       const checkForUpdates = async () => {
           try {
               const saved = await db.getConversations();
	       if (saved && shouldUpdateState(saved)) {
		   const currentConv = saved.find(c => c.id === currentConversationId);
                   console.log(
                       'Updating conversations from storage:',
                       `Current: ${conversations.length}, New: ${saved.length}, `,
                       `CurrentConvId: ${currentConversationId}`,
                       `Current conv exists: ${Boolean(currentConv)}`
                   );
                   setConversations(saved);
               }
           } catch (error) {
               console.error('Error syncing conversations:', error);
           }
       };

       if (!isInitialized) return; 
       pollInterval = setInterval(checkForUpdates, 5000);
       return () => clearInterval(pollInterval);
    }, []);

    const startNewChat = () => {
	const newId = uuidv4();
	setStreamingConversationId(null);
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
	if (isInitialized) {
            const messages = conversations.find(c => c.id === currentConversationId)?.messages || [];
            console.debug(`Updating current messages from conversation ${currentConversationId}:`, messages);
            if (messages.length > 0) {
                console.log('Setting current messages:', messages.length);
            }
            setCurrentMessages(messages);
        }
    }, [conversations, currentConversationId]);

    // Initialize database and load conversations
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
    }, []);

    // Load conversations from storage on mount
    useEffect(() => {
        const loadSavedConversations = async () => {
            try {
                await db.init();
                const saved = await db.getConversations();
                if (saved.length > 0) {
                    console.debug('Loading saved conversations:', saved.length);
                    setConversations(saved);
                }
            } catch (error) {
                console.error('Failed to load conversations:', error);
            }
        };
        loadSavedConversations();
    }, []);

    // Keep currentConversationRef in sync
    useEffect(() => {
        currentConversationRef.current = currentConversationId;
    }, [currentConversationId]);

    // Add effect to monitor storage events
    useEffect(() => {
        const handleStorageChange = async () => {
            try {
                const saved = await db.getConversations();
                console.debug('Storage event: Retrieved conversations:', 
                    saved.map(c => ({id: c.id, messageCount: c.messages.length}))
                );
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
	streamingConversationId,
	setStreamingConversationId,
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
