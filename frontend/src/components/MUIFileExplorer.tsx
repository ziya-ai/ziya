import React, { useState, useEffect, useCallback, useMemo, useRef, useLayoutEffect } from 'react';
import { useFolderContext } from '../context/FolderContext';
import { useTheme } from '../context/ThemeContext';
import { Folders } from '../utils/types';
import { debounce } from 'lodash';
import { message } from 'antd';
import { convertToTreeData } from '../utils/folderUtil';


// MUI imports
import { styled } from '@mui/material/styles';
import { TreeDataNode } from 'antd';
import Box from '@mui/material/Box';
import { TreeView } from '@mui/x-tree-view/TreeView';
import { TreeItem, TreeItemProps } from '@mui/x-tree-view/TreeItem';
import Typography from '@mui/material/Typography';
import Checkbox from '@mui/material/Checkbox';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import LinearProgress from '@mui/material/LinearProgress';
import Tooltip from '@mui/material/Tooltip';

// MUI icons
import FolderIcon from '@mui/icons-material/Folder';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import ArrowRightIcon from '@mui/icons-material/ArrowRight';
import RefreshIcon from '@mui/icons-material/Refresh';
import SearchIcon from '@mui/icons-material/Search';
import ClearIcon from '@mui/icons-material/Clear';
import MoreVert from '@mui/icons-material/MoreVert';

// Custom styled TreeItem with connected lines
const StyledTreeItem = styled((props: TreeItemProps) => (
  <TreeItem {...props} />
))(({ theme }) => ({
  '& .MuiTreeItem-iconContainer': {
    '& .MuiSvgIcon-root': {
      opacity: 0.3,
    },
    marginLeft: 4,
    paddingLeft: 16,
    borderLeft: `1px dashed ${theme.palette.mode === 'light' ? '#d9d9d9' : '#303030'}`,
  },
  '& .MuiTreeItem-content': {
    display: 'flex',
    padding: '1px 2px',
    borderRadius: '4px',
    transition: 'background-color 0.2s',
    '&:hover': {
      backgroundColor: theme.palette.mode === 'light' ? 'rgba(0, 0, 0, 0.04)' : 'rgba(255, 255, 255, 0.04)'
    }
  },
  // Reduce spacing between items
  '& .MuiTreeItem-root': {
    margin: '0 0 0 0',
    padding: 0,
    minHeight: 'auto',
  },
  '& .MuiTreeItem-group': {
    marginLeft: 15,
    paddingLeft: 18,
    borderLeft: `1px dashed ${theme.palette.mode === 'light' ? '#d9d9d9' : '#303030'}`,
  }
}));

interface CheckboxTreeItemProps {
  nodeId: string;
  label: string;  // This property is used in the component
  checked: boolean;
  indeterminate: boolean;
  onCheckboxClick: (nodeId: string, checked: boolean) => void;
  includedTokens?: number;
  totalTokens?: number;
  tokenCount?: number;
  icon: React.ElementType;
  isDragging?: boolean;
  children?: React.ReactNode;
  hasChildren?: boolean;
}

const CheckboxTreeItem = React.memo<CheckboxTreeItemProps>(({
  nodeId,
  label,
  checked,
  indeterminate,
  tokenCount = 0,
  onCheckboxClick,
  includedTokens = 0,
  totalTokens = 0,
  icon: Icon,
  isDragging = false,
  children,
  hasChildren = false,
  ...other
}) => {
  const { isDarkMode } = useTheme();

  const handleCheckboxClick = (event) => {
    event.stopPropagation();
    onCheckboxClick(nodeId, !checked);
  };

  // Format token count for display
  const formattedTokenCount = tokenCount.toLocaleString();
  const formattedIncludedTokens = includedTokens.toLocaleString();

  // Extract just the filename without any token count
  const cleanLabel = String(label).replace(/\s*\(\d+(?:,\d+)*\s*(?:tokens)?\)$/, '');

  return (
    <StyledTreeItem
      nodeId={nodeId}
      label={
        <Box sx={{ display: 'flex', alignItems: 'center', p: 0.1, pr: 0 }}>
          <Checkbox
            checked={checked}
            indeterminate={indeterminate}
            onClick={handleCheckboxClick}
            color="primary"
            size="small"
            sx={{ p: 0.5, mr: 0.5 }}
          />
          <Icon color="inherit" sx={{ mr: 0.5, fontSize: 16, visibility: 'visible' }} />
          <Typography variant="body2" sx={{
            fontWeight: Icon === FolderIcon ? 'bold' : 'normal',
            flexGrow: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>
            {cleanLabel}
          </Typography>
          {/* Show token count as secondary text only for files */}
          {!Icon.toString().includes('Folder') && tokenCount > 0 && (
            <Typography variant="caption" sx={{
              color: 'text.secondary',
              ml: 1,
              fontSize: '0.65rem',
              fontWeight: checked ? 'bold' : 'normal'
            }}>
              ({formattedTokenCount})
            </Typography>
          )}
          {/* Show token display for folders in the main flow */}
          {Icon.toString().includes('Folder') && totalTokens > 0 && (
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                ml: 1,
                fontSize: '0.65rem'
              }}
            >
              ({includedTokens > 0 ? (
                <><span style={{ fontWeight: 'bold' }}>{includedTokens.toLocaleString()}</span>/{totalTokens.toLocaleString()}</>
              ) : (
                totalTokens.toLocaleString()
              )})
            </Typography>
          )}
        </Box>
      }
      {...other}
    />
  );
});

export const MUIFileExplorer = () => {
  const {
    treeData,
    setTreeData,
    checkedKeys,
    setCheckedKeys,
    expandedKeys,
    setExpandedKeys,
    getFolderTokenCount,
    folders
  } = useFolderContext();

  const { isDarkMode } = useTheme();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [searchValue, setSearchValue] = useState('');
  const [filteredTreeData, setFilteredTreeData] = useState<any[]>([]);
  const [autoExpandParent, setAutoExpandParent] = useState(false);

  // Cache for token calculations
  const tokenCalculationCache = useRef(new Map());

  // Debug logging for tree data
  useEffect(() => {
    console.log('Tree data updated:', { nodeCount: treeData.length });
    console.log('Expanded keys:', expandedKeys);
  }, [treeData, expandedKeys]);

  // Debounced search function
  const debouncedSearch = useCallback(
    debounce((value) => {
      if (value) {
        const { filteredData, expandedKeys } = filterTreeData(treeData, value);
        setFilteredTreeData(filteredData);
        setExpandedKeys(prev => [...prev, ...expandedKeys]);
        setAutoExpandParent(true);
      } else {
        setFilteredTreeData([]);
        setAutoExpandParent(false);
      }
    }, 300),
    [treeData]
  );

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
    // Convert all nodeIds to strings to ensure consistent comparison
    const stringNodeIds = nodeIds.map(id => String(id));

    // Update expanded keys with the new set of IDs
    setExpandedKeys(stringNodeIds);

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
    // Find the node and its children
    const findNode = (nodes, id) => {
      for (const node of nodes) {
        if (node.key === id) {
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
      let keys = [node.key];
      if (node.children) {
        node.children.forEach(child => {
          keys = keys.concat(getAllChildKeys(child));
        });
      }
      return keys;
    };

    // Get all parent keys
    const getAllParentKeys = (key: React.Key, tree: TreeDataNode[]): string[] => {
      let parentKeys: string[] = [];
      const findParent = (currentKey, nodes) => {
        for (let i = 0; i < nodes.length; i++) {
          const node = nodes[i];
          if (node.children && node.children.some(child => child.key === currentKey)) {
            parentKeys.push(node.key);
            return node.key;
          } else if (node.children) {
            const foundParent = findParent(currentKey, node.children);
            if (foundParent) {
              parentKeys.push(node.key);
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
      const keysToAdd = node.children ? getAllChildKeys(node) : [nodeId];
      setCheckedKeys(prev => [...new Set([...prev, ...keysToAdd])]);
    } else {
      // Remove this node and all its children
      const keysToRemove = node.children ? getAllChildKeys(node) : [nodeId];
      // Also remove parent selections if needed
      const parentKeys = getAllParentKeys(nodeId, treeData);
      setCheckedKeys(prev =>
        prev.filter(key => !keysToRemove.includes(String(key)) && !parentKeys.includes(String(key)))
      );
      console.log('Unchecked keys:', keysToRemove);
    }
  };

  // Calculate token counts for a node
  const calculateTokens = useCallback((node, folders) => {
    const nodePath = node.key;
    const cacheKey = String(nodePath);

    // Check cache first
    if (tokenCalculationCache.current.has(cacheKey)) {
      return tokenCalculationCache.current.get(cacheKey);
    }

    // Calculate total tokens
    let totalTokens = getFolderTokenCount(String(nodePath), folders);

    // Calculate included tokens
    let includedTokens = 0;
    if (checkedKeys.includes(nodePath)) {
      includedTokens = totalTokens;
    } else if (node.children && node.children.length > 0) {
      for (const child of node.children) {
        const childResult = calculateTokens(child, folders);
        includedTokens += childResult.included;
      }
    }

    // Cache the result
    const result = { total: totalTokens, included: includedTokens };
    tokenCalculationCache.current.set(String(cacheKey), result);
    return result;
  }, [checkedKeys, getFolderTokenCount]);

  // Render the tree recursively
  const renderTree = (nodes) => {
    return nodes.map((node) => {
      // Calculate token counts
      const { total, included } = calculateTokens(node, folders);

      // Check if this node is checked or indeterminate
      const isChecked = checkedKeys.includes(node.key);

      // For directories, check if some but not all children are checked
      let isIndeterminate = false;
      if (node.children && node.children.length > 0) {
        const childKeys = node.children.map(child => child.key);
        const checkedChildKeys = childKeys.filter(key => checkedKeys.includes(key));
        isIndeterminate = checkedChildKeys.length > 0 && checkedChildKeys.length < childKeys.length;
      }

      // Determine icon based on whether it's a file or folder
      const icon = node.children && node.children.length > 0 ? FolderIcon : InsertDriveFileIcon;

      return (
        <CheckboxTreeItem
          key={node.key}
          nodeId={node.key}
          label={node.title}
          checked={isChecked}
          indeterminate={isIndeterminate}
          onCheckboxClick={handleCheckboxClick}
          hasChildren={node.children && node.children.length > 0}
          tokenCount={total}
          includedTokens={included}
          totalTokens={total}
          icon={icon}
        >
          {node.children && node.children.length > 0 && renderTree(node.children)}
        </CheckboxTreeItem>
      );
    });
  };

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', p: 1 }}>
      <Box sx={{ mb: 2 }}>
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

      <Button
        variant="outlined"
        startIcon={<RefreshIcon />}
        onClick={refreshFolders}
        disabled={isRefreshing}
        sx={{ mb: 2 }}
      >
        {isRefreshing ? 'Refreshing...' : 'Refresh Files'}
      </Button>

      <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
        <TreeView
          aria-label="file explorer"
          defaultCollapseIcon={<ArrowDropDownIcon />}
          defaultExpandIcon={<ArrowRightIcon />}
          defaultEndIcon={<div style={{ width: 24 }} />}
          expanded={expandedKeys.map(key => String(key))}
          onNodeToggle={handleNodeToggle}
          sx={{
            flexGrow: 1,
            overflowY: 'auto',
            '& .MuiTreeItem-iconContainer': {
              visibility: 'visible',
              width: 'auto'
            },
            '& .MuiTreeItem-group': {
              marginLeft: 15
            }
          }}
        >
          {renderTree(searchValue ? filteredTreeData : treeData)}
        </TreeView>
      </Box >
    </Box >
  );
};

