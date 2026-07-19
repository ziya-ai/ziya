import React, { createContext, ReactNode, useContext, useCallback, useEffect, useState, useLayoutEffect, useRef, useMemo } from 'react';
import { Folders } from "../utils/types";
import { message } from 'antd';
import { convertToTreeData, insertIntoFolders, updateTokenInFolders, removeFromFolders, sanitizeCheckedKeys, collectAllTreePaths } from "../utils/folderUtil";
import { TreeDataNode } from "antd";
import { debounce } from "../utils/debounce";
import { useConfig } from "./ConfigContext";
import { useProject } from "./ProjectContext";
import { fetchDefaultIncludedFolders } from "../apis/folderApi";
import { getTabState, setTabState } from '../utils/tabState';

// convertToTreeData does a full recursive rebuild of the whole folder tree.
// Calling it synchronously (as part of a setState) competes directly with
// the requestIdleCallback-scheduled MarkdownRenderer mounts in
// Conversation.tsx: during an active scan this function can run every ~1s,
// which starves those idle slots and makes conversation text appear frozen
// blank until the scan (and its polling) stops. Route the conversion
// itself through the same idle-time scheduling so it never wins that race.
//
// DEBUGGING THIS CLASS OF BUG (conversation text frozen/blank while a
// folder scan or other FolderContext update is running):
//   1. Filter the browser console for "TREE_IDLE" (this file) and
//      "MSG_QUEUE" (Conversation.tsx) — both log schedule time, wait
//      latency, and task duration for their respective idle-callback
//      queues, so the two can be correlated by timestamp.
//   2. If TREE_IDLE entries show short wait but long duration, the
//      rebuild itself (convertToTreeData) is the bottleneck — profile
//      frontend/src/utils/folderUtil.ts, not the scheduling.
//   3. If MSG_QUEUE shows long wait (scheduled_at far before ran_at) while
//      TREE_IDLE is firing frequently in that same window, FolderContext
//      is winning the idle-time race against markdown rendering — that's
//      this exact bug class.
//   4. Note the rIC "timeout" option forces a deadline-run even with zero
//      idle time available, so once a scheduled TREE_IDLE callback hits
//      its 500ms timeout it preempts whatever MSG_QUEUE was about to run.
//      That's a structural trade-off in the current scheduling, not new
//      breakage, if you see it in the logs.
let __folderTreeIdleSeq = 0;
const __folderTreeIdle: (cb: () => void, opts?: { timeout: number }) => number =
  (window as any).requestIdleCallback
    ? (window as any).requestIdleCallback.bind(window)
    : ((cb: () => void) => window.setTimeout(cb, 16) as unknown as number);

// Traced wrapper around __folderTreeIdle: logs when a rebuild is
// scheduled, when it actually runs, how long it waited for an idle slot,
// and how long the rebuild itself took. See the block comment above for
// how to use this output to diagnose main-thread contention with
// Conversation.tsx's deferred markdown-render queue.
const __folderTreeIdleTraced = (label: string, cb: () => void, opts?: { timeout: number }) => {
  const id = ++__folderTreeIdleSeq;
  const scheduledAt = performance.now();
  console.log(`📂 TREE_IDLE[${id}] scheduled (${label}), timeout=${opts?.timeout ?? 'none'}`);
  return __folderTreeIdle(() => {
    const waitMs = performance.now() - scheduledAt;
    const runStart = performance.now();
    try {
      cb();
    } finally {
      const durationMs = performance.now() - runStart;
      console.log(`📂 TREE_IDLE[${id}] ran (${label}) after wait=${waitMs.toFixed(0)}ms, duration=${durationMs.toFixed(0)}ms`);
    }
  }, opts);
};

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
  addFilesToContext: (filePaths: string[], options?: { isAutoAdd?: boolean }) => Promise<void>;
  autoAddedFiles: Set<string>;
  removeAutoAddedFiles: () => { removedCount: number; tokensRecovered: number };
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
      // Sanitize on hydration: corruption persisted across restarts otherwise.
      return saved ? sanitizeCheckedKeys(JSON.parse(saved)) : [];
    } catch {
      return [];
    }
  });
  // Ref-mirror so cleanupCheckedKeys can read latest checkedKeys without
  // being listed as a dep (which causes a new function ref on every key change,
  // re-triggering the cleanup effect and creating an infinite 2s loop).
  const checkedKeysRef = useRef<React.Key[]>([]);
  useEffect(() => { checkedKeysRef.current = checkedKeys; }, [checkedKeys]);
  // Heritage tracking: files that were auto-added by the diff context system
  const [autoAddedFiles, setAutoAddedFiles] = useState<Set<string>>(() => {
    try {
      const saved = getTabState('ZIYA_AUTO_ADDED_FILES');
      return saved ? new Set(JSON.parse(saved)) : new Set();
    } catch {
      return new Set();
    }
  });
  const autoAddedFilesRef = useRef(autoAddedFiles);
  autoAddedFilesRef.current = autoAddedFiles;

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
  const [accurateTokenCounts, setAccurateTokenCounts] = useState<Record<string, { count: number; timestamp: number }>>({});
  const progressPollTimerRef = useRef<NodeJS.Timeout | null>(null);
  const accurateCountTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastProcessedSelectionRef = useRef<string>('');
  const accurateTokenCountsRef = useRef(accurateTokenCounts);
  const fileTreeWsRef = useRef<WebSocket | null>(null);
  const externalRefetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Monitor FolderProvider render performance
  // Get current project from ProjectContext
  const { currentProject } = useProject();
  // Remove performance monitoring that's causing overhead

  // Create ref to avoid stale closures in async callbacks
  const currentProjectRef = useRef(currentProject);
  currentProjectRef.current = currentProject;

  // Keep ref in sync for stable closures
  accurateTokenCountsRef.current = accurateTokenCounts;

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
    const checkedKeys = checkedKeysRef.current;
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

    // External paths (under [external] root) are outside the project root by definition.
    // They were validated when added, so skip them during cleanup.
    const keysToValidate = checkedKeys.filter(key => !String(key).startsWith('[external]'));
    const externalKeys = checkedKeys.filter(key => String(key).startsWith('[external]'));
    console.log('🔍 CLEANUP: Validating', keysToValidate.length, 'files against project:', projectPath, `(skipping ${externalKeys.length} external)`);

    try {
      const response = await fetch('/api/files/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          files: keysToValidate.map(String),
          projectRoot: projectPath
        })
      });

      if (response.ok) {
        const { existingFiles } = await response.json();
        const existingSet = new Set(existingFiles);

        // Three-stage cleanup: server-validated files exist on disk, plus
        // client-side accept-list of folder paths that exist in the tree
        // (the validate endpoint only handles files, not directories), plus
        // external keys verbatim.
        const treePaths = collectAllTreePaths(folders);
        const cleanedProjectKeys = keysToValidate.filter(key => {
          const s = String(key);
          // File the server confirmed exists on disk
          if (existingSet.has(s)) return true;
          // Folder path that exists in the tree (validate endpoint
          // doesn't validate folders, so trust the tree here)
          if (treePaths.has(s)) return true;
          return false;
        });
        // Pass everything through the sanitizer one more time to drop
        // any structurally-invalid entries (corrupted strings, etc.)
        // that may have slipped through earlier filters.
        const cleanedKeys = sanitizeCheckedKeys([...cleanedProjectKeys, ...externalKeys]);

        if (cleanedKeys.length !== checkedKeys.length) {
          console.log(`🧹 CLEANUP: Removed ${checkedKeys.length - cleanedKeys.length} non-existent files from selection`);
          setCheckedKeys(cleanedKeys);
        }
      }
    } catch (error) {
      console.warn('Failed to cleanup checked keys:', error);
    }
  }, [folders, setCheckedKeys, currentProject]);

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

    window.addEventListener('restoreProjectFileSelections', handleRestoreSelections as unknown as EventListener);
    return () => {
      window.removeEventListener('restoreProjectFileSelections', handleRestoreSelections as unknown as EventListener);
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
    if (filePaths.length === 0) return;

    // Filter out files we already have recent accurate counts for (within 5 minutes)
    const now = Date.now() / 1000;
    const currentCounts = accurateTokenCountsRef.current;
    const filesToUpdate = filePaths.filter(path => {
      const existing = currentCounts[path];
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
      // Sanitize before persisting so corruption never enters storage
      // even if it briefly appeared in state (e.g. via a buggy event handler).
      setTabState('ZIYA_CHECKED_FOLDERS', JSON.stringify(sanitizeCheckedKeys(Array.from(checkedKeys))));
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
  // Note: updateAccurateTokens is unused (callers use debouncedUpdateAccurateTokens)
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
    const currentCounts = accurateTokenCountsRef.current;
    const filesToUpdate = filePaths.filter(path => {
      const existing = currentCounts[path];
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
  }, 1000), [folders, debouncedGetAccurateCounts]);

  // Cleanup debounce timers on unmount and when dependencies change
  useEffect(() => {
    return () => {
      if (accurateCountTimeoutRef.current) clearTimeout(accurateCountTimeoutRef.current);
      // Cancel any pending debounced calls to prevent leaked timers
      debouncedUpdateAccurateTokens.cancel?.();
      getAccurateTokenCounts.cancel?.();
    };
  }, [debouncedUpdateAccurateTokens, getAccurateTokenCounts]);

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
      if (progressPollTimerRef.current) clearTimeout(progressPollTimerRef.current);
    };
  }, []);

  // One-time setup for folder progress checking
  useEffect(() => {
    const checkFolderProgress = async () => {
      // Cancel any previously scheduled poll to prevent parallel chains
      if (progressPollTimerRef.current) {
        clearTimeout(progressPollTimerRef.current);
        progressPollTimerRef.current = null;
      }
      if (document.hidden) {
        progressPollTimerRef.current = setTimeout(checkFolderProgress, 2000);
        return;
      }
      try {
        console.log('Checking folder progress...');
        const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
        const progressUrl = projectPath
          ? `/folder-progress?${new URLSearchParams({ project_path: projectPath }).toString()}`
          : '/folder-progress';
        const response = await fetch(progressUrl);
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
            progressPollTimerRef.current = setTimeout(checkFolderProgress, 1000);
          } else {
            // Scanning completed on the server — act as a fallback in case
            // the WebSocket scan_complete message was missed.
            setIsScanning(false);
            setScanProgress(null);
            // Fetch the finished folder tree
            if (fetchFoldersRef.current) fetchFoldersRef.current();
          }
        }
      } catch (error) {
        console.debug('Progress check error:', error);
      }
    };

    // Only check progress if scanning is active
    console.log('isScanning changed:', isScanning);
    if (isScanning) {
      // Delay progress polling to avoid blocking initial render
      progressPollTimerRef.current = setTimeout(() => checkFolderProgress(), 2000);
    }

    return () => {
      if (progressPollTimerRef.current) clearTimeout(progressPollTimerRef.current);
      progressPollTimerRef.current = null;
    };
  }, [isScanning]);

  // ─── File-tree WebSocket: incremental updates without full rescan ───
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/file-tree`;

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isUnmounting = false;

    // ── Batch incoming file-tree events ──────────────────────────────
    // Operations like git checkout / npm install / builds send dozens of
    // events in rapid succession.  Processing each one synchronously
    // (shallow-copy folders + full convertToTreeData) overwhelms the
    // main thread and freezes / crashes the tab.
    //
    // Instead, accumulate events and flush once per animation frame.
    // This collapses 50 rapid events into a single folders update + one
    // tree rebuild.
    type PendingEvent = { type: string; filePath: string; tokenCount: number };
    let pendingEvents: PendingEvent[] = [];
    let flushRafId: number | null = null;

    const flushPendingEvents = () => {
      flushRafId = null;
      if (pendingEvents.length === 0) return;

      // Snapshot and clear before the setState
      const batch = pendingEvents;
      pendingEvents = [];

      setFolders((prev) => {
        if (!prev) return prev;
        const updated = { ...prev };

        for (const evt of batch) {
          if (evt.type === 'file_added') {
            insertIntoFolders(updated, evt.filePath, evt.tokenCount);
          } else if (evt.type === 'file_modified') {
            updateTokenInFolders(updated, evt.filePath, evt.tokenCount);
          } else if (evt.type === 'file_deleted') {
            removeFromFolders(updated, evt.filePath);
          }
        }

        // Single tree rebuild for the entire batch
        __folderTreeIdleTraced('ws-batch-rebuild', () => {
          try {
            const treeNodes = convertToTreeData(updated);
            setTreeData(treeNodes);
          } catch (e) {
            console.warn('📂 FILE_TREE_WS: tree rebuild error:', e);
          }
        }, { timeout: 500 });

        return updated;
      });
    };

    const enqueueEvent = (type: string, filePath: string, tokenCount: number) => {
      pendingEvents.push({ type, filePath, tokenCount });
      if (flushRafId === null) {
        flushRafId = requestAnimationFrame(flushPendingEvents);
      }
    };

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

          // AST indexing finished — notify ProjectContext to recalculate tokens
          if (type === 'ast_indexing_complete') {
            console.log(`📂 FILE_TREE_WS: AST indexing complete (${tokenCount} files)`);
            window.dispatchEvent(new CustomEvent('astIndexingComplete', { detail: { filesProcessed: tokenCount } }));
            return;
          }

          // Scan complete — fetch the finished tree and clear progress state.
          if (type === 'scan_complete') {
            setIsScanning(false);
            setScanProgress(null);
            if (progressIntervalRef.current) {
              clearInterval(progressIntervalRef.current);
              progressIntervalRef.current = null;
            }
            if (fetchFoldersRef.current) fetchFoldersRef.current();
            return;
          }

          // Remaining handlers below operate on a concrete file path, so
          // drop pathless events here (scan_complete, handled above, is the
          // one event type that legitimately carries an empty path).
          if (!filePath || !type) return;

          // External paths have a nested structure on the server that
          // doesn't match the flat broadcast format, so incremental
          // insert won't work.  Trigger a full refetch instead.
          if (filePath.startsWith('[external]')) {
            console.log(`📂 FILE_TREE_WS: External ${type} — ${filePath}, triggering refetch`);
            // Debounce: persisted external paths are all broadcast on startup at once
            // (one event per path). Without debouncing, 5 paths × N connected tabs =
            // a large burst of simultaneous folder fetches.
            if (externalRefetchTimerRef.current) clearTimeout(externalRefetchTimerRef.current);
            externalRefetchTimerRef.current = setTimeout(() => {
              externalRefetchTimerRef.current = null;
              if (fetchFoldersRef.current) fetchFoldersRef.current();
            }, 150);
            return;
          }

          // Batch into the next animation frame instead of processing
          // each event synchronously (prevents freeze on bulk file changes)
          enqueueEvent(type, filePath, tokenCount ?? 0);
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
      if (flushRafId !== null) cancelAnimationFrame(flushRafId);
      flushPendingEvents(); // flush any remaining events before disconnect
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close();
      }
      fileTreeWsRef.current = null;
    };
  }, []);

  const cancelScan = useCallback(async () => {
    try {
      // Cancel only THIS project's scan. The backend is per-directory now, so
      // a bare cancel would only hit whatever the server resolves as the
      // current project root — send our explicit path to be unambiguous.
      const projectPath = currentProjectRef.current?.path;
      const url = projectPath
        ? `/api/cancel-scan?project_path=${encodeURIComponent(projectPath)}`
        : '/api/cancel-scan';
      const response = await fetch(url, { method: 'POST' });
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
      progressIntervalRef.current = null;
    }
    // Scan completion is now signaled via the /ws/file-tree WebSocket (scan_complete event).
    // This function is retained so fetchFolders call sites need no changes.
  }, []);

  const fetchFoldersRef = useRef<() => Promise<void>>();

  const fetchFolders = useCallback(async () => {
    // Don't block the main thread - use MessageChannel for true async
    const channel = new MessageChannel();
    const closePorts = () => { try { channel.port1.close(); channel.port2.close(); } catch { } };
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
              __folderTreeIdleTraced('stale-and-scanning', () => {
                try {
                  const treeNodes = convertToTreeData(folderData);
                  setTreeData(treeNodes);
                } catch (conversionError) {
                  console.error('Error converting stale folders to tree data:', conversionError);
                }
              }, { timeout: 500 });
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
            __folderTreeIdleTraced('scan-complete', () => {
              try {
                const treeNodes = convertToTreeData(data);
                setTreeData(treeNodes);
              } catch (conversionError) {
                console.error('Error converting folders to tree data:', conversionError);
                setScanError('Failed to process folder structure');
              }
            }, { timeout: 500 });
          } else {
            console.warn('Received empty or invalid folder data');
            setFolders({});
            setTreeData([]);
          }
        }
      } catch (error) {
        setScanError(error instanceof Error ? error.message : 'Unknown error');
        setIsScanning(false);
      } finally {
        closePorts();
      }
    };

    // Post message to trigger async execution
    channel.port2.postMessage(null);
  }, [startProgressPolling]);

  useEffect(() => {
    fetchFoldersRef.current = fetchFolders;
  }, [fetchFolders]);

  // Auto-include documentation files (AGENTS.md recursively, README.md at the
  // project root only). Unions the server-provided keys into the current
  // selection rather than replacing it, so it never clobbers a user or
  // restored selection. Safe to call on initial load and on project switch.
  const seedDefaultIncludedFolders = useCallback(async (projectPath?: string) => {
    try {
      const keys = await fetchDefaultIncludedFolders(projectPath);
      if (!keys || keys.length === 0) return;
      setCheckedKeys(prev => {
        const existing = new Set(prev.map(String));
        const additions = keys.filter(k => !existing.has(String(k)));
        if (additions.length === 0) return prev;
        console.log(`📄 AUTO-CONTEXT: seeding ${additions.length} documentation file(s)`);
        return [...prev, ...additions];
      });
    } catch (e) {
      console.warn('Failed to seed default included folders:', e);
    }
  }, [setCheckedKeys]);

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

  // Re-fetch when the active project changes (e.g. after ProjectContext finishes loading)
  const prevProjectPath = useRef<string | null>(null);
  useEffect(() => {
    const newPath = currentProject?.path ?? null;
    if (newPath && newPath !== prevProjectPath.current) {
      prevProjectPath.current = newPath;
      fetchFolders();
      seedDefaultIncludedFolders(newPath);
    }
  }, [currentProject?.path, seedDefaultIncludedFolders]);

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

      // Stop progress polling for the old project
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current);
        progressIntervalRef.current = null;
      }

      // NOTE: We intentionally do NOT cancel the old project's server-side
      // scan on switch. Each project scans in its own per-directory thread, so
      // letting the old scan finish warms its cache — switching back is then
      // instant instead of re-scanning from zero (the old cross-project thrash).

      // Reset scanning state so the UI isn't stuck showing old project's scan
      setIsScanning(false);
      setScanProgress(null);
      setScanError(null);

      // Clear stale folder data from old project immediately
      setFolders(undefined);
      setTreeData([]);

      // Clear all selections
      setCheckedKeys([]);
      setExpandedKeys([]);

      // Clear sessionStorage to prevent stale selections
      sessionStorage.removeItem('ZIYA_CHECKED_FOLDERS');
      sessionStorage.removeItem('ZIYA_EXPANDED_FOLDERS');

      // Trigger refresh with new project path
      fetchFolders();
      seedDefaultIncludedFolders(projectPath);
    };

    window.addEventListener('projectSwitched', handleProjectSwitch as unknown as EventListener);
    return () => window.removeEventListener('projectSwitched', handleProjectSwitch as unknown as EventListener);
  }, [fetchFolders, seedDefaultIncludedFolders]);

  // Listen for context sync events from backend
  useEffect(() => {
    const handleContextSync = (event: CustomEvent) => {
      const { addedFiles, removedFiles, reason } = event.detail;
      const added = Array.isArray(addedFiles) ? addedFiles : [];
      const removed = Array.isArray(removedFiles) ? removedFiles : [];

      if (added.length === 0 && removed.length === 0) return;

      if (added.length > 0) {
        console.log('📂 CONTEXT_SYNC: Backend added files to context:', added);
      }
      if (removed.length > 0) {
        console.log('📂 CONTEXT_SYNC: Backend removed files from context:', removed);
      }

      // Update checkedKeys: union of (existing - removed) + added.
      setCheckedKeys(prev => {
        const removedSet = new Set(removed);
        const newKeys = prev.filter(k => !removedSet.has(k as string));
        added.forEach((file: string) => {
          if (!newKeys.includes(file)) newKeys.push(file);
        });
        return newKeys;
      });

      const reasonLabel = reason ? ` (${reason})` : '';
      console.log(
        `✅ UI synced${reasonLabel}: +${added.length} / -${removed.length} file(s)`
      );
    };

    window.addEventListener('syncContextFromBackend', handleContextSync as unknown as EventListener);
    return () => window.removeEventListener('syncContextFromBackend', handleContextSync as unknown as EventListener);
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

      console.log('📂 CONTEXT_ACTIVATION: Removing files from selection:', files.length);

      setCheckedKeys(prev => {
        const removedSet = new Set<string>(files);
        return prev.filter(k => !removedSet.has(k as string));
      });
    };
    window.addEventListener('addFilesToSelection', handleAddFiles as unknown as EventListener);
    window.addEventListener('removeFilesFromSelection', handleRemoveFiles as unknown as EventListener);
    return () => {
      window.removeEventListener('addFilesToSelection', handleAddFiles as unknown as EventListener);
      window.removeEventListener('removeFilesFromSelection', handleRemoveFiles as unknown as EventListener);
    };
  }, []);

  // Function to programmatically add files to context
  const addFilesToContext = useCallback(async (filePaths: string[], options?: { isAutoAdd?: boolean }) => {
    try {
      // Validate paths before adding — extractAllFilesFromDiff can produce garbage
      // paths from malformed diff content (e.g. code fragments concatenated with filenames)
      const validPaths = filePaths.filter(p => {
        if (!p || p.length > 500) return false;
        // Reject paths containing characters that don't belong in real file paths
        if (/[)(;{}!@#$%^&*+=<>?\s]/.test(p)) return false;
        return true;
      });
      if (validPaths.length === 0) return;
      if (validPaths.length !== filePaths.length) {
        console.warn('📁 CONTEXT: Rejected invalid paths:', filePaths.filter(p => !validPaths.includes(p)));
      }
      console.log('📁 CONTEXT: Adding files to context:', validPaths);

      // Add files to checked keys using the existing pattern
      setCheckedKeys(prev => {
        const newKeys = [...prev, ...validPaths.filter(file => !prev.includes(file))];
        console.log('📁 CONTEXT: Updated checked keys:', newKeys);

        // Save to localStorage immediately to persist the change
        setTabState('ZIYA_CHECKED_FOLDERS', JSON.stringify(newKeys));

        return newKeys;
      });

      // Track auto-added heritage when the option is set
      if (options?.isAutoAdd) {
        setAutoAddedFiles(prev => {
          const next = new Set(prev);
          validPaths.forEach(p => next.add(p));
          setTabState('ZIYA_AUTO_ADDED_FILES', JSON.stringify([...next]));
          return next;
        });
      }

      console.log('📁 CONTEXT: Files added to context successfully');
    } catch (error) {
      console.error('Error adding files to context:', error);
      throw error;
    }
  }, [setCheckedKeys]);

  // Remove all auto-added files from context and return stats
  const removeAutoAddedFiles = useCallback((): { removedCount: number; tokensRecovered: number } => {
    const currentAutoAdded = autoAddedFilesRef.current;
    if (currentAutoAdded.size === 0) return { removedCount: 0, tokensRecovered: 0 };

    // Calculate tokens that will be recovered
    let tokensRecovered = 0;
    currentAutoAdded.forEach(filePath => {
      const accurate = accurateTokenCounts[filePath];
      if (accurate && accurate.count > 0) {
        tokensRecovered += accurate.count;
      } else if (folders) {
        const estimated = getFolderTokenCount(filePath, folders);
        if (estimated > 0) tokensRecovered += estimated;
      }
    });

    const removedCount = currentAutoAdded.size;

    // Remove from checked keys
    setCheckedKeys(prev => {
      const filtered = prev.filter(key => !currentAutoAdded.has(String(key)));
      setTabState('ZIYA_CHECKED_FOLDERS', JSON.stringify(filtered));
      return filtered;
    });

    // Clear the auto-added set
    setAutoAddedFiles(new Set());
    setTabState('ZIYA_AUTO_ADDED_FILES', JSON.stringify([]));

    console.log(`📁 CONTEXT: Removed ${removedCount} auto-added files, recovered ~${tokensRecovered} tokens`);
    return { removedCount, tokensRecovered };
  }, [accurateTokenCounts, folders, getFolderTokenCount, setCheckedKeys]);

  // Prune auto-added entries that are no longer in checked keys
  useEffect(() => {
    const checkedSet = new Set(checkedKeys.map(String));
    setAutoAddedFiles(prev => {
      const pruned = new Set([...prev].filter(f => checkedSet.has(f)));
      if (pruned.size !== prev.size) {
        setTabState('ZIYA_AUTO_ADDED_FILES', JSON.stringify([...pruned]));
        return pruned;
      }
      return prev;
    });
  }, [checkedKeys]);

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
    autoAddedFiles,
    removeAutoAddedFiles,
    // Remove forceRefreshCounter from dependencies to prevent unnecessary re-renders
  }), [folders, treeData, checkedKeys, searchValue, expandedKeys, isScanning,
    scanProgress, scanError, accurateTokenCounts, addFilesToContext, autoAddedFiles, removeAutoAddedFiles]);

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
      autoAddedFiles: new Set<string>(),
      removeAutoAddedFiles: () => ({ removedCount: 0, tokensRecovered: 0 }),
    };
  }
  return context;
};
