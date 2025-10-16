import React, { createContext, ReactNode, useContext, useCallback, useEffect, useState, useLayoutEffect, useRef, useMemo } from 'react';
import { Folders } from "../utils/types";
import { message } from 'antd';
import { convertToTreeData } from "../utils/folderUtil";
import { useChatContext } from "./ChatContext";
import { TreeDataNode } from "antd";
import { debounce } from "../utils/debounce";

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
  const [folders, setFolders] = useState<Folders>();
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<React.Key[]>(() => {
    try {
      const saved = localStorage.getItem('ZIYA_CHECKED_FOLDERS');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const [searchValue, setSearchValue] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => {
    try {
      const saved = localStorage.getItem('ZIYA_EXPANDED_FOLDERS');
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

  // Monitor FolderProvider render performance
  // Remove performance monitoring that's causing overhead

  // Cleanup function to remove non-existent files from checkedKeys
  const cleanupCheckedKeys = useCallback(async () => {
    if (!folders || checkedKeys.length === 0) return;

    try {
      // Check which files actually exist
      const response = await fetch('/api/files/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: checkedKeys.map(String) })
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
  }, [folders, checkedKeys, setCheckedKeys]);

  // Run cleanup when folders are loaded
  useEffect(() => {
    if (folders && checkedKeys.length > 0) {
      // Debounce cleanup to avoid excessive API calls
      const timeoutId = setTimeout(cleanupCheckedKeys, 2000);
      return () => clearTimeout(timeoutId);
    }
  }, [folders, cleanupCheckedKeys]);

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
          console.log('Processing accurate token count results:', Object.keys(data.results).length, 'files');
          Object.entries(data.results).forEach(([path, result]: [string, any]) => {
            if (result.accurate_count !== undefined) {
              updated[path] = {
                count: result.accurate_count,
                timestamp: result.timestamp
              };
            }
            console.log(`Updated accurate count for ${path}: ${result.accurate_count}`);
          });

          // Debug log to compare with estimated counts
          Object.entries(updated).forEach(([path, result]) => {
            // Remove excessive logging
          });

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
      localStorage.setItem('ZIYA_EXPANDED_FOLDERS', JSON.stringify(Array.from(expandedKeys)));
    } catch (error) {
      console.warn('Failed to save expanded folders to localStorage (QuotaExceeded?):', error);
    }
  }, [expandedKeys]);

  // Save checked folders whenever they change
  useEffect(() => {
    try {
      localStorage.setItem('ZIYA_CHECKED_FOLDERS', JSON.stringify(Array.from(checkedKeys)));
    } catch (error) {
      console.warn('Failed to save checked folders to localStorage (QuotaExceeded?):', error);
    }
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
    // Don't block the main thread - use MessageChannel for true async
    const channel = new MessageChannel();
    channel.port1.onmessage = async () => {
      try {
        const response = await fetch('/api/folders');
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

  // Function to programmatically add files to context
  const addFilesToContext = useCallback(async (filePaths: string[]) => {
    try {
      console.log('📁 CONTEXT: Adding files to context:', filePaths);
      
      // Add files to checked keys using the existing pattern
      setCheckedKeys(prev => {
        const newKeys = [...prev, ...filePaths.filter(file => !prev.includes(file))];
        console.log('📁 CONTEXT: Updated checked keys:', newKeys);
        
        // Save to localStorage immediately to persist the change
        localStorage.setItem('ZIYA_CHECKED_FOLDERS', JSON.stringify(newKeys));
        
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
