import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
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

  // Extract clean label and token count from the original title
  const titleMatch = String(label).match(/^(.+?)\s*\(([0-9,]+)\s*tokens?\)$/);
  const cleanLabel = titleMatch ? titleMatch[1] : String(label);
  const extractedTokenCount = titleMatch ? parseInt(titleMatch[2].replace(/,/g, ''), 10) : tokenCount;

  // Use extracted token count if available, otherwise fall back to passed tokenCount
  const displayTokenCount = extractedTokenCount || tokenCount;
  const formattedIncludedTokens = includedTokens.toLocaleString();

  return (
    <StyledTreeItem
      nodeId={nodeId}
      label={
        <Box sx={{ display: 'flex', alignItems: 'center', py: 0.25, pr: 0, width: '100%' }}>
          <Checkbox
            checked={checked}
            indeterminate={indeterminate}
            onClick={handleCheckboxClick}
            color="primary"
            size="small"
            sx={{ p: 0.5, mr: 0.5, color: isDarkMode ? '#90caf9' : undefined }}
          />
          <Icon color={isDarkMode ? "inherit" : "action"} sx={{ mr: 0.5, fontSize: 16, visibility: 'visible', color: isDarkMode ? (Icon === FolderIcon ? '#69c0ff' : '#91d5ff') : (Icon === FolderIcon ? 'primary.main' : 'text.secondary') }} />
          <Typography
            variant="body2"
            sx={{
              fontWeight: checked && !hasChildren ? 'bold' : (hasChildren ? 'bold' : 'normal'),
              flexGrow: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              color: isDarkMode ? 'text.primary' : 'inherit'
            }}
          >
            {cleanLabel}
          </Typography>
          {!hasChildren && displayTokenCount > 0 && (
            <Typography variant="caption" sx={{
              color: isDarkMode ? '#aaa' : 'text.secondary',
              ml: 1,
              fontSize: '0.7rem',
              fontFamily: 'monospace',
              fontWeight: checked ? 'bold' : 'normal',
              ...(checked && { color: isDarkMode ? 'text.primary' : 'primary.main' })
            }}>
              ({displayTokenCount.toLocaleString()})
            </Typography>
          )}
          {/* Show token display for folders in the main flow */}
          {hasChildren && totalTokens > 0 && (
            <Typography
              variant="caption"
              sx={{
                color: isDarkMode ? '#aaa' : 'text.secondary',
                ml: 1,
                fontSize: '0.7rem',
                fontFamily: 'monospace',
              }}
            >
              (<Typography
                component="span"
                sx={{ fontWeight: 'bold', color: includedTokens > 0 ? (isDarkMode ? '#ffffff' : '#000000') : 'inherit' }}
              >{includedTokens.toLocaleString()}</Typography>
              /{totalTokens.toLocaleString()})
            </Typography>
          )}
        </Box>
      }
      {...other}
    />
  );
});

CheckboxTreeItem.displayName = 'CheckboxTreeItem';

// Add this helper function to determine if a node has children
const nodeHasChildren = (node: any): boolean => {
  return node.children && Array.isArray(node.children) && node.children.length > 0;
};

// Add this helper function to extract token count from title
const extractTokenCount = (title: string): number => {
  const match = String(title).match(/\(([0-9,]+)\s*tokens?\)/);
  return match ? parseInt(match[1].replace(/,/g, ''), 10) : 0;
};
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
    // Find the node in the tree
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
      let keys = [String(node.key)];
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
          if (node.children && node.children.some(child => String(child.key) === String(currentKey))) {
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
      const keysToAdd = nodeHasChildren(node) ? getAllChildKeys(node) : [String(nodeId)];
      setCheckedKeys(prev => [...new Set([...prev.map(String), ...keysToAdd])]);
    } else {
      // Remove this node and all its children
      const keysToRemove = nodeHasChildren(node) ? getAllChildKeys(node) : [String(nodeId)];
      // Also remove parent selections if needed
      const parentKeys = getAllParentKeys(nodeId, treeData);
      setCheckedKeys(prev =>
        prev.map(String).filter(key => !keysToRemove.includes(key) && !parentKeys.includes(key))
      );
    }
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

    if (checkedKeys.includes(nodePath)) {
      directoryIncludedTokens = directoryTotalTokens;
    }

    const result = { included: directoryIncludedTokens, total: directoryTotalTokens };
    tokenCalculationCache.current.set(String(cacheKey), result);
    return result;
  }, [checkedKeys, getFolderTokenCount]);

  // Render the tree recursively
  const renderTree = (nodes) => {
    return nodes.map((node) => {
      const hasChildren = nodeHasChildren(node);

      // Calculate token counts
      const { total, included } = calculateTokens(node, folders);

      // Check if this node is checked or indeterminate
      const isChecked = checkedKeys.map(String).includes(String(node.key));

      // For directories, check if some but not all children are checked
      let isIndeterminate = false;
      if (hasChildren && !isChecked) {
        const childKeys = node.children.map(child => String(child.key));
        const stringCheckedKeys = checkedKeys.map(String);
        const checkedChildKeys = childKeys.filter(key => stringCheckedKeys.includes(key));
        isIndeterminate = checkedChildKeys.length > 0 && checkedChildKeys.length < childKeys.length;
      }

      // Determine icon based on whether it's a file or folder
      const icon = node.children && node.children.length > 0 ? FolderIcon : InsertDriveFileIcon;

      return (
        <CheckboxTreeItem
          key={node.key}
          nodeId={String(node.key)}
          label={node.title}
          checked={isChecked}
          indeterminate={isIndeterminate}
          onCheckboxClick={handleCheckboxClick}
          hasChildren={hasChildren}
          tokenCount={total}
          includedTokens={included}
          totalTokens={total}
          icon={icon}
        >
          {hasChildren && renderTree(node.children)}
        </CheckboxTreeItem>
      );
    });
  };

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
          <TreeView
            aria-label="MUI file explorer"
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
