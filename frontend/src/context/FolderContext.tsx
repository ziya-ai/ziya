import React, { createContext, ReactNode, useContext, useCallback, useEffect, useState, useLayoutEffect, useRef, useMemo } from 'react';
import { Folders } from "../utils/types";
import { message } from 'antd';
import { convertToTreeData, insertIntoFolders, updateTokenInFolders, removeFromFolders } from "../utils/folderUtil";
import { useChatContext } from "./ChatContext";
import { TreeDataNode } from "antd";
import { debounce } from "../utils/debounce";
import { useConfig } from "./ConfigContext";
import { useProject } from "./ProjectContext";
import { getTabState, setTabState } from '../utils/tabState';

export interface FolderContextType {
  folders: Folders | undefined;
  treeData: TreeDataNode[];
  checkedKeys: React.Key[];
  setTreeData: React.Dispatch<React.SetStateAction<TreeDataNode[]>>;
  setCheckedKeys: React.Dispatch<React.SetStateAction<React.Key[]>>;
  searchValue: string;
  setSearchValue: React.Dispatch<React.SetStateAction<string>>;
  expandedKeys: React.Key[];
  setExpandedKeys: React.Dispatch<React.SetStateAction<React.Key[]>>;
  // New scanning state
  isScanning: boolean;
  scanProgress: {
    directories: number;
    files: number;
    elapsed: number;
  } | null;
  scanError: string | null;
  getFolderTokenCount: (path: string, folderData: Folders) => number;
  accurateTokenCounts: Record<string, { count: number; timestamp: number }>;
  addFilesToContext: (filePaths: string[]) => Promise<void>;
}

const FolderContext = createContext<FolderContextType | undefined>(undefined);

export const FolderProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const renderStart = useRef(performance.now());
  const renderCount = useRef(0);
  const { isEphemeralMode } = useConfig();
  const ephemeralInitialized = useRef(false);
  const [folders, setFolders] = useState<Folders>();
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<React.Key[]>(() => {
    try {
      const saved = getTabState('ZIYA_CHECKED_FOLDERS');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const [searchValue, setSearchValue] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => {
    try {
      const saved = getTabState('ZIYA_EXPANDED_FOLDERS');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const [isScanning, setIsScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<{ directories: number; files: number; elapsed: number } | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const scanTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const progressIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const [forceRefreshCounter, setForceRefreshCounter] = useState(0);
  const [accurateTokenCounts, setAccurateTokenCounts] = useState<Record<string, { count: number; timestamp: number }>>({});
  const accurateCountTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastProcessedSelectionRef = useRef<string>('');
  const fileTreeWsRef = useRef<WebSocket | null>(null);

  // Monitor FolderProvider render performance
  // Get current project from ProjectContext
  const { currentProject } = useProject();
  // Remove performance monitoring that's causing overhead
  
  // Create ref to avoid stale closures in async callbacks
  const currentProjectRef = useRef(currentProject);
  currentProjectRef.current = currentProject;

  // CRITICAL: Clear persisted folder selections when ephemeral mode is detected
  useEffect(() => {
    if (isEphemeralMode && !ephemeralInitialized.current) {
      console.log('🔒 EPHEMERAL: Clearing persisted folder selections');
      ephemeralInitialized.current = true;

      // Clear all folder-related localStorage
      try {
        sessionStorage.removeItem('ZIYA_CHECKED_FOLDERS');
        sessionStorage.removeItem('ZIYA_EXPANDED_FOLDERS');

        // Also clear the state immediately
        setCheckedKeys([]);
        setExpandedKeys([]);

        console.log('✅ EPHEMERAL: Folder state cleared');
      } catch (e) {
        console.error('Failed to clear folder state in ephemeral mode:', e);
      }
    }
  }, [currentProject]);
  
  const cleanupCheckedKeys = useCallback(async () => {
    if (!folders || checkedKeys.length === 0) return;

    // Use ref to get the LATEST project path (prevents stale closures)
    const projectPath = currentProjectRef.current?.path;
    
    // Debug: log what we're actually using vs what we have
    console.log('🔍 CLEANUP_DEBUG:', {
      refPath: projectPath,
      directPath: currentProject?.path,
      refId: currentProjectRef.current?.id,
      directId: currentProject?.id
    });
    if (!projectPath) {
      console.warn('🧹 CLEANUP: No valid project path available, skipping validation');
      return;
    }
    
    console.log('🔍 CLEANUP: Validating', checkedKeys.length, 'files against project:', projectPath);

    try {
      const response = await fetch('/api/files/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          files: checkedKeys.map(String),
          projectRoot: projectPath
        })
      });

      if (response.ok) {
        const { existingFiles } = await response.json();
        const existingSet = new Set(existingFiles);

        // Filter out non-existent files
        const cleanedKeys = checkedKeys.filter(key => existingSet.has(String(key)));

        if (cleanedKeys.length !== checkedKeys.length) {
          console.log(`🧹 CLEANUP: Removed ${checkedKeys.length - cleanedKeys.length} non-existent files from selection`);
          setCheckedKeys(cleanedKeys);
        }
      }
    } catch (error) {
      console.warn('Failed to cleanup checked keys:', error);
    }
  }, [folders, checkedKeys, setCheckedKeys, currentProject]);

  // Run cleanup when folders are loaded
  useEffect(() => {
    if (folders && checkedKeys.length > 0) {
      // Debounce cleanup to avoid excessive API calls
      const timeoutId = setTimeout(cleanupCheckedKeys, 2000);
      return () => clearTimeout(timeoutId);
    }
  }, [folders, cleanupCheckedKeys]);

  // Listen for file selection restoration after project switches
  useEffect(() => {
    const handleRestoreSelections = (event: CustomEvent) => {
      const { projectId, selections } = event.detail;

      // Only restore if this is for the current project
      if (currentProject?.id === projectId && selections && Array.isArray(selections)) {
        console.log(`📂 FolderContext: Restoring ${selections.length} file selections for project ${projectId}`);
        setCheckedKeys(selections);
      }
    };

    window.addEventListener('restoreProjectFileSelections', handleRestoreSelections as EventListener);
    return () => {
      window.removeEventListener('restoreProjectFileSelections', handleRestoreSelections as EventListener);
    };
  }, [currentProject?.id]);

  const getFolderTokenCount = useCallback((path: string, folderData: Folders | undefined): number => {
    if (!folderData) {
      // console.warn(`getFolderTokenCount: folderData is undefined for path "${path}"`);
      return 0;
    }

    let current: Folders | undefined = folderData;
    const parts = path.split('/');

    for (const part of parts) {
      if (!current) {
        break;
      }
      const node = current[part];
      if (node) {
        if (parts.indexOf(part) === parts.length - 1) { // Last part of the path
          return node.token_count || 0;
        }
        current = node.children;
      } else {
        // console.warn(`getFolderTokenCount: Path segment "${part}" not found in current node for path "${path}".`);
        return 0; // Path segment not found
      }
    }

    return 0;
  }, []);

  // Function to get accurate token counts for selected files
  const getAccurateTokenCounts = useMemo(() => debounce(async (filePaths: string[]) => {
    // More aggressive limits for large repositories
    if (filePaths.length > 100) {
      console.warn(`Limiting token count batch from ${filePaths.length} to 100 files`);
      filePaths = filePaths.slice(0, 100);
    }
    if (filePaths.length === 0 || filePaths.length > 50) return;

    // Filter out files we already have recent accurate counts for (within 5 minutes)
    const now = Date.now() / 1000;
    const filesToUpdate = filePaths.filter(path => {
      const existing = accurateTokenCounts[path];
      return !existing || (now - existing.timestamp) > 300; // 5 minutes
    });

    if (filesToUpdate.length === 0) return;

    try {
      console.log(`Making API request for accurate token counts: ${filesToUpdate.length} files`, filesToUpdate);
      console.log(`Getting accurate token counts for ${filesToUpdate.length} files (batch)`);
      const response = await fetch('/api/accurate-token-count', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_paths: filesToUpdate }),
      });

      if (!response.ok) {
        throw new Error(`Failed to get accurate token counts: ${response.status}`);
      }

      const data = await response.json();
      console.log('Received accurate token counts:', data);
      if (data.results) {
        setAccurateTokenCounts(prev => {
          const updated = { ...prev };
          const counts: Record<string, number> = {};
          Object.entries(data.results).forEach(([path, result]: [string, any]) => {
            if (result.accurate_count !== undefined) {
              updated[path] = {
                count: result.accurate_count,
                timestamp: result.timestamp
              };
              counts[path] = result.accurate_count;
            }
          });
          console.debug('Updated accurate counts:', counts);

          // Force a refresh of components that depend on token counts
          setForceRefreshCounter(prev => prev + 1);

          return updated;
        });

        // Dispatch update event without forcing tree data changes
        requestAnimationFrame(() => {
          const event = new CustomEvent('accurateTokenCountsUpdated', {
            detail: { updatedPaths: Object.keys(data.results) }
          });
          window.dispatchEvent(event);
        });

      }

    } catch (error) {
      console.error('Error getting accurate token counts:', error);
    }
  }, 3000), []); // Further increased debounce time for large repos

  // Debounced function to get accurate counts
  const debouncedGetAccurateCounts = useCallback(
    debounce((filePaths: string[]) => {
      getAccurateTokenCounts(filePaths);
    }, 1000), // Wait 1 second after selection changes
    [getAccurateTokenCounts]
  );

  // Save expanded folders whenever they change
  useEffect(() => {
    try {
      setTabState('ZIYA_EXPANDED_FOLDERS', JSON.stringify(Array.from(expandedKeys)));
    } catch (error) {
      console.warn('Failed to save expanded folders to localStorage (QuotaExceeded?):', error);
    }
  }, [expandedKeys]);

  // Save checked folders whenever they change
  useEffect(() => {
    try {
      setTabState('ZIYA_CHECKED_FOLDERS', JSON.stringify(Array.from(checkedKeys)));
    } catch (error) {
      console.warn('Failed to save checked folders to localStorage (QuotaExceeded?):', error);
    }
  }, [checkedKeys]);

  // Update dynamic tools when file selection changes
  useEffect(() => {
    const updateDynamicTools = async () => {
      if (!checkedKeys || checkedKeys.length === 0) return;

      try {
        const response = await fetch('/api/dynamic-tools/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ files: checkedKeys.map(String) })
        });

        if (response.ok) {
          const data = await response.json();
          console.log('Dynamic tools updated:', data);
        }
      } catch (error) {
        console.debug('Failed to update dynamic tools:', error);
      }
    };

    // Debounce the update to avoid excessive calls
    const timeoutId = setTimeout(updateDynamicTools, 1000);
    return () => clearTimeout(timeoutId);
  }, [checkedKeys]);

  // Get accurate token counts for selected files
  const updateAccurateTokens = useCallback((checkedKeys) => {
    console.log('updateAccurateTokens called with:', checkedKeys.length, 'keys');
    debouncedGetAccurateCounts(checkedKeys);
  }, []);

  const debouncedUpdateAccurateTokens = useCallback(debounce((checkedKeys) => {
    // Add much more aggressive throttling
    if (!folders || checkedKeys.length === 0) return;

    // Check if selection actually changed
    const selectionSignature = checkedKeys.sort().join(',');
    if (selectionSignature === lastProcessedSelectionRef.current) {
      console.log('Selection unchanged, skipping accurate token count request');
      return;
    }
    lastProcessedSelectionRef.current = selectionSignature;

    // Optimize by limiting the number of files we process at once
    const filePaths = checkedKeys.filter(key => {
      const keyStr = String(key);
      // Simple heuristic: if it has an extension, it's likely a file
      return keyStr.includes('.') && !keyStr.endsWith('/');
    }).map(key => String(key));

    // Filter out files we already have accurate counts for
    const now = Date.now() / 1000;
    const filesToUpdate = filePaths.filter(path => {
      const existing = accurateTokenCounts[path];
      return !existing || (now - existing.timestamp) > 3600; // 1 hour cache
    });

    if (filesToUpdate.length === 0) {
      console.log('All selected files already have accurate token counts, skipping API call');
      return;
    }

    console.log(`Need accurate counts for ${filesToUpdate.length} of ${filePaths.length} selected files`);


    if (filePaths.length > 0) {
      // More reasonable batch size for accurate token counting
      const limitedPaths = filesToUpdate.slice(0, 20);

      // Use requestIdleCallback to avoid blocking UI
      const processTokens = () => {
        debouncedGetAccurateCounts(limitedPaths);
      };

      if ('requestIdleCallback' in window) {
        requestIdleCallback(processTokens);
      } else {
        setTimeout(processTokens, 0);
      }
    }
  }, 1000), [folders, debouncedGetAccurateCounts, accurateTokenCounts]);

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (accurateCountTimeoutRef.current) clearTimeout(accurateCountTimeoutRef.current);
    };
  }, []);

  // Debounced accurate token updates - completely non-blocking
  useEffect(() => {
    // Defer all token counting to not block UI
    const timeoutId = setTimeout(() => {
      if (checkedKeys.length > 0) {
        console.log('Checked keys changed, current count:', checkedKeys.length);
        debouncedUpdateAccurateTokens(checkedKeys);
      } else {
        console.log('No items selected, skipping accurate token updates');
      }
    }, 100); // Small delay to ensure UI renders first

    return () => clearTimeout(timeoutId);
  }, [checkedKeys, debouncedUpdateAccurateTokens]);

  // Remove chat context dependency that was causing render loops

  // Cleanup intervals on unmount
  useEffect(() => {
    return () => {
      if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
      if (scanTimeoutRef.current) clearTimeout(scanTimeoutRef.current);
    };
  }, []);

  // One-time setup for folder progress checking
  useEffect(() => {
    const checkFolderProgress = async () => {
      try {
        console.log('Checking folder progress...');
        const response = await fetch('/folder-progress');
        console.log('Progress response:', response.ok, response.status);
        if (response.ok) {
          const data = await response.json();
          console.log('Progress data:', data);
          if (data.active) {
            setScanProgress({
              directories: data.progress?.directories || 0,
              files: data.progress?.files || 0,
              elapsed: data.progress?.elapsed || 0
            });

            // Only schedule another check if scanning is still active
            setTimeout(checkFolderProgress, 1000);
          } else {
            // Scanning completed
            setScanProgress(null);
          }
        }
      } catch (error) {
        console.debug('Progress check error:', error);
      }
    };

    // Only check progress if scanning is active
    console.log('isScanning changed:', isScanning);
    if (isScanning) {
      checkFolderProgress();
    };
  }, [isScanning]);

  // ─── File-tree WebSocket: incremental updates without full rescan ───
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/file-tree`;

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isUnmounting = false;

    const connect = () => {
      if (isUnmounting) return;
      ws = new WebSocket(wsUrl);
      fileTreeWsRef.current = ws;

      ws.onopen = () => {
        console.log('📂 FILE_TREE_WS: Connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'connected') return;

          const { type, path: filePath, token_count: tokenCount } = data;
          if (!filePath || !type) return;

          // Skip external paths
          if (filePath.startsWith('[external]')) return;

          console.log(`📂 FILE_TREE_WS: ${type} — ${filePath} (${tokenCount ?? 0} tokens)`);

          setFolders((prev) => {
            if (!prev) return prev;
            const updated = { ...prev };

            if (type === 'file_added') {
              insertIntoFolders(updated, filePath, tokenCount ?? 0);
            } else if (type === 'file_modified') {
              updateTokenInFolders(updated, filePath, tokenCount ?? 0);
            } else if (type === 'file_deleted') {
              removeFromFolders(updated, filePath);
            }

            try {
              const treeNodes = convertToTreeData(updated);
              setTreeData(treeNodes);
            } catch (e) {
              console.warn('📂 FILE_TREE_WS: tree rebuild error:', e);
            }

            return updated;
          });
        } catch (e) {
          console.warn('📂 FILE_TREE_WS: message error:', e);
        }
      };

      ws.onclose = () => {
        console.log('📂 FILE_TREE_WS: Disconnected');
        fileTreeWsRef.current = null;
        if (!isUnmounting) {
          reconnectTimer = setTimeout(connect, 5000);
        }
      };

      ws.onerror = () => {
        // onclose fires after onerror — handles reconnect
      };
    };

    connect();

    return () => {
      isUnmounting = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close();
      }
      fileTreeWsRef.current = null;
    };
  }, []);

  const cancelScan = useCallback(async () => {
    try {
      const response = await fetch('/api/cancel-scan', { method: 'POST' });
      if (response.ok) {
        message.info('Folder scan cancellation requested.');
      }
    } catch (error) {
      console.error('Error cancelling scan:', error);
    }
  }, []);

  const startProgressPolling = useCallback(() => {
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
    }

    progressIntervalRef.current = setInterval(async () => {
      try {
        const response = await fetch('/folder-progress');
        if (response.ok) {
          const data = await response.json();
          if (data.active) {
            setScanProgress({
              directories: data.progress?.directories || 0,
              files: data.progress?.files || 0,
              elapsed: data.progress?.elapsed || 0
            });
          } else {
            if (progressIntervalRef.current) {
              clearInterval(progressIntervalRef.current);
              progressIntervalRef.current = null;
            }
            setScanProgress(null);
            setIsScanning(false);
            if (fetchFoldersRef.current) {
              fetchFoldersRef.current();
            }
          }
        }
      } catch (error) {
        console.debug('Progress check error:', error);
      }
    }, 1000); // Poll every second
  }, []);

  const fetchFoldersRef = useRef<() => Promise<void>>();

  const fetchFolders = useCallback(async () => {
    // Don't fetch if a scan is already in progress
    if (isScanning) {
      console.log('Scan already in progress, skipping fetch');
      return;
    }

    // Don't block the main thread - use MessageChannel for true async
    const channel = new MessageChannel();
    channel.port1.onmessage = async () => {
      try {
        // Build URL with project_path parameter if we have a current project
        let url = '/api/folders';
        const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
        if (projectPath) {
          const params = new URLSearchParams({ project_path: projectPath });
          url = `/api/folders?${params.toString()}`;
        }

        const response = await fetch(url, {
          headers: projectPath ? { 'X-Project-Root': projectPath } : {},
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch folders: ${response.status}`);
        }
        const data = await response.json();

        if (data.error) {
          setScanError(data.error);
          setIsScanning(false);
          return;
        }

        console.log('Folders response:', { _scanning: data._scanning, _stale_and_scanning: data._stale_and_scanning });
        if (data._scanning || data._stale_and_scanning) {
          console.log('Setting isScanning to TRUE');
          setIsScanning(true);
          setScanError(null);
          startProgressPolling();
          if (data._stale_and_scanning) {
            const { _stale_and_scanning, ...folderData } = data;
            if (folderData && Object.keys(folderData).length > 0) {
              setFolders(folderData);
              try {
                const treeNodes = convertToTreeData(folderData);
                setTreeData(treeNodes);
              } catch (conversionError) {
                console.error('Error converting stale folders to tree data:', conversionError);
              }
            }
          }
        } else {
          setIsScanning(false);
          setScanError(null);
          if (progressIntervalRef.current) {
            clearInterval(progressIntervalRef.current);
            progressIntervalRef.current = null;
          }

          // Validate data before setting
          if (data && typeof data === 'object' && Object.keys(data).length > 0) {
            setFolders(data);
            try {
              const treeNodes = convertToTreeData(data);
              setTreeData(treeNodes);
            } catch (conversionError) {
              console.error('Error converting folders to tree data:', conversionError);
              setScanError('Failed to process folder structure');
            }
          } else {
            console.warn('Received empty or invalid folder data');
            setFolders({});
            setTreeData([]);
          }
        }
      } catch (error) {
        setScanError(error instanceof Error ? error.message : 'Unknown error');
        setIsScanning(false);
      }
    };

    // Post message to trigger async execution
    channel.port2.postMessage(null);
  }, [startProgressPolling]);

  useEffect(() => {
    fetchFoldersRef.current = fetchFolders;
  }, [fetchFolders]);

  useEffect(() => {
    // Make folder fetching completely asynchronous and non-blocking
    const asyncInit = async () => {
      // Use requestIdleCallback to ensure this doesn't block the main thread
      if ('requestIdleCallback' in window) {
        requestIdleCallback(() => {
          fetchFolders();
        });
      } else {
        // Fallback for browsers without requestIdleCallback
        setTimeout(() => {
          fetchFolders();
        }, 100);
      }
    };

    // Call the async function
    asyncInit();
  }, []); // Add empty dependency array

  // Listen for manual refresh events
  useEffect(() => {
    const handleRefreshEvent = () => {
      console.log('Received refresh event, triggering fetchFolders');
      setIsScanning(true);
      setScanProgress(null);
      setScanError(null);
      fetchFolders();
    };

    window.addEventListener('refreshFolders', handleRefreshEvent);
    return () => window.removeEventListener('refreshFolders', handleRefreshEvent);
  }, [fetchFolders]);

  // Add timeout handling with user notification
  useEffect(() => {
    if (isScanning) {
      // Set a client-side timeout as backup
      scanTimeoutRef.current = setTimeout(() => {
        if (isScanning) {
          message.warning({
            content: (
              <div>
                Folder scan is taking longer than expected. You can continue using Ziya.
                <button
                  onClick={cancelScan}
                  style={{
                    background: '#ff4d4f',
                    color: 'white',
                    border: 'none',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    marginLeft: '8px'
                  }}
                >
                  Cancel Scan
                </button>
              </div>
            ),
            duration: 0, // Don't auto-dismiss
            key: 'scan-timeout'
          });
        }
      }, 60000); // 60 second warning
    } else {
      // Clear the warning message when scanning completes
      message.destroy('scan-timeout');
    }

    return () => {
      if (scanTimeoutRef.current) {
        clearTimeout(scanTimeoutRef.current);
      }
    };
  }, [isScanning, cancelScan]);

  // Listen for project switch - clear selection and refresh
  useEffect(() => {
    const handleProjectSwitch = (event: CustomEvent) => {
      const { projectPath } = event.detail;
      console.log('📂 PROJECT_SWITCH: Clearing selection and refreshing for:', projectPath);

      // Clear all selections
      setCheckedKeys([]);
      setExpandedKeys([]);

      // Clear sessionStorage to prevent stale selections
      sessionStorage.removeItem('ZIYA_CHECKED_FOLDERS');
      sessionStorage.removeItem('ZIYA_EXPANDED_FOLDERS');

      // Trigger refresh with new project path
      fetchFolders();
    };

    window.addEventListener('projectSwitched', handleProjectSwitch as EventListener);
    return () => window.removeEventListener('projectSwitched', handleProjectSwitch as EventListener);
  }, [fetchFolders]);

  // Listen for context sync events from backend
  useEffect(() => {
    const handleContextSync = (event: CustomEvent) => {
      const { addedFiles, reason } = event.detail;

      if (!addedFiles || addedFiles.length === 0) return;

      console.log('📂 CONTEXT_SYNC: Backend added files to context:', addedFiles);

      // Update checkedKeys to include the new files
      setCheckedKeys(prev => {
        const newKeys = [...prev];
        addedFiles.forEach((file: string) => {
          if (!newKeys.includes(file)) {
            newKeys.push(file);
          }
        });
        return newKeys;
      });

      console.log(`✅ UI synced: ${addedFiles.length} file(s) added to context`);
    };

    window.addEventListener('syncContextFromBackend', handleContextSync as EventListener);
    return () => window.removeEventListener('syncContextFromBackend', handleContextSync as EventListener);
  }, []);

  // Listen for context activation/deactivation from ProjectContext
  useEffect(() => {
    const handleAddFiles = (event: CustomEvent) => {
      const { files } = event.detail;
      if (!files || files.length === 0) return;

      console.log('📂 CONTEXT_ACTIVATION: Adding files to selection:', files.length);

      setCheckedKeys(prev => {
        const newKeys = new Set(prev);
        files.forEach((file: string) => newKeys.add(file));
        return Array.from(newKeys);
      });
    };

    const handleRemoveFiles = (event: CustomEvent) => {
      const { files } = event.detail;
      if (!files || files.length === 0) return;

      console.log('📂 CONTEXT_DEACTIVATION: Removing files from selection:', files.length);

      setCheckedKeys(prev => {
        const filesSet = new Set(files);
        return prev.filter(key => !filesSet.has(String(key)));
      });
    };

    window.addEventListener('addFilesToSelection', handleAddFiles as EventListener);
    window.addEventListener('removeFilesFromSelection', handleRemoveFiles as EventListener);

    return () => {
      window.removeEventListener('addFilesToSelection', handleAddFiles as EventListener);
      window.removeEventListener('removeFilesFromSelection', handleRemoveFiles as EventListener);
    };
  }, []);

  // Function to programmatically add files to context
  const addFilesToContext = useCallback(async (filePaths: string[]) => {
    try {
      console.log('📁 CONTEXT: Adding files to context:', filePaths);

      // Add files to checked keys using the existing pattern
      setCheckedKeys(prev => {
        const newKeys = [...prev, ...filePaths.filter(file => !prev.includes(file))];
        console.log('📁 CONTEXT: Updated checked keys:', newKeys);

        // Save to localStorage immediately to persist the change
        setTabState('ZIYA_CHECKED_FOLDERS', JSON.stringify(newKeys));

        return newKeys;
      });

      console.log('📁 CONTEXT: Files added to context successfully');
    } catch (error) {
      console.error('Error adding files to context:', error);
      throw error;
    }
  }, [setCheckedKeys]);

  const contextValue = useMemo(() => ({
    folders,
    getFolderTokenCount,
    setTreeData,
    treeData,
    checkedKeys: checkedKeys.slice(0), // Return a copy to prevent mutation
    setCheckedKeys,
    searchValue,
    setSearchValue,
    expandedKeys,
    setExpandedKeys,
    isScanning,
    scanProgress,
    scanError,
    accurateTokenCounts,
    addFilesToContext,
    // Remove forceRefreshCounter from dependencies to prevent unnecessary re-renders
  }), [folders, treeData, checkedKeys, searchValue, expandedKeys, isScanning,
    scanProgress, scanError, accurateTokenCounts, forceRefreshCounter, addFilesToContext]);

  return (
    <FolderContext.Provider value={contextValue}>
      {children}
    </FolderContext.Provider>
  );
};

export const useFolderContext = () => {
  const context = useContext(FolderContext);
  if (context === undefined) {
    // Return safe defaults when called outside FolderProvider
    return {
      folders: undefined,
      treeData: [] as TreeDataNode[],
      setTreeData: () => { },
      checkedKeys: [] as React.Key[],
      setCheckedKeys: () => { },
      searchValue: '',
      setSearchValue: () => { },
      expandedKeys: [] as React.Key[],
      setExpandedKeys: () => { },
      isScanning: false,
      scanProgress: null as {
        directories: number;
        files: number;
        elapsed: number;
      } | null,
      scanError: null,
      getFolderTokenCount: () => 0,
      accurateTokenCounts: {} as Record<string, { count: number; timestamp: number }>,
      addFilesToContext: async () => { },
    };
  }
  return context;
};
