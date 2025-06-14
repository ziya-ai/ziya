import React, { useState, useEffect, useCallback, useMemo, useRef, Fragment } from 'react';
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
import Tooltip from '@mui/material/Tooltip';
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
    setCheckedKeys,
    expandedKeys,
    setExpandedKeys,
    getFolderTokenCount,
  } = useFolderContext();

  const { isDarkMode } = useTheme();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [searchValue, setSearchValue] = useState('');
  const [filteredTreeData, setFilteredTreeData] = useState<any[]>([]);
  const [autoExpandParent, setAutoExpandParent] = useState(false);

  // Cache for token calculations
  const tokenCalculationCache = useRef(new Map());

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

    // Calculate token counts using the same logic as Ant Design version
    const { total, included } = calculateTokens(node, folders);

    // Check if this node is indeterminate (some but not all children selected)
    const isIndeterminate = hasChildren && !isChecked &&
      node.children.some(child => checkedKeys.includes(String(child.key)));

    // Extract clean label and token count
    const titleMatch = String(node.title).match(/^(.+?)\s*\(([0-9,]+)\s*tokens?\)$/);
    const cleanLabel = titleMatch ? titleMatch[1] : String(node.title);
    const tokenCount = titleMatch ? parseInt(titleMatch[2].replace(/,/g, ''), 10) : 0;

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

  // Handle tree node expansion
  const handleNodeToggle = (event: React.SyntheticEvent, nodeIds: string[]) => {
    console.log('MUI Node toggle:', {
      event: event.type,
      nodeIds,
      current: expandedKeys
    });

    // Convert all nodeIds to strings to ensure consistent comparison
    const stringNodeIds = nodeIds.map(id => String(id));

    // Update expanded keys with the new set of IDs
    setExpandedKeys(stringNodeIds);
    console.log('MUI Updated expanded nodes:', stringNodeIds);

    setAutoExpandParent(false);
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
  const calculateTokens = useCallback((node, folders) => {
    const nodePath = node.key;
    const cacheKey = String(nodePath);

    if (tokenCalculationCache.current.has(cacheKey)) {
      const cached = tokenCalculationCache.current.get(cacheKey);
      return cached;
    }

    if (!node.children || node.children.length === 0) { // It's a file
      const fileTotalTokens = getFolderTokenCount(String(nodePath), folders);
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
        const childResult = calculateTokens(child, folders);
        directoryTotalTokens += childResult.total;
        directoryIncludedTokens += childResult.included;
      }
    }

    // Fix for base directories: if this directory is checked, all its tokens should be included
    if (checkedKeys.includes(String(nodePath))) {
      directoryIncludedTokens = directoryTotalTokens;
    }

    const result = { included: directoryIncludedTokens, total: directoryTotalTokens };
    tokenCalculationCache.current.set(String(cacheKey), result);
    return result;
  }, [checkedKeys, getFolderTokenCount]);

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
            '& .MuiBox-root': {
              maxWidth: '100%'
            }
          }}>
            {(searchValue ? filteredTreeData : muiTreeData).map(node => (
              <TreeNode key={node.key} node={node} level={0} />
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
};
