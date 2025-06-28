import React, { useState, useEffect, useCallback, useMemo, useRef, useLayoutEffect } from 'react';
import { useFolderContext } from '../context/FolderContext';
import { useTheme } from '../context/ThemeContext';
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
import Collapse from '@mui/material/Collapse';

// MUI icons
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import ArrowRightIcon from '@mui/icons-material/ArrowRight';
import RefreshIcon from '@mui/icons-material/Refresh';
import SearchIcon from '@mui/icons-material/Search';
import ClearIcon from '@mui/icons-material/Clear';
export const MUIFileExplorer = () => {
  const {
    treeData,
    setTreeData,
    folders,
    checkedKeys,
    isScanning,
    scanError,
    setCheckedKeys,
    expandedKeys,
    setExpandedKeys,
    getFolderTokenCount,
    accurateTokenCounts,
  } = useFolderContext();

  const { isDarkMode } = useTheme();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [searchValue, setSearchValue] = useState('');
  const [filteredTreeData, setFilteredTreeData] = useState<any[]>([]);
  const [autoExpandParent, setAutoExpandParent] = useState(false);

  // Cache for token calculations
  const tokenCalculationCache = useRef(new Map());
  const lastAccurateCountsRef = useRef<Record<string, any>>({});

  // Force recalculation when accurate token counts change
  // Helper function to determine if a node has children
  const nodeHasChildren = (node: any): boolean => {
    return !!(node && node.children && Array.isArray(node.children) && node.children.length > 0);
  };

  // Use the same tree data as the Ant Design version for consistency
  const muiTreeData = useMemo(() => {
    console.log('MUI Using treeData from context:', {
      nodeCount: treeData.length,
      treeDataExists: !!treeData,
      hasChildren: treeData.filter(node => node.children && node.children.length > 0).length,
      sampleNode: treeData[0],
      sampleNodeChildren: treeData[0]?.children?.length || 0
    });

    // Debug the structure of the first few nodes
    console.log('MUI First 3 tree nodes:', treeData.slice(0, 3).map(node => ({
      key: node.key,
      title: node.title,
      hasChildren: !!(node.children && node.children.length > 0),
      childCount: node.children ? node.children.length : 0
    })));

    // Debug log for specific folders we know should have children
    const frontendNode = treeData.find(node => node.key === 'frontend');
    if (frontendNode) {
      console.log('MUI Frontend node structure:', {
        key: frontendNode.key,
        hasChildren: !!(frontendNode.children && frontendNode.children.length > 0),
        childCount: frontendNode.children ? frontendNode.children.length : 0,
        children: frontendNode.children?.slice(0, 3)
      });
    }

    // If we have tree data, we're no longer loading
    if (treeData.length > 0) {
      setIsLoading(false);
    }

    return treeData;
  }, [treeData]);

  // Simple custom tree renderer that manually handles hierarchy
  const TreeNode = ({ node, level = 0 }) => {
    const hasChildren = node.children && node.children.length > 0;
    const isExpanded = expandedKeys.includes(String(node.key));
    const isChecked = checkedKeys.includes(String(node.key));

    // Always calculate token counts using the original full tree, not filtered data
    const originalNode = findNodeInOriginalTree(node.key);
    
    // Use accurate count if available for this specific file
    let nodeTokens = folders ? getFolderTokenCount(String(node.key), folders) : 0;
    const accurateData = accurateTokenCounts[String(node.key)];
    
    // Only use accurate counts for files, not directories
    // And only log once per render cycle to avoid spamming
    if (accurateData && !hasChildren && !node.loggedAccurate) {
      nodeTokens = accurateData.count;
      console.log(`Using accurate token count for ${node.key}: ${nodeTokens} (estimated would be ${folders ? getFolderTokenCount(String(node.key), folders) : 0})`);
    }
    
    const { total, included } = calculateTokens(originalNode || node, folders, nodeTokens);

    // Check if this node is indeterminate (some but not all children selected)
    const isIndeterminate = hasChildren && !isChecked &&
      node.children.some(child => checkedKeys.includes(String(child.key)));

    // Extract clean label and token count
    const titleMatch = String(node.title).match(/^(.+?)\s*\(([0-9,]+)\s*tokens?\)$/);
    const cleanLabel = titleMatch ? titleMatch[1] : String(node.title);

    const handleToggle = () => {
      if (hasChildren) {
        setExpandedKeys(prev =>
          isExpanded
            ? prev.filter(key => key !== String(node.key))
            : [...prev, String(node.key)]
        );
      }
    };

    const handleCheck = (event) => {
      event.stopPropagation();
      handleCheckboxClick(String(node.key), !isChecked);
    };

    return (
      <Box key={node.key}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            py: 0.25,
            pl: level * 2 + 1, // Reduced padding to move everything left
            pr: 1, // Add right padding to prevent text from touching scrollbar
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
              left: level * 14 - 10, // More aggressive multiplier for better alignment
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
              fontWeight: hasChildren ? 'bold' : (isChecked && total > 0 ? 'bold' : 'normal'),
              color: isChecked && !hasChildren && total > 0 ? (isDarkMode ? '#ffffff' : '#000000') : (isDarkMode ? 'text.primary' : 'inherit')
            }}
          >
            {cleanLabel}
          </Typography>

          {/* Token count */}
          {!hasChildren && total > 0 && (
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
              ({total.toLocaleString()})
            </Typography>
          )}

          {/* Token display for folders showing included/total */}
          {hasChildren && total > 0 && (
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
                {included.toLocaleString()}
              </Typography>/{total.toLocaleString()})
            </Typography>
          )}
        </Box>

        {/* Children */}
        {hasChildren && (
          <Collapse in={isExpanded}>
            <Box sx={{ pl: 1 }}>
              {node.children.map(child => (
                <TreeNode key={child.key} node={child} level={level + 1} />
              ))}
            </Box>
          </Collapse>
        )}
      </Box>
    );
  };

  // Helper function to find a node in the original unfiltered tree
  const findNodeInOriginalTree = (nodeKey: string): any => {
    const findInTree = (tree: any[], key: string): any => {
      for (const node of tree) {
        if (String(node.key) === String(key)) {
          return node;
        }
        if (node.children) {
          const found = findInTree(node.children, key);
          if (found) return found;
        }
      }
      return null;
    };
    return findInTree(muiTreeData, nodeKey);
  };

  // Effect to load folders on component mount
  useEffect(() => {
    const loadFolders = async () => {
      if (isLoading) return; // Prevent multiple simultaneous loads
      try {
        const response = await fetch('/api/folders');
        if (!response.ok) {
          throw new Error(`Failed to load folders: ${response.status}`);
        }
        const data: Folders = await response.json();

        // Convert and sort data
        const sortedData = sortTreeData(convertToTreeData(data));
        setTreeData(sortedData);
      } catch (err) {
        console.error('Failed to load folders:', err);
        message.error('Failed to load folder structure');
      } finally {
        setIsLoading(false);
      }
    };

    // Only load folders if we don't already have them
    // Also check if we're not already loading to prevent race conditions
    if ((!folders || Object.keys(folders).length === 0) && !isLoading) {
      setIsLoading(true);
      loadFolders();
    } else {
      if (folders && Object.keys(folders).length > 0) setIsLoading(false);
    }
  }, [folders, setTreeData]);

  // Debounced search function
  const debouncedSearch = useCallback(
    debounce((value) => {
      if (value) {
        console.log('MUI Applying search filter:', value);
        const { filteredData, expandedKeys } = filterTreeData(muiTreeData, value);
        setFilteredTreeData(filteredData);
        setExpandedKeys(prev => [...prev, ...expandedKeys]);
        setAutoExpandParent(true);
      } else {
        setFilteredTreeData([]);
        setAutoExpandParent(false);
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
      if (nodeTitle.toLowerCase().includes(searchValue.toLowerCase())) {
        expandedKeys.push(node.key);
        return node;
      }

      if (node.children) {
        const filteredChildren = node.children
          .map(child => filter(child))
          .filter(child => child !== null);

        if (filteredChildren.length > 0) {
          expandedKeys.push(node.key);
          return { ...node, children: filteredChildren };
        }
      }

      return null;
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
      const response = await fetch('/api/folders?refresh=true');
      if (!response.ok) {
        throw new Error(`Failed to refresh folders: ${response.status}`);
      }
      const data: Folders = await response.json();

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

      // Convert and sort data
      const sortedData = sortTreeData(convertToTreeData(data));

      setTreeData(sortedData);
      message.success('Folder structure refreshed');
    } catch (err) {
      console.error('Failed to refresh folders:', err);
      message.error('Failed to refresh folders');
    } finally {
      setIsRefreshing(false);
    }
  };
  // Handle checkbox click
  // This function is crucial for selecting/deselecting folders and files
  const handleCheckboxClick = (nodeId, checked) => {
    console.log('MUI Checkbox click:', { nodeId, checked });

    // Find the node in the tree
    const findNode = (nodes, id) => {
      for (const node of nodes) {
        if (String(node.key) === String(id)) {
          return node;
        }
        if (node.children) {
          const found = findNode(node.children, id);
          if (found) return found;
        }
      }
      return null;
    };

    // Get all child keys
    const getAllChildKeys = (node): string[] => {
      let keys = [String(node.key)];
      if (node.children) {
        node.children.forEach(child => {
          keys = keys.concat(getAllChildKeys(child));
        });
      }
      return keys;
    };

    // Get all parent keys
    const getAllParentKeys = (key: React.Key, tree: any[]): string[] => {
      let parentKeys: string[] = [];
      const findParent = (currentKey, nodes) => {
        for (let i = 0; i < nodes.length; i++) {
          const node = nodes[i];
          if (node.children && node.children.some(child => String(child.key) === String(currentKey))) {
            parentKeys.push(String(node.key));
            return node.key;
          } else if (node.children) {
            const foundParent = findParent(currentKey, node.children);
            if (foundParent) {
              parentKeys.push(String(node.key));
              return foundParent;
            }
          }
        }
        return null;
      };

      findParent(key, tree);
      return parentKeys;
    };

    const node = findNode(treeData, nodeId);

    if (checked) {
      // Add this node and all its children
      console.log('MUI Adding node and children:', nodeId, nodeHasChildren(node));
      const keysToAdd = nodeHasChildren(node) ? getAllChildKeys(node) : [String(nodeId)];
      setCheckedKeys(prev => [...new Set([...prev.map(String), ...keysToAdd])]);
    } else {
      // Remove this node and all its children
      const keysToRemove = nodeHasChildren(node) ? getAllChildKeys(node) : [String(nodeId)];
      // Also remove parent selections if needed
      const parentKeys = getAllParentKeys(nodeId, muiTreeData);
      setCheckedKeys(prev =>
        prev.map(String).filter(key => !keysToRemove.includes(key) && !parentKeys.includes(key))
      );
    }

    // Clear the token calculation cache when selections change
    tokenCalculationCache.current.clear();
  };

  // Calculate token counts for a node
  const calculateTokens = useCallback((node, folders, overrideTokens?: number) => {
    const nodePath = node.key;
    const cacheKey = `${String(nodePath)}_${overrideTokens || 0}_${Object.keys(accurateTokenCounts).length}`;

    if (tokenCalculationCache.current.has(cacheKey)) {
      const cached = tokenCalculationCache.current.get(cacheKey);
      return cached;
    }

    if (!node.children || node.children.length === 0) { // It's a file
      let fileTotalTokens;
      if (overrideTokens !== undefined) {
        fileTotalTokens = overrideTokens;
      } else {
        fileTotalTokens = folders ? getFolderTokenCount(String(nodePath), folders) : 0;
      }
      const fileIncludedTokens = checkedKeys.includes(nodePath) ? fileTotalTokens : 0;
      const result = { included: fileIncludedTokens, total: fileTotalTokens };
      tokenCalculationCache.current.set(cacheKey, result);
      return result;
    }

    // It's a directory
    let directoryTotalTokens = 0;
    let directoryIncludedTokens = 0;

    if (node.children && node.children.length > 0) {
      for (const child of node.children) {
        // Always use original tree structure for child calculations
        const originalChild = findNodeInOriginalTree(child.key) || child;
        const childResult = calculateTokens(originalChild, folders);
        directoryTotalTokens += childResult.total;
        directoryIncludedTokens += childResult.included;
      }
    }

    // Fix for base directories: if this directory is checked, all its tokens should be included
    if (checkedKeys.includes(String(nodePath))) {
      directoryIncludedTokens = directoryTotalTokens;
    }

    const result = { included: directoryIncludedTokens, total: directoryTotalTokens };
    tokenCalculationCache.current.set(cacheKey, result);
    return result;
  }, [checkedKeys, getFolderTokenCount, accurateTokenCounts]);
  
  // Clear token calculation cache when accurate counts change
  useLayoutEffect(() => {
    // Only run if we have accurate token counts
    const accurateCountsKeys = Object.keys(accurateTokenCounts);
    if (accurateCountsKeys.length > 0 && 
        JSON.stringify(accurateCountsKeys) !== JSON.stringify(Object.keys(lastAccurateCountsRef.current))) {
      console.log('Accurate token counts changed, clearing calculation cache');
      
      // Clear the calculation cache
      tokenCalculationCache.current.clear();
      
      // Force a deep copy and update of the tree data to trigger recalculation
      const deepCopyTree = (nodes) => {
        return nodes.map(node => ({
          ...node,
          children: node.children ? deepCopyTree(node.children) : undefined
        }));
      };
      
      if (treeData.length > 0) {
        // Create a deep copy of the tree data
        const newTreeData = deepCopyTree(treeData);
        
        // Update the tree data to trigger a re-render
        setTreeData(newTreeData);
      }
      
      // Update the reference to the current accurate counts
      lastAccurateCountsRef.current = {...accurateTokenCounts};
    }
  }, [accurateTokenCounts, setTreeData, treeData]);
  
  // Listen for accurate token counts update events
  useEffect(() => {
    const handleAccurateTokenCountsUpdated = (event) => {
      console.log('Received accurateTokenCountsUpdated event:', event.detail);
      // Clear the token calculation cache
      tokenCalculationCache.current.clear();
      // Force a re-render of the tree
      setTreeData(prevData => JSON.parse(JSON.stringify(prevData)));
    };
    
    window.addEventListener('accurateTokenCountsUpdated', handleAccurateTokenCountsUpdated);
    return () => window.removeEventListener('accurateTokenCountsUpdated', handleAccurateTokenCountsUpdated);
  }, [setTreeData]);
  // Show loading state while scanning and no data
  if (isScanning && (!muiTreeData || muiTreeData.length === 0)) {
    return (
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1 }}>
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
            Loading folder structure...
            <br />
            <Typography variant="caption" color="text.secondary">
              This may take a moment for large repositories
            </Typography>
          </Typography>
        </Box>
      </Box>
    );
  }

  // Show error state if scan failed and no cached data
  if (scanError && (!muiTreeData || muiTreeData.length === 0)) {
    return (
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1 }}>
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
  if (!isScanning && (!muiTreeData || muiTreeData.length === 0)) {
    return (
      <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1 }}>
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
      <Box sx={{ mb: 1 }}>
        <TextField
          fullWidth
          placeholder="Search folders"
          value={searchValue}
          onChange={handleSearchChange}
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

      <Box sx={{ mb: 1 }}>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={refreshFolders}
          disabled={isRefreshing}
          sx={{ mb: 1 }}
          size="small"
        >
          {isRefreshing ? 'Refreshing...' : 'Refresh Files'}
        </Button>
      </Box>

      <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
        {/* Show overlay when scanning with existing data */}
        {isScanning && muiTreeData && muiTreeData.length > 0 && (
          <LinearProgress sx={{ mb: 1 }} />
        )}
        
        {isLoading ? (
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
            {(searchValue ? filteredTreeData : muiTreeData).map(node => (
              <TreeNode key={node.key} node={node} level={0} />
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
};
