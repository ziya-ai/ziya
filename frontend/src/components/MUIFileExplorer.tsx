import React, { useState, useEffect, useCallback, useMemo, useRef, useLayoutEffect } from 'react';
import { useFolderContext } from '../context/FolderContext';
import { useTheme } from '../context/ThemeContext';
import { fetchConfig } from '../apis/chatApi';
import { Folders } from '../utils/types';
import { debounce } from 'lodash';
import { message } from 'antd';
import { convertToTreeData } from '../utils/folderUtil';
import { getFileIcon, getFolderIcon } from '../utils/fileIcons';

// MUI imports
import Typography from '@mui/material/Typography';
import Checkbox from '@mui/material/Checkbox';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import LinearProgress from '@mui/material/LinearProgress';
import Box from '@mui/material/Box';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Radio from '@mui/material/Radio';
import RadioGroup from '@mui/material/RadioGroup';
import FormControlLabel from '@mui/material/FormControlLabel';
import Collapse from '@mui/material/Collapse';

// MUI icons
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import ArrowRightIcon from '@mui/icons-material/ArrowRight';
import RefreshIcon from '@mui/icons-material/Refresh';
import AddIcon from '@mui/icons-material/Add';
import FolderIcon from '@mui/icons-material/Folder';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import SearchIcon from '@mui/icons-material/Search';
import ClearIcon from '@mui/icons-material/Clear';

// TypeScript interfaces
interface TreeNodeData {
  key: string;
  title: string;
  children?: TreeNodeData[];
  loggedAccurate?: boolean;
}

interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
}

interface TreeNodeProps {
  node: TreeNodeData;
  level?: number;
  originalNode?: TreeNodeData;
}

function formatNumber(num: number): string {
  if (num === undefined || num === null) return '0';
  const numStr = String(num);
  if (num < 1000) return numStr;
  let result = '';
  let count = 0;
  for (let i = numStr.length - 1; i >= 0; i--) {
    result = numStr[i] + result;
    count++;
    if (count % 3 === 0 && i > 0) {
      result = ',' + result;
    }
  }
  return result;
}

export const MUIFileExplorer = () => {
  const {
    treeData,
    setTreeData,
    folders,
    checkedKeys,
    setCheckedKeys,
    searchValue,
    setSearchValue,
    expandedKeys,
    setExpandedKeys,
    isScanning,
    scanProgress,
    scanError,
    getFolderTokenCount,
    accurateTokenCounts
  } = useFolderContext();

  const { isDarkMode } = useTheme();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [filteredTreeData, setFilteredTreeData] = useState<any[]>([]);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [foldersLoadedFromDB, setFoldersLoadedFromDB] = useState(false);
  const lastCheckedKeysRef = useRef<string>('');
  const lastDBFetchRef = useRef<number>(0);
  const lastClickRef = useRef<number>(0);

  // State for add path dialog
  const [addPathDialogOpen, setAddPathDialogOpen] = useState(false);
  const [browsePath, setBrowsePath] = useState('');
  const [pathInput, setPathInput] = useState('');
  const [browseEntries, setBrowseEntries] = useState<BrowseEntry[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [addMode, setAddMode] = useState<'browser' | 'context'>('context');
  const [projectRoot, setProjectRoot] = useState<string>('~');

  // Lightweight caches - only computed when needed
  const tokenCalculationCache = useRef(new Map());
  const nodePathCache = useRef(new Map());
  const lastAccurateCountsRef = useRef<Record<string, any>>({});

  // Track if we have any data loaded (either cached or fresh)
  const [hasLoadedData, setHasLoadedData] = useState(false);

  // Load project root from backend config
  useEffect(() => {
    const loadProjectRoot = async () => {
      try {
        const response = await fetch('/api/config');
        const config = await response.json();
        setProjectRoot(config.projectRoot || '~');
      } catch (error) {
        console.warn('Failed to load project root, using home directory:', error);
      }
    };
    loadProjectRoot();
  }, []);

  // Ensure component initializes immediately when folder data is available
  // This prevents the issue where users starting on chat history have no file context
  useEffect(() => {
    if (folders && Object.keys(folders).length > 0 && !hasLoadedData) {
      setHasLoadedData(true);
      setIsInitialLoad(false);
    }
  }, [folders, hasLoadedData]);

  // Force recalculation when accurate token counts change
  // Helper function to determine if a node has children
  const nodeHasChildren = (node: any): boolean => {
    return !!(node && node.children && Array.isArray(node.children) && node.children.length > 0);
  };

  // Lightweight tree data - only process what's visible
  const muiTreeData = useMemo(() => {
    // For initial render, show empty state immediately
    if (isInitialLoad && !hasLoadedData && !isScanning) return [];
    if (!folders) return [];

    // Return tree data as-is for now - optimization happens in TreeNode
    return treeData;
  }, [treeData, isInitialLoad, hasLoadedData]);

  // Fast node lookup cache
  const getNodeFromCache = useCallback((nodeKey: string) => {
    if (nodePathCache.current.has(nodeKey)) {
      return nodePathCache.current.get(nodeKey);
    }
    // Only search when actually needed
    return null;
  }, []);

  // Optimized TreeNode with minimal re-renders
  const TreeNode = React.memo(({ node, level = 0, originalNode }: TreeNodeProps) => {
    // Use originalNode for checkbox calculations if provided (during search)
    // This ensures we check against ALL children, not just filtered ones
    const nodeForCheckCalculations = originalNode || node;

    // But use node for rendering decisions
    const hasChildren = nodeHasChildren(node);
    const isExpanded = expandedKeys.includes(String(node.key));

    // Debug log when checkedKeys changes for this node
    if (process.env.NODE_ENV === 'development' && String(node.key).includes('test')) {
      console.log(`TreeNode ${node.key}: isChecked=${isChecked}, checkedKeys.length=${checkedKeys.length}`);
    }

    // Get accurate token count if available
    const accurateData = accurateTokenCounts[String(node.key)];

    // Calculate token total for this node (used for styling)
    const nodeTokenTotal = useMemo(() => {
      if (hasChildren) return 0;

      const estimatedTokens = getFolderTokenCount(String(node.key), folders || {});

      // Check for tool-backed files (marked as -1)
      if (estimatedTokens === -1 || (accurateData && accurateData.count === -1)) {
        return -1;
      } else if (accurateData && accurateData.count > 0) {
        // Only use accurate data if it has a positive count
        return accurateData.count;
      } else if (estimatedTokens > 0) {
        return estimatedTokens;
      }

      return 0;
    }, [hasChildren, node.key, accurateData, folders]);

    // Extract clean label and token count from title
    // Title is just the filename now (no token count in title)
    const cleanLabel = String(node.title);

    // Helper function to check if any descendants are selected
    const hasSelectedDescendants = useCallback((node: any): boolean => {
      if (!node.children) return false;

      for (const child of node.children) {
        if (checkedKeys.includes(String(child.key))) {
          return true;
        }
        if (hasSelectedDescendants(child)) {
          return true;
        }
      }
      return false;
    }, [checkedKeys]);

    // Helper function to check if all children of a node are selected
    const areAllChildrenSelected = useCallback((checkNode: any, currentCheckedKeys: string[]): boolean => {
      if (!checkNode.children || checkNode.children.length === 0) return true;

      const checkedSet = new Set(currentCheckedKeys);
      return checkNode.children.every(child => {
        const childKey = String(child.key);

        // If the child is directly selected, it's considered selected
        if (checkedSet.has(childKey)) {
          // But if it's a directory, we also need to verify all its children are selected
          if (nodeHasChildren(child)) {
            return areAllChildrenSelected(child, currentCheckedKeys);
          }
          return true; // File is selected
        }

        // If child is not directly selected but is a directory, 
        // check if all its children are selected (making it implicitly selected)
        if (nodeHasChildren(child)) {
          return areAllChildrenSelected(child, currentCheckedKeys);
        }

        // File is not selected
        return false;
      });
    }, []);

    // Compute isChecked: parent is checked if explicitly selected OR all children are selected
    const isChecked = useMemo(() => {
      if (checkedKeys.includes(String(node.key))) return true;
      if (!hasChildren) return false;
      // Check if all children are selected, which means parent should show as checked
      return areAllChildrenSelected(nodeForCheckCalculations, checkedKeys.map(String));
    }, [checkedKeys, node.key, hasChildren, node, areAllChildrenSelected]);

    // Check if this node is indeterminate (some but not all children selected)
    const isIndeterminate = useMemo(() => {
      if (!hasChildren || isChecked) return false;

      // A node is indeterminate if:
      // 1. It has children
      // 2. It's not directly selected
      // 3. Some (but not all) of its descendants are selected
      const hasAnySelected = hasSelectedDescendants(nodeForCheckCalculations);
      return hasAnySelected && !areAllChildrenSelected(nodeForCheckCalculations, checkedKeys.map(String));
    }, [hasChildren, isChecked, node, hasSelectedDescendants, areAllChildrenSelected]);

    const handleToggle = useCallback(() => {
      if (hasChildren) {
        setExpandedKeys(prev =>
          isExpanded
            ? prev.filter(key => key !== String(node.key))
            : [...prev, String(node.key)]
        );
      }
    }, [hasChildren, isExpanded, node.key]);

    const handleCheck = useCallback((event) => {
      event.stopPropagation();

      // Use the proper hierarchy checkbox logic
      handleCheckboxClick(String(node.key), !isChecked);

      // Force a re-render of this node to update token counts
      tokenCalculationCache.current.clear();
    }, [isChecked, node.key, handleCheckboxClick]);

    return (
      <Box key={node.key}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            py: 0.25,
            pl: level * 2 + 1,
            pr: 1,
            position: 'relative',
            '&:hover': {
              backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)'
            }
          }}
        >
          {/* Expand/collapse icon */}
          <IconButton
            size="small"
            onClick={handleToggle}
            sx={{
              p: 0.25,
              position: 'absolute',
              left: level * 14 - 10,
              visibility: hasChildren ? 'visible' : 'hidden'
            }}
          >
            {hasChildren && (isExpanded ? <ArrowDropDownIcon fontSize="small" /> : <ArrowRightIcon fontSize="small" />)}
          </IconButton>

          {/* Checkbox */}
          <Checkbox
            checked={isChecked}
            indeterminate={isIndeterminate}
            onClick={handleCheck}
            size="small"
            sx={{ p: 0.25, mr: 0.5, ml: 0 }}
            id={`checkbox-${node.key}`}
            name={`checkbox-${node.key}`}
          />

          {/* Icon */}
          {hasChildren ?
            getFolderIcon(isExpanded) :
            getFileIcon(cleanLabel)}

          {/* Label */}
          <Typography
            variant="body2"
            sx={{
              flexGrow: 1,
              fontWeight: hasChildren ? 'bold' : (isChecked && nodeTokenTotal > 0 ? 'bold' : 'normal'),
              color: isChecked && !hasChildren && nodeTokenTotal > 0 ? (isDarkMode ? '#ffffff' : '#000000') : (isDarkMode ? 'text.primary' : 'inherit')
            }}
          >
            {cleanLabel}
          </Typography>

          {/* Token count - only show if calculated */}
          {!hasChildren && (
            (() => {
              let nodeTokens = 0;
              let total = 0;
              let included = 0;

              // Get token count from folder context
              const estimatedTokens = getFolderTokenCount(String(node.key), folders || {});

              // Check for tool-backed files (marked as -1)
              if (estimatedTokens === -1 || (accurateData && accurateData.count === -1)) {
                // Tool-backed file - show special marker
                return (
                  <Typography variant="caption" sx={{ ml: 1, fontSize: '0.7rem', fontFamily: 'monospace', color: '#1890ff' }}>
                    (*)
                  </Typography>
                );
              } else if (accurateData && accurateData.count > 0) {
                // Only use accurate data if it has a positive count
                nodeTokens = accurateData.count;
                total = nodeTokens;
                included = isChecked ? nodeTokens : 0;
              } else if (estimatedTokens > 0 || estimatedTokens === -1) {
                nodeTokens = estimatedTokens;
                total = estimatedTokens;
                included = isChecked ? estimatedTokens : 0;
              }

              return (
                <Typography
                  variant="caption"
                  sx={{
                    ml: 1,
                    fontSize: '0.7rem',
                    fontFamily: 'monospace',
                    color: isDarkMode ? '#aaa' : 'text.secondary',
                    fontWeight: isChecked && total > 0 ? 'bold' : 'normal',
                    ...(isChecked && total > 0 && { color: isDarkMode ? '#ffffff' : '#000000' })
                  }}
                >
                  {total === -1 ? '(*)' : (total > 0 ? `(${formatNumber(total)}${accurateData ? '✓' : ''})` : '(0)')}
                </Typography>
              );
            })()
          )}

          {/* Token display for folders showing included/total */}
          {hasChildren && (
            (() => {
              // For directories, ensure we're getting the correct token count
              const dirPath = String(node.key);

              // Use memoized calculation with caching to prevent excessive recalculations
              // This is critical for performance when scrolling
              const cacheKey = `display:${dirPath}:${checkedKeys.join(',')}`;
              let folderTokens = tokenCalculationCache.current.get(cacheKey);

              if (!folderTokens) {
                const totalTokens = calculateChildrenTotal(nodeForCheckCalculations);
                // If this folder is directly selected, include all tokens
                const includedTokens = isChecked ? totalTokens : calculateChildrenIncluded(nodeForCheckCalculations);

                folderTokens = { total: totalTokens, included: includedTokens };
                tokenCalculationCache.current.set(cacheKey, folderTokens);
              }

              const { total, included } = folderTokens;

              return total > 0 ? (
                <Typography
                  variant="caption"
                  sx={{
                    ml: 1,
                    fontSize: '0.7rem',
                    fontFamily: 'monospace',
                    color: isDarkMode ? '#aaa' : 'text.secondary'
                  }}
                >
                  (<Typography
                    component="span"
                    sx={{
                      fontWeight: included > 0 ? 'bold' : 'normal',
                      fontSize: 'inherit',
                      color: included > 0 ? (isDarkMode ? '#ffffff' : '#000000') : 'inherit'
                    }}
                  >
                    {formatNumber(included)}
                  </Typography>/{formatNumber(total)})
                </Typography>
              ) : (
                <Typography
                  variant="caption"
                  sx={{
                    ml: 1,
                    fontSize: '0.7rem',
                    fontFamily: 'monospace',
                    color: isDarkMode ? '#aaa' : 'text.secondary'
                  }}
                >
                  (0/0)
                </Typography>
              );
            })()
          )}
        </Box>

        {/* Children - only render when expanded */}
        {hasChildren && isExpanded && (
          <Collapse in={isExpanded}>
            <Box sx={{ pl: 1 }}>
              {node.children?.map(child => (
                <TreeNode key={child.key} node={child} level={level + 1} originalNode={findOriginalNode(child.key)} />
              ))}
            </Box>
          </Collapse>
        )}
      </Box>
    );
  }, (prevProps, nextProps) => {
    // Only re-render if these specific props changed
    const prevNode = prevProps.node;
    const nextNode = nextProps.node;
    const prevLevel = prevProps.level;
    const nextLevel = nextProps.level;

    // Check if node key is the same
    if (prevNode.key !== nextNode.key) return false;

    // Check if level changed
    if (prevLevel !== nextLevel) return false;

    // Check if expanded state changed for this node
    const prevExpanded = expandedKeys.includes(String(prevNode.key));
    const nextExpanded = expandedKeys.includes(String(nextNode.key));
    if (prevExpanded !== nextExpanded) return false;

    // Check if checked state changed for this node
    const prevChecked = checkedKeys.includes(String(prevNode.key));
    const nextChecked = checkedKeys.includes(String(nextNode.key));
    if (prevChecked !== nextChecked) return false;

    // If nothing important changed, prevent re-render
    return true;
  });

  // Helper function to find original node from full tree by key
  const findOriginalNode = useCallback((nodeKey: string): TreeNodeData | undefined => {
    const findInTree = (nodes: TreeNodeData[]): TreeNodeData | undefined => {
      for (const node of nodes) {
        if (String(node.key) === String(nodeKey)) {
          return node;
        }
        if (node.children) {
          const found = findInTree(node.children);
          if (found) return found;
        }
      }
      return undefined;
    };
    return findInTree(muiTreeData);
  }, [muiTreeData]);

  // Effect to load folders on component mount - with improved caching
  // Update tree data when folders change
  useEffect(() => {
    if (folders && Object.keys(folders).length > 0) {
      console.log('MUI Folders updated, converting to tree data');
      const sortedData = sortTreeData(convertToTreeData(folders as any));

      setTreeData(sortedData);
      setHasLoadedData(true);
      setIsInitialLoad(false);

      console.log('MUI: Updated tree data with', sortedData.length, 'top-level nodes');
    }
  }, [folders]);

  // Re-apply search filter when tree data changes (e.g., after project switch)
  // This prevents stale filteredTreeData from showing old project directories
  useEffect(() => {
    if (searchValue && muiTreeData.length > 0) {
      const { filteredData, expandedKeys: newExpandedKeys } = filterTreeData(muiTreeData, searchValue);
      setFilteredTreeData(filteredData);
      setExpandedKeys(prev => {
        const merged = new Set([...prev, ...newExpandedKeys]);
        return Array.from(merged);
      });
    }
  }, [muiTreeData]);

  // Separate effect for database folder loading - with guards to prevent loops
  useEffect(() => {
    const now = Date.now();
    const timeSinceLastFetch = now - lastDBFetchRef.current;
    const MIN_FETCH_INTERVAL = 60000; // Minimum 60 seconds between DB fetches

    // Skip if already loaded or too soon since last fetch
    if (foldersLoadedFromDB || timeSinceLastFetch < MIN_FETCH_INTERVAL) {
      return;
    }

    lastDBFetchRef.current = now;
    setFoldersLoadedFromDB(true);
    console.log('MUI: One-time database folder load completed');
  }, []); // Empty deps - only run once on mount

  // Compute expanded keys based on selected items - only expand paths with selections
  useEffect(() => {
    // Track if checkedKeys actually changed to avoid fighting with manual expansions
    const checkedKeysSignature = checkedKeys.sort().join(',');
    if (lastCheckedKeysRef.current === checkedKeysSignature) {
      return; // No change in selections, don't recalculate
    }
    lastCheckedKeysRef.current = checkedKeysSignature;

    // Skip if no tree data or checked keys
    if (!treeData || treeData.length === 0 || checkedKeys.length === 0) {
      // Don't collapse manually expanded folders when there are no selections
      // Users should be able to browse the tree without selections
      // Only collapse if we're certain the user wants to reset (e.g., explicit deselect all action)
      // For now, preserve manual expansions by doing nothing here
      // if (checkedKeys.length === 0 && expandedKeys.length > 0) {
      //   setExpandedKeys([]);
      // }
      return;
    }

    // Helper function to find all ancestor keys for selected items
    const findAncestorsOfSelected = (nodes: any[], checkedSet: Set<string>, parentPath: string[] = []): Set<string> => {
      const ancestors = new Set<string>();

      for (const node of nodes) {
        const nodeKey = String(node.key);
        const currentPath = [...parentPath, nodeKey];

        // Check if this node or any descendant is selected
        const isNodeSelected = checkedSet.has(nodeKey);
        const hasSelectedDescendants = hasSelectedInSubtree(node, checkedSet);

        if (isNodeSelected || hasSelectedDescendants) {
          // Add all ancestors to the expansion set
          parentPath.forEach(ancestorKey => ancestors.add(ancestorKey));

          // If this node has children and contains selections, recursively process
          if (node.children && node.children.length > 0) {
            const childAncestors = findAncestorsOfSelected(node.children, checkedSet, currentPath);
            childAncestors.forEach(key => ancestors.add(key));
          }
        }
      }

      return ancestors;
    };

    // Helper to check if a node has any selected descendants
    const hasSelectedInSubtree = (node: any, checkedSet: Set<string>): boolean => {
      if (!node.children) return false;

      for (const child of node.children) {
        const childKey = String(child.key);
        if (checkedSet.has(childKey)) {
          return true;
        }
        if (hasSelectedInSubtree(child, checkedSet)) {
          return true;
        }
      }
      return false;
    };

    // Calculate which directories should be expanded
    const checkedSet = new Set(checkedKeys.map(String));
    const requiredExpansions = findAncestorsOfSelected(treeData, checkedSet);

    // Only update if the expansion set has changed
    const newExpandedKeys = Array.from(requiredExpansions).sort();
    const currentExpandedSorted = [...expandedKeys].sort();

    // Compare sorted arrays to avoid unnecessary updates
    if (JSON.stringify(newExpandedKeys) === JSON.stringify(currentExpandedSorted)) {
      return;
    }

    // When search is active, preserve search-driven expansions
    // When not searching, MERGE with existing manual expansions instead of replacing
    if (searchValue) {
      const mergedKeys = Array.from(new Set([...expandedKeys, ...newExpandedKeys]));
      setExpandedKeys(mergedKeys);
    } else {
      const mergedKeys = Array.from(new Set([...expandedKeys, ...newExpandedKeys]));
      setExpandedKeys(mergedKeys);
    }
  }, [treeData, checkedKeys]); // Run when tree data or selections change

  // Debounced search function
  const debouncedSearch = useCallback(
    debounce((value) => {
      if (value) {
        console.log('MUI Applying search filter:', value);
        const { filteredData, expandedKeys } = filterTreeData(muiTreeData, value);
        setFilteredTreeData(filteredData);
        setExpandedKeys(prev => [...prev, ...expandedKeys]);
      } else {
        setFilteredTreeData([]);
        console.log('MUI Clearing search filter');
      }
    }, 300),
    [muiTreeData]
  );

  // Sort the tree data recursively
  const sortTreeData = (nodes) => {
    return nodes.sort((a, b) =>
      String(a.title).toLowerCase()
        .localeCompare(String(b.title).toLowerCase())
    )
      .map(node => ({
        ...node,
        children: node.children ? sortTreeData(node.children) : undefined
      }));
  };

  // Handle search input change
  const handleSearchChange = (e) => {
    const value = e.target.value;
    setSearchValue(value);
    debouncedSearch(value);
  };

  // Clear search
  const handleClearSearch = () => {
    setSearchValue('');
    debouncedSearch('');
  };

  // Filter tree data based on search value
  const filterTreeData = (data, searchValue) => {
    const expandedKeys: string[] = [];

    const filter = (node) => {
      const nodeTitle = String(node.title);
      const nodeMatches = nodeTitle.toLowerCase().includes(searchValue.toLowerCase());

      if (node.children) {
        const filteredChildren = node.children
          .map(child => filter(child))
          .filter(child => child !== null);

        // If this node matches, include it with all its children (unfiltered)
        if (nodeMatches) {
          expandedKeys.push(node.key);
          return { ...node, children: node.children }; // Return with all children
        }

        // If this node doesn't match but has matching children, include it with filtered children
        if (filteredChildren.length > 0) {
          expandedKeys.push(node.key);
          return { ...node, children: filteredChildren };
        }
      }

      // If this is a leaf node, only include it if it matches
      return nodeMatches ? node : null;
    };

    const filteredData = data
      .map(node => filter(node))
      .filter(node => node !== null);

    return { filteredData, expandedKeys };
  };

  // Refresh folders
  const refreshFolders = async () => {
    setIsRefreshing(true);
    try {
      // Just trigger the refresh - let FolderContext handle the rest
      const response = await fetch('/api/folders?refresh=true');
      if (!response.ok) {
        throw new Error(`Failed to refresh folders: ${response.status}`);
      }

      // Trigger FolderContext to refetch by dispatching an event
      window.dispatchEvent(new CustomEvent('refreshFolders'));
      message.success('Folder structure refreshed');
    } catch (err) {
      console.error('Failed to refresh folders:', err);
      message.error('Failed to refresh folders');
    } finally {
      setIsRefreshing(false);
    }
  };

  // Browse directory on server
  const browseDirectory = useCallback(async (dirPath: string) => {
    setBrowseLoading(true);
    try {
      const response = await fetch(`/api/browse-directory?path=${encodeURIComponent(dirPath)}`);
      if (response.ok) {
        const data = await response.json();
        setBrowsePath(data.current_path || dirPath);
        setPathInput(data.current_path || dirPath);
        setBrowseEntries(data.entries || []);
      } else {
        const error = await response.json();
        message.error(error.detail || 'Failed to browse directory');
      }
    } catch (error) {
      console.error('Error browsing directory:', error);
      message.error('Failed to browse directory');
    } finally {
      setBrowseLoading(false);
    }
  }, []);

  // Open add path dialog
  const handleOpenAddPathDialog = useCallback(() => {
    setAddPathDialogOpen(true);
    setSelectedPaths(new Set());
    setAddMode('browser'); // Default to browser (safer, allows review before adding to context) // Default to context for files
    browseDirectory(projectRoot);
  }, [browseDirectory, projectRoot]);

  // Close dialog
  const handleCloseAddPathDialog = useCallback(() => {
    setAddPathDialogOpen(false);
    setSelectedPaths(new Set());
    setBrowseEntries([]);
  }, []);

  // Handle path input submit
  const handlePathInputSubmit = useCallback(() => {
    if (pathInput.trim()) {
      browseDirectory(pathInput.trim());
    }
  }, [pathInput, browseDirectory]);

  // Handle path input keydown
  const handlePathInputKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handlePathInputSubmit();
    }
  }, [handlePathInputSubmit]);

  // Toggle path selection
  const togglePathSelection = useCallback((path: string) => {
    setSelectedPaths(prev => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);
  // Quick add single item
  const handleQuickAdd = useCallback(async (path: string) => {
    try {
      const isDir = browseEntries.find(e => e.path === path)?.is_dir;

      const response = await fetch('/api/add-explicit-paths', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          paths: [path],
          // Files always go directly to context
          // Directories use the current addMode setting
          add_to_context: isDir ? (addMode === 'context') : true
        })
      });


      if (response.ok) {
        message.success(`Added: ${path.split('/').pop()}`);
        // Files always go to context, so only trigger context update
        // Directories respect addMode setting
        if (isDir && addMode === 'browser') {
          // Only refresh folders if we added a directory to the browser
          window.dispatchEvent(new CustomEvent('refreshFolders'));
        } else {
          // Files and context-mode directories trigger context update
          window.dispatchEvent(new CustomEvent('contextUpdated'));
        }
      } else {
        const error = await response.json();
        message.error(error.detail || 'Failed to add path');
      }
    } catch (error) {
      console.error('Error adding path:', error);
      message.error('Failed to add path');
    }
  }, [addMode, browseEntries]);

  // Navigate up one directory
  const handleNavigateUp = useCallback(() => {
    const parentPath = browsePath.replace(/\/[^/]+\/?$/, '') || '/';
    browseDirectory(parentPath);
  }, [browsePath, browseDirectory]);

  // Add selected paths
  const handleAddSelectedPaths = useCallback(async () => {
    if (selectedPaths.size === 0) {
      message.warning('No items selected');
      return;
    }

    // Check if any selected paths are directories
    const selectedEntries = browseEntries.filter(e => selectedPaths.has(e.path));
    const hasDirectories = selectedEntries.some(e => e.is_dir);
    const onlyFiles = selectedEntries.every(e => !e.is_dir);

    try {
      const response = await fetch('/api/add-explicit-paths', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          paths: Array.from(selectedPaths),
          // Files always go to context, directories respect addMode
          add_to_context: onlyFiles ? true : (addMode === 'context')
        })
      });

      if (response.ok) {
        const result = await response.json();
        message.success(`Added ${result.added_count || selectedPaths.size} path(s)`);
        handleCloseAddPathDialog();
        window.dispatchEvent(new CustomEvent('refreshFolders'));
      } else {
        const error = await response.json();
        message.error(error.detail || 'Failed to add paths');
      }
    } catch (error) {
      console.error('Error adding paths:', error);
      message.error('Failed to add paths');
    }
  }, [selectedPaths, addMode, browseEntries, handleCloseAddPathDialog]);

  // Handle checkbox click
  // This function is crucial for selecting/deselecting folders and files
  const handleCheckboxClick = (nodeId, checked) => {
    // Clear token calculation cache immediately when any selection changes
    tokenCalculationCache.current.clear();

    // Immediate visual feedback - just toggle the clicked node for instant response
    setCheckedKeys(prev => {
      const currentChecked = prev.map(String);
      if (checked && !currentChecked.includes(String(nodeId))) {
        return [...prev, String(nodeId)];
      } else if (!checked) {
        const filtered = prev.filter(k => String(k) !== String(nodeId));
        // If this results in no checked keys, ensure we return an empty array
        if (filtered.length === 0) {
          return [];
        }
        return filtered;
      }
      return prev;
    });

    console.log('MUI Checkbox click:', { nodeId, checked });

    // Debounce rapid clicks
    if (Date.now() - lastClickRef.current < 200) return;
    lastClickRef.current = Date.now();

    // Find the clicked node in the tree
    const findNodeInTree = (nodes, targetId) => {
      for (const node of nodes) {
        if (String(node.key) === String(targetId)) {
          return node;
        }
        if (node.children && node.children.length > 0) {
          const found = findNodeInTree(node.children, targetId);
          if (found) return found;
        }
      }
      return null;
    };

    // Get all descendant keys (children, grandchildren, etc.)
    const getAllDescendantKeys = (node: any): string[] => {
      const keys: string[] = [];
      if (node.children && node.children.length > 0) {
        for (const child of node.children) {
          keys.push(String(child.key));
          keys.push(...getAllDescendantKeys(child));
        }
      }
      return keys;
    };

    // Get all ancestor keys (parent, grandparent, etc.)
    const getAllAncestorKeys = (targetKey: string, tree: any[]): string[] => {
      const findPath = (nodes: any[], target: string, path: string[] = []): string[] | null => {
        for (const node of nodes) {
          const currentPath = [...path, String(node.key)];
          if (String(node.key) === target) {
            return currentPath.slice(0, -1); // Return path without the target itself
          }
          if (node.children && node.children.length > 0) {
            const found = findPath(node.children, target, currentPath);
            if (found) return found;
          }
        }
        return null;
      };
      return findPath(tree, targetKey) || [];
    };

    // Check if all children of a node are selected
    const areAllChildrenSelected = (node: any, currentCheckedKeys: string[]): boolean => {
      if (!node.children || node.children.length === 0) return true;

      const checkedSet = new Set(currentCheckedKeys);
      return node.children.every(child => {
        const childKey = String(child.key);

        // If the child is directly selected, it's considered selected
        if (checkedSet.has(childKey)) {
          // But if it's a directory, we also need to verify all its children are selected
          if (nodeHasChildren(child)) {
            return areAllChildrenSelected(child, currentCheckedKeys);
          }
          return true; // File is selected
        }

        // If child is not directly selected but is a directory, 
        // check if all its children are selected (making it implicitly selected)
        if (nodeHasChildren(child)) {
          return areAllChildrenSelected(child, currentCheckedKeys);
        }

        // File is not selected
        return false;
      });
    };

    // Check if any children of a node are selected
    const areAnyChildrenSelected = (node: any, currentCheckedKeys: string[]): boolean => {
      if (!node.children || node.children.length === 0) return false;

      const checkedSet = new Set(currentCheckedKeys);
      return node.children.some(child => {
        return checkedSet.has(String(child.key)) || areAnyChildrenSelected(child, currentCheckedKeys);
      });
    };

    const clickedNode = findNodeInTree(muiTreeData, nodeId);
    if (!clickedNode) {
      console.warn('Could not find clicked node:', nodeId);
      return;
    }

    // Use setTimeout to batch the full hierarchy update after immediate feedback
    setTimeout(() => {
      setCheckedKeys(prev => {
        const currentChecked = prev.map(String);
        const checkedSet = new Set(currentChecked);
        let newCheckedKeys = [...currentChecked];

        if (checked) {
          // Add the node itself
          if (!checkedSet.has(String(nodeId))) {
            newCheckedKeys.push(String(nodeId));
          }

          // Add all descendants
          const descendantKeys = getAllDescendantKeys(clickedNode);
          descendantKeys.forEach(key => {
            if (!checkedSet.has(key)) {
              newCheckedKeys.push(key);
            }
          });
        } else {
          // Remove the node itself and all descendants
          const keysToRemove = new Set([String(nodeId), ...getAllDescendantKeys(clickedNode)]);
          newCheckedKeys = newCheckedKeys.filter(key => !keysToRemove.has(key));
        }

        // Also handle the case when selecting a child - update parents
        if (checked) {
          const ancestorKeys = getAllAncestorKeys(String(nodeId), muiTreeData);
          ancestorKeys.forEach(ancestorKey => {
            // Skip processing ancestors that are already correctly selected
            if (newCheckedKeys.includes(ancestorKey)) {
              return;
            }

            const ancestorNode = findNodeInTree(muiTreeData, ancestorKey);
            if (ancestorNode) {
              // Only add parent if ALL children are selected - this is the key fix
              const allChildrenSelected = areAllChildrenSelected(ancestorNode, newCheckedKeys);
              if (allChildrenSelected) {
                newCheckedKeys.push(ancestorKey);
              }
            }
          });
        } else {
          // When deselecting, remove any ancestors that should no longer be selected
          const ancestorKeys = getAllAncestorKeys(String(nodeId), muiTreeData);
          ancestorKeys.forEach(ancestorKey => {
            const ancestorNode = findNodeInTree(muiTreeData, ancestorKey);
            if (ancestorNode) {
              // Remove ancestor if not all children are selected
              const allChildrenSelected = areAllChildrenSelected(ancestorNode, newCheckedKeys);
              if (!allChildrenSelected) {
                // Not all children selected - remove ancestor from selection
                newCheckedKeys = newCheckedKeys.filter(key => key !== ancestorKey);
              }
            }
          });
        }

        // Clear the token calculation cache when selections change
        tokenCalculationCache.current.clear();

        const finalKeys = Array.from(new Set(newCheckedKeys));
        console.log('Final checked keys after hierarchy update:', finalKeys);
        return finalKeys;
      });
    }, 0);

    // Clear the token calculation cache when selections change
    tokenCalculationCache.current.clear();
  };

  // Helper function to calculate included tokens for a directory's children
  const calculateChildrenIncluded = useCallback((node: any): number => {
    if (!node.children) return 0;
    if (!folders && !node.children.length) return 0;

    // Use cached result if available
    const nodePath = String(node.key);
    const cacheKey = `${nodePath}:${checkedKeys.join(',')}`;
    if (tokenCalculationCache.current.has(cacheKey)) {
      return tokenCalculationCache.current.get(cacheKey);
    }

    let included = 0;
    for (const child of node.children) {
      const childKey = String(child.key);
      const isChildDirectlySelected = checkedKeys.includes(childKey);

      if (isChildDirectlySelected) {
        // For files, prioritize accurate counts
        const isFile = !nodeHasChildren(child);
        let childTotal = 0;

        if (isFile) {
          // For files, use accurate count first, then fall back to estimated
          const childAccurate = accurateTokenCounts[childKey];
          // Only use accurate data if it exists AND has a positive count
          childTotal = (childAccurate && childAccurate.count > 0) ? childAccurate.count : getFolderTokenCount(childKey, folders || {});
        } else {
          // For directories, recursively calculate
          childTotal = calculateChildrenTotal(child);
        }

        // Skip tool-backed files (indicated by -1)
        if (childTotal === -1) {
          continue;
        }

        included += childTotal;
        // Debug logging removed to improve performance
      } else if (nodeHasChildren(child)) {
        // Only include partial selections from subdirectories
        const childIncluded = calculateChildrenIncluded(child);
        included += childIncluded;
        // Debug logging removed to improve performance
      }
      // Debug logging removed to improve performance
    }

    // Cache the result
    tokenCalculationCache.current.set(cacheKey, included);
    return included;
  }, [checkedKeys, folders, accurateTokenCounts, getFolderTokenCount, nodeHasChildren]);

  // Helper function to calculate total tokens for a directory's children
  const calculateChildrenTotal = useCallback((node: any): number => {
    if (!node.children) return 0;
    if (!folders) return 0;

    // Use cached result if available
    const nodePath = String(node.key);
    const cacheKey = `total:${nodePath}`;
    if (tokenCalculationCache.current.has(cacheKey)) {
      return tokenCalculationCache.current.get(cacheKey);
    }

    let total = 0;
    for (const child of node.children) {
      const childKey = String(child.key);
      if (nodeHasChildren(child)) {
        // For directories, recursively calculate
        total += calculateChildrenTotal(child);
      } else {
        // For files, use accurate count if available
        const childAccurate = accurateTokenCounts[childKey];
        if (childAccurate && childAccurate.count !== undefined) {
          // Use accurate count (even if 0 or -1)
          total += Math.max(0, childAccurate.count); // Treat -1 (tool-backed) as 0 for totals
        } else {
          total += getFolderTokenCount(childKey, folders || {}) || 0;
        }
      }
    }

    tokenCalculationCache.current.set(cacheKey, total);
    return total;
  }, [folders, accurateTokenCounts, getFolderTokenCount, nodeHasChildren]);


  const getTokenCount = useCallback((key: string) => {
    return accurateTokenCounts[String(key)]?.count ?? getFolderTokenCount(key, folders || {});
  }, [accurateTokenCounts, getFolderTokenCount]);

  // Calculate token counts for a node
  const calculateTokens = useCallback((node, overrideTokens?: number): { included: number; total: number } => {
    const nodePath = node.key;
    // For leaf nodes (files)
    //
    if (!nodeHasChildren(node)) {
      // Get token count directly from accurateTokenCounts
      const tokenCount = accurateTokenCounts[nodePath]?.count || getFolderTokenCount(nodePath, folders || {}) || 0;
      const result = {
        included: checkedKeys.includes(nodePath) ? tokenCount : 0,
        total: tokenCount
      }
      return result;
    }

    // For directories, use the existing functions that work for individual files
    const total = calculateChildrenTotal(node);
    const included = checkedKeys.includes(nodePath) ? total : calculateChildrenIncluded(node);

    return { included, total };

  }, [checkedKeys, accurateTokenCounts, getFolderTokenCount, calculateChildrenIncluded, calculateChildrenTotal, nodeHasChildren]);

  // Clear token calculation cache when accurate counts change
  useLayoutEffect(() => {
    // Only run if we have accurate token counts
    // Clear cache immediately when accurate counts change
    tokenCalculationCache.current.clear();

    const accurateCountsKeys = Object.keys(accurateTokenCounts);
    if (accurateCountsKeys.length > 0 &&
      JSON.stringify(accurateCountsKeys) !== JSON.stringify(Object.keys(lastAccurateCountsRef.current))) {
      console.log('Accurate token counts changed, clearing calculation cache');

      // Clear the calculation cache
      tokenCalculationCache.current.clear();

      // Force a re-render by updating tree data
      if (treeData.length > 0) {
        // Create a shallow copy to trigger re-render without expensive deep copy
        setTreeData([...treeData]);
      }

      // Update the reference to the current accurate counts
      lastAccurateCountsRef.current = { ...accurateTokenCounts };
    }
  }, [accurateTokenCounts, setTreeData, treeData]);

  // Listen for accurate token counts update events
  useEffect(() => {
    const handleAccurateTokenCountsUpdated = (event) => {
      console.log('Received accurateTokenCountsUpdated event:', event.detail);
      // Clear the token calculation cache
      tokenCalculationCache.current.clear();
      // Force a re-render of the tree
      setTreeData(prevData => [...prevData]); // Shallow copy for performance
    };

    window.addEventListener('accurateTokenCountsUpdated', handleAccurateTokenCountsUpdated);
    return () => window.removeEventListener('accurateTokenCountsUpdated', handleAccurateTokenCountsUpdated);
  });
  // Show loading state while scanning and no data
  if ((isScanning || isInitialLoad) && (!hasLoadedData || !muiTreeData || muiTreeData.length === 0)) {
    const showSlowLoadingTip = scanProgress && scanProgress.elapsed >= 60;
    return (
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1, minHeight: '200px' }}>
        <Box sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '200px',
          gap: 2
        }}>
          <LinearProgress sx={{ width: '80%' }} />
          <Typography variant="body2" color="text.secondary" align="center">
            {scanProgress ?
              `Scanning: ${scanProgress.files} files, ${scanProgress.directories} directories (${scanProgress.elapsed.toFixed(1)}s)` :
              'Loading folder structure...'}
            <br />
            <Typography variant="caption" color="text.secondary">
              This may take a moment for large repositories
            </Typography>
          </Typography>
          {showSlowLoadingTip && (
            <Typography variant="caption" color="warning.main">
              Tip: Use --include-only or --exclude flags to restrict scope
            </Typography>
          )}
        </Box>
      </Box>
    );
  }

  // Show error state if scan failed and no cached data
  if (scanError && (!muiTreeData || muiTreeData.length === 0)) {
    return (
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1, minHeight: '200px' }}>
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <Typography variant="h6" color="error" gutterBottom>
            Failed to load folder structure
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {scanError}
          </Typography>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={refreshFolders}
            sx={{ mt: 2 }}
            size="small"
          >
            Try Again
          </Button>
        </Box>
      </Box>
    );
  }

  // Show empty state if no folders loaded and not scanning
  if (!isScanning && !isInitialLoad && hasLoadedData && (!muiTreeData || muiTreeData.length === 0)) {
    return (
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1, minHeight: '200px' }}>
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <Typography variant="body1" color="text.secondary" gutterBottom>
            No files found
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Try refreshing or check your directory permissions
          </Typography>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={refreshFolders}
            sx={{ mt: 2 }}
            size="small"
          >
            Refresh Files
          </Button>
        </Box>
      </Box>
    );
  }

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1 }}>
      <Box sx={{ mb: 1, flexShrink: 0 }}>
        <TextField
          fullWidth
          placeholder="Search folders"
          value={searchValue}
          onChange={handleSearchChange}
          id="folder-search"
          name="folder-search"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
            endAdornment: searchValue ? (
              <InputAdornment position="end">
                <IconButton size="small" onClick={handleClearSearch}>
                  <ClearIcon fontSize="small" />
                </IconButton>
              </InputAdornment>
            ) : null
          }}
          size="small"
          variant="outlined"
        />
      </Box>

      <Box sx={{ mb: 1, flexShrink: 0 }}>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={refreshFolders}
            disabled={isRefreshing}
            size="small"
          >
            {isRefreshing ? 'Refreshing...' : 'Refresh Files'}
          </Button>
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={handleOpenAddPathDialog}
            size="small"
          >
            Add Path
          </Button>
        </Box>
      </Box>

      <Box sx={{ flexGrow: 1, overflow: 'auto', minHeight: 0 }}>
        {/* Show overlay when scanning with existing data */}
        {isScanning && muiTreeData && muiTreeData.length > 0 && (
          <Box sx={{ mb: 1 }}>
            <LinearProgress sx={{ mb: 0.5 }} />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', textAlign: 'center' }}>
              {scanProgress ?
                `Scanning: ${scanProgress.files} files, ${scanProgress.directories} directories (${scanProgress.elapsed.toFixed(1)}s)` :
                'Scanning folder structure...'}
            </Typography>
          </Box>
        )}

        {(isScanning && !hasLoadedData) ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <LinearProgress sx={{ width: '80%', mb: 2 }} />
            <Typography variant="body2" color="text.secondary">
              Loading folder structure...
            </Typography>
          </Box>
        ) : (
          <Box sx={{
            height: '100%',
            overflowY: 'auto',
            position: 'relative',
            opacity: (isScanning && muiTreeData && muiTreeData.length > 0) ? 0.7 : 1,
            pointerEvents: (isScanning && muiTreeData && muiTreeData.length > 0) ? 'none' : 'auto',
            transition: 'opacity 0.3s ease',
            '& .MuiBox-root': {
              maxWidth: '100%'
            }
          }}>
            {scanError && muiTreeData && muiTreeData.length > 0 && (
              <Typography variant="caption" color="error" sx={{ display: 'block', mb: 1, px: 1 }}>
                Warning: {scanError} (showing cached data)
              </Typography>
            )}
            {/* Only render visible nodes */}
            {(searchValue ? filteredTreeData : muiTreeData)
              .slice(0, isInitialLoad ? 0 : undefined)
              .map(node => (
                <TreeNode key={node.key} node={node} level={0} originalNode={searchValue ? findOriginalNode(node.key) : undefined} />
              ))}
          </Box>
        )}
      </Box>

      {/* Add Path Dialog */}
      <Dialog
        open={addPathDialogOpen}
        onClose={handleCloseAddPathDialog}
        maxWidth="sm"
        fullWidth
        PaperProps={{ sx: { height: '70vh', maxHeight: 600 } }}
      >
        <DialogTitle sx={{ pb: 1 }}>Add Files</DialogTitle>
        <DialogContent sx={{ p: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Path input bar */}
          <Box sx={{ px: 2, py: 1.5, borderBottom: 1, borderColor: 'divider', display: 'flex', gap: 1, alignItems: 'center' }}>
            <Typography variant="body2" sx={{ fontWeight: 500, flexShrink: 0 }}>Path:</Typography>
            <TextField
              size="small"
              fullWidth
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              onKeyDown={handlePathInputKeyDown}
              placeholder="/path/to/directory"
              sx={{
                '& .MuiOutlinedInput-root': {
                  fontFamily: 'monospace',
                  fontSize: '0.875rem'
                }
              }}
            />
            <Button
              variant="contained"
              size="small"
              onClick={handlePathInputSubmit}
              disabled={browseLoading}
              sx={{ flexShrink: 0 }}
            >
              Go
            </Button>
          </Box>

          {/* Directory listing */}
          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {browseLoading ? (
              <LinearProgress />
            ) : (
              <List dense disablePadding>
                {/* Parent directory entry */}
                {browsePath && browsePath !== '/' && (
                  <ListItemButton onClick={handleNavigateUp} sx={{ borderBottom: 1, borderColor: 'divider' }}>
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      <ArrowUpwardIcon fontSize="small" />
                    </ListItemIcon>
                    <ListItemText primary=".." secondary="Parent directory" />
                  </ListItemButton>
                )}

                {browseEntries.map((entry) => (
                  <ListItemButton
                    key={entry.path}
                    selected={selectedPaths.has(entry.path)}
                    onClick={() => {
                      // Click to navigate into directory, or toggle selection for files
                      entry.is_dir ? browseDirectory(entry.path) : togglePathSelection(entry.path);
                    }}
                    sx={{ pr: 1 }}
                  >
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      {entry.is_dir ? (
                        <FolderIcon fontSize="small" sx={{ color: 'primary.main' }} />
                      ) : (
                        <InsertDriveFileIcon fontSize="small" sx={{ color: 'text.secondary' }} />
                      )}
                    </ListItemIcon>
                    <ListItemText
                      primary={entry.name}
                      primaryTypographyProps={{
                        fontWeight: selectedPaths.has(entry.path) ? 600 : 400,
                        color: selectedPaths.has(entry.path) ? 'primary.main' : 'text.primary'
                      }}
                      secondary={entry.is_dir ? (
                        <Typography variant="caption" component="span" sx={{ fontSize: '0.7rem', opacity: 0.7 }}>
                          Click to open • Click [+] to add directory
                        </Typography>
                      ) : undefined}
                      secondaryTypographyProps={{
                        component: 'div'
                      }}
                    />
                    <IconButton
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (entry.is_dir) {
                          // For directories, toggle selection instead of quick add
                          togglePathSelection(entry.path);
                        } else {
                          handleQuickAdd(entry.path);
                        }
                      }}
                      title={`Add ${entry.name}`}
                      sx={{
                        opacity: 0.6,
                        '&:hover': { opacity: 1, color: 'primary.main' }
                      }}
                    >
                      <AddIcon fontSize="small" />
                    </IconButton>
                  </ListItemButton>
                ))}

                {browseEntries.length === 0 && !browseLoading && (
                  <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
                    Empty directory
                  </Typography>
                )}
              </List>
            )}
          </Box>

          {/* Add mode selection - only show if directories are selected */}
          {(() => {
            const selectedEntries = browseEntries.filter(e => selectedPaths.has(e.path));
            const hasDirectories = selectedEntries.some(e => e.is_dir);

            if (!hasDirectories) return null;

            return (
              <Box sx={{ px: 2, py: 1.5, borderTop: 1, borderColor: 'divider', bgcolor: 'action.hover' }}>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                  What to do with selected directories?
                </Typography>
                <RadioGroup
                  row
                  value={addMode}
                  onChange={(e) => setAddMode(e.target.value as 'browser' | 'context')}
                >
                  <FormControlLabel
                    value="browser"
                    control={<Radio size="small" />}
                    label={<Typography variant="body2">Add directory to file browser</Typography>}
                  />
                  <FormControlLabel
                    value="context"
                    control={<Radio size="small" />}
                    label={<Typography variant="body2">Add all child files directly to context</Typography>}
                  />
                </RadioGroup>
              </Box>
            );
          })()}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseAddPathDialog}>Cancel</Button>
          <Button
            onClick={handleAddSelectedPaths}
            variant="contained"
            disabled={selectedPaths.size === 0}
          >
            Add{selectedPaths.size > 0 ? ` (${selectedPaths.size})` : ''}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};
