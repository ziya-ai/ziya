import clsx from 'clsx';
import React, { useState, useCallback, useEffect, memo, useRef, useMemo, useLayoutEffect } from 'react';
import { message, Modal, Form, notification, Spin, Input, Switch, theme, Dropdown, Menu as AntMenu } from 'antd'; // Added AntD Dropdown & Menu
import { useChatContext } from '../context/ChatContext';
import { useTheme } from '../context/ThemeContext';
import { Conversation, ConversationFolder } from '../utils/types';
import { db } from '../utils/db';
import { v4 as uuidv4 } from 'uuid';
import { useFolderContext } from '../context/FolderContext';
import { DragDropContext, Droppable, Draggable } from 'react-beautiful-dnd';
// MUI imports
import { Radio } from 'antd';
import { styled } from '@mui/material/styles';
import { TreeView } from '@mui/x-tree-view/TreeView';
import { TreeItem, TreeItemProps } from '@mui/x-tree-view/TreeItem';
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import Menu from '@mui/material/Menu';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import IconButton from '@mui/material/IconButton';
import { Divider as MuiDivider } from '@mui/material';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import Tooltip from '@mui/material/Tooltip';
import FormControlLabel from '@mui/material/FormControlLabel';
import Fab from '@mui/material/Fab';
import { Divider as AntDivider } from 'antd';

// MUI icons
import CreateNewFolderIcon from '@mui/icons-material/CreateNewFolder';
import FolderIcon from '@mui/icons-material/Folder';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import Box from '@mui/material/Box';
import MenuItem from '@mui/material/MenuItem';
import ChatIcon from '@mui/icons-material/Chat';
import AddIcon from '@mui/icons-material/Add';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PushPinIcon from '@mui/icons-material/PushPin';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import DownloadIcon from '@mui/icons-material/Download';
import UploadIcon from '@mui/icons-material/Upload';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import ArrowRightIcon from '@mui/icons-material/ArrowRight';
import SyncIcon from '@mui/icons-material/Sync';
import AddCommentIcon from '@mui/icons-material/AddComment';
import NoteAddIcon from '@mui/icons-material/NoteAdd';

// Ant Design Icons for the menu items
import {
  EditOutlined,
  DeleteOutlined,
  CopyOutlined as AntCopyOutlined,
  CompressOutlined as AntCompressOutlined,
  SettingOutlined as AntSettingOutlined,
  FolderOutlined as AntFolderOutlined,
  PushpinOutlined as AntPushpinOutlined,
} from '@ant-design/icons';

// Spinning animation for the loading icon
const SpinningSync = styled(SyncIcon)(({ theme }) => ({
  animation: 'spin 2s linear infinite',
  '@keyframes spin': {
    '0%': {
      color: theme.palette.mode === 'dark' ? '#1890ff' : 'inherit',
      transform: 'rotate(0deg)',
    },
    '100%': {
      transform: 'rotate(360deg)',
    },
  },
}));

// Define drag data type
interface DragData {
  nodeId: string;
  nodeType: 'conversation' | 'folder';
}

// Completely rewritten StyledTreeItem with proper syntax
const StyledTreeItem = styled((props: TreeItemProps) => (
  <TreeItem {...props} />
))(({ theme }) => ({
  '& .MuiTreeItem-iconContainer': {
    marginLeft: '-24px', // Move chevron into the left padding
    marginRight: '6px',   // Add space between chevron and icon
    width: '16px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    '& .MuiSvgIcon-root': {
      fontSize: '16px',
      opacity: 0.7,
    },
  },

  // Root level items should align properly
  '&.MuiTreeItem-root > .MuiTreeItem-content': {
    paddingLeft: '24px',
  },

  '& .MuiTreeItem-group': {
    marginLeft: 15,
    paddingLeft: 18,
    borderLeft: `1px dashed ${theme.palette.mode === 'light' ? '#d9d9d9' : '#606060'}`
  },

  '& .MuiTreeItem-content': {
    display: 'flex',
    padding: '4px 8px',
    paddingLeft: '24px', // Provide space for the chevron
    alignItems: 'center',
    borderRadius: '4px',
    transition: 'background-color 0.2s',
    '&:hover': {
      backgroundColor: theme.palette.mode === 'light' ? 'rgba(0, 0, 0, 0.04)' : 'rgba(255, 255, 255, 0.04)'
    },
    '&.Mui-selected': {
      backgroundColor: theme.palette.mode === 'light' ? '#e6f7ff' : '#177ddc',
      color: theme.palette.mode === 'light' ? 'inherit' : '#ffffff',
      '&:hover': {
        backgroundColor: theme.palette.mode === 'light' ? '#e6f7ff' : '#177ddc'
      }
    }
  },

  '& .MuiTreeItem-root': {
    margin: 0,
    padding: 0,
    minHeight: 'auto'
  },

  '&.drag-over > .MuiTreeItem-content': {
    backgroundColor: theme.palette.mode === 'light' ? 'rgba(24, 144, 255, 0.2)' : 'rgba(24, 144, 255, 0.3)',
    border: `1px dashed ${theme.palette.mode === 'light' ? '#1890ff' : '#177ddc'}`,
    borderRadius: '4px',
  },

  '&.dragging > .MuiTreeItem-content': {
    opacity: 0.4
  }
}));

// Custom TreeItem component for chat items
interface ChatTreeItemProps {
  nodeId: string;
  labelText: string;  // This property is used in the component
  isFolder?: boolean;
  isPinned?: boolean;
  isCurrentItem?: boolean;
  isStreaming?: boolean;
  hasUnreadResponse?: boolean;
  conversationCount?: number;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
  onAddChat: (id: string) => void;
  onPin: (id: string) => void;
  onConfigure: (id: string) => void;
  onFork: (id: string) => void;
  onCompress: (id: string) => void;
  onMove: (id: string, folderId: string | null) => void;
  onOpenMoveMenu?: (id: string, anchorEl: HTMLElement) => void;
  onCreateSubfolder?: (id: string) => void;
  isEditing?: boolean;
  editValue?: string;
  onEditChange?: (value: string) => void;
  children?: React.ReactNode;
  isDragOver?: boolean;
  className?: string;
  onEditSubmit: (id: string, value: string) => void;
  style?: React.CSSProperties;
  onDragStart?: (event: React.DragEvent) => void;
  onDragOver?: (event: React.DragEvent) => void;
  onDrop?: (event: React.DragEvent) => void;
}

const ChatTreeItem = memo<ChatTreeItemProps>((props) => {
  const {
    nodeId,
    labelText,
    isFolder = false,
    isPinned = false,
    isCurrentItem = false,
    isStreaming = false,
    hasUnreadResponse = false,
    conversationCount = 0,
    onEdit,
    onDelete,
    onAddChat,
    onPin,
    onConfigure,
    onFork,
    onCompress,
    onMove,
    onOpenMoveMenu,
    onCreateSubfolder,
    isEditing = false,
    editValue = '',
    onEditChange,
    onEditSubmit,
    className,
    style,
    onDragStart,
    onDragOver,
    onDrop,
    ...other
  } = props;

  const { isDarkMode } = useTheme();
  const [isHovered, setIsHovered] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  // Focus the edit input when it becomes visible
  useEffect(() => {
    if (isEditing && editInputRef.current) {
      setTimeout(() => {
        if (editInputRef.current) {
          editInputRef.current.focus();
        }
      }, 50);
    }
  }, [isEditing]);

  // Handle menu interactions

  // Double-click handler for editing
  const handleLabelDoubleClick = (e) => {
    e.stopPropagation();
    onEdit(nodeId);
  };

  const handleEditKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      onEditSubmit(nodeId, editValue);
    } else if (e.key === 'Escape') {
      onEdit(nodeId);
    }
  };

  let itemClassName = className || '';

  return (
    <StyledTreeItem
      className={itemClassName.trim()}
      style={style}
      nodeId={nodeId}
      label={
        <div
          style={{ width: '100%' }}
          onMouseDown={(e) => {
            // Prevent TreeView from handling the mouse down event
            e.stopPropagation();
          }}
          draggable={true}
          onDragStart={(e) => {
            e.stopPropagation();
            // Set drag data
            const dragData = { nodeId, nodeType: isFolder ? 'folder' : 'conversation' };
            e.dataTransfer.setData('application/ziya-node', JSON.stringify(dragData));
            e.dataTransfer.effectAllowed = 'move';

            // Create a custom drag image
            e.dataTransfer.setDragImage(e.currentTarget, 10, 10);

            // Add visual feedback
            if (e.currentTarget instanceof HTMLElement) {
              e.currentTarget.style.opacity = '0.4';
            }
          }}
          onDragEnd={(e) => {
            e.stopPropagation();
            // Reset visual feedback
            if (e.currentTarget instanceof HTMLElement) {
              e.currentTarget.style.opacity = '1';
            }
          }}
        >
          <Box // This Box is for the entire label content layout
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
            sx={{
              display: 'flex',
              alignItems: 'center',
              p: 0.5,
              width: '100%',
            }}
          >
            {isFolder ? (
              <FolderIcon color="primary" sx={{ mr: 1, fontSize: 20 }} />
            ) : (
              <ChatIcon sx={{ mr: 1, fontSize: 20 }} />
            )}

            {isEditing ? (
              <TextField
                inputRef={editInputRef}
                value={editValue}
                onChange={(e) => onEditChange && onEditChange(e.target.value)}
                onKeyDown={handleEditKeyDown}
                onBlur={() => onEditSubmit(nodeId, editValue)}
                variant="standard"
                size="small"
                fullWidth
                autoFocus
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <Box sx={{ display: 'flex', alignItems: 'center', flex: 1, overflow: 'hidden' }}>
                {hasUnreadResponse && !isCurrentItem && (
                  <CheckCircleIcon fontSize="small" color="success" sx={{ mr: 0.5, fontSize: 16 }} />
                )}
                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: isFolder ? 'bold' : 'normal',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap'
                  }}
                  onDoubleClick={handleLabelDoubleClick}
                >
                  {labelText}
                </Typography>
                {isPinned && (
                  <PushPinIcon fontSize="small" color="primary" sx={{ ml: 0.5, fontSize: 14 }} />
                )}
                {isFolder && conversationCount > 0 && (
                  <Typography variant="caption" sx={{ ml: 0.5, color: 'text.secondary' }}>({conversationCount})</Typography>
                )}

                <Box sx={{
                  ml: 'auto',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  opacity: isHovered ? 1 : 0,
                  transition: 'opacity 0.2s ease-in-out'
                }}>
                  {isFolder && (
                    <Tooltip title="New chat in this folder">
                      <IconButton
                        size="small"
                        onClick={(e) => { e.stopPropagation(); onAddChat(nodeId); }}
                        sx={{ p: 0.5, mr: 0.5 }} // Added margin right
                      >
                        <AddIcon fontSize="small" sx={{ fontSize: '16px' }} />
                      </IconButton>
                    </Tooltip>
                  )}
                  <Dropdown
                    overlay={<AntActionMenu // Use the new AntD menu component
                      isFolder={isFolder}
                      nodeId={nodeId}
                      onEdit={onEdit} onDelete={onDelete} onFork={onFork} onCompress={onCompress}
                      onOpenMoveMenu={onOpenMoveMenu} // Pass the handler
                      onConfigure={onConfigure} onPin={onPin} isPinned={isPinned} onCreateSubfolder={onCreateSubfolder}
                    />}
                    trigger={['click']}
                    placement="bottomRight"
                  >
                    <IconButton size="small" sx={{ p: 0.5 }} onClick={e => e.stopPropagation()} >
                      <MoreVertIcon fontSize="small" sx={{ fontSize: '16px' }} />
                    </IconButton>
                  </Dropdown>
                </Box>
              </Box>
            )}
            {isStreaming && (
              <Box sx={{
                display: 'flex',
                alignItems: 'center',
                mt: 0.5,
                color: isDarkMode ? '#4cc9f0' : '#1890ff'
              }}>
                <SpinningSync sx={{ fontSize: '12px', mr: 0.5 }} />
                <Typography variant="caption" sx={{ fontSize: '11px' }}>
                  Receiving response...
                </Typography>
              </Box>
            )}</Box>
        </div>}
      {...other} // Spread any other props like children  
      draggable={true}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {/* Menu is always rendered but its 'open' state and 'key' control its behavior */}
      {props.children}
    </StyledTreeItem>
  );
});

const AntActionMenu = ({ isFolder, nodeId, onEdit, onDelete, onFork, onCompress, onOpenMoveMenu, onConfigure, onPin, isPinned, onCreateSubfolder }) => {
  const handleAntAction = (actionCallback: (id: string) => void, originalEvent?: React.MouseEvent | Event) => {
    originalEvent?.stopPropagation();
    actionCallback(nodeId);
  };

  const items: any[] = [];

  if (!isFolder) {
    items.push(
      { key: 'edit', label: 'Rename', icon: <EditOutlined />, onClick: (e) => handleAntAction(onEdit, e.domEvent) },
      { key: 'fork', label: 'Fork', icon: <AntCopyOutlined />, onClick: (e) => handleAntAction(onFork, e.domEvent) },
      { key: 'compress', label: 'Compress', icon: <AntCompressOutlined />, onClick: (e) => handleAntAction(onCompress, e.domEvent) },
      {
        key: 'move', label: 'Move to', icon: <AntFolderOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onOpenMoveMenu && onOpenMoveMenu(nodeId, e.domEvent.currentTarget as HTMLElement); // Pass the clicked element as anchor
        }
      },
      { type: 'divider' as const },
      { key: 'delete', label: 'Delete', icon: <DeleteOutlined />, onClick: (e) => handleAntAction(onDelete, e.domEvent), danger: true }
    );
  } else { // isFolder
    items.push(
      { key: 'edit', label: 'Rename', icon: <EditOutlined />, onClick: (e) => handleAntAction(onEdit, e.domEvent) },
      { key: 'new-subfolder', label: 'New Subfolder', icon: <CreateNewFolderIcon />, onClick: (e) => handleAntAction(onCreateSubfolder, e.domEvent) },
      { key: 'configure', label: 'Configuration', icon: <AntSettingOutlined />, onClick: (e) => handleAntAction(onConfigure, e.domEvent) },
      { key: 'pin', label: isPinned ? 'Unpin' : 'Pin to Top', icon: <AntPushpinOutlined />, onClick: (e) => handleAntAction(onPin, e.domEvent) },
      {
        key: 'move', label: 'Move to', icon: <AntFolderOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onOpenMoveMenu && onOpenMoveMenu(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      { type: 'divider' as const },
      { key: 'delete', label: 'Delete', icon: <DeleteOutlined />, onClick: (e) => handleAntAction(onDelete, e.domEvent), danger: true }
    );
  }

  return <AntMenu items={items} />;
};

const MoveToFolderMenu = ({
  anchorEl,
  open,
  onClose,
  folders,
  onMove,
  nodeId,
  onMoveFolder
}) => {
  const isMovingFolder = nodeId && !nodeId.startsWith('conv-');

  // Helper function to check if a folder is a descendant of another
  const isDescendantFolder = (folderId: string, potentialAncestorId: string): boolean => {
    if (folderId === potentialAncestorId) return true;

    const folder = folders.find(f => f.id === folderId);
    if (!folder || !folder.parentId) return false;

    return isDescendantFolder(folder.parentId, potentialAncestorId);
  };

  // Group folders by parent ID
  const foldersByParent = useMemo(() => {
    const map = new Map();
    map.set(null, []); // Root level folders

    folders.forEach(folder => {
      // Skip the folder being moved and its descendants when moving a folder
      if (isMovingFolder && (folder.id === nodeId || isDescendantFolder(folder.id, nodeId))) {
        return;
      }

      const parentId = folder.parentId || null;
      if (!map.has(parentId)) {
        map.set(parentId, []);
      }
      map.get(parentId).push(folder);
    });

    return map;
  }, [folders, nodeId, isMovingFolder]);

  // Recursive function to render folder menu items
  const renderFolderItems = (parentId = null, level = 0) => {
    const foldersInLevel = foldersByParent.get(parentId) || [];

    return foldersInLevel.map(folder => (
      <React.Fragment key={folder.id}>
        <MenuItem
          onClick={(e) => {
            e.stopPropagation();
            if (isMovingFolder) {
              // Handle folder move
              onMoveFolder(nodeId, folder.id);
            } else {
              // Handle conversation move
              onMove(nodeId, folder.id);
            }
            onClose();
          }}
          sx={{ pl: 2 + level * 2 }}
        >
          <ListItemIcon>
            <FolderIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{folder.name}</ListItemText>
        </MenuItem>
        {foldersByParent.has(folder.id) && renderFolderItems(folder.id, level + 1)}
      </React.Fragment>
    ));
  };

  return (
    <Menu
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      anchorOrigin={{
        vertical: 'top',
        horizontal: 'right',
      }}
      transformOrigin={{
        vertical: 'top',
        horizontal: 'right',
      }}
    >
      <MenuItem onClick={(e) => {
        e.stopPropagation();
        if (isMovingFolder) {
          onMoveFolder(nodeId, null);
        } else {
          onMove(nodeId, null);
        }
        onClose();
      }}>
        <ListItemIcon><FolderIcon fontSize="small" /></ListItemIcon>
        <ListItemText>Root</ListItemText>
      </MenuItem>
      {renderFolderItems()}
    </Menu>
  );
};

const MUIChatHistory = () => {
  const {
    conversations,
    setCurrentConversationId,
    currentConversationId,
    setConversations,
    currentMessages,
    isLoadingConversation,
    streamingConversations,
    startNewChat,
    loadConversation,
    folders,
    setFolders,
    currentFolderId,
    setCurrentFolderId,
    createFolder,
    updateFolder,
    deleteFolder,
    moveConversationToFolder
  } = useChatContext();

  const { isDarkMode } = useTheme();
  const [expandedNodes, setExpandedNodes] = useState<React.Key[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const [pinnedFolders, setPinnedFolders] = useState<Set<string>>(new Set());
  const [newFolderDialogOpen, setNewFolderDialogOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const initialExpandedRef = useRef<boolean>(false);
  const [folderConfigForm] = Form.useForm();

  const [moveToMenuState, setMoveToMenuState] =
    useState<{
      anchorEl: null | HTMLElement;
      nodeId: null | string
    }>({ anchorEl: null, nodeId: null });

  // Load pinned folders from localStorage on mount
  useEffect(() => {
    try {
      const savedPinnedFolders = localStorage.getItem('ZIYA_PINNED_FOLDERS');
      if (savedPinnedFolders) {
        setPinnedFolders(new Set(JSON.parse(savedPinnedFolders)));
      }
    } catch (error) {
      console.error('Error loading pinned folders:', error);
    }
  }, []);

  // Initialize expanded nodes with folder IDs on first render
  useEffect(() => {
    if (!initialExpandedRef.current && folders.length > 0) {
      // Expand all folders initially
      const folderIds = folders.map(folder => folder.id);
      setExpandedNodes(folderIds);
      initialExpandedRef.current = true;

      // Log the expanded nodes for debugging
      console.log('Initial expanded nodes:', folderIds);
    }
  }, [folders]);

  // Save pinned folders to localStorage when they change
  useEffect(() => {
    if (pinnedFolders.size > 0) {
      localStorage.setItem('ZIYA_PINNED_FOLDERS', JSON.stringify([...pinnedFolders]));
    }
  }, [pinnedFolders]);

  // Handle opening the move menu
  const handleOpenMoveMenu = (nodeId: string, anchorEl: HTMLElement) => {
    console.log("Opening move menu for:", nodeId, "anchored to:", anchorEl);
    setMoveToMenuState({ anchorEl, nodeId });
  };

  // Handle closing the move menu
  const handleCloseMoveMenu = () => {
    console.log("Closing move menu");
    setMoveToMenuState({ anchorEl: null, nodeId: null });
  };

  // Function to toggle pin status
  const togglePinFolder = (folderId: string) => {
    setPinnedFolders(prev => {
      const newPinned = new Set(prev);
      if (newPinned.has(folderId)) {
        newPinned.delete(folderId);
        message.info('Folder unpinned');
      } else {
        newPinned.add(folderId);
        message.success('Folder pinned to top');
      }
      return newPinned;
    });
  }

  // Handle creating a new folder at current level
  const handleCreateFolderAtCurrentLevel = async () => {
    try {
      // Create a new folder at the current folder level (as a sibling)
      const createdFolderId = await createFolder('New Folder', currentFolderId);
      const newFolderId = String(createdFolderId);

      // Ensure parent folder is expanded if creating a subfolder
      if (currentFolderId && !expandedNodes.includes(currentFolderId)) {
        setExpandedNodes(prev => [...prev, currentFolderId]);
      }

      message.success('New folder created successfully');

      // Start editing the folder name immediately
      if (newFolderId) {
        setTimeout(() => {
          setEditingId(newFolderId);
          setEditValue('New Folder');
        }, 100);
      }
    } catch (error) {
      console.error('Error creating folder:', error);
      message.error('Failed to create folder');
    }
  };

  // Handle creating a new chat at current folder level
  const handleCreateChatAtCurrentLevel = async () => {
    try {
      await setCurrentFolderId(currentFolderId);
      await startNewChat(currentFolderId);
    } catch (error) {
      message.error('Failed to create new chat');
    }
  };

  // Handle moving a folder
  const handleMoveFolder = async (folderId: string, targetParentId: string | null) => {
    try {
      const folder = folders.find(f => f.id === folderId);
      if (!folder) return;

      // Prevent moving a folder into itself or its descendants
      if (targetParentId === folderId) {
        message.error("Cannot move a folder into itself");
        return;
      }

      const isDescendant = (checkId: string, ancestorId: string): boolean => {
        if (checkId === ancestorId) return true;
        const checkFolder = folders.find(f => f.id === checkId);
        if (!checkFolder || !checkFolder.parentId) return false;
        return isDescendant(checkFolder.parentId, ancestorId);
      };

      if (targetParentId && isDescendant(targetParentId, folderId)) {
        message.error("Cannot move a folder into one of its descendants");
        return;
      }

      // Update the folder's parent
      const updatedFolder = {
        ...folder,
        parentId: targetParentId
      };

      await updateFolder(updatedFolder);
      message.success('Folder moved successfully');
    } catch (error) {
      message.error('Failed to move folder');
      console.error('Move folder error:', error);
    }
  };

  // Handle moving a folder
  const handleMoveFolder = async (folderId: string, targetParentId: string | null) => {
    try {
      const folder = folders.find(f => f.id === folderId);
      if (!folder) return;

      // Prevent moving a folder into itself or its descendants
      if (targetParentId === folderId) {
        message.error("Cannot move a folder into itself");
        return;
      }

      const isDescendant = (checkId: string, ancestorId: string): boolean => {
        if (checkId === ancestorId) return true;
        const checkFolder = folders.find(f => f.id === checkId);
        if (!checkFolder || !checkFolder.parentId) return false;
        return isDescendant(checkFolder.parentId, ancestorId);
      };

      if (targetParentId && isDescendant(targetParentId, folderId)) {
        message.error("Cannot move a folder into one of its descendants");
        return;
      }

      // Update the folder's parent
      const updatedFolder = {
        ...folder,
        parentId: targetParentId
      };

      await updateFolder(updatedFolder);
      message.success('Folder moved successfully');
    } catch (error) {
      message.error('Failed to move folder');
      console.error('Move folder error:', error);
    }
  };

  // Handle edit actions
  const handleEdit = (id: string) => {
    if (id.startsWith('conv-')) {
      const conversationId = id.substring(5);
      const conversation = conversations.find(c => c.id === conversationId);
      if (conversation) {
        setEditValue(conversation.title);
        setEditingId(conversationId);
      }
    } else {
      const folder = folders.find(f => f.id === id);
      if (folder) {
        setEditValue(folder.name);
        setEditingId(folder.id);
      }
    }
  };

  // Add a separate handler specifically for TreeView's onNodeSelect
  const handleTreeNodeSelect = (event: React.SyntheticEvent<Element, Event>, nodeIds: string) => {
    if (!nodeIds) {
      return;
    }

    const nodeId = nodeIds; // Use the selected node ID directly
    if (nodeId.startsWith('conv-')) {
      handleConversationClick(nodeId.substring(5));
    } else {
      setCurrentFolderId(nodeId);
    }
  };

  // Add handler for node expansion
  const handleNodeToggle = (event: React.SyntheticEvent, nodeIds: string[]) => {
    console.log('Node toggle:', {
      event: event.type,
      nodeIds,
      current: expandedNodes
    });

    setExpandedNodes(nodeIds);
    console.log('Updated expanded nodes:', nodeIds);
  };


  // Handle adding a new chat to a folder
  const handleAddChat = async (folderId: string) => {
    try {
      console.log('Adding new chat to folder:', folderId);

      // Ensure the folder is expanded before creating the chat
      if (!expandedNodes.includes(folderId)) {
        setExpandedNodes(prev => [...prev, folderId]);
      }

      // Also expand any parent folders
      const expandParentFolders = (targetFolderId: string) => {
        const folder = folders.find(f => f.id === targetFolderId);
        if (folder?.parentId) {
          const parentId = folder.parentId;
          if (!expandedNodes.includes(parentId)) {
            setExpandedNodes(prev => [...prev, parentId]);
          }
          expandParentFolders(parentId); // Recursively expand parents
        }
      };
      expandParentFolders(folderId);

      await setCurrentFolderId(folderId);
      setIsLoading(true);
      await startNewChat(folderId);
      setIsLoading(false);
    } catch (error) {
      console.error('Error creating new chat in folder:', error);
      message.error('Failed to create new chat');
      setIsLoading(false);
    }
  };

  // Add the missing handleEditChange function
  const handleEditChange = (value: string) => {
    setEditValue(value);
  };

  // Handle edit submission
  const handleEditSubmit = async (id: string, newValue: string) => {
    if (!newValue.trim()) {
      message.error('Name cannot be empty');
      return;
    }

    if (id.startsWith('conv-')) {
      const conversationId = id.substring(5);
      try {
        // Update state first - no timestamp update for rename
        const updatedConversations = conversations.map(conv =>
          conv.id === conversationId ? {
            ...conv,
            title: newValue,
            // Keep original lastAccessedAt - renaming shouldn't affect sort order
            _version: Date.now() // Only update version for sync purposes
          } : conv
        );

        // Persist to IndexedDB before updating state
        await db.saveConversations(updatedConversations);

        // Update state after successful save
        setConversations(updatedConversations);
        setEditingId(null);
        setEditValue('');
      } catch (error) {
        console.error('Error saving conversation title:', error);
        message.error('Failed to save conversation title');
      }
    } else {
      try {
        // Find the folder
        const folder = folders.find(f => f.id === id);
        if (!folder) return;

        // Update the folder name
        const updatedFolder = {
          ...folder,
          name: newValue,
          updatedAt: Date.now()
        };

        // Save to database
        await updateFolder(updatedFolder);

        // Clear editing state
        setEditingId(null);
        setEditValue('');

        message.success('Folder renamed successfully');
      } catch (error) {
        console.error('Error saving folder name:', error);
        message.error('Failed to save folder name');
      }
    }
  };

  // Handle delete action
  const handleDelete = (id: string) => {
    handleNodeSelect(null as any, id);
  };

  // Handle node selection
  const handleNodeSelect = (event: React.SyntheticEvent, nodeId: string) => {
    if (nodeId.startsWith('conv-')) {
      const conversationId = nodeId.substring(5);

      // If this is a direct click on a conversation (not a delete action)
      if (event !== null) {
        // Load the conversation
        handleConversationClick(conversationId);
        return;
      }

      // This is a delete action (called from handleDelete)
      if (event === null) {
        Modal.confirm({
          title: 'Delete Conversation',
          content: 'Are you sure you want to delete this conversation?',
          onOk: async () => {
            try {
              console.debug('Deleting conversation:', {
                id: conversationId,
                currentActive: conversations.filter(c => c.isActive).length,
                isCurrentConversation: conversationId === currentConversationId
              });

              // First persist to IndexedDB
              const updatedConversations = conversations.map(conv =>
                conv.id === conversationId
                  ? { ...conv, isActive: false }
                  : conv);
              await db.saveConversations(updatedConversations);

              // Then update React state
              setConversations(updatedConversations);

              message.success('Conversation deleted');
            } catch (error) {
              console.error('Error deleting conversation:', error);
              message.error('Failed to delete conversation');
            }
          }
        });
      }
    } else if (nodeId && event !== null) {
      // This is a folder selection (not a delete action)
      // If this is a direct click on a folder
      // Set the current folder
      setCurrentFolderId(nodeId);
      return;
    }

    // Find the folder
    const folder = folders.find(f => f.id === nodeId);
    if (!folder) return;

    // Count conversations in this folder
    const conversationsInFolder = conversations.filter(c => c.folderId === nodeId && c.isActive !== false);
    const isEmpty = conversationsInFolder.length === 0;

    // If folder is empty, delete without confirmation
    if (isEmpty) {
      deleteFolder(nodeId)
        .then(() => {
          message.success('Folder deleted');
        })
        .catch(error => {
          message.error('Failed to delete folder');
        });
      return;
    }

    // Otherwise, ask for confirmation
    Modal.confirm({
      title: 'Delete Folder',
      content: `Are you sure you want to delete this folder? All ${conversationsInFolder.length} conversation(s) within the folder will also be deleted.`,
      onOk: async () => {
        try {
          await deleteFolder(nodeId);
          message.success('Folder deleted successfully');
        } catch (error) {
          message.error('Failed to delete folder');
        }
      }
    });
  };


  // Handle creating a new folder
  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) {
      message.error('Folder name cannot be empty');
      return;
    }

    try {
      const newFolderId = await createFolder(newFolderName, currentFolderId);
      setNewFolderDialogOpen(false);
      setNewFolderName('');
      message.success('Folder created successfully');

      // Expand the parent folder if it exists
      if (currentFolderId && !expandedNodes.includes(currentFolderId)) {
        setExpandedNodes(prev => [...prev, currentFolderId]);
      }
    } catch (error) {
      message.error('Failed to create folder');
    }
  };

  // Handle moving a conversation to a folder
  const handleMoveConversation = async (conversationId: string, folderId: string | null) => {
    if (conversationId.startsWith('conv-')) {
      conversationId = conversationId.substring(5);
    }

    try {
      await moveConversationToFolder(conversationId, folderId);
      message.success('Conversation moved successfully');
    } catch (error) {
      message.error('Failed to move conversation');
    }
  };

  // Handle forking a conversation
  const handleForkConversation = async (conversationId: string) => {
    if (conversationId.startsWith('conv-')) {
      conversationId = conversationId.substring(5);
    }

    try {
      const conversation = conversations.find(c => c.id === conversationId);
      if (!conversation) return;

      // Create a copy of the conversation with a new ID
      const newId = uuidv4();
      const forkedConversation: Conversation = {
        ...conversation,
        id: newId,
        title: `Fork: ${conversation.title}`,
        lastAccessedAt: Date.now(),
        _version: Date.now(),
        hasUnreadResponse: false
      };

      // Add the forked conversation to the list
      const updatedConversations = [...conversations, forkedConversation];

      // Save to database
      await db.saveConversations(updatedConversations);

      // Update state
      setConversations(updatedConversations);

      // Switch to the new conversation
      await loadConversation(newId);

      message.success('Conversation forked successfully');
    } catch (error) {
      message.error('Failed to fork conversation');
    }
  };

  // Handle compressing a conversation (placeholder)
  const handleCompressConversation = (conversationId: string) => {
    if (conversationId.startsWith('conv-')) {
      conversationId = conversationId.substring(5);
    }

    message.info('Conversation compression is not yet implemented');
  };

  // Handle configuring a folder
  const handleConfigureFolder = (folderId: string) => showFolderConfigDialog(folderId);

  // Handle creating a subfolder
  const handleCreateSubfolder = async (parentFolderId: string) => {
    try {
      // Create a new subfolder with default name and settings
      const createdFolderId = await createFolder('New Folder', parentFolderId);
      const newFolderId = String(createdFolderId);

      // Ensure parent folder is expanded to show the new subfolder
      if (!expandedNodes.includes(parentFolderId)) {
        setExpandedNodes(prev => [...prev, parentFolderId]);
      }

      message.success('New folder created successfully');

      // start editing the folder name immediately
      if (newFolderId) {
        setTimeout(() => {
          setEditingId(newFolderId);
          setEditValue('New Folder');
        }, 100);
      }
    } catch (error) {
      console.error('Error creating subfolder:', error);
      message.error('Failed to create subfolder');
    }
  };

  // Handle conversation click
  const handleConversationClick = useCallback(async (conversationId: string) => {
    try {
      setIsLoading(true);
      // Only load if it's a different conversation
      if (conversationId !== currentConversationId) {
        console.log('Loading conversation:', conversationId);
        await loadConversation(conversationId);
      }
    } catch (error) {
      message.error('Failed to load conversation');
    }
  }, [currentConversationId, isLoadingConversation, loadConversation]);

  // Export/import functionality

  // Handle exporting conversations
  const handleExportConversations = async () => {
    try {
      const data = await db.exportConversations();
      const blob = new Blob([data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `ziya-conversations-${new Date().toISOString()}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      message.success('Conversations exported successfully');
    } catch (error) {
      message.error('Failed to export conversations');
    }
  };

  // Handle importing conversations
  const handleImportConversations = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;

      try {
        const reader = new FileReader();
        reader.onload = async (e) => {
          const content = e.target?.result as string;
          await db.importConversations(content);
          const newConversations = await db.getConversations();
          setConversations(newConversations);
          message.success('Conversations imported successfully');
        };
        reader.readAsText(file);
      } catch (error) {
        message.error('Failed to import conversations');
      }
    };
    input.click();
  };

  // Build tree data from folders and conversations
  const treeData = useMemo(() => {
    const folderMap = new Map();
    folders.forEach(folder => {
      folderMap.set(folder.id, {
        id: folder.id,
        name: folder.name,
        children: [], // Initialize for sub-folders and conversations
        folder: folder, // Keep the original folder object
        conversationCount: 0,
        isPinned: pinnedFolders.has(folder.id),
        lastActivityTime: 0, // Will be calculated from conversations
        createdAt: folder.createdAt || 0 // Use creation time as fallback
      });
    })

    // Add conversations to their respective folders in the map
    const activeConversations = conversations.filter(conv => conv.isActive !== false);
    activeConversations.forEach(conv => {
      if (conv.folderId && folderMap.has(conv.folderId)) {
        const folderNode = folderMap.get(conv.folderId);
        folderNode.children.push({ // Add conversation node directly
          id: `conv-${conv.id}`,
          name: conv.title,
          conversation: conv // Keep the original conversation object
        });
        folderNode.conversationCount++;

        // Only update folder's lastActivityTime if conversation has actual activity
        // Use lastAccessedAt only if it's greater than 0 (indicating actual access)
        const convActivityTime = conv.lastAccessedAt || 0;
        if (convActivityTime > 0 && convActivityTime > folderNode.lastActivityTime) {
          folderNode.lastActivityTime = convActivityTime;
        }
      }

    });

    // The nodes in folderMap now contain their conversation children
    const rootItems: any[] = [];
    folders.forEach(folder => {
      const node = folderMap.get(folder.id); // Get the node (which now includes conversation children)
      if (folder.parentId && folderMap.has(folder.parentId)) {
        const parentNode = folderMap.get(folder.parentId);
        // Ensure parentNode.children is initialized
        if (!parentNode.children) parentNode.children = [];
        parentNode.children.push(node); // Add this folder (with its conv children) to its parent
      } else {
        rootItems.push(node);
      }
    });

    // Add conversations that are not in any folder to the root
    activeConversations.forEach(conv => {
      if (!conv.folderId) {
        rootItems.push({
          id: `conv-${conv.id}`,
          name: conv.title,
          conversation: conv
        });
      }
    });

    // Debug log to check folder structure
    console.log('Built folder structure:',
      rootItems.map(item => ({
        id: item.id, name: item.name, childCount: item.children?.length || 0, isFolder: !!item.folder
      }))
    );

    // Sort each level - folders first, then conversations
    const sortNodes = (nodes: any[]): any[] => {
      if (!nodes) return [];
      return nodes.sort((a, b) => {
        // Pinned folders come first
        if (a.isPinned && !b.isPinned) return -1;
        if (!a.isPinned && b.isPinned) return 1;

        // Get activity times for both items
        const getActivityTime = (item: any) => {
          if (item.folder) {
            // For folders, use lastActivityTime if > 0, otherwise use createdAt
            return item.lastActivityTime > 0 ? item.lastActivityTime : item.createdAt;
          } else {
            // For conversations, use lastAccessedAt if available
            return item.conversation?.lastAccessedAt ?? 0;
          }
        };

        const aTime = getActivityTime(a);
        const bTime = getActivityTime(b);

        // If both have activity times, sort by most recent first
        if (aTime > 0 && bTime > 0) {
          return bTime - aTime;
        }

        // If only one has activity time, it comes first
        if (aTime > 0 && bTime === 0) return -1;
        if (bTime > 0 && aTime === 0) return 1;

        // If neither has activity time, fall back to type-based sorting
        // Folders come before conversations as a tiebreaker
        if (a.folder && b.folder) {
          return 0; // Equal priority, will be handled by creation time below
        } else if (a.folder && !b.folder) {
          return -1; // Folder before conversation when no activity
        } else if (!a.folder && b.folder) {
          return 1; // Conversation after folder when no activity
        }

        // Sort conversations by last accessed time (most recent first)
        if (!a.folder && !b.folder) {
          const aActivityTime = a.conversation?.lastAccessedAt ?? 0;
          const bActivityTime = b.conversation?.lastAccessedAt ?? 0;

          // If both have activity time, sort by that
          if (aActivityTime > 0 && bActivityTime > 0) {
            return bActivityTime - aActivityTime;
          }

          // If only one has activity time, it comes first
          if (aActivityTime > 0 && bActivityTime === 0) return -1;
          if (bActivityTime > 0 && aActivityTime === 0) return 1;

          // If neither has activity time, sort by creation time (ID-based for now)
          // This keeps newly created conversations in a stable order without jumping to top
          return a.conversation?.id?.localeCompare(b.conversation?.id) || 0;
        }

        return 0;
      });
    };

    // Apply sorting to all levels
    const sortRecursive = (nodes: any[]) => {
      const sorted = sortNodes(nodes);
      sorted.forEach(node => {
        if (node.children && node.children.length > 0) {
          node.children = sortRecursive(node.children);
        }
      });
      return sorted;
    };

    return sortRecursive(rootItems);
  }, [folders, conversations, pinnedFolders]);

  // Handle drag and drop using MUI TreeView's built-in functionality
  const handleDragStart = (event: React.DragEvent, nodeId: string) => {
    console.log('Drag started:', nodeId);
    const nodeType = nodeId.startsWith('conv-') ? 'conversation' : 'folder';
    const dragData = { nodeId, nodeType };
    event.dataTransfer.setData('application/json', JSON.stringify(dragData));
  };

  const handleDragOver = (event: React.DragEvent, nodeId: string) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = async (event: React.DragEvent, nodeId: string) => {
    event.preventDefault();

    try {
      const dragDataStr = event.dataTransfer.getData('application/json');
      if (!dragDataStr) return;

      const { nodeId: sourceNodeId, nodeType } = JSON.parse(dragDataStr);

      if (sourceNodeId === nodeId) return; // Can't drop on itself

      console.log(`Dropping ${sourceNodeId} onto ${nodeId}`);

      if (nodeType === 'conversation') {
        const targetFolderId = nodeId.startsWith('conv-') ? null : nodeId;
        await handleMoveConversation(sourceNodeId, targetFolderId);
      } else if (nodeType === 'folder') {
        const targetParentId = nodeId.startsWith('conv-') ? null : nodeId;
        await handleMoveFolder(sourceNodeId, targetParentId);
      }
    } catch (error) {
      console.error('Drop error:', error);
      message.error('Failed to move item');
    }
  };

  // Create a unified folder configuration dialog that works for both creation and editing
  const showFolderConfigDialog = (folderId?: string) => {
    const isEditing = !!folderId;

    const folder: ConversationFolder | undefined = isEditing ? folders.find(f => f.id === folderId) : undefined;
    // Add dark mode styles for modal
    if (isDarkMode) {
      const styleEl = document.createElement('style');
      styleEl.id = 'dark-modal-styles';
      styleEl.textContent = `
              .dark-theme-modal .ant-modal-content {
                background-color: #141414 !important;
              color: #ffffff !important;
        }
              .dark-theme-modal .ant-modal-header {
                background-color: #141414 !important;
              border-bottom-color: #303030 !important;
        }
              .dark-theme-modal .ant-modal-title {
                color: #ffffff !important;
        }
              .dark-theme-modal .ant-btn {
                 color: rgba(255, 255, 255, 0.85) !important; /* Light text for default buttons */
                border-color: #434343 !important; /* Dark border for default buttons */
                background-color: #262626 !important; /* Dark background for default buttons */
        }
              .dark-theme-modal .ant-btn:hover:not(.ant-btn-primary) {
                 color: #ffffff !important;
                 border-color: #535353 !important;
                 background-color: #303030 !important; /* Slightly lighter on hover */
         }
              .dark-theme-modal .ant-btn-primary {
                background-color: #1890ff !important;
               border-color: #1890ff !important;
               text-shadow: none !important;
               color: #ffffff !important;
        }
              .dark-theme-modal .ant-modal-body {
                background-color: #141414 !important;
              color: #ffffff !important;
        }
              .dark-theme-modal .ant-modal-footer {
                border-top-color: #303030 !important;
              background-color: #141414 !important;
        }
              .dark-theme-modal .ant-modal-close {
                color: #ffffff !important;
        }
              .dark-theme-modal .ant-radio-button-wrapper {
                color: #ffffff !important;
              border-color: #434343 !important;
        }
              .dark-theme-modal .ant-radio-button-wrapper-checked:not(.ant-radio-button-wrapper-disabled) {
                color: #ffffff !important;
              background: #177ddc !important;
              border-color: #177ddc !important;
        }
              .dark-theme-modal .ant-form-item-label > label {
                color: #ffffff !important;
        }
              .dark-theme-modal .ant-input {
                background-color: #1f1f1f !important;
              color: #ffffff !important;
              border-color: #434343 !important;
        }
              .dark-theme-modal .ant-input-textarea {
                background-color: #1f1f1f !important;
              color: #ffffff !important;
              border-color: #434343 !important;
        }
              .dark-theme-modal .ant-divider {
                border-color: #303030 !important;
        }
              .dark-theme-modal .ant-switch {
                background-color: rgba(255, 255, 255, 0.25) !important;
        }
              .dark-theme-modal .ant-switch-checked {
                background-color: #1890ff !important;
        }
              `;

      // Remove any existing style element and add the new one
      document.getElementById('dark-modal-styles')?.remove();
      document.head.appendChild(styleEl);
    }

    // Reset form to default values
    folderConfigForm.resetFields();

    // Set initial values based on whether we're editing or creating
    folderConfigForm.setFieldsValue({
      name: folder?.name || '',
      useGlobalContext: folder?.useGlobalContext !== false, // Default to true if undefined
      useGlobalModel: folder?.useGlobalModel !== false, // Default to true if undefined
      systemInstructions: folder?.systemInstructions || ''
    });

    console.log("Folder config dialog initial values:", {
      isEditing,
      name: folder?.name || '',
      useGlobalContext: folder?.useGlobalContext !== false,
      useGlobalModel: folder?.useGlobalModel !== false,
      systemInstructions: folder?.systemInstructions || ''
    });

    Modal.confirm({
      title: <span style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>
        {isEditing ? 'Edit Folder' : 'Create New Folder'}
      </span>,
      width: 500,
      wrapClassName: isDarkMode ? 'dark-theme-modal' : '',
      content: (
        <div style={{
          color: isDarkMode ? '#ffffff' : '#000000',
          padding: '14px'
        }}>
          <Form
            form={folderConfigForm}
            layout="vertical"
          >
            <Form.Item
              name="name"
              label={<span style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>Folder Name</span>}
              rules={[{ required: true, message: 'Please enter a folder name' }]}
            >
              <Input
                style={{
                  backgroundColor: isDarkMode ? '#1f1f1f' : '#ffffff',
                  color: isDarkMode ? '#ffffff' : '#000000',
                  borderColor: isDarkMode ? '#434343' : '#d9d9d9'
                }}
              />
            </Form.Item>

            <AntDivider style={{ borderColor: isDarkMode ? '#303030' : '#f0f0f0' }} />

            <Form.Item
              name="useGlobalContext"
              label={<span style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>Use Global File Context</span>}
              valuePropName="checked"
              tooltip="When enabled, this folder will use the global file context. When disabled, you can set a specific file context for this folder."
            >
              <Switch
                className={isDarkMode ? 'dark-theme-switch' : ''}
              />
            </Form.Item>


            <AntDivider style={{ borderColor: isDarkMode ? '#303030' : '#f0f0f0' }} />

            <Form.Item
              name="useGlobalModel"
              label={<span style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>Use Global Model Configuration</span>}
              valuePropName="checked"
              tooltip="When enabled, this folder will use the global model configuration. When disabled, you can set a specific model for this folder."
            >
              <Switch
                className={isDarkMode ? 'dark-theme-switch' : ''}
              />
            </Form.Item>


            <Form.Item
              name="systemInstructions"
              label={<span style={{ color: isDarkMode ? '#ffffff' : '#000000' }}>Additional System Instructions</span>}
            >
              <Input.TextArea
                autoSize={{ minRows: 4, maxRows: 12 }}
                style={{
                  width: '100%',
                  backgroundColor: isDarkMode ? '#1f1f1f' : '#ffffff',
                  color: isDarkMode ? '#ffffff' : '#000000',
                  borderColor: isDarkMode ? '#434343' : '#d9d9d9'
                }}
              />
            </Form.Item>
          </Form>
        </div>
      ),
      onOk: async () => {
        try {
          // Get form values
          const values = folderConfigForm.getFieldsValue();
          if (!values.name || !values.name.trim()) {
            message.error('Please enter a folder name');
            return Promise.reject('Please enter a folder name');
          }

          if (isEditing && folder) {
            // Update existing folder
            const updatedFolder = {
              ...folder,
              name: values.name,
              useGlobalContext: values.useGlobalContext,
              useGlobalModel: values.useGlobalModel,
              systemInstructions: values.systemInstructions,
              updatedAt: Date.now()
            };
            await updateFolder(updatedFolder);
            message.success(`Folder "${values.name}" updated successfully`);
          } else {
            // Create new folder
            const newFolderId = await createFolder(values.name, currentFolderId);

            // Ensure parent folder is expanded when creating a subfolder
            if (currentFolderId && !expandedNodes.includes(currentFolderId)) {
              setExpandedNodes(prev => [...prev, currentFolderId]);
            }

            const updatedFolder = {
              id: newFolderId,
              name: values.name,
              parentId: currentFolderId,
              useGlobalContext: values.useGlobalContext,
              useGlobalModel: values.useGlobalModel,
              systemInstructions: values.systemInstructions,
              createdAt: Date.now(),
              updatedAt: Date.now()
            };
            await updateFolder(updatedFolder);
            message.success(`Folder "${values.name}" created successfully`);
          }
        } catch (error) {
          message.error('Failed to create folder');
          console.error('Error creating folder:', error);
          return Promise.reject(error);
        }
      },
      okButtonProps: {
        style: {
          backgroundColor: isDarkMode ? '#177ddc' : '#1890ff',
        }
      },
      cancelButtonProps: {
        style: {
          color: isDarkMode ? '#ffffff' : undefined,
        }
      }
    });
  };

  // Handle creating a new folder
  const handleCreateNewFolder = async () => {
    try {
      // Create a new folder with default name and settings at root level
      const createdFolderId = await createFolder('New Folder', currentFolderId);
      const newFolderId = String(createdFolderId);

      // Ensure parent folder is expanded if creating a subfolder
      if (currentFolderId && !expandedNodes.includes(currentFolderId)) {
        setExpandedNodes(prev => [...prev, currentFolderId]);
      }

      message.success('New folder created successfully');

      // Start editing the folder name immediately
      if (newFolderId) {
        setTimeout(() => {
          setEditingId(newFolderId);
          setEditValue('New Folder');
        }, 100);
      }
    } catch (error) {
      console.error('Error creating folder:', error);
      message.error('Failed to create folder');
    }
  };

  // Render the tree recursively
  const renderTree = (nodes: any[]) => {
    return nodes.map(node => {
      const isFolder = Boolean(node.folder);
      const nodeId = node.id;
      const labelText = isFolder ? node.name : node.name;
      const isPinned = isFolder && pinnedFolders.has(node.id);
      const isCurrentItem = isFolder
        ? node.id === currentFolderId
        : node.id.startsWith('conv-') && node.id.substring(5) === currentConversationId;
      const hasUnreadResponse = !isFolder && node.id.startsWith('conv-') &&
        node.conversation?.hasUnreadResponse && node.id.substring(5) !== currentConversationId;

      // Fix streaming detection - ensure we're checking the actual conversation ID
      const conversationId = !isFolder && node.id.startsWith('conv-') ? node.id.substring(5) : null;
      const isStreamingConv = conversationId && streamingConversations.has(conversationId);

      const conversationCount = isFolder ? node.conversationCount : 0;
      const isEditing = editingId === (isFolder ? node.id : node.id.substring(5));

      // Add draggable attribute to enable drag and drop
      return (
        <ChatTreeItem
          key={nodeId}
          nodeId={nodeId}
          labelText={labelText}
          isFolder={isFolder}
          isPinned={isPinned}
          isCurrentItem={isCurrentItem}
          isStreaming={isStreamingConv}
          hasUnreadResponse={hasUnreadResponse}
          conversationCount={conversationCount}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onAddChat={handleAddChat}
          onPin={togglePinFolder}
          onConfigure={handleConfigureFolder}
          onFork={handleForkConversation}
          onCompress={handleCompressConversation}
          onMove={handleMoveConversation}
          onOpenMoveMenu={handleOpenMoveMenu}
          onCreateSubfolder={handleCreateSubfolder}
          isEditing={isEditing}
          editValue={editValue}
          onEditChange={handleEditChange}
          onEditSubmit={handleEditSubmit}
          // Add drag and drop props
          onDragStart={(e) => handleDragStart(e, nodeId)}
          onDragOver={(e) => handleDragOver(e, nodeId)}
          onDrop={(e) => handleDrop(e, nodeId)}
        >
          {isFolder && node.children && node.children.length > 0 ? renderTree(node.children) : null}
        </ChatTreeItem>
      );
    });
  };

  return isLoading && !currentConversationId ? (
    <Box sx={{
      height: '100%',
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center'
    }}>
      <Spin size="large" />
    </Box>
  ) : (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Tree View with integrated action buttons */}
      <Box sx={{ flexGrow: 1, overflow: 'auto', pt: 1 }}>
        {(() => {
          const treeViewStyles = {
            height: '100%',
            overflowY: 'auto' as const,
            '& .MuiTreeItem-root': {
              '&.Mui-selected > .MuiTreeItem-content': {
                bgcolor: isDarkMode ? '#177ddc' : '#e6f7ff',
                color: isDarkMode ? '#ffffff' : 'inherit',
              },
              '&.drag-over > .MuiTreeItem-content': {
                backgroundColor: isDarkMode ? 'rgba(24, 144, 255, 0.2)' : 'rgba(24, 144, 255, 0.1)',
                border: isDarkMode ? '1px dashed #177ddc' : '1px dashed #1890ff'
              }
            },
            '& .MuiTreeItem-content': {
              padding: '0px 8px',
              minHeight: '20px'
            }
          };

          return (
            <TreeView
              aria-label="chat history"
              sx={treeViewStyles}
              defaultCollapseIcon={<ArrowDropDownIcon />}
              defaultExpandIcon={<ArrowRightIcon />}
              defaultEndIcon={<div style={{ width: 24 }} />}
              expanded={expandedNodes.map(String)}
              selected={currentConversationId ? 'conv-' + currentConversationId : currentFolderId || ''}
              onNodeToggle={handleNodeToggle}
              onNodeSelect={handleTreeNodeSelect}
              // Disable TreeView's built-in selection behavior during drag
              disableSelection={false}
              className="chat-history-tree"
            >
              {renderTree(treeData)}
            </TreeView>
          );
        })()}
      </Box>

      {/* Export/Import buttons */}
      <Box sx={{
        mt: 'auto',
        display: 'flex',
        justifyContent: 'flex-end',
        p: 2,
        borderTop: isDarkMode ? '1px solid #303030' : '1px solid #e8e8e8'
      }}>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={handleExportConversations}
            size="small"
          >
            Export
          </Button>
          <Button
            variant="outlined"
            startIcon={<UploadIcon />}
            onClick={handleImportConversations}
            size="small"
          >
            Import
          </Button>
        </Box>
      </Box>

      {/* Render the move menu */}
      <MoveToFolderMenu
        anchorEl={moveToMenuState.anchorEl}
        open={Boolean(moveToMenuState.anchorEl)}
        onClose={handleCloseMoveMenu}
        folders={folders}
        onMove={handleMoveConversation}
        onMoveFolder={handleMoveFolder}
        nodeId={moveToMenuState.nodeId}
      />
    </Box>
  );
};

export default MUIChatHistory;
