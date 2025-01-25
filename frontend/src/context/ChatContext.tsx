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
        }, 1000); // 1 second debounce
    }, []);

    const addMessageToCurrentConversation = (message: Message) => {
        if (!currentConversationId) return;

	const timestamp = Date.now();
	console.log(`Adding message to conversation ${currentConversationId}:`, message);

	if (message.role === 'human' && isStreaming) {
            return;
        }

	messageUpdateCount.current += 1;
        const updateId = messageUpdateCount.current;

        console.debug('Message update lifecycle:', {
            updateId,
            messageType: message.role,
            conversationId: currentConversationId,
	    currentMessageCount: currentMessages.length
        });
 
	console.log('Adding message:', {
            role: message.role,
            content: message.content.substring(0, 50)
        });

	const processConversationUpdate = (prevConversations: Conversation[]): Conversation[] => {
	   // Find existing conversation
           const existingConversation = prevConversations.find(c => c.id === currentConversationId);

	   // Get message count safely
           const getMessageCount = (conv: Conversation | undefined | null): number => {
               return conv?.messages?.length ?? 0;
           };

	   const isFirstMessage = existingConversation?.messages.length === 0;
	   const updatedConversations = !existingConversation
               ? [...prevConversations, {
                    // Create and add new conversation
                    id: currentConversationId,
		    title: message.role === 'human'
                       ? message.content.slice(0, 45) + (message.content.length > 45 ? '...' : '')
                       : prevConversations.find(c => c.id === currentConversationId)?.title || 'New Conversation',
                    messages: [message],
                    lastAccessedAt: Date.now(),
                    isActive: true,
		    _version: Date.now()
	       }]
	       : prevConversations.map(conv =>
                   // Update existing conversation
                   conv.id === currentConversationId
		       ? {
                        ...conv,
                        messages: [...conv.messages, message],
                        lastAccessedAt: Date.now(),
                        _version: Date.now(),
                        title: isFirstMessage && message.role === 'human' ? message.content.slice(0, 45) + '...' : conv.title
                       }
                       : conv
               );

	   console.debug('Conversation update processed:', {
                existingConversationFound: Boolean(existingConversation),
		updatedConversationCount: prevConversations.length + (existingConversation ? 0 : 1),
		messageCount: existingConversation?.messages?.length ?? 0 + 1,
                conversationId: currentConversationId
            });

	   // Log after state update
            console.debug('Message update complete:', {
                updateId,
                totalMessages: currentMessages.length + 1,
                conversationId: currentConversationId
            });

	    // Immediately save to database
            console.debug('Saving conversation update:', {
                conversationId: currentConversationId,
	        messageCount: getMessageCount(existingConversation) + 1,
		isNew: !existingConversation
            });

	    // Add detailed logging around the save operation
            console.debug('Attempting to save conversations:', {
                conversationId: currentConversationId,
                messageCount: updatedConversations.find(c => c.id === currentConversationId)?.messages.length,
                totalConversations: updatedConversations.length,
                firstMessage: updatedConversations.find(c => c.id === currentConversationId)?.messages[0]?.content.substring(0, 50)
            });
            
            db.saveConversations(updatedConversations).then(() => {
                console.debug('Database save completed successfully');
            }).catch(error => {
                console.error('Database save failed:', error);
                setDbError(error instanceof Error ? error.message : 'Failed to save conversation');
            });

	    return updatedConversations;

	};

        setConversations(processConversationUpdate);

    };

    // Add storage event listener to detect changes from other tabs or windows
    useEffect(() => {
       let pollInterval: NodeJS.Timeout;
       const checkForUpdates = async () => {
           try {
		   const saved = await db.getConversations();
                   const currentConv = saved.find(c => c.id === currentConversationId);

                   console.log('State sync check:',
                       {
                           currentMessagesCount: currentMessages.length,
			   currentVersion: conversations.find(c => c.id === currentConversationId)?._version,
                           savedMessagesCount: currentConv?.messages.length,
                           hasCurrentConversation: Boolean(currentConv),
                           messageUpdateCount: messageUpdateCount.current
                       }
                   );

                   if (saved && shouldUpdateState(saved)) {
                       console.log(
                       'Updating conversations from storage:',
                       `Current: ${conversations.length}, New: ${saved.length}, `,
                       `CurrentConvId: ${currentConversationId}`,
                       `Current conv exists: ${Boolean(currentConv)}`,
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

    // Add effect to verify state update
    useEffect(() => {
        console.debug('Conversation state updated:', {
            currentMessageCount: currentMessages.length
        });
    }, [currentMessages.length]);

    const startNewChat = () => {
	const newId = uuidv4();
	const newConversation: Conversation = {
            id: newId,
            title: 'New Conversation',
            messages: [],
            lastAccessedAt: Date.now(),
            isActive: true,
            _version: Date.now()
        };
        // First update state
	setStreamingConversationId(null);
	setConversations(prevConversations => {
            const updatedConversations = prevConversations.map(conv => ({
                ...conv,
                isActive: false
	    }));
            return [...updatedConversations, newConversation];
        });
        // Then save to database
        db.saveConversations([...conversations, newConversation])
            .then(() => {
                setCurrentConversationId(newId);
                setStreamedContent('');
                setCurrentMessages([]);
            })
            .catch(error => {
                console.error('Failed to save new conversation:', error);
            });
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
		console.log('Database initialized with conversations:', saved);
                setConversations(saved);
                setIsInitialized(true);
            } catch (error) {
                console.error('Failed to initialize:', error);
            }
        };
        initialize();
	// Add error listener for IndexedDB
        const request = indexedDB.open('ZiyaDB');
        request.onerror = (event) => {
            const error = (event.target as IDBOpenDBRequest).error?.message || 'Unknown IndexedDB error';
            setDbError(error);
        };

	// Cleanup database connection on unmount
        return () => {
            if (db.db) {
                db.db.close();
            }
        };
    }, []);

    // Load conversations from storage on mount
    useEffect(() => {
        const loadSavedConversations = async () => {
            try {
                await db.init();
                console.log('Loading saved conversations...');
	  	const saved = await db.getConversations();
                if (saved.length > 0) {
                    console.debug('Loading saved conversations:', saved.length);
                    setConversations(saved);
                }
		console.log('Conversations loaded successfully:', saved);
            } catch (error) {
                console.error('Failed to load conversations:', error);
            }
        };
        loadSavedConversations();
    }, []);

    // Add effect to monitor database state
    useEffect(() => {
        const checkDatabaseState = () => {
            const dbRequest = indexedDB.open('ZiyaDB');
            dbRequest.onsuccess = () => {
                const db = dbRequest.result;
                console.log('Database state:', {
                    name: db.name,
                    version: db.version,
                    objectStoreNames: Array.from(db.objectStoreNames)
                });
            };
        };
        checkDatabaseState();
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
