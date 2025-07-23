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
}

const FolderContext = createContext<FolderContextType | undefined>(undefined);

export const FolderProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const renderStart = useRef(performance.now());
  const renderCount = useRef(0);
  const [folders, setFolders] = useState<Folders>();
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<React.Key[]>(() => {
    const saved = localStorage.getItem('ZIYA_CHECKED_FOLDERS');
    return saved ? JSON.parse(saved) : [];
  });

  const [searchValue, setSearchValue] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => {
    const saved = localStorage.getItem('ZIYA_EXPANDED_FOLDERS');
    return saved ? JSON.parse(saved) : [];
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

  // Debounced accurate token updates
  useEffect(() => {
    if (checkedKeys.length > 0) {
      console.log('Checked keys changed, current count:', checkedKeys.length);
      debouncedUpdateAccurateTokens(checkedKeys);
    } else {
      console.log('No items selected, skipping accurate token updates');
    }
  }, [checkedKeys, debouncedUpdateAccurateTokens]);

  // Remove chat context dependency that was causing render loops

  const startProgressPolling = useCallback(() => {
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
    }
    
    // Set up more frequent polling for better progress updates
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
            // Scanning completed or not active
            if (progressIntervalRef.current) {
              clearInterval(progressIntervalRef.current);
              progressIntervalRef.current = null;
            }
            setScanProgress(null);
          }
        }
      } catch (error) {
        console.debug('Progress check error:', error);
      }
    }, 500); // Poll every 500ms for smoother updates
  }, []);

  const cancelScan = useCallback(async () => {
    try {
      const response = await fetch('/folder-cancel', { method: 'POST' });
      if (response.ok) {
        const data = await response.json();
        if (data.status === 'cancellation_requested') {
          message.info('Folder scan cancellation requested');
          setIsScanning(false);
          setScanProgress(null);
          setScanError(null);

          // Clear intervals
          if (progressIntervalRef.current) {
            clearInterval(progressIntervalRef.current);
            progressIntervalRef.current = null;
          }
          if (scanTimeoutRef.current) {
            clearTimeout(scanTimeoutRef.current);
            scanTimeoutRef.current = null;
          }
        }
      }
    } catch (error) {
      console.error('Error cancelling scan:', error);
      message.error('Failed to cancel scan');
    }
  }, []);

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
        const response = await fetch('/folder-progress');
        if (response.ok) {
          const data = await response.json();
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
    if (isScanning) {
      checkFolderProgress();
    }
  }, [isScanning]);

  useEffect(() => {
    // Make folder loading independent and non-blocking
    const fetchFoldersAsync = async () => {
      try {
        setIsScanning(true);
        setScanError(null);
        setScanProgress({ directories: 0, files: 0, elapsed: 0 });

        // Start progress polling
        startProgressPolling();

        // Try the fast endpoint first
        let response = await fetch('/api/folders-with-accurate-tokens');
        if (!response.ok) {
          // Fall back to regular endpoint
          response = await fetch('/api/folders');
        }

        if (!response.ok) {
          throw new Error(`Failed to fetch folders: ${response.status}`);
        }
        const data = await response.json();

        // Handle timeout or error responses
        if (data.error) {
          setScanError(data.error);
          if (data.timeout) {
            message.warning({
              content: `Folder scan timed out after ${data.timeout_seconds || 45}s. ${data.suggestion || 'Try reducing the scope or increasing timeout.'}`,
              duration: 10
            });
          }
          return;
        }

        // Log the raw folder structure
        console.debug('Raw folder structure loaded');
        console.debug('Raw folder structure:', {
          componentsPath: data?.frontend?.src?.components,
          d3Files: Object.keys(data?.frontend?.src?.components?.children || {})
            .filter(f => f.includes('D3') || f === 'Debug.tsx')
        });

        // Store the complete folder structure
        setFolders(data);

        // Check if we got accurate tokens and update state
        if (data._has_accurate_tokens) {
          console.log('Received folder structure with pre-calculated accurate tokens');
          // No need to fetch accurate tokens separately
        }

        // Move heavy computation to async to avoid blocking UI
        // Use requestIdleCallback if available, otherwise setTimeout
        const scheduleWork = (callback: () => void) => {
          if ('requestIdleCallback' in window) {
            requestIdleCallback(callback, { timeout: 1000 });
          } else {
            setTimeout(callback, 0);
          }
        };

        scheduleWork(async () => {
          try {
            // Get all available file paths recursively (optimized)
            const getAllPaths = (obj: any, prefix: string = ''): string[] => {
              const paths: string[] = [];
              const stack: Array<{ obj: any, prefix: string }> = [{ obj, prefix }];

              while (stack.length > 0) {
                const { obj: currentObj, prefix: currentPrefix } = stack.pop()!;

                for (const [key, value] of Object.entries(currentObj)) {
                  const path = currentPrefix ? `${currentPrefix}/${key}` : key;
                  paths.push(path);

                  if (value && typeof value === 'object' && 'children' in value && value.children) {
                    stack.push({ obj: value.children, prefix: path });
                  }
                }
              }

              return paths;
            };

            const availablePaths = getAllPaths(data);
            console.debug('Available paths:', {
              total: availablePaths.length,
              d3Files: availablePaths.filter(p => p.includes('D3') || p.includes('Debug.tsx')),
              componentFiles: availablePaths.filter(p => p.includes('components/'))
            });

            // Convert to tree data and set expanded keys for top-level folders
            const treeNodes = convertToTreeData(data);
            setTreeData(treeNodes);
            // Don't auto-expand top-level folders - keep them collapsed
            // setExpandedKeys(prev => [...prev, ...treeNodes.map(node => node.key)]);

            // Update checked keys to include all available files and maintain selections
            setCheckedKeys(prev => {
              const currentChecked = new Set(prev as string[]);

              // Get the parent directory of any checked directory
              const getParentDir = (path: string) => {
                const parts = path.split('/');
                return parts.slice(0, -1).join('/');
              };

              // If a directory is checked, include all its files
              const newChecked = new Set<string>();
              for (const path of availablePaths) {
                const parentDir = getParentDir(path);
                if (currentChecked.has(path) || currentChecked.has(parentDir)) {
                  newChecked.add(path);
                }
              }

              console.log('Syncing checked keys:', {
                before: currentChecked.size,
                after: newChecked.size,
                added: [...newChecked].filter(k => !currentChecked.has(k)),
                removed: [...currentChecked].filter(k => !newChecked.has(k))
              });

              // Debug log for D3 files
              const d3Files = [...newChecked].filter(k => k.includes('D3') || k.includes('Debug.tsx'));
              if (d3Files.length > 0) {
                console.log('D3 files in checked keys:', d3Files);
              }

              // Return the updated set of checked keys
              return [...newChecked];
            });
          } catch (error) {
            console.error('Error processing folder paths:', error);
          }
        });
      } catch (error) {
        setScanError(error instanceof Error ? error.message : 'Unknown error occurred');
        console.error('Error fetching folders:', error);
        message.error({
          content: `Failed to load folder structure: ${error instanceof Error ? error.message : 'Unknown error'}`,
          duration: 8
        });
      } finally {
        setIsScanning(false);
        setScanProgress(null);

        // Clear progress polling
        if (progressIntervalRef.current) {
          clearInterval(progressIntervalRef.current);
          progressIntervalRef.current = null;
        }
      }
    };

    // Start folder loading immediately but don't await it
    // This prevents blocking other initialization processes
    fetchFoldersAsync();
  }, []);

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
    accurateTokenCounts
    // Remove forceRefreshCounter from dependencies to prevent unnecessary re-renders
  }), [folders, treeData, checkedKeys, searchValue, expandedKeys, isScanning,
    scanProgress, scanError, accurateTokenCounts, forceRefreshCounter]);

  return (
    <FolderContext.Provider value={contextValue}>
      {children}
    </FolderContext.Provider>
  );
};

export const useFolderContext = () => {
  const context = useContext(FolderContext);
  if (context === undefined) {
    // Don't throw error during initialization - return safe defaults
    console.warn('useFolderContext called before FolderProvider is ready, returning defaults');
    return {
      folders: undefined,
      treeData: [],
      checkedKeys: [],
      setTreeData: () => { },
      setCheckedKeys: () => { },
      searchValue: '',
      setSearchValue: () => { },
      expandedKeys: [],
      setExpandedKeys: () => { },
      isScanning: false,
      scanProgress: null,
      scanError: null,
      accurateTokenCounts: {},
      getFolderTokenCount: () => 0
    };
  }
  return context;
};
