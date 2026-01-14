import React, { useEffect, useState, useCallback, useMemo, useRef, useLayoutEffect, memo } from 'react';
import { Message } from '../utils/types';
import { useFolderContext } from "../context/FolderContext";
import { Tooltip, Spin, Progress, Typography, message, ProgressProps, Dropdown, Modal } from "antd";
import { debounce } from "lodash";
import { useTheme } from '../context/ThemeContext';
import { ModelSettings } from './ModelConfigModal';
import { useChatContext } from "../context/ChatContext";
import { CheckCircleOutlined, CloseCircleOutlined, DashboardOutlined } from '@ant-design/icons';

// Global request deduplication cache
const activeRequests = new Map<string, Promise<any>>();

const getTokenCount = async (text: string): Promise<number> => {
    // Create a cache key based on the text content
    const cacheKey = `token-count-${text.length}-${text.substring(0, 100)}`;
    
    // If there's already an active request for this text, return the same promise
    if (activeRequests.has(cacheKey)) {
        console.debug('Reusing existing token count request');
        return activeRequests.get(cacheKey);
    }
    
    try {
        const requestPromise = fetch('/api/token-count', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        }).then(async (response) => {
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Token count request failed');
            }
            const data = await response.json();
            return data.token_count;
        });
        
        // Cache the promise
        activeRequests.set(cacheKey, requestPromise);
        
        // Clean up cache when request completes
        requestPromise.finally(() => activeRequests.delete(cacheKey));
        
        return requestPromise;
    } catch (error) {
        // Clean up cache on error
        activeRequests.delete(cacheKey);
        message.error({
            content: error instanceof Error ? error.message : 'An unknown error occurred',
            duration: 5
        });
        console.error('Error getting token count:', error);
        return 0;
    }
};

export const TokenCountDisplay = memo(() => {
    const [containerWidth, setContainerWidth] = useState(0);

    const tokenCalculationTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const { folders, checkedKeys, getFolderTokenCount, accurateTokenCounts } = useFolderContext();
    const { currentMessages, currentConversationId, isStreaming, currentFolderId, folders: chatFolders, folderFileSelections } = useChatContext();
    const [totalTokenCount, setTotalTokenCount] = useState(0);
    const [chatTokenCount, setChatTokenCount] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const { isDarkMode } = useTheme();
    const lastMessageCount = useRef<number>(0);
    const containerRef = useRef<HTMLDivElement>(null);
    const lastMessageContent = useRef<string>('');
    const tokenDetailsRef = useRef<{ [key: string]: number }>({});
    const [modelLimits, setModelLimits] = useState<{
        token_limit: number | null;
        max_input_tokens: number;
        max_output_tokens: number;
    }>({ token_limit: null, max_input_tokens: 200000, max_output_tokens: 1024 });
    const modelCapabilitiesFetchRef = useRef<number>(0);
    const [astEnabled, setAstEnabled] = useState(false);
    const [astTokenCount, setAstTokenCount] = useState<number>(0);
    const [astResolutions, setAstResolutions] = useState<Record<string, any>>({});
    const [astResolutionsLoaded, setAstResolutionsLoaded] = useState(false);
    const [currentAstResolution, setCurrentAstResolution] = useState<string>('medium');
    const [astResolutionLoading, setAstResolutionLoading] = useState(false);
    const [mcpEnabled, setMcpEnabled] = useState(false);
    const [mcpTokenCount, setMcpTokenCount] = useState(0);
    const [mcpServerCount, setMcpServerCount] = useState(0);
    const builtinServerNames = ['time', 'shell'];

    const lastMuteSignatureRef = useRef<string>('');
    const lastTokenCalcRunRef = useRef<number>(0);
    const [cacheHealth, setCacheHealth] = useState<any>(null);
    const [showTelemetryModal, setShowTelemetryModal] = useState(false);
    
    const tokenLimit = modelLimits.max_input_tokens || modelLimits.token_limit || 4096;
    const warningThreshold = useMemo(() => Math.floor(tokenLimit * 0.7), [tokenLimit]);
    const dangerThreshold = useMemo(() => Math.floor(tokenLimit * 0.9), [tokenLimit]);

    // Create ref outside of the effect
    const fetchAttemptedRef = useRef(false);

    // Fetch AST resolutions data
    const fetchAstResolutions = useCallback(async () => {
        try {
            const response = await fetch('/api/ast/resolutions');
            if (response.ok) {
                const data = await response.json();
                console.log('Fetched AST resolutions:', data.resolutions);
                setAstResolutions(data.resolutions || {});
                setCurrentAstResolution(data.current_resolution || 'medium');
                setAstResolutionsLoaded(true);

                // Update token count with the current resolution's value
                const currentResolutionData = data.resolutions?.[data.current_resolution || 'medium'];
                if (currentResolutionData?.token_count !== undefined) {
                    setAstTokenCount(currentResolutionData.token_count);
                }

                return data;
            }
        } catch (error) {
            console.debug('Could not fetch AST resolutions:', error);
        }
        return null;
    }, []);

    // Check if AST is enabled and fetch initial data
    useEffect(() => {
        let isMounted = true;

        const checkAstEnabled = async () => {
            try {
                const response = await fetch('/api/ast/status');
                if (response.ok) {
                    const data = await response.json();
                    if (isMounted) {
                        setAstEnabled(data.enabled === true);

                        if (data.enabled === true) {
                            // Fetch resolution options first to get the correct token count
                            const resolutionData = await fetchAstResolutions();

                            // Only use the status token count if we don't have resolution data
                            if (!resolutionData && data.token_count !== undefined) {
                                setAstTokenCount(data.token_count);
                            }
                        }
                    }

                    // Set up polling if AST is indexing
                    if (isMounted && data.enabled === true && data.is_indexing && !data.is_complete) {
                        const pollForCompletion = setInterval(async () => {
                            try {
                                const pollResponse = await fetch('/api/ast/status');
                                if (pollResponse.ok) {
                                    const pollData = await pollResponse.json();
                                    if (isMounted && pollData.token_count !== undefined) {
                                        setAstTokenCount(pollData.token_count);
                                    }
                                    // Stop polling when indexing is complete
                                    if (!pollData.is_indexing || pollData.is_complete) {
                                        clearInterval(pollForCompletion);
                                    }
                                }
                            } catch (error) {
                                console.debug('Error polling AST status:', error);
                                clearInterval(pollForCompletion);
                            }
                        }, 3000); // Poll every 3 seconds

                        // Clean up interval after 5 minutes to prevent infinite polling
                        setTimeout(() => clearInterval(pollForCompletion), 300000);
                    }
                }
            } catch (error) {
                console.debug('Could not determine AST status:', error);
                if (isMounted) {
                    setAstEnabled(false);
                }
            }
        };

        checkAstEnabled();
    }, [fetchAstResolutions]);

    // Check MCP status and fetch token costs
    useEffect(() => {
        let isMounted = true;

        const checkMcpStatus = async () => {
            try {
                const response = await fetch('/api/mcp/status');
                if (response.ok) {
                    const data = await response.json();
                    if (isMounted) {
                        const isEnabled = data.initialized && !data.disabled;
                        setMcpEnabled(isEnabled);

                        if (isEnabled && data.token_costs) {
                            // Count non-builtin servers
                            const nonBuiltinServers = Object.keys(data.servers || {}).filter(
                                name => !builtinServerNames.includes(name)
                            );
                            const hasNonBuiltinServers = nonBuiltinServers.length > 0;

                            // Only show MCP tokens if there are non-builtin servers
                            if (hasNonBuiltinServers) {
                                setMcpTokenCount(data.token_costs.enabled_tool_tokens || 0);
                                setMcpServerCount(nonBuiltinServers.length);
                            } else {
                                setMcpTokenCount(0);
                                setMcpServerCount(0);
                            }
                        }
                    }
                }
            } catch (error) {
                console.debug('Could not fetch MCP status:', error);
            }
        };

        checkMcpStatus();

        return () => {
            isMounted = false;
        };
    }, []);

    const handleAstResolutionChange = useCallback(async (newResolution: string) => {
        setAstResolutionLoading(true);
        console.log('AST resolution change requested:', newResolution);
        try {
            // Update the token count immediately with the estimated value for instant feedback
            if (astResolutions[newResolution]) {
                setAstTokenCount(astResolutions[newResolution].token_count);
                setCurrentAstResolution(newResolution);
            }

            // Call the API to change resolution and trigger re-indexing
            const response = await fetch('/api/ast/change-resolution', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ resolution: newResolution }),
            });

            if (response.ok) {
                message.success(`AST resolution changed to ${newResolution}. Re-indexing in progress.`);
            } else {
                throw new Error('Failed to change AST resolution');
            }
        } catch (error) {
            message.error('Failed to change AST resolution');
            // Revert the UI changes on error
            if (astResolutions[currentAstResolution]) {
                setAstTokenCount(astResolutions[currentAstResolution].token_count);
            }
        } finally {
            setAstResolutionLoading(false);
        }
    }, [astResolutions, currentAstResolution]);
    // Create menu items for AST resolution dropdown
    const astMenuItems = useMemo(() => {
        console.log('Creating AST resolution menu, resolutions loaded:', astResolutionsLoaded, 'resolutions:', astResolutions);
        if (Object.keys(astResolutions).length === 0) return [];

        return Object.entries(astResolutions).map(([key, data]: [string, any]) => ({
            key,
            label: (
                <span style={{ display: 'flex', justifyContent: 'space-between', minWidth: 120 }}>
                    <span style={{ textTransform: 'capitalize' }}>{key}</span>
                    <span style={{ color: '#666', fontSize: '11px' }}>
                        {Math.round(data.token_count / 1000)}k
                    </span>
                </span>
            ),
        }));
    }, [astResolutions, astResolutionsLoaded]);

    // Fetch cache health periodically
    const fetchCacheHealth = useCallback(async () => {
        try {
            const response = await fetch('/api/telemetry/cache-health');
            const data = await response.json();
            setCacheHealth(data);
            
            // Show warning if cache is broken
            if (data.health_summary && !data.health_summary.cache_working) {
                console.warn('🚨 CACHE HEALTH: Cache is not working properly');
            }
        } catch (error) {
            console.debug('Error fetching cache health:', error);
        }
    }, []);

    const handleMenuClick = ({ key }: { key: string }) => {
        console.log('Menu item clicked:', key);
        handleAstResolutionChange(key);
    };

    // Monitor container width for responsive layout
    useLayoutEffect(() => {
        if (!containerRef.current) return;

        const updateWidth = () => {
            if (containerRef.current) {
                setContainerWidth(containerRef.current.offsetWidth);
            }
        };

        // Initial measurement
        updateWidth();

        // Set up resize observer
        const resizeObserver = new ResizeObserver(updateWidth);
        resizeObserver.observe(containerRef.current);

        return () => {
            if (containerRef.current) {
                resizeObserver.unobserve(containerRef.current);
            }
            resizeObserver.disconnect();
        };
    }, []);

    // One-time fetch of model capabilities
    useEffect(() => {
        // Use a ref to track if this component is mounted
        const abortController = new AbortController();
        const isMounted = { current: true };

        const fetchModelCapabilities = async () => {
            if (fetchAttemptedRef.current) return; // Only try once
            fetchAttemptedRef.current = true;

            try {
                const response = await fetch('/api/current-model', {
                    signal: abortController.signal
                });
                
                if (!response.ok) {
                    throw new Error('Failed to fetch current model settings');
                }

                // Only update state if component is still mounted
                if (!isMounted.current) return;

                const data = await response.json();
                const capabilities = data.capabilities || {};
                const settings = data.settings || {};

                // Add null checks to prevent errors
                setModelLimits({
                    token_limit: capabilities?.token_limit || 4096,
                    max_input_tokens: settings?.max_input_tokens || capabilities?.token_limit || 4096,
                    max_output_tokens: settings?.max_output_tokens || capabilities?.max_output_tokens || 1024
                });

                console.debug('Model limits updated:', { capabilities, settings });
            } catch (error) {
                if (error instanceof Error && error.name === 'AbortError') {
                    console.debug('Model capabilities fetch aborted');
                    return;
                }
                console.error('Failed to load model capabilities:', error);
            }
        };

        // Only fetch once when component mounts
        fetchModelCapabilities();

        // Cleanup function to prevent state updates after unmount
        return () => {
            isMounted.current = false;
            abortController.abort();
        };
    }, []);

    interface ModelSettingsEventDetail {
        settings?: ModelSettings;
        capabilities?: {
            token_limit: number;
            max_input_tokens: number;
            max_output_tokens: number;
        };
    }

    // Listen for model settings changes
    useEffect(() => {
        const handleModelSettingsChange = async (event: CustomEvent<ModelSettingsEventDetail>) => {
            // CRITICAL FIX: Prevent redundant fetches when switching conversations
            const now = Date.now();
            if (now - modelCapabilitiesFetchRef.current < 2000) {
                console.debug('🔒 DEDUP: Skipping redundant model capabilities fetch (within 2s cooldown)');
                return;
            }
            modelCapabilitiesFetchRef.current = now;
            // Debounce to prevent duplicate calls
            if (Date.now() - lastTokenCalcRunRef.current < 1000) return;
            lastTokenCalcRunRef.current = Date.now();
            
            console.log('TokenCountDisplay received modelSettingsChanged event:', {
                eventDetail: event.detail,
                hasSettings: !!event.detail?.settings,
                hasCapabilities: !!event.detail?.capabilities,
                currentLimits: modelLimits
            });

            try {

                if (!event.detail) {
                    // If no detail provided, fetch fresh data
                    const response = await fetch('/api/current-model');
                    if (!response.ok) throw new Error(`Failed to fetch model settings: ${response.status}`);
                    const data = await response.json();

                    // Use token_limit from capabilities if available, otherwise use max_input_tokens
                    const tokenLimit = data.capabilities?.token_limit || data.settings?.max_input_tokens || 4096;

                    setModelLimits({
                        token_limit: tokenLimit,
                        max_input_tokens: data.settings.max_input_tokens || tokenLimit,
                        max_output_tokens: data.settings.max_output_tokens
                    });
                    console.log('TokenCountDisplay updated limits from API call:', {
                        token_limit: data.capabilities.token_limit,
                        max_input_tokens: data.settings.max_input_tokens || data.capabilities.token_limit,
                        max_output_tokens: data.settings.max_output_tokens
                    });
                } else {
                    // Use provided data
                    if (event.detail.settings && event.detail.capabilities) {
                        const { settings, capabilities } = event.detail;

                        // Use token_limit from capabilities if available, otherwise use max_input_tokens
                        const tokenLimit = capabilities.token_limit || settings.max_input_tokens || 4096;

                        const newLimits = {
                            token_limit: tokenLimit,
                            max_input_tokens: settings.max_input_tokens || tokenLimit,
                            max_output_tokens: settings.max_output_tokens || capabilities.max_output_tokens
                        };
                        console.log('TokenCountDisplay updating limits from event:', newLimits);
                        setModelLimits(newLimits);

                        // Force a re-render by updating state
                        setTotalTokenCount(prev => {
                            console.log('Forcing token count re-render');
                            return prev;
                        });
                    } else {
                        throw new Error('Missing settings or capabilities in event data');
                    }
                }
            } catch (error) {
                console.error('Error updating token limits:', error);
            }
        };

        window.addEventListener('modelSettingsChanged', handleModelSettingsChange as unknown as EventListener);

        return () => {
            window.removeEventListener('modelSettingsChanged', handleModelSettingsChange as unknown as EventListener);
        };
    }, [modelLimits]);

    const combinedTokenCount = totalTokenCount + chatTokenCount + (astEnabled ? astTokenCount : 0) + (mcpEnabled && mcpServerCount > 0 ? mcpTokenCount : 0);

  // Optimized token calculation with better debouncing
    const performTokenCalculation = useCallback(() => {
        // Helper to recursively calculate total tokens for a folder, using accurate counts when available
        // This matches the logic used by the file explorer tree
        const calculateFolderTotal = (path: string, folderData: any): number => {
            if (!folderData) return 0;
            
            // Navigate to the folder in the structure
            let current = folderData;
            const parts = path.split('/').filter(p => p.length > 0);
            
            for (const part of parts) {
                if (!current || !current[part]) {
                    return 0;
                }
                current = current[part];
                
                // If not the last part, descend into children
                if (parts.indexOf(part) < parts.length - 1) {
                    current = current.children;
                    if (!current) return 0;
                }
            }
            
            // If it's a file, use accurate count if available
            if (!current.children) {
                const accurateData = accurateTokenCounts[path];
                return (accurateData && accurateData.count > 0) ? accurateData.count : (current.token_count || 0);
            }
            
            // For directories, recursively sum all children using accurate counts
            let total = 0;
            const children = current.children || {};
            
            for (const [childName, childNode] of Object.entries(children) as [string, any][]) {
                const childPath = path ? `${path}/${childName}` : childName;
                
                if (childNode.children) {
                    // Subdirectory - recurse
                    total += calculateFolderTotal(childPath, folderData);
                } else {
                    // File - use accurate count if available, skip tool-backed (-1)
                    const accurateData = accurateTokenCounts[childPath];
                    const fileTokens = (accurateData && accurateData.count > 0) ? accurateData.count : (childNode.token_count || 0);
                    if (fileTokens > 0) {
                        total += fileTokens;
                    }
                }
            }
            
            return total;
        };
        
        console.log('Token calculation triggered');
        if (!folders) return;

        if (folders && checkedKeys.length > 0) {
            // Check if we're in a folder with folder-specific file selections
            const currentFolder = currentFolderId ? chatFolders.find(f => f.id === currentFolderId) : null;

            let total = 0;
      
      // Use a more efficient calculation approach
      const checkedSet = new Set(checkedKeys.map(String));
            const details: { [key: string]: number } = {};
            let accurateFileCount = 0;
            
            // Helper to check if a path's parent is already checked (to avoid double-counting)
            const hasCheckedParent = (path: string): boolean => {
                const parts = path.split('/');
                for (let i = parts.length - 1; i > 0; i--) {
                    const parentPath = parts.slice(0, i).join('/');
                    if (checkedSet.has(parentPath)) {
                        return true;
                    }
                }
                return false;
            };
            
            // Use getFolderTokenCount for each checked path
            checkedKeys.forEach(key => {
                const path = String(key);
                if (!folders) {
                    tokenDetailsRef.current = {};
                    return;
                }
                
                // Skip this path if its parent is already checked
                if (hasCheckedParent(path)) {
                    return;
                }
                
                // Use recursive calculation that matches file explorer logic
                let tokens = calculateFolderTotal(path, folders);
                
                // Skip tool-backed files (marked as -1) from total count
                if (tokens === -1) {
                    details[path] = -1; // Mark but don't add to total
                } else if (tokens > 0) {
                    details[path] = tokens;
                    total += tokens;
                }
            });
            // Only log token details when debugging specific issues
            // console.debug('Token count details:', details);

            tokenDetailsRef.current = details;
            setTotalTokenCount(total);
            
            // Log accuracy info
            if (accurateFileCount > 0 && accurateFileCount % 5 === 0) {
                console.log(`Using accurate token counts for ${accurateFileCount} files`);
            }
        } else {
            tokenDetailsRef.current = {};
            setTotalTokenCount(0);
        }
    }, [checkedKeys, folders, getFolderTokenCount, currentFolderId, chatFolders, folderFileSelections, accurateTokenCounts]);

    const tokenCalculationEffect = useCallback(() => {
        // Clear any existing timeout
        if (tokenCalculationTimeoutRef.current) {
            clearTimeout(tokenCalculationTimeoutRef.current);
        }
        
        // Schedule calculation for later - don't block UI
        tokenCalculationTimeoutRef.current = setTimeout(() => {
            performTokenCalculation();
        }, 500); // Shorter delay but still batched
    }, [performTokenCalculation]);

    useEffect(() => {
        tokenCalculationEffect();
    }, [tokenCalculationEffect]);

    const getTokenColor = (count: number): string => {
        if (count >= dangerThreshold) return '#ff4d4f';  // Red
        if (count >= warningThreshold) return '#faad14'; // Orange
        return '#52c41a';  // Green
    };

    const getTokenStyle = (count: number) => ({
        color: getTokenColor(count),
        fontWeight: 'bold'
    });

    const currentConversationRef = useRef<string>('');
    const previousMessagesRef = useRef<string>('');
    const hasMessagesChanged = useCallback((messages: Message[]) => {
        // Include mute state in change detection to catch mute/unmute actions
        const activeMessages = messages.filter(msg => msg.muted !== true);
        const muteStates = messages.map(msg => msg.muted || false).join(',');
        const messagesContent = activeMessages.length > 0 ? activeMessages.map(msg => msg.content).join('\n') : '';
        const messageSignature = `${messages.length}:${activeMessages.length}:${muteStates}:${messagesContent}`;

        // Reset the previous signature when conversation changes
        if (currentConversationId !== currentConversationRef.current) {
            currentConversationRef.current = currentConversationId;
        }

        if (messageSignature !== previousMessagesRef.current) {
            previousMessagesRef.current = messageSignature;
            console.debug('Messages changed:', { length: messages.length, content: messagesContent.slice(0, 100) });
            return true;
        }
        return false;
    }, [previousMessagesRef, currentConversationId]);

    const updateChatTokens = useCallback(async () => {
        if (!currentMessages || currentMessages.length === 0) {
            setChatTokenCount(0);
            lastMessageCount.current = 0;
            lastMessageContent.current = '';
            previousMessagesRef.current = '';
            console.debug('Skipping token count update - no messages');
            setIsLoading(false);
            return;
        }

        setIsLoading(true);
        try {
            // Only count tokens for non-muted messages
            const allText = currentMessages.filter(msg => msg.muted !== true).map(msg => msg.content).join('\n');
            console.debug('Token count calculation:', { totalMessages: currentMessages.length, activeMessages: currentMessages.filter(msg => msg.muted !== true).length });
            const tokens = await getTokenCount(allText);
            setChatTokenCount(tokens);
            lastMessageCount.current = currentMessages.length;
            lastMessageContent.current = allText;
        } catch (error) {
            console.error('Failed to get token count:', error);
            setChatTokenCount(0);
        } finally {
            setIsLoading(false);
        }
    }, [currentMessages]);

    // update chat tokens only when messages or conversation change
    useEffect(() => {
        // Only log in development mode and not for routine checks
        if (process.env.NODE_ENV === 'development' &&
            (currentMessages.length > 0 || hasMessagesChanged(currentMessages))) {
            console.debug('Conversation or messages changed:', {
                conversationId: currentConversationId
            });
        }

        if (!currentConversationId || currentMessages.length === 0) {
            // Silently reset token count without logging
            setChatTokenCount(0);

            lastMessageCount.current = 0;
            lastMessageContent.current = '';
            previousMessagesRef.current = '';
            return;
        }

        // Only update tokens if we have messages
        if (hasMessagesChanged(currentMessages)) {
            console.debug('Updating chat tokens for conversation:', currentConversationId);
            updateChatTokens();
        }
    }, [currentMessages, updateChatTokens, currentConversationId, hasMessagesChanged, isStreaming]);

    // Listen for mute changes with updated messages
    useEffect(() => {
        const handleMuteChange = (event: CustomEvent) => {
            if (event.detail?.conversationId === currentConversationId && event.detail?.updatedMessages) {
                console.log('Token counter: Received updated messages from mute event');
                // Calculate tokens directly from the updated messages
                const updatedMessages = event.detail.updatedMessages;
                const allText = updatedMessages.filter(msg => msg.muted !== true).map(msg => msg.content).join('\n');
                getTokenCount(allText).then(tokens => setChatTokenCount(tokens));
            }
        };
        
        window.addEventListener('messagesMutedChanged', handleMuteChange as EventListener);
        return () => window.removeEventListener('messagesMutedChanged', handleMuteChange as EventListener);
    }, [currentConversationId]);
    // Listen for conversation updates (including mute changes)
    useEffect(() => {
        const handleConversationUpdate = () => {
            if (currentMessages && currentMessages.length > 0) {
                updateChatTokens();
            }
        };
        
        window.addEventListener('conversationsUpdated', handleConversationUpdate);
        return () => window.removeEventListener('conversationsUpdated', handleConversationUpdate);
    }, [currentMessages, updateChatTokens]);

    // Instead of listening to events, just recalculate whenever messages change
    // This ensures we always use the current muted state
    useEffect(() => {
        if (currentMessages && currentMessages.length > 0) {
            const currentSignature = currentMessages.map(m => `⟨MATH_INLINE:{m.id}:⟩{m.muted || false}`).join('|');
            if (currentSignature !== lastMuteSignatureRef.current) {
                lastMuteSignatureRef.current = currentSignature;
                updateChatTokens();
            }
        }
    }, [currentMessages, updateChatTokens]);

    const getProgressStatus = (count: number): ProgressProps['status'] => {
        if (count >= dangerThreshold) return 'exception';
        if (count >= warningThreshold) return 'normal';
        return 'success';
    };

    // Determine if we should show the detailed total format
    const showDetailedTotal = containerWidth > 350;

    // Helper to get file token display with accuracy indicator
    const getFileTokenDisplay = () => {
        const accurateCount = Object.keys(accurateTokenCounts).length;
        
        // Check if we have any tool-backed files (marked as -1)
        const hasToolBackedFiles = Object.values(tokenDetailsRef.current).some(count => count === -1);
        
        const selectedFiles = checkedKeys.filter(key => {
            const keyStr = String(key);
            return keyStr.includes('.') && !keyStr.endsWith('/') && 
                   keyStr.split('/').pop()?.includes('.');
        }).length;
        
        const isFullyAccurate = selectedFiles > 0 && accurateCount === selectedFiles;
        const hasAnyAccurate = accurateCount > 0;
        
        return `${totalTokenCount.toLocaleString()}${hasToolBackedFiles ? '(*)' : ''}${isFullyAccurate ? '✓' : (hasAnyAccurate ? '~' : '')}`;
    };

    // Build breakdown items (Files, MCP, AST, Chat)
    const breakdownItems: React.ReactElement[] = [];
    
    // Always show Files
    breakdownItems.push(
        <Tooltip key="files" title="Tokens from selected files">
            <span style={{ fontSize: '10px' }}>
                Files: <span style={getTokenStyle(totalTokenCount)}>{getFileTokenDisplay()}</span>
            </span>
        </Tooltip>
    );
    
    // Add MCP if enabled and has non-builtin servers
    if (mcpEnabled && mcpServerCount > 0) {
        breakdownItems.push(
            <Tooltip key="mcp" title={`MCP tool tokens from ${mcpServerCount} server${mcpServerCount !== 1 ? 's' : ''}`}>
                <span style={{ fontSize: '10px' }}>
                    MCP: <span style={getTokenStyle(mcpTokenCount)}>{mcpTokenCount.toLocaleString()}</span>
                </span>
            </Tooltip>
        );
    }
    
    // Add AST if enabled
    if (astEnabled) {
        const astComponent = astResolutionsLoaded ? (
            <Dropdown
                menu={{ items: astMenuItems, onClick: handleMenuClick }}
                trigger={['click']}
                disabled={astResolutionLoading}
            >
                <span style={{
                    fontSize: '10px',
                    cursor: 'pointer',
                    userSelect: 'none',
                    opacity: astResolutionLoading ? 0.6 : 1,
                    padding: '2px 4px',
                    borderRadius: '2px',
                    border: '1px solid transparent',
                    display: 'inline-block'
                }}
                    onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = 'rgba(0,0,0,0.05)';
                        e.currentTarget.style.borderColor = '#d9d9d9';
                    }}
                    onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = 'transparent';
                        e.currentTarget.style.borderColor = 'transparent';
                    }}
                >
                    AST: <span style={getTokenStyle(astTokenCount)}>{astTokenCount.toLocaleString()}</span>
                </span>
            </Dropdown>
        ) : (
            <span style={{ fontSize: '10px', opacity: 0.6 }}>
                AST: <span style={getTokenStyle(astTokenCount)}>
                    {astTokenCount.toLocaleString()}
                </span>
                {astResolutionLoading && <span style={{ marginLeft: '4px' }}>⟳</span>}
            </span>
        );
        
        breakdownItems.push(
            <Tooltip key="ast" title="AST tokens - click to change resolution">
                {astComponent}
            </Tooltip>
        );
    }
    
    // Always show Chat
    breakdownItems.push(
        <Tooltip key="chat" title="Tokens from chat history">
            <span style={{ fontSize: '10px' }}>
                Chat: <span style={getTokenStyle(chatTokenCount)}>{chatTokenCount.toLocaleString()}</span>
            </span>
        </Tooltip>
    );

    // Only refresh cache health when modal is open
    useEffect(() => {
        if (!showTelemetryModal) return;
        
        // Fetch immediately when modal opens
        fetchCacheHealth();
        
        // Then refresh every 10 seconds while modal is open
        const interval = setInterval(fetchCacheHealth, 10000);
        
        return () => clearInterval(interval);
    }, [fetchCacheHealth, showTelemetryModal]);

    // Cache health indicator
    const cacheHealthIndicator = cacheHealth && (
        <Tooltip title={
            cacheHealth.health_summary?.cache_working ?
                `Cache Working: ${cacheHealth.global_stats?.overall_cache_efficiency?.toFixed(1)}% efficiency` :
                `⚠️ Cache Issues Detected (${cacheHealth.health_summary?.issues_detected} conversations)`
        }>
            {cacheHealth.health_summary?.cache_working ? (
                <CheckCircleOutlined style={{ marginLeft: '8px', color: '#52c41a', cursor: 'pointer' }} 
                    onClick={() => setShowTelemetryModal(true)} />
            ) : (
                <CloseCircleOutlined style={{ 
                    marginLeft: '8px', 
                    color: '#ff4d4f', 
                    cursor: 'pointer',
                    animation: 'pulse 2s infinite'
                }} 
                    onClick={() => setShowTelemetryModal(true)} />
            )}
        </Tooltip>
    );

    const tokenDisplay = useMemo(() => (
        <div ref={containerRef} className="token-summary" style={{
            backgroundColor: 'inherit',
            padding: '4px',
            borderBottom: '1px solid',
            borderBottomColor: isDarkMode ? '#303030' : '#e8e8e8',
            transition: 'all 0.3s ease',
            minHeight: '70px',
            boxSizing: 'border-box',
            position: 'relative'
        }}>
            {isLoading && (
                <div style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    background: 'rgba(0, 0, 0, 0.1)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    zIndex: 1,
                    transition: 'all 0.3s ease'
                }}>
                    <Spin size="small" />
                </div>
            )}
            <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
                opacity: isLoading ? 0.5 : 1,
                transition: 'opacity 0.3s ease'
            }}>
                {/* Header row with Token Estimate and Total */}
                <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: '2px'
                }}>
                    <Typography.Text strong style={{ fontSize: '12px' }}>
                        Token Estimate
                    </Typography.Text>
                    <Tooltip title={`${combinedTokenCount.toLocaleString()} of ${tokenLimit.toLocaleString()} tokens (${Math.round((combinedTokenCount / tokenLimit) * 100)}%)`}>
                        <span style={{ fontSize: '11px' }}>
                            {containerWidth > 180 && 'Total: '}
                            <span style={getTokenStyle(combinedTokenCount)}>
                                {containerWidth > 250
                                    ? `${combinedTokenCount.toLocaleString()} / ${tokenLimit.toLocaleString()}`
                                    : combinedTokenCount.toLocaleString()}
                            </span>
                        </span>
                    </Tooltip>
                </div>
                
                {/* Breakdown row */}
                <div style={{
                    display: 'flex',
                    gap: '8px',
                    flexWrap: 'wrap',
                    fontSize: '10px'
                }}>
                    {breakdownItems}
                </div>
                
                <Tooltip title={`${combinedTokenCount.toLocaleString()} of ${tokenLimit.toLocaleString()} maximum input tokens used`} mouseEnterDelay={0.5}>
                    <div>
                        <Progress
                            percent={Math.min(100, Math.max(0, Math.round((combinedTokenCount / tokenLimit) * 100)))}
                            size="small"
                            status={getProgressStatus(combinedTokenCount)}
                            showInfo={false}
                            strokeWidth={4}
                            style={{ margin: '4px 0', transition: 'all 0.3s ease' }}
                        />
                    </div>
                </Tooltip>
            </div>
        </div>
    ), [isLoading, combinedTokenCount, getProgressStatus, isDarkMode, breakdownItems, tokenLimit, containerWidth]);

    return (
        <>
            <div className="token-display-container">
            <div className="token-display" style={{ padding: '0 8px' }}>
                {tokenDisplay}
                
                {/* Add cache health and telemetry controls */}
                <div style={{ 
                    display: 'flex', 
                    justifyContent: 'flex-end', 
                    alignItems: 'center',
                    padding: '4px 8px',
                    gap: '8px'
                }}>
                    {cacheHealthIndicator}
                    
                    <Tooltip title="Open Telemetry Dashboard">
                        <DashboardOutlined 
                            style={{ cursor: 'pointer', color: '#1890ff', fontSize: '14px' }}
                            onClick={() => setShowTelemetryModal(true)}
                        />
                    </Tooltip>
                </div>
            </div>
            
            {/* Telemetry Modal */}
            <Modal
                title="Cache & Throttling Telemetry"
                open={showTelemetryModal}
                onCancel={() => setShowTelemetryModal(false)}
                width={1200}
                footer={null}
                styles={{
                    body: { 
                        maxHeight: '70vh', 
                        overflow: 'auto',
                        backgroundColor: isDarkMode ? '#141414' : '#ffffff'
                    }
                }}
            >
                {cacheHealth && (
                    <div>
                        {/* Health Alert */}
                        {!cacheHealth.health_summary?.cache_working && (
                            <div style={{
                                backgroundColor: '#fff1f0',
                                border: '1px solid #ffa39e',
                                borderRadius: '4px',
                                padding: '12px',
                                marginBottom: '16px'
                            }}>
                                <div style={{ fontWeight: 'bold', color: '#cf1322', marginBottom: '8px' }}>
                                    🚨 Cache Issues Detected
                                </div>
                                <div style={{ fontSize: '13px', color: '#434343' }}>
                                    {cacheHealth.health_summary.issues_detected} conversation(s) with cache problems. 
                                    Caching may be disabled or broken, leading to increased throttling.
                                </div>
                            </div>
                        )}

                        {/* Global Stats Grid */}
                        <div style={{ 
                            display: 'grid', 
                            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                            gap: '16px',
                            marginBottom: '24px'
                        }}>
                            <div style={{ 
                                padding: '16px', 
                                backgroundColor: isDarkMode ? '#1f1f1f' : '#fafafa',
                                borderRadius: '4px'
                            }}>
                                <div style={{ fontSize: '12px', color: '#8c8c8c' }}>Cache Efficiency</div>
                                <div style={{ 
                                    fontSize: '24px', 
                                    fontWeight: 'bold',
                                    color: cacheHealth.global_stats?.overall_cache_efficiency > 50 ? '#52c41a' : '#ff4d4f'
                                }}>
                                    {cacheHealth.global_stats?.overall_cache_efficiency?.toFixed(1)}%
                                </div>
                            </div>

                            <div style={{ 
                                padding: '16px', 
                                backgroundColor: isDarkMode ? '#1f1f1f' : '#fafafa',
                                borderRadius: '4px'
                            }}>
                                <div style={{ fontSize: '12px', color: '#8c8c8c' }}>Cost Savings</div>
                                <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#52c41a' }}>
                                    {cacheHealth.global_stats?.estimated_cost_savings_pct?.toFixed(1)}%
                                </div>
                                <div style={{ fontSize: '11px', color: '#8c8c8c', marginTop: '4px' }}>
                                    {cacheHealth.global_stats?.total_cached_tokens?.toLocaleString()} tokens cached
                                </div>
                            </div>

                            <div style={{ 
                                padding: '16px', 
                                backgroundColor: isDarkMode ? '#1f1f1f' : '#fafafa',
                                borderRadius: '4px'
                            }}>
                                <div style={{ fontSize: '12px', color: '#8c8c8c' }}>Throttle Events</div>
                                <div style={{ 
                                    fontSize: '24px', 
                                    fontWeight: 'bold',
                                    color: cacheHealth.health_summary?.throttle_pressure === 'high' ? '#ff4d4f' :
                                           cacheHealth.health_summary?.throttle_pressure === 'medium' ? '#faad14' : '#52c41a'
                                }}>
                                    {cacheHealth.global_stats?.total_throttle_events}
                                </div>
                                <div style={{ fontSize: '11px', color: '#8c8c8c', marginTop: '4px' }}>
                                    Pressure: {cacheHealth.health_summary?.throttle_pressure?.toUpperCase()}
                                </div>
                            </div>
                        </div>

                        {/* Recent Conversations Table */}
                        <div style={{ marginTop: '24px' }}>
                            <h3>Recent Conversations</h3>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                                <thead>
                                    <tr style={{ borderBottom: '2px solid #d9d9d9' }}>
                                        <th style={{ padding: '8px', textAlign: 'left' }}>ID</th>
                                        <th style={{ padding: '8px', textAlign: 'right' }}>Iterations</th>
                                        <th style={{ padding: '8px', textAlign: 'right' }}>Fresh</th>
                                        <th style={{ padding: '8px', textAlign: 'right' }}>Cached</th>
                                        <th style={{ padding: '8px', textAlign: 'center' }}>Efficiency</th>
                                        <th style={{ padding: '8px', textAlign: 'center' }}>Throttles</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {cacheHealth.conversations?.slice(0, 10).map((conv: any) => (
                                        <tr key={conv.conversation_id} style={{ 
                                            borderBottom: '1px solid #f0f0f0',
                                            backgroundColor: conv.has_cache_issue ? 'rgba(255, 77, 79, 0.05)' : 'transparent'
                                        }}>
                                            <td style={{ padding: '8px' }}>
                                                <code style={{ fontSize: '10px' }}>
                                                    {conv.conversation_id.substring(0, 12)}...
                                                </code>
                                            </td>
                                            <td style={{ padding: '8px', textAlign: 'right' }}>{conv.iteration_count}</td>
                                            <td style={{ padding: '8px', textAlign: 'right' }}>
                                                {conv.fresh_tokens.toLocaleString()}
                                            </td>
                                            <td style={{ padding: '8px', textAlign: 'right', color: conv.cached_tokens > 0 ? '#52c41a' : '#ff4d4f' }}>
                                                {conv.cached_tokens.toLocaleString()}
                                                {conv.has_cache_issue && (
                                                    <span style={{ marginLeft: '4px' }}>⚠️</span>
                                                )}
                                            </td>
                                            <td style={{ padding: '8px', textAlign: 'center' }}>
                                                <span style={{ 
                                                    color: conv.cache_efficiency > 50 ? '#52c41a' : 
                                                           conv.cache_efficiency > 20 ? '#faad14' : '#ff4d4f'
                                                }}>
                                                    {conv.cache_efficiency.toFixed(1)}%
                                                </span>
                                            </td>
                                            <td style={{ padding: '8px', textAlign: 'center' }}>
                                                <span style={{ 
                                                    color: conv.throttle_count === 0 ? '#52c41a' : 
                                                           conv.throttle_count < 3 ? '#faad14' : '#ff4d4f'
                                                }}>
                                                    {conv.throttle_count > 0 ? `${conv.throttle_count}x` : '✓'}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </Modal>
            
            <style>{`
                @keyframes pulse {
                    0%, 100% { opacity: 1; }
                    50% { opacity: 0.5; }
                }
            `}</style>
        </div>
        </>
    );
});
