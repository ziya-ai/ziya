import React, { useState, useCallback, useEffect, memo, useRef, useMemo } from 'react';
import { FixedSizeList, ListProps as ReactWindowListProps } from 'react-window';
import { message, Modal, Form, Spin, Input, Switch, Dropdown, Menu as AntMenu } from 'antd';
import { ConversationHealthDebugModal } from './ConversationHealthDebug';
import ExportConversationModal from './ExportConversationModal';
import { useConversationList } from '../context/ConversationListContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { useStreamingContext } from '../context/StreamingContext';
import { useTheme } from '../context/ThemeContext';
import { useProject } from '../context/ProjectContext';
import { Conversation, ConversationFolder, SearchResult } from '../utils/types';
import SwarmRecoveryPanel from './SwarmRecoveryPanel';
import { db } from '../utils/db';
import { v4 as uuidv4 } from 'uuid';
import type { DelegateMeta, TaskPlan, DelegateStatus } from '../types/delegate';
// MUI imports
import { styled } from '@mui/material/styles';
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Button from '@mui/material/Button';
import Menu from '@mui/material/Menu';
import PublicIcon from '@mui/icons-material/Public';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import { Divider as AntDivider } from 'antd';

// MUI icons
import CreateNewFolderIcon from '@mui/icons-material/CreateNewFolder';
import FolderIcon from '@mui/icons-material/Folder';
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
import SearchIcon from '@mui/icons-material/Search';
import CloseIcon from '@mui/icons-material/Close';
import AddCommentIcon from '@mui/icons-material/AddComment';

// Ant Design Icons for the menu items
import {
  EditOutlined,
  DeleteOutlined,
  CopyOutlined as AntCopyOutlined,
  CompressOutlined as AntCompressOutlined,
  SettingOutlined as AntSettingOutlined,
  ExportOutlined as AntExportOutlined,
  FolderOutlined as AntFolderOutlined,
  PushpinOutlined as AntPushpinOutlined,
  GlobalOutlined as AntGlobalOutlined,
  SwapOutlined as AntSwapOutlined,
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

// Custom TreeItem component for chat items
interface ChatTreeItemProps {
  nodeId: string;
  labelText: string;  // This property is used in the component
  isFolder?: boolean;
  isTaskPlanFolder?: boolean;
  taskPlanProgress?: string;   // e.g. "3/4"
  onSwarmRecovery?: (folderId: string) => void;
  delegateStatus?: DelegateStatus | 'orchestrator' | null;
  onDelegateRetry?: (nodeId: string) => void;
  onDelegateSkip?: (nodeId: string) => void;
  isPinned?: boolean;
  isCurrentItem?: boolean;
  isGlobalItem?: boolean;
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
  onExport?: (id: string) => void;
  onMove: (id: string, folderId: string | null) => void;
  onToggleGlobal?: (id: string) => void;
  onMoveToProject?: (id: string, anchorEl: HTMLElement) => void;
  onOpenMoveMenu?: (id: string, anchorEl: HTMLElement) => void;
  onCreateSubfolder?: (id: string) => void;
  onMoveFolder?: (id: string, parentId: string | null) => void;
  onCustomDragEnd?: (draggedId: string, targetId: string, dragType: 'folder' | 'conversation') => void;
  onMouseDown?: (event: React.MouseEvent) => void;
  isEditing?: boolean;
  editValue?: string;
  onEditChange?: (value: string) => void;
  depth?: number;
  isExpanded?: boolean;
  hasChildren?: boolean;
  onToggleExpand?: (nodeId: string) => void;
  children?: React.ReactNode;
  isDragOver?: boolean;
  className?: string;
  onEditSubmit: (id: string, value: string) => void;
  style?: React.CSSProperties;
  onDragOver?: (event: React.DragEvent) => void;
  onDragLeave?: () => void;
  onDrop?: (event: React.DragEvent) => void;
}

const ChatTreeItem = memo<ChatTreeItemProps>((props) => {
  const {
    nodeId,
    labelText,
    isFolder = false,
    isTaskPlanFolder = false,
    taskPlanProgress,
    delegateStatus,
    isPinned = false,
    isCurrentItem = false,
    onDelegateRetry,
    onDelegateSkip,
    isGlobalItem = false,
    isStreaming = false,
    hasUnreadResponse = false,
    conversationCount = 0,
    onEdit,
    onDelete,
    onAddChat,
    onExport,
    onPin,
    onConfigure,
    onToggleGlobal,
    onMoveToProject,
    onFork,
    onCompress,
    onMove,
    onOpenMoveMenu,
    onCreateSubfolder,
    isEditing = false,
    editValue = '',
    onMoveFolder,
    onCustomDragEnd,
    onMouseDown,
    onEditChange,
    onEditSubmit,
    className,
    style,
    onDragOver,
    onDragLeave,
    onDrop,
    depth,
    isExpanded,
    hasChildren,
    onToggleExpand,
    ...other
  } = props;

  const { isDarkMode } = useTheme();
  const [isHovered, setIsHovered] = useState(false);
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
    <div
      className={`virtual-tree-row ${itemClassName.trim()}`}
      data-node-id={nodeId}
      style={{
        ...style,
        display: 'flex',
        alignItems: 'center',
        height: VIRTUAL_ROW_HEIGHT,
        boxSizing: 'border-box',
        cursor: 'default',
        borderRadius: 4,
        padding: `4px 8px 4px ${12 + (depth || 0) * 20 + (!isFolder && (depth || 0) > 0 ? 10 : 0)}px`,
        transition: 'background-color 0.15s',
        backgroundColor: props.isCurrentItem
          ? (isDarkMode ? '#177ddc' : '#e6f7ff')
          : undefined,
        color: props.isCurrentItem && isDarkMode ? '#fff' : undefined,
      }}
      onClick={(e) => {
        // If folder with children, toggle expand on the chevron area or the whole row
        if (isFolder && hasChildren) {
          onToggleExpand?.(nodeId);
        }
      }}
    >
      {/* Expand/collapse chevron for folders */}
      <span
        style={{ width: 20, flexShrink: 0, display: isFolder ? 'flex' : 'none', alignItems: 'center', justifyContent: 'center', opacity: 0.7, cursor: isFolder && hasChildren ? 'pointer' : 'default' }}
      >
        {isFolder && hasChildren ? (
          isExpanded ? <ArrowDropDownIcon sx={{ fontSize: 18 }} /> : <ArrowRightIcon sx={{ fontSize: 18 }} />
        ) : <span style={{ width: 18 }} />}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{ width: '100%' }}
          onMouseDown={onMouseDown}
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
              isTaskPlanFolder ? (
                <span style={{ marginRight: 8, fontSize: 16 }}>⚡</span>
              ) : (
                <FolderIcon color="primary" sx={{ mr: 1, fontSize: 20 }} />
              )
            ) : (
              delegateStatus ? (
                <span style={{
                  marginRight: 8,
                  fontSize: 14,
                  display: 'inline-flex',
                  alignItems: 'center',
                }}>
                  {delegateStatus === 'orchestrator' && '🎯'}
                  {delegateStatus === 'crystal' && '💎'}
                  {delegateStatus === 'running' && <span style={{ animation: 'pulse 2s infinite' }}>🔵</span>}
                  {delegateStatus === 'compacting' && <span style={{ animation: 'pulse 1.5s infinite' }}>🟢</span>}
                  {delegateStatus === 'proposed' && '⏳'}
                  {delegateStatus === 'ready' && '⏳'}
                  {delegateStatus === 'failed' && '❌'}
                </span>
              ) : (
                <ChatIcon sx={{ mr: 1, fontSize: 20 }} />
              )
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
                {/* Delegate status badge (after label text) */}
                {delegateStatus === 'crystal' && (
                  <Typography variant="caption" sx={{ ml: 0.5, color: '#52c41a', fontWeight: 500 }}>✓</Typography>
                )}
                {(delegateStatus === 'running' || delegateStatus === 'compacting') && (
                  <Typography variant="caption" sx={{ ml: 0.5, color: '#1890ff', fontSize: 10 }}>⟳</Typography>
                )}
                {delegateStatus === 'failed' && (
                  <Typography variant="caption" sx={{ ml: 0.5, color: '#ff4d4f', fontWeight: 500 }}>✗</Typography>
                )}
                {isPinned && (
                  <PushPinIcon fontSize="small" color="primary" sx={{ ml: 0.5, fontSize: 14 }} />
                )}
                {isGlobalItem && (
                  isHovered ? <Tooltip title="Visible in all projects"><PublicIcon fontSize="small" color="info" sx={{ ml: 0.5, fontSize: 14 }} /></Tooltip> : <PublicIcon fontSize="small" color="info" sx={{ ml: 0.5, fontSize: 14 }} />
                )}
                {isFolder && conversationCount > 0 && (
                  <Typography variant="caption" sx={{ ml: 0.5, color: 'text.secondary' }}>({conversationCount})</Typography>
                )}
                {isTaskPlanFolder && taskPlanProgress && (
                  <Typography variant="caption" sx={{ ml: 0.5, px: 0.5, borderRadius: '8px', backgroundColor: '#52c41a', color: '#fff', fontWeight: 600, fontSize: 10 }}>{taskPlanProgress}</Typography>
                )}

                <Box sx={{
                  ml: 'auto',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  opacity: isHovered ? 1 : 0,
                  transition: 'opacity 0.2s ease-in-out'
                }}>
                  {isFolder && !isTaskPlanFolder && (
                      isHovered ? (
                        <Tooltip title="New chat in this folder">
                          <IconButton
                            size="small"
                            onClick={(e) => { e.stopPropagation(); onAddChat(nodeId); }}
                            sx={{ p: 0.5, mr: 0.5 }}
                          >
                            <AddIcon fontSize="small" sx={{ fontSize: '16px' }} />
                          </IconButton>
                        </Tooltip>
                      ) : null
                  )}
                    {isHovered ? (
                      <Dropdown
                        dropdownRender={() => <AntActionMenu
                          isFolder={isFolder}
                          nodeId={nodeId} isTaskPlanFolder={isTaskPlanFolder} onCopyToProject={props.onCopyToProject}
                          delegateStatus={delegateStatus} onDelegateRetry={props.onDelegateRetry} onDelegateSkip={props.onDelegateSkip}
                          onSwarmRecovery={props.onSwarmRecovery}
                          onEdit={onEdit} onDelete={onDelete} onFork={onFork} onCompress={onCompress} onExport={onExport}
                          onOpenMoveMenu={onOpenMoveMenu}
                          onToggleGlobal={onToggleGlobal} onMoveToProject={onMoveToProject} isGlobalItem={isGlobalItem}
                          onConfigure={onConfigure} onPin={onPin} isPinned={isPinned} onCreateSubfolder={onCreateSubfolder}
                        />}
                        trigger={['click']}
                        placement="bottomRight"
                      >
                        <IconButton size="small" sx={{ p: 0.5 }} onClick={e => e.stopPropagation()} >
                          <MoreVertIcon fontSize="small" sx={{ fontSize: '16px' }} />
                        </IconButton>
                      </Dropdown>
                    ) : (
                      <IconButton size="small" sx={{ p: 0.5, opacity: 0, pointerEvents: 'none' }}>
                        <MoreVertIcon fontSize="small" sx={{ fontSize: '16px' }} />
                      </IconButton>
                    )}
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
                  Processing…
                </Typography>
              </Box>
            )}</Box>
        </div>
      </div>
    </div>
  );
});
const AntActionMenu = ({ isFolder, nodeId, onEdit, onDelete, onFork, onCompress, onExport, onOpenMoveMenu, onToggleGlobal, onMoveToProject, onCopyToProject, isGlobalItem, onConfigure, onPin, isPinned, onCreateSubfolder, isTaskPlanFolder, onSwarmRecovery, delegateStatus, onDelegateRetry, onDelegateSkip }) => {
  const handleAntAction = (actionCallback: (id: string) => void, originalEvent?: React.MouseEvent | Event) => {
    originalEvent?.stopPropagation();
    actionCallback(nodeId);
  };

  const items: any[] = [];

  if (!isFolder) {
    // Delegate recovery actions for failed/interrupted delegates
    const isFailedDelegate = delegateStatus === 'failed' || delegateStatus === 'interrupted';
    if (isFailedDelegate && onDelegateRetry) {
      items.push(
        { key: 'delegate-retry', label: '🔄 Retry delegate', onClick: (e) => { e.domEvent.stopPropagation(); onDelegateRetry(nodeId); } },
        { key: 'delegate-skip', label: '⏭️ Skip & unblock downstream', onClick: (e) => { e.domEvent.stopPropagation(); onDelegateSkip?.(nodeId); } },
        { type: 'divider' as const },
      );
    }

    items.push(
      { key: 'edit', label: 'Rename', icon: <EditOutlined />, onClick: (e) => handleAntAction(onEdit, e.domEvent) },
      { key: 'fork', label: 'Fork', icon: <AntCopyOutlined />, onClick: (e) => handleAntAction(onFork, e.domEvent) },
      { key: 'compress', label: 'Compress', icon: <AntCompressOutlined />, onClick: (e) => handleAntAction(onCompress, e.domEvent) },
      {
        key: 'move', label: 'Move to folder', icon: <AntFolderOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onOpenMoveMenu && onOpenMoveMenu(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      {
        key: 'global-toggle', label: isGlobalItem ? '📌 This project only' : '🌐 Share across projects', icon: <AntGlobalOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onToggleGlobal && onToggleGlobal(nodeId);
        }
      },
      {
        key: 'move-project', label: 'Move to project', icon: <AntSwapOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onMoveToProject && onMoveToProject(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      {
        key: 'copy-project', label: 'Copy to project', icon: <AntCopyOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onCopyToProject && onCopyToProject(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      { key: 'export', label: 'Export', icon: <AntExportOutlined />, onClick: (e) => handleAntAction(onExport, e.domEvent) },
      { type: 'divider' as const },
      { key: 'delete', label: 'Delete', icon: <DeleteOutlined />, onClick: (e) => handleAntAction(onDelete, e.domEvent), danger: true }
    );
  } else { // isFolder
    items.push(
      { key: 'edit', label: 'Rename', icon: <EditOutlined />, onClick: (e) => handleAntAction(onEdit, e.domEvent) },
    );
    if (isTaskPlanFolder && onSwarmRecovery) {
      items.push({ key: 'swarm-recovery', label: '🔧 Swarm Recovery', onClick: (e) => { e.domEvent.stopPropagation(); onSwarmRecovery(nodeId); } });
    }
    if (!isTaskPlanFolder) {
      items.push(
        { key: 'new-subfolder', label: 'New Subfolder', icon: <CreateNewFolderIcon />, onClick: (e) => handleAntAction(onCreateSubfolder, e.domEvent) },
      );
    }
    items.push(
      { key: 'configure', label: 'Configuration', icon: <AntSettingOutlined />, onClick: (e) => handleAntAction(onConfigure, e.domEvent) },
      { key: 'pin', label: isPinned ? 'Unpin' : 'Pin to Top', icon: <AntPushpinOutlined />, onClick: (e) => handleAntAction(onPin, e.domEvent) },
      {
        key: 'move', label: 'Move to folder', icon: <AntFolderOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onOpenMoveMenu && onOpenMoveMenu(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      {
        key: 'global-toggle', label: isGlobalItem ? '📌 This project only' : '🌐 Share across projects', icon: <AntGlobalOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onToggleGlobal && onToggleGlobal(nodeId);
        }
      },
      {
        key: 'move-project', label: 'Move to project', icon: <AntSwapOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onMoveToProject && onMoveToProject(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      {
        key: 'copy-project', label: 'Copy to project', icon: <AntCopyOutlined />, onClick: (e) => {
          e.domEvent.stopPropagation();
          onCopyToProject && onCopyToProject(nodeId, e.domEvent.currentTarget as HTMLElement);
        }
      },
      { type: 'divider' as const },
      { key: 'delete', label: 'Delete', icon: <DeleteOutlined />, onClick: (e) => handleAntAction(onDelete, e.domEvent), danger: true }
    );
  }

  return <AntMenu items={items} />;
};

// Sort comparator extracted so both full-rebuild and sort-only fast path share it.
function sortComparator(a: any, b: any, taskPlanBoost: Map<string, number>): number {
  if (a.isPinned && !b.isPinned) return -1;
  if (!a.isPinned && b.isPinned) return 1;

  const aDel = a.delegateMeta;
  const bDel = b.delegateMeta;
  if (aDel && bDel) {
    if (aDel.role === 'orchestrator' && bDel.role !== 'orchestrator') return -1;
    if (bDel.role === 'orchestrator' && aDel.role !== 'orchestrator') return 1;
    return (a.conversation?.lastAccessedAt ?? 0) - (b.conversation?.lastAccessedAt ?? 0);
  }

  const getTime = (item: any) => {
    if (item.folder) return item.lastActivityTime > 0 ? item.lastActivityTime : item.createdAt;
    const ct = item.conversation?.lastAccessedAt ?? 0;
    const boost = item.conversation?.id ? (taskPlanBoost.get(item.conversation.id) || 0) : 0;
    return Math.max(ct, boost);
  };
  const aT = getTime(a), bT = getTime(b);
  if (aT > 0 && bT > 0) return bT - aT;
  if (aT > 0) return -1;
  if (bT > 0) return 1;

  if (a.folder && !b.folder) return -1;
  if (!a.folder && b.folder) return 1;
  if (!a.folder && !b.folder) {
    const aA = a.conversation?.lastAccessedAt ?? 0;
    const bA = b.conversation?.lastAccessedAt ?? 0;
    if (aA > 0 && bA > 0) return bA - aA;
    if (aA > 0) return -1;
    if (bA > 0) return 1;
    return a.conversation?.id?.localeCompare(b.conversation?.id) || 0;
  }
  return 0;
}

// Re-anchor TaskPlan folders immediately after their source conversation.
// Sorting may separate them; this restores adjacency.
function reanchorTaskPlanFolders(
  items: any[],
  anchoredIds: Set<string>,
  folderMap: Map<string, any>,
  _depth = 0,
): void {
  if (_depth > 20 || anchoredIds.size === 0) return;
  for (const fid of anchoredIds) {
    const fn = folderMap.get(fid);
    const srcId = fn?.taskPlan?.source_conversation_id;
    if (!srcId) continue;
    const srcIdx = items.findIndex(n => n.id === `conv-${srcId}`);
    const curIdx = items.findIndex(n => n.id === fid);
    if (srcIdx !== -1 && curIdx !== -1 && curIdx !== srcIdx + 1) {
      items.splice(curIdx, 1);
      const ns = items.findIndex(n => n.id === `conv-${srcId}`);
      items.splice(ns + 1, 0, fn);
    }
  }
  for (const item of items) {
    if (item.children?.length) reanchorTaskPlanFolders(item.children, anchoredIds, folderMap, _depth + 1);
  }
}

// Virtualization: flattened node for react-window rendering
interface FlatNode {
  id: string;
  name: string;
  depth: number;
  isFolder: boolean;
  isExpanded: boolean;
  hasChildren: boolean;
  node: any; // original tree node
}

/** Walk the tree and return only visible (expanded-ancestor) nodes with depth. */
function flattenVisibleNodes(
  nodes: any[],
  expandedSet: Set<string>,
  depth: number = 0,
  visited?: Set<string>
): FlatNode[] {
  if (depth > 20) return []; // Prevent stack overflow from circular references
  const seen = visited || new Set<string>();
  const result: FlatNode[] = [];
  for (const node of nodes) {
    if (seen.has(node.id)) continue; // Skip circular references
    seen.add(node.id);
    // Only real folders (with node.folder or node.taskPlan) are collapsible.
    // Conversations may have children (e.g. delegate swarm anchoring) but
    // should always render their children inline, not require expansion.
    const isFolder = Boolean(node.folder) || Boolean(node.taskPlan);
    const hasChildren = Boolean(node.children?.length);
    const isExpanded = isFolder && expandedSet.has(node.id);
    result.push({
      id: node.id,
      name: node.name,
      depth,
      isFolder,
      isExpanded,
      hasChildren,
      node,
    });
    // Only recurse into children if this node is expanded
    // For non-folder nodes with children (e.g. conversations with delegates),
    // always show children inline.
    if (hasChildren && (!isFolder || isExpanded)) {
      result.push(...flattenVisibleNodes(node.children, expandedSet, depth + 1, seen));
    }
  }
  return result;
}

const VIRTUAL_ROW_HEIGHT = 36;

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
      if (folder.taskPlan) {
        return;
      }

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
  const renderFolderItems = (parentId = null, level = 0): React.ReactNode => {
    if (level > 20) return null;
    const foldersInLevel = (foldersByParent.get(parentId) || []).slice().sort((a, b) => a.name.localeCompare(b.name));

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

const MoveToProjectMenu = ({
  anchorEl,
  open,
  onClose,
  projects,
  currentProjectId,
  onMoveToProject,
  nodeId
}: {
  anchorEl: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  projects: Array<{ id: string; name: string; path: string }>;
  currentProjectId: string | undefined;
  onMoveToProject: (conversationId: string, projectId: string) => void;
  nodeId: string | null;
}) => {
  if (!nodeId) return null;

  // Strip conv- prefix for conversations
  const cleanId = nodeId.startsWith('conv-') ? nodeId.substring(5) : nodeId;

  return (
    <Menu
      anchorEl={anchorEl}
      open={open}
      onClose={onClose}
      anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      transformOrigin={{ vertical: 'top', horizontal: 'right' }}
    >
      {projects
        .filter(p => p.id !== currentProjectId)
        .map(project => (
          <MenuItem
            key={project.id}
            onClick={(e) => {
              e.stopPropagation();
              onMoveToProject(cleanId, project.id);
              onClose();
            }}
          >
            <ListItemIcon><FolderIcon fontSize="small" /></ListItemIcon>
            <ListItemText primary={project.name} secondary={project.path} />
          </MenuItem>
        ))}
      {projects.filter(p => p.id !== currentProjectId).length === 0 && (
        <MenuItem disabled>No other projects available</MenuItem>
      )}
    </Menu>
  );
};

const MUIChatHistory = () => {
  const {
    conversations,
    isProjectSwitching,
    setConversations,
    isLoadingConversation,
    toggleConversationGlobal,
    moveConversationToProject,
    moveFolderToProject,
    toggleFolderGlobal,
    copyConversationToProject,
    folders,
    setFolders,
    currentFolderId,
    setCurrentFolderId,
    createFolder,
    updateFolder,
    deleteFolder,
    moveConversationToFolder
  } = useConversationList();
  const {
    currentConversationId,
    setDynamicTitleLength,
    startNewChat,
    loadConversation,
    loadConversationAndScrollToMessage,
    streamingConversations,
  } = useActiveChat();

  const { isDarkMode } = useTheme();
  const { projects, currentProject, switchProject } = useProject();
  const [expandedNodes, setExpandedNodes] = useState<React.Key[]>([]);
  const chatHistoryRef = useRef<HTMLDivElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const virtualListRef = useRef<FixedSizeList>(null);
  // When set, the next flatNodes change scrolls to this node instead of the
  // current conversation.  Used after folder creation so the user sees the
  // new folder rather than being yanked to the active chat.
  const scrollToNodeIdRef = useRef<string | null>(null);
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
  const [moveToProjectMenuState, setMoveToProjectMenuState] =
    useState<{
      anchorEl: null | HTMLElement;
      nodeId: null | string;
      mode: 'move' | 'copy';
    }>({ anchorEl: null, nodeId: null, mode: 'move' });
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportConversationId, setExportConversationId] = useState<string | null>(null);
  const [showHealthDebug, setShowHealthDebug] = useState(false);
  const [swarmRecoveryFolderId, setSwarmRecoveryFolderId] = useState<string | null>(null);
  const [showSwarmRecovery, setShowSwarmRecovery] = useState(false);

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchAllProjects, setSearchAllProjects] = useState(false);

  // Custom drag state to replace HTML5 drag and drop
  const [customDragState, setCustomDragState] = useState<{
    isDragging: boolean;
    draggedNodeId: string | null;
    draggedNodeType: 'folder' | 'conversation' | null;
    ghostElement: HTMLElement | null;
    draggedText: string;
  }>({
    isDragging: false,
    draggedNodeId: null,
    draggedNodeType: null,
    ghostElement: null,
    draggedText: ''
  });

  // Debounced search function
  const searchTimeoutRef = useRef<NodeJS.Timeout>();
  const performSearch = useCallback(async (query: string) => {
    if (!query || query.trim().length === 0) {
      setSearchResults([]);
      setIsSearching(false);
      return;
    }

    setIsSearching(true);

    try {
      const results = await db.searchConversations(query, {
        caseSensitive: false,
        maxSnippetLength: 150,
        projectId: searchAllProjects ? undefined : currentProject?.id
      });
      // Enrich results with project names for display
      const enriched = results.map(r => ({
        ...r,
        projectName: r.projectId ? projects.find(p => p.id === r.projectId)?.name : undefined
      }));
      setSearchResults(enriched);
      console.log(`🔍 Search for "${query}" found ${results.length} conversations`);
    } catch (error) {
      console.error('Search error:', error);
      message.error('Search failed');
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, [searchAllProjects, currentProject?.id, projects]);

  // Handle search input with debouncing
  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);

    // Clear previous timeout
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    // Debounce search by 300ms
    searchTimeoutRef.current = setTimeout(() => {
      performSearch(value);
    }, 300);
  }, [performSearch]);

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
  }, [searchAllProjects, currentProject?.id, projects]);

  // Re-run search when scope changes
  useEffect(() => {
    if (searchQuery.trim()) {
      performSearch(searchQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchAllProjects]);

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

  // Ensure the folder chain containing the current conversation is always expanded.
  // This handles project switches, search-result navigation, and any other code path
  // that programmatically selects a conversation that might live inside a collapsed folder.
  useEffect(() => {
    if (!currentConversationId) return;

    const conversation = conversations.find(c => c.id === currentConversationId);
    if (!conversation?.folderId) return;

    // Collect this folder and all its ancestors
    const foldersToExpand: string[] = [];
    let currentId: string | null | undefined = conversation.folderId;
    const visited = new Set<string>(); // guard against circular parentId references

    while (currentId && !visited.has(currentId)) {
      visited.add(currentId);
      const folder = folders.find(f => f.id === currentId);
      if (!folder) break;
      foldersToExpand.push(folder.id);
      currentId = folder.parentId;
    }

    if (foldersToExpand.length === 0) return;

    // Only update state if there are folders that aren't already expanded
    setExpandedNodes(prev => {
      const prevSet = new Set(prev.map(String));
      const missing = foldersToExpand.filter(id => !prevSet.has(id));
      if (missing.length === 0) return prev; // no-op, avoid re-render
      return [...prev, ...missing];
    });

  }, [currentConversationId, conversations, folders]);

  // Auto-expand newly appeared TaskPlan folders and their source conversations
  useEffect(() => {
    folders.forEach(folder => {
      const sourceId = folder.taskPlan?.source_conversation_id;
      if (!sourceId) return;
      setExpandedNodes(prev => {
        const needsFolder = !prev.includes(folder.id);
        if (!needsFolder) return prev;
        return [...prev, folder.id];
      });
    });
  }, [folders]);
  // Add keyboard shortcut to open debug modal (Ctrl+Shift+D)
  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'D') {
        e.preventDefault();
        setShowHealthDebug(true);
      }
    };
    window.addEventListener('keydown', handleKeyPress);
    return () => window.removeEventListener('keydown', handleKeyPress);
  }, []);

  // Save pinned folders to localStorage when they change
  useEffect(() => {
    if (pinnedFolders.size > 0) {
      localStorage.setItem('ZIYA_PINNED_FOLDERS', JSON.stringify([...pinnedFolders]));
    }
  }, [pinnedFolders]);

  // Handle panel width measurement for dynamic title length
  useEffect(() => {
    let lastWidth = 0;
    let timeoutId: NodeJS.Timeout;

    const resizeObserver = new ResizeObserver((entries) => {
      // Debounce resize events to prevent excessive firing
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        const entry = entries[0];
        if (entry && Math.abs(entry.contentRect.width - lastWidth) > 10) {
          lastWidth = entry.contentRect.width;
          const calculatedLength = Math.max(30, Math.min(80, Math.floor(lastWidth / 6)));
          setDynamicTitleLength(calculatedLength);
        }
      }, 250); // 250ms debounce
    });

    if (chatHistoryRef.current) {
      resizeObserver.observe(chatHistoryRef.current);
    }

    return () => {
      clearTimeout(timeoutId);
      resizeObserver.disconnect();
    };
  }, [setDynamicTitleLength]);

  // Custom drag implementation
  const createDragGhost = useCallback((text: string): HTMLElement => {
    const ghost = document.createElement('div');
    ghost.style.cssText = `
      position: fixed;
      background: linear-gradient(135deg, rgba(24, 144, 255, 0.95), rgba(64, 169, 243, 0.95));
      color: white;
      padding: 6px 12px;
      border-radius: 4px;
      font-size: 12px;
      pointer-events: none;
      z-index: 10000;
      box-shadow: 0 2px 8px rgba(0,0,0,0.3);
      font-family: system-ui;
      border: 1px solid rgba(255,255,255,0.3);
    `;
    ghost.textContent = `Moving: ${text}`;
    ghost.id = 'mui-drag-ghost';
    document.body.appendChild(ghost);
    return ghost;
  }, []);

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

  // Handle toggling global status
  const handleToggleGlobal = async (nodeId: string) => {
    const isConversation = nodeId.startsWith('conv-');

    if (isConversation) {
      const cleanId = nodeId.substring(5);
      await toggleConversationGlobal(cleanId);
      // Re-read from state after toggle (state is updated by the callback)
      const conv = conversations.find(c => c.id === cleanId);
      // The toggle flips the value, so the NEW state is the opposite of what we just read
      const wasGlobal = conv?.isGlobal;
      message.success(!wasGlobal ? 'Shared across all projects' : 'Restricted to current project');
    } else {
      // It's a folder ID directly
      const folder = folders.find(f => f.id === nodeId);
      await toggleFolderGlobal(nodeId);
      // Same logic: state was toggled, so the message should reflect the NEW state
      const wasGlobal = folder?.isGlobal;
      message.success(!wasGlobal ? 'Folder shared across all projects' : 'Folder restricted to current project');
    }
  };

  // Handle opening the move-to-project menu
  const handleOpenMoveToProjectMenu = (nodeId: string, anchorEl: HTMLElement) => {
    setMoveToProjectMenuState({ anchorEl, nodeId, mode: 'move' });
  };

  // Handle opening the copy-to-project menu
  const handleOpenCopyToProjectMenu = (nodeId: string, anchorEl: HTMLElement) => {
    setMoveToProjectMenuState({ anchorEl, nodeId, mode: 'copy' });
  };

  // Handle moving a conversation to another project
  const handleMoveToProject = async (nodeId: string, targetProjectId: string) => {
    const targetProject = projects.find(p => p.id === targetProjectId);
    const targetName = targetProject?.name || 'Unknown';
    const isMove = moveToProjectMenuState.mode === 'move';

    // Check if this is a folder (folder IDs exist in the folders array)
    const isFolder = folders.some(f => f.id === nodeId);
    if (isFolder) {
      if (isMove) {
        await moveFolderToProject(nodeId, targetProjectId);
        message.success(`Folder moved to project "${targetName}"`);
      } else {
        message.info('Folder copy is not yet supported');
      }
      setMoveToProjectMenuState({ anchorEl: null, nodeId: null, mode: 'move' });
      return;
    }

    if (isMove) {
      await moveConversationToProject(nodeId, targetProjectId);
      message.success(`Moved to project "${targetName}"`);
    } else {
      await copyConversationToProject(nodeId, targetProjectId);
      message.success(`Copied to project "${targetName}"`);
    }
    setMoveToProjectMenuState({ anchorEl: null, nodeId: null, mode: 'move' });
  };

  // Handle moving a conversation to a folder
  const handleMoveConversation = useCallback(async (conversationId: string, folderId: string | null) => {
    console.log('🔧 handleMoveConversation called:', { conversationId, folderId });

    // Get the original conversation ID for finding the conversation
    const originalConversationId = conversationId;

    // Strip conv- prefix if present
    let cleanConversationId = conversationId;
    if (conversationId.startsWith('conv-')) {
      cleanConversationId = conversationId.substring(5);
      console.log('🔧 Stripped conv- prefix, new ID:', cleanConversationId);
    }

    // Get the current folder ID before the move to compare
    const conversationBeforeMove = conversations.find(c => c.id === cleanConversationId);
    const currentFolderId = conversationBeforeMove?.folderId ?? null;
    console.log('🔧 Current folder ID before move:', currentFolderId, 'Target folder ID:', folderId);

    try {
      // Check if we're actually moving to a different folder
      if (currentFolderId === folderId) {
        console.log('🔧 No move needed - conversation is already in target folder:', folderId);
        return; // Exit early without showing success message
      }

      // Defensive check: ensure conversation is active before moving
      // This prevents corrupting conversations that somehow had isActive set to false
      const conversationToMove = conversations.find(c => c.id === cleanConversationId);
      if (conversationToMove && conversationToMove.isActive === false) {
        console.warn('🔧 DEFENSIVE: Conversation was marked inactive, restoring to active before move');
        conversationToMove.isActive = true;
        await db.saveConversation(conversationToMove);
      }

      console.log('🔧 Calling moveConversationToFolder with:', { conversationId: cleanConversationId, folderId });
      await moveConversationToFolder(cleanConversationId, folderId);

      // Force a re-render by checking if the state actually changed
      setTimeout(() => {
        const updatedConv = conversations.find(c => c.id === cleanConversationId);
        console.log('📊 Conversation state after move:', {
          id: cleanConversationId,
          folderId: updatedConv?.folderId,
          expectedFolderId: folderId,
          moveWorked: updatedConv?.folderId === folderId
        });

        if (updatedConv?.folderId !== folderId) {
          console.error('❌ MOVE WAS OVERWRITTEN - conversation folder ID was reset!');
        }
      }, 100);

      // Show a more descriptive success message
      const targetFolderName = folderId
        ? folders.find(f => f.id === folderId)?.name || 'selected folder'
        : 'root';
      const sourceFolderName = currentFolderId
        ? folders.find(f => f.id === currentFolderId)?.name || 'previous folder'
        : 'root';
      message.success(`Conversation moved from ${sourceFolderName} to ${targetFolderName}`);
      console.log('✅ Move completed successfully');
    } catch (error) {
      console.error('❌ Move failed:', error);
      message.error('Failed to move conversation');
    }
  }, [conversations, moveConversationToFolder, folders]);

  // Handle moving a folder
  const handleMoveFolder = useCallback(async (folderId: string, targetParentId: string | null, insertionContext?: { type: string; targetNodeId?: string }) => {
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

      // Calculate the appropriate timestamp for ordering
      let orderingTimestamp = Date.now();

      if (insertionContext && (insertionContext.type === 'above' || insertionContext.type === 'below')) {
        // Find the target folder for insertion ordering
        const targetFolder = folders.find(f => f.id === insertionContext.targetNodeId);

        if (targetFolder) {
          // Get all sibling folders in the target parent
          const siblings = folders
            .filter(f => f.parentId === targetParentId && f.id !== folderId)
            .sort((a, b) => (a.updatedAt || a.createdAt || 0) - (b.updatedAt || b.createdAt || 0));

          const targetIndex = siblings.findIndex(f => f.id === targetFolder.id);

          if (targetIndex !== -1) {
            if (insertionContext.type === 'above') {
              // Insert before target - set timestamp slightly before target
              const targetTime = targetFolder.updatedAt || targetFolder.createdAt || Date.now();
              const previousFolder = targetIndex > 0 ? siblings[targetIndex - 1] : null;
              const previousTime = previousFolder ? (previousFolder.updatedAt || previousFolder.createdAt || 0) : 0;

              // Set timestamp between previous and target
              orderingTimestamp = previousTime + Math.floor((targetTime - previousTime) / 2);
              if (orderingTimestamp <= previousTime) orderingTimestamp = previousTime + 1;
            } else {
              // Insert after target - set timestamp slightly after target
              const targetTime = targetFolder.updatedAt || targetFolder.createdAt || Date.now();
              const nextFolder = targetIndex < siblings.length - 1 ? siblings[targetIndex + 1] : null;
              const nextTime = nextFolder ? (nextFolder.updatedAt || nextFolder.createdAt || Date.now() + 10000) : Date.now() + 10000;

              // Set timestamp between target and next
              orderingTimestamp = targetTime + Math.floor((nextTime - targetTime) / 2);
              if (orderingTimestamp <= targetTime) orderingTimestamp = targetTime + 1;
            }
          }
        }
      }

      // Update the folder's parent
      const updatedFolder = {
        ...folder,
        parentId: targetParentId,
        updatedAt: orderingTimestamp
      };

      await updateFolder(updatedFolder);
      message.success('Folder moved successfully');
    } catch (error) {
      message.error('Failed to move folder');
      console.error('Move folder error:', error);
    }
  }, [folders, updateFolder]);

  const startCustomDrag = useCallback((nodeId: string, nodeType: 'folder' | 'conversation', text: string) => {
    const ghostElement = createDragGhost(text);

    // Clean up any existing ghost elements first (defensive programming)
    const existingGhost = document.getElementById('mui-drag-ghost');
    if (existingGhost && existingGhost !== ghostElement) {
      console.log('🧹 DRAG_START: Cleaning up existing ghost element');
      existingGhost.remove();
    }

    setCustomDragState({
      isDragging: true,
      draggedNodeId: nodeId,
      draggedNodeType: nodeType,
      ghostElement,
      draggedText: text
    });

    // Suppress native text selection while dragging
    document.body.style.userSelect = 'none';
  }, [createDragGhost]);

  const endCustomDrag = useCallback(async (dropTargetId?: string | undefined, insertionContext?: { type: string; targetNodeId?: string }) => {
    if (!customDragState.isDragging || !customDragState.draggedNodeId) return;
    // Clear all visual feedback
    document.querySelectorAll<HTMLElement>('[data-node-id]').forEach((item) => {
      if (item instanceof HTMLElement) {
        item.style.backgroundColor = '';
        item.style.border = '';
        item.style.opacity = '1';
        item.style.boxShadow = '';
      }
    });

    // Remove insertion lines
    document.querySelectorAll('.drop-insertion-line').forEach(line => {
      line.remove();
    });

    if (dropTargetId && dropTargetId !== customDragState.draggedNodeId) {
      try {
        if (customDragState.draggedNodeType === 'conversation') {
          const targetFolderId = dropTargetId.startsWith('conv-') ? null : dropTargetId;
          console.log('🔍 FOLDER ID LOGIC:', {
            dropTargetId,
            startsWithConv: dropTargetId?.startsWith('conv-'),
            targetFolderId
          });
          console.log('📝 Moving conversation:', customDragState.draggedNodeId, 'to folder:', targetFolderId);
          await handleMoveConversation(customDragState.draggedNodeId, targetFolderId);
        } else if (customDragState.draggedNodeType === 'folder') {
          const targetParentId = dropTargetId.startsWith('conv-') ? null : dropTargetId;
          await handleMoveFolder(customDragState.draggedNodeId, targetParentId, insertionContext);
        }
      } catch (error) {
        console.error('Drop error:', error);
        message.error('Failed to move item');
      }
    }

    // Handle the case where dropTargetId is undefined (root level drop)
    if (dropTargetId === undefined) {
      if (customDragState.draggedNodeType === 'conversation') {
        await handleMoveConversation(customDragState.draggedNodeId, null);
      } else if (customDragState.draggedNodeType === 'folder') {
        await handleMoveFolder(customDragState.draggedNodeId, null, insertionContext);
      }
    }

    // Cleanup (always runs, regardless of success or failure)
    if (customDragState.ghostElement && customDragState.ghostElement.parentNode) {
      customDragState.ghostElement.remove();
    }

    // Final cleanup of any remaining visual artifacts
    document.querySelectorAll<HTMLElement>('[data-node-id]').forEach((item) => {
      if (item instanceof HTMLElement) {
        item.style.backgroundColor = '';
        item.style.border = '';
      }
    });

    // Final cleanup of insertion lines
    document.querySelectorAll('.drop-insertion-line').forEach(line => {
      line.remove();
    });

    // Restore native text selection
    document.body.style.userSelect = '';

    setCustomDragState({
      isDragging: false,
      draggedNodeId: null,
      draggedNodeType: null,
      ghostElement: null,
      draggedText: ''
    });
  }, [customDragState, handleMoveConversation, handleMoveFolder]);

  // Global mouse tracking for custom drag
  useEffect(() => {
    const handleMouseLeave = (e: MouseEvent) => {
      // Check if mouse left the chat history panel
      const chatHistoryContainer = chatHistoryRef.current;
      if (!chatHistoryContainer) return;

      const rect = chatHistoryContainer.getBoundingClientRect();
      const mouseX = e.clientX;
      const mouseY = e.clientY;

      // If mouse is outside the chat history panel bounds
      if (mouseX < rect.left || mouseX > rect.right ||
        mouseY < rect.top || mouseY > rect.bottom) {

        if (customDragState.isDragging) {
          console.log('🚫 Mouse left chat history panel - canceling drag operation');
          endCustomDrag(); // Cancel drag without applying any changes
        }
      }
    };

    const handleMouseMove = (e: MouseEvent) => {
      if (!customDragState.isDragging || !customDragState.ghostElement) return;

      // Only handle mouse events within the chat history area
      const chatHistoryContainer = chatHistoryRef.current;
      if (!chatHistoryContainer || !chatHistoryContainer.contains(e.target as Node)) {
        // Mouse is outside chat history - don't interfere
        return;
      }

      customDragState.ghostElement.style.left = (e.clientX + 10) + 'px';
      customDragState.ghostElement.style.top = (e.clientY + 10) + 'px';

      // Clear all highlighting first
      document.querySelectorAll<HTMLElement>('[data-node-id]').forEach((item) => {
        if (item instanceof HTMLElement) {
          item.style.backgroundColor = '';
          item.style.border = '';
          item.style.boxShadow = '';
        }
      });

      // Remove any existing insertion lines
      document.querySelectorAll('.drop-insertion-line').forEach(line => {
        line.remove();
      });

      // Check for root drop zone hover
      const rootDropZone = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-root-drop-zone]') as HTMLElement | null;
      if (rootDropZone) {
        rootDropZone.style.borderBottom = '2px solid #1890ff';
        rootDropZone.style.boxShadow = '0 2px 4px rgba(24, 144, 255, 0.3)';
        return; // Don't process tree item highlighting
      }

      // Clear root drop zone styling when not hovering over it
      document.querySelectorAll<HTMLElement>('[data-root-drop-zone]').forEach(el => {
        el.style.borderBottom = '';
        el.style.boxShadow = '';
      });

      // Enhanced drop zone detection with hierarchical insertion
      const elementBelow = document.elementFromPoint(e.clientX, e.clientY);
      const treeItemBelow = elementBelow?.closest('[data-node-id]');
      let targetNodeId: string | undefined = treeItemBelow?.getAttribute('data-node-id') || undefined;

      if (treeItemBelow && treeItemBelow instanceof HTMLElement) {
        const dropTargetText = treeItemBelow.textContent?.trim();

        // Folder detection: folder node IDs are raw UUIDs; conversation IDs start with "conv-"
        const isFolder = targetNodeId ? !targetNodeId.startsWith('conv-') : false;

        const treeContainer = treeItemBelow.closest('.chat-history-tree');
        const rect = treeItemBelow.getBoundingClientRect();
        const itemHeight = rect.height;
        const relativeY = e.clientY - rect.top;

        // Determine insertion position and target
        let insertionType: 'above' | 'below' | 'inside' = 'below';
        const targetLevel = 0;

        // When dragging a conversation over a folder, always show as "inside"
        // since the drop handler treats it as "move into folder" regardless.
        const draggingConversation = customDragState.draggedNodeType === 'conversation';

        if (isFolder && (draggingConversation || (relativeY > itemHeight * 0.3 && relativeY < itemHeight * 0.7))) {
          // Folder target: always "inside" for conversations,
          // center zone "inside" for folder-on-folder reordering
          insertionType = 'inside';
        } else if (relativeY < itemHeight * 0.5) {
          // Top half - insert ABOVE
          insertionType = 'above';
        } else {
          // Bottom half - insert BELOW
          insertionType = 'below';
        }

        // Create insertion line/highlight with proper visual feedback
        const insertionLine = document.createElement('div');
        insertionLine.className = 'drop-insertion-line';

        if (insertionType === 'inside') {
          // Inside folder - show as green highlight
          insertionLine.style.cssText = `
            position: absolute;
            left: ${20 + targetLevel * 15}px;
            right: 10px;
            height: ${itemHeight}px;
            background: rgba(82, 196, 26, 0.15);
            border: 2px dashed #52c41a;
            border-radius: 4px;
            z-index: 999;
            pointer-events: none;
            display: flex;
            align-items: center;
            padding-left: 8px;
            font-size: 12px;
            color: #52c41a;
            font-weight: bold;
          `;
          insertionLine.textContent = '📁 Drop inside folder';
        } else {
          // Above/below - show as insertion line with proper indentation
          insertionLine.style.cssText = `
            position: absolute;
            left: ${20 + targetLevel * 15}px;
            right: 10px;
            height: 2px;
            background: #1890ff;
            z-index: 1000;
            pointer-events: none;
            box-shadow: 0 0 4px rgba(24, 144, 255, 0.5);
          `;
        }

        const containerRect = treeContainer?.getBoundingClientRect();

        if (containerRect) {
          const lineY = insertionType === 'above' ?
            rect.top - containerRect.top - 2 :
            insertionType === 'inside' ?
              rect.top - containerRect.top :
              rect.bottom - containerRect.top + 2;

          insertionLine.style.top = lineY + 'px';

          // Add to the tree container
          if (treeContainer instanceof HTMLElement) {
            treeContainer.style.position = 'relative';
            treeContainer.appendChild(insertionLine);
          }
        }

        // Store the insertion context for the drop handler
        insertionLine.setAttribute('data-insertion-type', insertionType);
        insertionLine.setAttribute('data-target-node-id', targetNodeId || '');
        insertionLine.setAttribute('data-target-level', targetLevel.toString());
        insertionLine.setAttribute('data-target-node', dropTargetText || '');
      }
    };

    const handleMouseUp = (e: MouseEvent) => {
      if (customDragState.isDragging) {
        // Read insertion context BEFORE clearing visual feedback
        const insertionLine = document.querySelector('.drop-insertion-line');
        const insertionContext = insertionLine ? {
          type: insertionLine.getAttribute('data-insertion-type') || 'below',
          targetNodeId: insertionLine.getAttribute('data-target-node-id') || undefined,
          level: insertionLine.getAttribute('data-target-level'),
          target: insertionLine.getAttribute('data-target-node')
        } : null;

        console.log('📍 Insertion context captured:', insertionContext);

        // Clear all visual feedback immediately
        document.querySelectorAll<HTMLElement>('[data-node-id]').forEach((item) => {
          if (item instanceof HTMLElement) {
            item.style.backgroundColor = '';
            item.style.border = '';
            item.style.opacity = '1';
            item.style.boxShadow = '';
          }
        });

        // Remove all insertion lines (AFTER reading the context)
        document.querySelectorAll('.drop-insertion-line').forEach(line => {
          line.remove();
        });

        // Remove ghost element BEFORE elementFromPoint so it can't intercept
        if (customDragState.ghostElement) {
          customDragState.ghostElement.remove();
        }
        // Clear root drop zone styling
        document.querySelectorAll<HTMLElement>('[data-root-drop-zone]').forEach(el => {
          el.style.borderBottom = '';
          el.style.boxShadow = '';
        });

        const elementBelow = document.elementFromPoint(e.clientX, e.clientY);
        // Check for root drop zone first
        const rootDropZone = elementBelow?.closest('[data-root-drop-zone]');
        const dropTarget = rootDropZone ? null : elementBelow?.closest('[data-node-id]');

        let targetNodeId: string | undefined;
        let insertionType = insertionContext?.type || 'below'; // Use captured context

        if (dropTarget) {
          targetNodeId = dropTarget.getAttribute('data-node-id') || undefined;
          console.log('🎯 Found exact target nodeId from DOM:', targetNodeId);
        }

        // Root drop zone → move to root level
        if (rootDropZone) {
          targetNodeId = undefined;
          console.log('🎯 Dropped on root drop zone - moving to root level');
        }

        if (dropTarget) {
          console.log('📍 Using captured insertion context:', insertionType);
          console.log('🔍 Target nodeId from data attribute:', targetNodeId);

          // When dragging a CONVERSATION onto a FOLDER row, always treat as
          // "move into folder" regardless of insertion position.
          const targetIsFolder = targetNodeId && !targetNodeId.startsWith('conv-');
          const draggingConversation = customDragState.draggedNodeType === 'conversation';

          if (draggingConversation && targetIsFolder) {
            // Keep targetNodeId as the folder ID — drop INTO the folder
          } else if (insertionType !== 'inside') {
            // For other cases (folder reorder, conv-to-conv), resolve to parent level
            if (targetNodeId?.startsWith('conv-')) {
              const convId = targetNodeId.substring(5);
              const conv = conversations.find(c => c.id === convId);
              targetNodeId = conv?.folderId || undefined;
            } else {
              const folder = folders.find(f => f.id === targetNodeId);
              targetNodeId = folder?.parentId || undefined;
            }
          }
        }

        // Handle the case where no target was found - this should be a root level drop
        if (targetNodeId === undefined) {
          console.log('🎯 No specific target found - treating as root level drop');
        }

        console.log('🎯 Final target resolution:', { targetNodeId, draggedId: customDragState.draggedNodeId });
        console.log('🎯 Target folder details:', {
          targetFolder: targetNodeId ? folders.find(f => f.id === targetNodeId) : null,
          exactMatch: true, // Now using exact nodeId match instead of text matching
          insertionContext: insertionContext
        });
        endCustomDrag(targetNodeId ?? undefined, insertionContext || undefined);
      }
    };

    if (customDragState.isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);

      return () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [customDragState, endCustomDrag, folders, conversations, pinnedFolders]);

  // Escape key cancels active drag
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && customDragState.isDragging) {
        e.preventDefault();
        console.log('⎋ ESC: Cancelling drag operation');

        // Remove ghost element
        if (customDragState.ghostElement) {
          customDragState.ghostElement.remove();
        }
        // Clear all visual artifacts
        document.querySelectorAll('.drop-insertion-line').forEach(line => line.remove());
        document.querySelectorAll<HTMLElement>('[data-node-id]').forEach(item => {
          item.style.backgroundColor = '';
          item.style.border = '';
          item.style.boxShadow = '';
        });
        document.body.style.userSelect = '';
        setCustomDragState({
          isDragging: false, draggedNodeId: null, draggedNodeType: null,
          ghostElement: null, draggedText: ''
        });
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [customDragState.isDragging, customDragState.ghostElement]);

  // Cleanup on component unmount - critical for preventing ghost element leaks
  useEffect(() => {
    return () => {
      // Force cleanup of any remaining drag artifacts on unmount
      const ghostElement = document.getElementById('mui-drag-ghost');
      if (ghostElement) {
        console.log('🧹 UNMOUNT_CLEANUP: Removing orphaned ghost element');
        ghostElement.remove();
      }

      // Clean up any remaining insertion lines
      document.querySelectorAll('.drop-insertion-line').forEach(line => line.remove());
    };
  }, []); // Empty dependency array - only run on unmount

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



  // Handle adding a new chat to a folder
  const handleAddChat = async (folderId: string) => {
    try {
      console.log('Adding new chat to folder:', folderId);

      // Ensure the folder is expanded before creating the chat
      if (!expandedNodes.includes(folderId)) {
        setExpandedNodes(prev => [...prev, folderId]);
      }

      // Also expand any parent folders
      const expandParentFolders = (targetFolderId: string, _depth = 0) => {
        if (_depth > 20) return;
        const folder = folders.find(f => f.id === targetFolderId);
        if (folder?.parentId) {
          const parentId = folder.parentId;
          if (!expandedNodes.includes(parentId)) {
            setExpandedNodes(prev => [...prev, parentId]);
          }
          expandParentFolders(parentId, _depth + 1);
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
            isActive: conv.isActive !== false ? true : conv.isActive,  // Preserve active state
            // Keep original lastAccessedAt - renaming shouldn't affect sort order
            _version: Date.now() // Only update version for sync purposes
          } : conv
        );

        // Persist only the changed conversation to IndexedDB
        const changed = updatedConversations.find(c => c.id === conversationId);
        if (changed) await db.saveConversation(changed);

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
                folderId: conversations.find(c => c.id === conversationId)?.folderId,
                id: conversationId,
                currentActive: conversations.filter(c => c.isActive).length,
                isCurrentConversation: conversationId === currentConversationId
              });

              // Delete from server first (cross-port sync)
              const projectId = conversations.find(c => c.id === conversationId)?.projectId;
              // Track the folder this conversation belonged to so we can touch its timestamp
              const parentFolderId = conversations.find(c => c.id === conversationId)?.folderId;

              if (projectId) {
                const { deleteChat } = await import('../api/conversationSyncApi');
                await deleteChat(projectId, conversationId);
                console.log('📡 Server delete succeeded for', conversationId.substring(0, 8));
              }

              // Remove from IndexedDB entirely so no sync path re-pushes it
              await db.deleteConversation(conversationId);
              const updatedConversations = conversations.filter(
                (conv: any) => conv.id !== conversationId
              );
              // If the deleted conversation was active, switch to another existing
              // one rather than creating a new one. Do NOT call startNewChat here —
              // its stale conversations closure would write the deleted conversation
              // back to IDB before the deletion save can land.
              if (conversationId === currentConversationId) {
                const next = updatedConversations
                  .filter((c: any) => c.isActive !== false)
                  .sort((a: any, b: any) => (b.lastAccessedAt || 0) - (a.lastAccessedAt || 0));
                if (next.length > 0) {
                  await loadConversation(next[0].id);
                } else {
                  setCurrentConversationId('');
                }
              }

              message.success('Conversation deleted');

              // Update the parent folder's updatedAt so it sorts correctly
              if (parentFolderId) {
                const parentFolder = folders.find(f => f.id === parentFolderId);
                if (parentFolder) {
                  await updateFolder({
                    ...parentFolder,
                    updatedAt: Date.now(),
                  });
                }
              }
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
          // Collect this folder and all descendant folder IDs
          const allFolderIds = new Set<string>();
          const collectFolderIds = (folderId: string) => {
            allFolderIds.add(folderId);
            folders
              .filter(f => f.parentId === folderId)
              .forEach(child => collectFolderIds(child.id));
          };
          collectFolderIds(nodeId);

          // Remove all conversations belonging to these folders
          const updatedConversations = conversations.filter(
            c => !c.folderId || !allFolderIds.has(c.folderId)
          );
          const toDelete = conversations.filter(c => c.folderId && allFolderIds.has(c.folderId));
          await Promise.all(toDelete.map(c => db.deleteConversation(c.id)));
          setConversations(updatedConversations);

          await deleteFolder(nodeId);
          message.success('Folder deleted successfully');
        } catch (error) {
          message.error('Failed to delete folder');
        }
      }
    });
  };

  // Handle forking a conversation
  const handleForkConversation = (conversationId: string) => {
    if (conversationId.startsWith('conv-')) {
      conversationId = conversationId.substring(5);
    }

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
      hasUnreadResponse: false,
      isActive: true
    };

    // Update state synchronously — don't await anything on the click path.
    // During streaming, the DB write lock is held by debounced saves, and
    // awaiting it causes the async function to yield while streaming
    // re-renders thrash the Dropdown overlay, effectively swallowing the action.
    setConversations(prev => [...prev, forkedConversation]);

    // Navigate directly instead of loadConversation (which also awaits and
    // relies on conversationsRef that hasn't synced yet for the new entry).
    setCurrentConversationId(newId);

    message.success('Conversation forked successfully');

    // Persist to DB in the background — non-blocking
    db.saveConversation(forkedConversation).catch(err => {
      console.error('Failed to persist forked conversation:', err);
    });
  };

  // Handle compressing a conversation (placeholder)
  const handleCompressConversation = (conversationId: string) => {
    if (conversationId.startsWith('conv-')) {
      conversationId = conversationId.substring(5);
    }

    message.info('Conversation compression is not yet implemented');
  };

  // Handle exporting a conversation
  const handleExportConversation = (conversationId: string) => {
    if (conversationId.startsWith('conv-')) {
      conversationId = conversationId.substring(5);
    }

    console.log('Opening export modal for conversation:', conversationId);
    setExportConversationId(conversationId);
    setShowExportModal(true);
  };

  // Handle configuring a folder
  const handleConfigureFolder = (folderId: string) => showFolderConfigDialog(folderId);

  // Handle swarm recovery panel
  const handleSwarmRecovery = useCallback((folderId: string) => {
    setSwarmRecoveryFolderId(folderId);
    setShowSwarmRecovery(true);
  }, []);

  // Delegate-level recovery actions (retry / skip) directly from context menu
  const handleDelegateRetry = useCallback(async (nodeId: string) => {
    const convId = nodeId.startsWith('conv-') ? nodeId.substring(5) : nodeId;
    const conv = conversations.find(c => c.id === convId);
    if (!conv?.delegateMeta) return;

    const delegateId = conv.delegateMeta.delegate_id;
    const groupId = conv.folderId;
    const projectId = currentProject?.id;
    if (!delegateId || !groupId || !projectId) {
      message.error('Missing delegate or project information');
      return;
    }

    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
      if (projectPath) headers['X-Project-Root'] = projectPath;

      const res = await fetch(
        `/api/v1/projects/${projectId}/groups/${groupId}/retry-delegate`,
        { method: 'POST', headers, body: JSON.stringify({ delegate_id: delegateId }) }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `Retry failed: ${res.status}`);
      }
      message.success(`🔄 Retrying "${conv.title.replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]\s*/u, '')}"`);
    } catch (err: any) {
      message.error(`Retry failed: ${err.message}`);
    }
  }, [conversations, currentProject?.id]);

  const handleDelegateSkip = useCallback(async (nodeId: string) => {
    const convId = nodeId.startsWith('conv-') ? nodeId.substring(5) : nodeId;
    const conv = conversations.find(c => c.id === convId);
    if (!conv?.delegateMeta) return;

    const delegateId = conv.delegateMeta.delegate_id;
    const delegateName = conv.title.replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]\s*/u, '');
    const groupId = conv.folderId;
    const projectId = currentProject?.id;
    if (!delegateId || !groupId || !projectId) {
      message.error('Missing delegate or project information');
      return;
    }

    Modal.confirm({
      title: 'Skip delegate?',
      content: (
        <div>
          <p>Creates a stub crystal for <strong>{delegateName}</strong> so downstream delegates can proceed.</p>
          <p style={{ color: '#faad14' }}>The delegate's work will be marked as incomplete.</p>
        </div>
      ),
      okText: 'Skip & Unblock',
      onOk: async () => {
        try {
          const headers: Record<string, string> = { 'Content-Type': 'application/json' };
          const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
          if (projectPath) headers['X-Project-Root'] = projectPath;

          const res = await fetch(
            `/api/v1/projects/${projectId}/groups/${groupId}/promote-stub`,
            { method: 'POST', headers, body: JSON.stringify({ delegate_id: delegateId }) }
          );
          if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
            throw new Error(err.detail || `Skip failed: ${res.status}`);
          }
          message.success(`⏭️ Skipped "${delegateName}" — downstream unblocked`);
        } catch (err: any) {
          message.error(`Skip failed: ${err.message}`);
        }
      },
    });
  }, [conversations, currentProject?.id]);

  const swarmRecoveryFolder = swarmRecoveryFolderId ? folders.find(f => f.id === swarmRecoveryFolderId) : null;

  // Handle creating a subfolder
  const handleCreateSubfolder = async (parentFolderId: string) => {
    try {
      // Create a new subfolder with default name and settings
      const createdFolderId = await createFolder('New Folder', parentFolderId);
      const newFolderId = String(createdFolderId);

      // Tell the scroll effect to focus on the new folder instead of the active conversation
      scrollToNodeIdRef.current = newFolderId;

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
    } finally {
      setIsLoading(false);
    }
  }, [currentConversationId, loadConversation]);

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
          message.destroy(); // Clear any existing messages
          const content = e.target?.result as string;

          let parsedContent: any;
          try {
            parsedContent = JSON.parse(content);
          } catch (parseErr) {
            message.error('Invalid JSON file');
            return;
          }

          // Extract conversations list from either format
          const importedConversations: any[] = Array.isArray(parsedContent)
            ? parsedContent
            : (parsedContent?.conversations || []);
          const importedFolders: any[] = Array.isArray(parsedContent)
            ? []
            : (parsedContent?.folders || []);

          if (importedConversations.length === 0) {
            message.warning('No conversations found in file');
            return;
          }

          // Pre-flight: check how many are actually new (not already in DB)
          const existingConversations = await db.getConversations();
          const existingIds = new Set(existingConversations.map(c => c.id));
          const newCount = importedConversations.filter(c => c.id && !existingIds.has(c.id)).length;

          if (newCount === 0) {
            message.info('All conversations in this file already exist — nothing to import');
            return;
          }

          // Create the import root folder (named after the file).
          // Only created after we've confirmed there's something to import.
          const folderName = file.name.replace(/\.json$/i, '');
          let importFolderId: string;
          try {
            importFolderId = await createFolder(folderName, null);
          } catch (folderErr) {
            console.error('Failed to create import folder:', folderErr);
            message.error('Failed to create import folder — import aborted');
            return;
          }

          message.loading(`Importing ${newCount} conversation(s)...`, 0);

          try {
            await db.importConversations(content, importFolderId);

            const newConversations = await db.getConversations();
            const newFolders = await db.getFolders();
            setConversations(newConversations);
            setFolders(newFolders);

            // Expand the new import folder so the user sees the result
            setExpandedNodes(prev => prev.includes(importFolderId) ? prev : [...prev, importFolderId]);

            message.destroy();
            let successMsg = `Imported ${newCount} conversation(s) into "${folderName}"`;
            if (importedFolders.length > 0) {
              successMsg += ` with ${importedFolders.length} folder(s) preserved`;
            }
            message.success(successMsg);
          } catch (importErr) {
            console.error('Import failed:', importErr);
            message.destroy();
            message.error('Import failed — see console for details');
            // Clean up the empty import folder on failure
            try { await deleteFolder(importFolderId); } catch (_) { /* best effort */ }
          }
        };
        reader.readAsText(file);
      } catch (error) {
        message.destroy();
        message.error('Failed to read import file');
      }
    };
    input.click();
  };
  // Build tree data from folders and conversations
  // Stability refs to prevent unnecessary rebuilds
  const lastTreeDataInputsRef = useRef<number>(0);
  const lastSortHashRef = useRef<number>(0);
  const lastTreeDataRef = useRef<any[]>([]);
  // Refs for sort-only fast path: reuse structure, only re-sort
  const lastAnchoredIdsRef = useRef<Set<string>>(new Set());
  const lastFolderMapRef = useRef<Map<string, any>>(new Map());
  const lastTaskPlanBoostRef = useRef<Map<string, number>>(new Map());

  const treeDataRaw = useMemo(() => {
    // PRE-RENDER CHECKPOINT: Write to localStorage BEFORE the expensive
    // tree build so we can diagnose stack overflows that kill the JS engine
    // before any error handler can fire.
    try {
      localStorage.setItem('ZIYA_TREE_BUILD_CHECKPOINT', JSON.stringify({
        timestamp: new Date().toISOString(),
        conversationCount: conversations.length,
        folderCount: folders.length,
        phase: 'start',
      }));
    } catch {}

    // PRE-RENDER CHECKPOINT: Write to localStorage BEFORE the expensive
    // tree build so we can diagnose stack overflows that kill the JS engine
    // before any error handler can fire.
    try {
      localStorage.setItem('ZIYA_TREE_BUILD_CHECKPOINT', JSON.stringify({
        timestamp: new Date().toISOString(),
        conversationCount: conversations.length,
        folderCount: folders.length,
        phase: 'start',
      }));
    } catch {}

    // During project switch, conversation/folder arrays are transitional
    // (mix of old + new project data). Return cached tree to avoid
    // rendering a broken hierarchy that flashes nonsensical state.
    if (isProjectSwitching) return lastTreeDataRef.current;

    // Filter null/undefined entries that can slip in from corrupt IDB data
    // or cross-tab sync races.  A single null entry in the forEach causes
    // a TypeError that kills the React tree (useMemo runs during render,
    // errors propagate above all error boundaries).
    const safeFolders = folders.filter(Boolean);
    const safeConversations = conversations.filter(Boolean);

    // Two-level hash: structural (folder/conversation identity) vs sort
    // (activity times that only affect display order).  When only sort
    // fields change we skip the expensive tree assembly and only re-sort.
    const fnv1a = () => {
      let h = 0x811c9dc5;
      return {
        add(s: string) {
          for (let i = 0; i < s.length; i++) {
            h ^= s.charCodeAt(i);
            h = Math.imul(h, 0x01000193);
          }
        },
        value() { return h >>> 0; }
      };
    };

    // Structural hash: identity, titles, folder placement, delegate status
    const sh = fnv1a();
    safeFolders.forEach(f => { sh.add(f.id || ''); sh.add(f.name || ''); sh.add(f.parentId || ''); sh.add(f.isGlobal ? 'g' : ''); sh.add(f.taskPlan?.source_conversation_id || ''); });
    safeConversations.forEach(c => { sh.add(c.id || ''); sh.add(c.title || ''); sh.add(c.folderId || ''); sh.add(c.isActive === false ? '0' : '1'); sh.add(c.isGlobal ? 'g' : ''); sh.add(c.delegateMeta?.status || ''); });
    const structuralHash = sh.value();

    // Sort hash: activity times and pin state that only affect ordering
    const oh = fnv1a();
    safeConversations.forEach(c => { oh.add(String(c.lastAccessedAt || 0)); });
    pinnedFolders.forEach(id => oh.add(id));
    const sortHash = oh.value();

    // Fast exit: nothing changed at all
    if (structuralHash === lastTreeDataInputsRef.current
      && sortHash === lastSortHashRef.current
      && lastTreeDataRef.current.length > 0) {
      return lastTreeDataRef.current;
    }

    // ── Sort-only fast path ─────────────────────────────────────────
    // Structure unchanged but activity times shifted → reuse tree
    // structure, just update activity times and re-sort in place.
    if (structuralHash === lastTreeDataInputsRef.current
      && lastTreeDataRef.current.length > 0) {
      try {
        localStorage.setItem('ZIYA_TREE_BUILD_CHECKPOINT', JSON.stringify({
          timestamp: new Date().toISOString(), phase: 'sort-only-start',
          conversationCount: conversations.length, folderCount: folders.length,
        }));
      } catch {}
      try {
        localStorage.setItem('ZIYA_TREE_BUILD_CHECKPOINT', JSON.stringify({
          timestamp: new Date().toISOString(), phase: 'sort-only-start',
          conversationCount: conversations.length, folderCount: folders.length,
        }));
      } catch {}
      const convMap = new Map(conversations.map(c => [c.id, c]));
      const anchoredFolderIds = lastAnchoredIdsRef.current;
      const folderMap = lastFolderMapRef.current;

      // Shallow-copy each node so we never mutate the previous render's
      // tree.  Mutating in place violates React's immutability contract
      // and causes inconsistent renders during concurrent updates
      // (e.g. streaming + SERVER_SYNC firing in the same frame).
      const cloneNode = (node: any, _depth = 0): any => {
        if (_depth > 30) return { ...node, children: [], conversationCount: 0 };

        // Check if the conversation reference actually changed
        let conversationChanged = false;
        if (node.conversation) {
          const fresh = convMap.get(node.conversation.id);
          if (fresh && fresh !== node.conversation) {
            conversationChanged = true;
          }
        }

        // Recurse into children first to see if any changed
        let newChildren = node.children;
        if (node.children) {
          newChildren = node.children.map((c: any) => cloneNode(c, _depth + 1));
        }
        const childrenChanged = newChildren !== node.children &&
          newChildren.some((c: any, i: number) => c !== node.children[i]);

        // Recompute folder activity time if this is a folder with children
        let newActivityTime = node.lastActivityTime;
        let activityChanged = false;
        if (node.folder && newChildren) {
          let maxTime = 0;
          for (const child of newChildren) {
            const t = child.conversation?.lastAccessedAt || child.lastActivityTime || 0;
            if (t > maxTime) maxTime = t;
          }
          if (maxTime !== node.lastActivityTime) {
            newActivityTime = maxTime;
            activityChanged = true;
          }
        }

        const pinChanged = node.folder && (pinnedFolders.has(node.id) !== node.isPinned);

        // If nothing changed, reuse the original node reference
        if (!conversationChanged && !childrenChanged && !activityChanged && !pinChanged) {
          return node;
        }

        // Something changed — create a shallow copy with only the changed fields
        const copy = { ...node };
        if (conversationChanged) {
          copy.conversation = convMap.get(node.conversation.id);
        }
        if (childrenChanged) {
          copy.children = newChildren;
        }
        if (activityChanged) {
          copy.lastActivityTime = newActivityTime;
        }
        if (pinChanged) {
          copy.isPinned = pinnedFolders.has(node.id);
        }
        return copy;
      };
      const tree = lastTreeDataRef.current.map(cloneNode);
      try {
        localStorage.setItem('ZIYA_TREE_BUILD_CHECKPOINT', JSON.stringify({
          timestamp: new Date().toISOString(), phase: 'sort-only-cloned',
          treeLength: tree.length,
        }));
      } catch {}

      // Rebuild taskPlanBoost from refreshed data
      const taskPlanBoost = new Map<string, number>();
      for (const fid of anchoredFolderIds) {
        const origFn = folderMap.get(fid);
        const srcId = origFn?.taskPlan?.source_conversation_id;
        if (!srcId) continue;
        // Find the cloned version in the new tree for accurate activity time
        const findNode = (items: any[], _depth = 0): any => {
          if (_depth > 30) return null;
          for (const n of items) {
            if (n.id === fid) return n;
            if (n.children) { const f = findNode(n.children, _depth + 1); if (f) return f; }
          }
          return null;
        };
        const clonedFn = findNode(tree);
        const activity = clonedFn?.lastActivityTime || origFn?.lastActivityTime || 0;
        if (activity > (taskPlanBoost.get(srcId) || 0)) {
          taskPlanBoost.set(srcId, activity);
        }
      }
      lastTaskPlanBoostRef.current = taskPlanBoost;

      // Sort the cloned arrays (safe — we own these copies)
      const resortRecursive = (nodes: any[], _d = 0): any[] => {
        if (_d > 20) return nodes;
        nodes.sort((a, b) => sortComparator(a, b, taskPlanBoost));
        nodes.forEach(n => { if (n.children?.length) n.children = resortRecursive(n.children, _d + 1); });
        return nodes;
      };
      resortRecursive(tree);

      // Re-anchor TaskPlan folders after sort
      if (anchoredFolderIds.size > 0) {
        const reanchor = (items: any[], _d = 0) => {
          if (_d > 20) return;
          for (const fid of anchoredFolderIds) {
            const origFn = folderMap.get(fid);
            const srcId = origFn?.taskPlan?.source_conversation_id;
            if (!srcId) continue;
            const srcIdx = items.findIndex(n => n.id === `conv-${srcId}`);
            const curIdx = items.findIndex(n => n.id === fid);
            if (srcIdx !== -1 && curIdx !== -1 && curIdx !== srcIdx + 1) {
              const [moved] = items.splice(curIdx, 1);
              const ns = items.findIndex(n => n.id === `conv-${srcId}`);
              items.splice(ns + 1, 0, moved);
            }
          }
          for (const item of items) { if (item.children?.length) reanchor(item.children, _d + 1); }
        };
        reanchor(tree);
      }

      lastSortHashRef.current = sortHash;
      lastTreeDataRef.current = tree;
      return tree;
    }

    // ── Full rebuild ────────────────────────────────────────────────
    const folderMap = new Map();
    safeFolders.forEach(folder => {
      folderMap.set(folder.id, {
        id: folder.id,
        name: folder.name,
        children: [], // Initialize for sub-folders and conversations
        folder: folder, // Keep the original folder object
        conversationCount: 0,
        taskPlan: folder.taskPlan || null,
        isPinned: pinnedFolders.has(folder.id),
        lastActivityTime: 0, // Will be calculated from conversations
        createdAt: folder.createdAt || 0 // Use creation time as fallback
      });
    });

    // Add conversations to their respective folders in the map
    const activeConversations = safeConversations.filter(conv => conv.isActive !== false);

    // Map conv.id → tree node, for reparenting TaskPlan folders under source conversation
    const convNodeMap = new Map<string, any>();

    // Debug: Log if current conversation is missing from active list
    if (currentConversationId && !activeConversations.find(c => c.id === currentConversationId)) {
      console.error('🚨 HISTORY_CORRUPTION: Current conversation missing from active list:', currentConversationId);
      console.error('🚨 Current conversation state:', safeConversations.find(c => c.id === currentConversationId));
    }

    activeConversations.forEach(conv => {
      if (conv.folderId && folderMap.has(conv.folderId)) {
        const folderNode = folderMap.get(conv.folderId);
        const convNode = {
          id: `conv-${conv.id}`,
          name: conv.title || 'Untitled',
          conversation: conv,
          delegateMeta: conv.delegateMeta || null,
          children: [] as any[],  // May hold TaskPlan folders spawned from this conversation
        };
        folderNode.children.push(convNode);
        convNodeMap.set(conv.id, convNode);
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
    safeFolders.forEach(folder => {
      const node = folderMap.get(folder.id); // Get the node (which now includes conversation children)
      // Detect ancestor cycles: walk up the parentId chain and ensure
      // we never revisit a folder.  Mutual cycles like A→B→A would
      // cause circular children references that crash cloneNode.
      let hasCycle = false;
      if (folder.parentId && folder.parentId !== folder.id) {
        const visited = new Set<string>([folder.id]);
        let cur: string | null | undefined = folder.parentId;
        while (cur) {
          if (visited.has(cur)) { hasCycle = true; break; }
          visited.add(cur);
          const ancestor = folders.find(f => f.id === cur);
          cur = ancestor?.parentId;
        }
        if (hasCycle) {
          console.error(`🔄 CYCLE: Folder "${folder.name}" (${folder.id}) → parent ${folder.parentId} creates a cycle. Placing at root.`);
        }
      }
      if (!hasCycle && folder.parentId && folder.parentId !== folder.id && folderMap.has(folder.parentId)) {
        const parentNode = folderMap.get(folder.parentId);
        // Ensure parentNode.children is initialized
        if (!parentNode.children) parentNode.children = [];
        parentNode.children.push(node); // Add this folder (with its conv children) to its parent
      } else {
        rootItems.push(node);
      }
    });

    // Add conversations that are not in any folder (or whose folder is missing from
    // the current view, e.g. a globally-shared conv whose folder isn't shared) to root
    activeConversations.forEach(conv => {
      if (!conv.folderId || !folderMap.has(conv.folderId)) {
        // Delegate conversations belong in their swarm folder. If the folder
        // hasn't synced yet, hide them rather than showing orphaned "New
        // Conversation" entries at root that vanish when clicked.
        if (conv.delegateMeta && conv.folderId) return;

        const convNode = {
          id: `conv-${conv.id}`,
          name: conv.title || 'Untitled',
          conversation: conv,
          delegateMeta: conv.delegateMeta || null,
          children: [] as any[],
        };
        rootItems.push(convNode);
        convNodeMap.set(conv.id, convNode);
      }
    });

    // Anchor TaskPlan folders as siblings immediately after their source
    // conversation.  MUI TreeView can't expand conversation nodes, so we
    // place swarm folders adjacent to their parent chat instead.
    const anchoredFolderIds = new Set<string>();
    // Remove a node from anywhere in the tree (cross-level).
    const removeNodeFromTree = (tree: any[], node: any, _depth = 0): boolean => {
      if (_depth > 20) return false;
      const idx = tree.indexOf(node);
      if (idx !== -1) { tree.splice(idx, 1); return true; }
      for (const item of tree) {
        if (item.children && removeNodeFromTree(item.children, node, _depth + 1)) return true;
      }
      return false;
    };
    const anchorFolder = (items: any[], folder: any, sourceConvId: string, _depth = 0): boolean => {
      if (_depth > 20) return false;
      const srcIdx = items.findIndex(n => n.id === `conv-${sourceConvId}`);
      if (srcIdx !== -1) {
        // Remove folder from wherever it currently sits in the whole tree
        removeNodeFromTree(rootItems, folder);
        // Re-find source index after possible splice shift
        const newSrcIdx = items.findIndex(n => n.id === `conv-${sourceConvId}`);
        items.splice(newSrcIdx + 1, 0, folder);
        return true;
      }
      for (const item of items) {
        // Skip recursing into folder's own subtree — inserting folder there creates a cycle
        if (item !== folder && item.children && anchorFolder(item.children, folder, sourceConvId, _depth + 1)) return true;
      }
      return false;
    };
    safeFolders.forEach(folder => {
      const sourceConvId = folder.taskPlan?.source_conversation_id;
      if (!sourceConvId) return;
      const folderNode = folderMap.get(folder.id);
      if (!folderNode) return;
      if (anchorFolder(rootItems, folderNode, sourceConvId)) {
        anchoredFolderIds.add(folder.id);
      }
    });
    // Deduplicate: anchored folders may appear twice (once from folder tree
    // building, once from anchoring).  Keep only the anchored position.
    if (anchoredFolderIds.size > 0) {
      const removeDupes = (items: any[], seen: Set<string>, _depth = 0) => {
        if (_depth > 30) return;
        for (let i = items.length - 1; i >= 0; i--) {
          if (!items[i]) continue;
          // Process children first so the deeper (anchored) copy is found first
          if (items[i].children) removeDupes(items[i].children, seen, _depth + 1);
          if (items[i].folder && anchoredFolderIds.has(items[i].id)) {
            if (seen.has(items[i].id)) {
              items.splice(i, 1);
            } else {
              seen.add(items[i].id);
            }
          }
        }
      };
      removeDupes(rootItems, new Set<string>());
    }

    // Build a map of source-conversation → max activity time across all
    // anchored TaskPlan folders.  This lets the parent conversation sort
    // by the newest change in *either* itself or its TaskPlan members.
    const taskPlanBoost = new Map<string, number>();
    for (const fid of anchoredFolderIds) {
      const fn = folderMap.get(fid);
      const srcId = fn?.taskPlan?.source_conversation_id;
      if (!srcId) continue;
      const activity = fn.lastActivityTime || 0;
      if (activity > (taskPlanBoost.get(srcId) || 0)) {
        taskPlanBoost.set(srcId, activity);
      }
    }

    // Roll up conversation counts from subfolders into parent folders.
    // After the tree is assembled, each folder's conversationCount only
    // reflects its direct conversation children.  Walk bottom-up so that
    // nested subfolder counts propagate all the way to the root.
    const rollUpConversationCount = (node: any, _depth = 0): number => {
      if (_depth > 20) return 0;
      if (!node.folder) return 0; // leaf conversation node
      let total = node.conversationCount || 0; // direct conversations
      if (node.children) {
        for (const child of node.children) {
          if (child.folder) {
            total += rollUpConversationCount(child, _depth + 1);
          }
        }
      }
      node.conversationCount = total;
      return total;
    };

    // Roll up lastActivityTime from subfolders into parent folders.
    // After the tree is assembled, each folder's lastActivityTime only
    // reflects its direct conversation children.  Walk bottom-up so that
    // nested subfolder activity propagates all the way to the root.
    // Without this, a folder 2+ levels above the active conversation
    // won't sort to the top because its lastActivityTime is stale.
    const rollUpLastActivityTime = (node: any, _depth = 0): number => {
      if (_depth > 20) return 0;
      if (!node.folder) return 0;
      let maxTime = node.lastActivityTime || 0;
      if (node.children) {
        for (const child of node.children) {
          if (child.folder) {
            const childTime = rollUpLastActivityTime(child, _depth + 1);
            if (childTime > maxTime) maxTime = childTime;
          } else if (child.conversation) {
            const convTime = child.conversation.lastAccessedAt || 0;
            if (convTime > maxTime) maxTime = convTime;
          }
        }
      }
      node.lastActivityTime = maxTime;
      return maxTime;
    };

    // Apply roll-up before sorting
    rootItems.forEach(item => { if (item.folder) { rollUpConversationCount(item); rollUpLastActivityTime(item); } });

    // Sort using extracted comparator shared with the fast path
    const sortRecursive = (nodes: any[], _depth = 0): any[] => {
      if (_depth > 20) return nodes;
      const sorted = nodes.sort((a, b) => sortComparator(a, b, taskPlanBoost));
      sorted.forEach(node => {
        if (node.children && node.children.length > 0) {
          node.children = sortRecursive(node.children, _depth + 1);
        }
      });
      return sorted;
    };

    let result = sortRecursive(rootItems);

    // Re-anchor TaskPlan folders that sorting separated from their source
    reanchorTaskPlanFolders(result, anchoredFolderIds, folderMap);

    // Save refs for sort-only fast path on next render.
    // Without storing the hashes, the fast-exit and sort-only paths
    // never activate — every render triggers a full O(N·M) rebuild.
    lastAnchoredIdsRef.current = anchoredFolderIds;
    lastFolderMapRef.current = folderMap;
    lastTreeDataInputsRef.current = structuralHash;
    lastSortHashRef.current = sortHash;

    lastTreeDataRef.current = result;

    // POST-RENDER CHECKPOINT: If we get here, the tree built successfully
    try {
      localStorage.setItem('ZIYA_TREE_BUILD_CHECKPOINT', JSON.stringify({
        timestamp: new Date().toISOString(),
        conversationCount: conversations.length,
        folderCount: folders.length,
        phase: 'complete',
        nodeCount: result.length,
      }));
    } catch {}

    return result;
  }, [conversations, folders, pinnedFolders, isProjectSwitching]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounce treeData updates: during startup, conversations change 4+ times
  // in rapid succession. Only rebuild the flattened tree once things settle.
  const [treeData, setTreeData] = useState<any[]>([]);
  const treeDataTimerRef = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => {
    if (treeDataTimerRef.current) clearTimeout(treeDataTimerRef.current);
    // If tree is empty OR the raw reference is identical (useMemo cache hit),
    // update immediately. Otherwise debounce to coalesce rapid changes.
    if (treeData.length === 0 || treeDataRaw === treeData) {
      setTreeData(treeDataRaw);
    } else {
      treeDataTimerRef.current = setTimeout(() => setTreeData(treeDataRaw), 150);
    }
  }, [treeDataRaw]);

  // Virtualization: flatten visible tree nodes
  const expandedSet = useMemo(() => new Set(expandedNodes.map(String)), [expandedNodes]);
  const flatNodes = useMemo(() => flattenVisibleNodes(treeData, expandedSet), [treeData, expandedSet]);

  // Priority scroll: when scrollToNodeIdRef is set (e.g. after folder creation),
  // scroll to that node instead of the current conversation.
  useEffect(() => {
    const targetId = scrollToNodeIdRef.current;
    if (!targetId || !virtualListRef.current) return;
    const rowIndex = flatNodes.findIndex(n => n.id === targetId);
    if (rowIndex !== -1) {
      virtualListRef.current.scrollToItem(rowIndex, 'smart');
      scrollToNodeIdRef.current = null; // consume — one-shot
    }
    // If the node isn't visible yet (expand hasn't propagated), keep the ref
    // so the next flatNodes change can pick it up.
  }, [flatNodes]);

  useEffect(() => {
    if (!currentConversationId || !virtualListRef.current || scrollToNodeIdRef.current) return;
    const targetNodeId = `conv-${currentConversationId}`;
    const rowIndex = flatNodes.findIndex(n => n.id === targetNodeId);
    console.log('🔍 SCROLL_EFFECT:', { hasRef: !!virtualListRef.current, targetNodeId, rowIndex, flatNodesLen: flatNodes.length });
    if (rowIndex === -1) return;
    virtualListRef.current.scrollToItem(rowIndex, 'smart');
  }, [currentConversationId, flatNodes]);

  useEffect(() => {
    if (!currentConversationId || !virtualListRef.current || scrollToNodeIdRef.current) return;
    const targetNodeId = `conv-${currentConversationId}`;
    const timer = setTimeout(() => {
      if (!virtualListRef.current) return;
      const rowIndex = flatNodes.findIndex(n => n.id === targetNodeId);
      console.log('🔍 SCROLL_TIMEOUT:', { hasRef: !!virtualListRef.current, targetNodeId, rowIndex, flatNodesLen: flatNodes.length });
      if (rowIndex !== -1) virtualListRef.current.scrollToItem(rowIndex, 'smart');
    }, 100);
    return () => clearTimeout(timer);
  }, [currentConversationId]);

  // After search clears and tree becomes visible again, scroll to active conversation
  useEffect(() => {
    if (searchQuery || !currentConversationId || !virtualListRef.current) return;
    const timer = setTimeout(() => {
      if (!virtualListRef.current) return;
      const targetNodeId = `conv-${currentConversationId}`;
      const rowIndex = flatNodes.findIndex(n => n.id === targetNodeId);
      if (rowIndex !== -1) virtualListRef.current.scrollToItem(rowIndex, 'smart');
    }, 150);
    return () => clearTimeout(timer);
  }, [searchQuery, currentConversationId, flatNodes]);

  // Precompute indent guide continuation flags for each visible row.
  // guides[i][d] === true means "a sibling at depth d exists below row i",
  // so a vertical guide line should be drawn at that indent level.
  const indentGuides = useMemo(() => {
    const n = flatNodes.length;
    if (n === 0) return [] as boolean[][];
    const maxDepth = flatNodes.reduce((m, f) => Math.max(m, f.depth), 0);
    const hasNext: boolean[] = new Array(maxDepth + 1).fill(false);
    const guides: boolean[][] = new Array(n);
    for (let i = n - 1; i >= 0; i--) {
      const depth = flatNodes[i].depth;
      const flags: boolean[] = [];
      for (let d = 0; d < depth; d++) flags.push(hasNext[d]);
      guides[i] = flags;
      hasNext[depth] = true;
      for (let d = depth + 1; d <= maxDepth; d++) hasNext[d] = false;
    }
    return guides;
  }, [flatNodes]);

  // Measure available height for the virtual list
  const treeContainerRef = useRef<HTMLDivElement>(null);
  const [treeContainerHeight, setTreeContainerHeight] = useState(600);
  useEffect(() => {
    const el = treeContainerRef.current;
    if (!el) return;
    // Set initial height immediately
    setTreeContainerHeight(el.clientHeight || 600);
    const ro = new ResizeObserver(([entry]) => {
      const h = entry.contentRect.height;
      if (h > 0) setTreeContainerHeight(h);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

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

            scrollToNodeIdRef.current = String(newFolderId);

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

  // renderVirtualRow is defined inline in the FixedSizeList below

  const highlightSnippet = useCallback((text: string, query: string) => {
    if (!query || !text) return <>{text}</>;
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const parts = text.split(new RegExp(`(${escaped})`, 'gi'));
    return (
      <>
        {parts.map((part, i) =>
          part.toLowerCase() === query.toLowerCase()
            ? <mark key={i} style={{
                backgroundColor: isDarkMode ? '#b8860b' : '#fff176',
                color: isDarkMode ? '#fff' : '#000',
                borderRadius: '2px',
                padding: '0 1px'
              }}>{part}</mark>
            : part
        )}
      </>
    );
  }, [isDarkMode]);

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
    <>
      <Box ref={chatHistoryRef} sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        {/* Tree View with integrated action buttons */}

        {/* Search Input */}
        <Box sx={{ p: 2, borderBottom: isDarkMode ? '1px solid #303030' : '1px solid #e8e8e8', display: 'flex', gap: 1, alignItems: 'center' }}>
          <TextField
            fullWidth
            size="small"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <SearchIcon sx={{ mr: 1, color: isDarkMode ? '#888' : '#999' }} />
                ),
                endAdornment: searchQuery && (
                  <IconButton
                    size="small"
                    onClick={() => {
                      setSearchQuery('');
                      setSearchResults([]);
                    }}
                  >
                    <CloseIcon fontSize="small" />
                  </IconButton>
                )
              }
            }}
            sx={{ flex: 1, minWidth: 0 }}
          />
          {/* Search scope toggle — only visible when search is active */}
          {searchQuery && (
            <Tooltip title={searchAllProjects ? 'Searching all projects' : 'Searching this project only'}>
              <IconButton
                size="small"
                onClick={() => setSearchAllProjects(prev => !prev)}
                sx={{
                  color: searchAllProjects ? '#1890ff' : (isDarkMode ? '#888' : '#999'),
                  border: searchAllProjects ? '1px solid #1890ff' : `1px solid ${isDarkMode ? '#555' : '#ccc'}`,
                  width: 32,
                  height: 32,
                  flexShrink: 0
                }}
              >
                <PublicIcon sx={{ fontSize: 18 }} />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="New folder">
            <IconButton
              size="small"
              onClick={async () => {
                const newId = await createFolder('New Folder', currentFolderId);
                // Focus the virtual list on the new folder
                scrollToNodeIdRef.current = String(newId);
                message.success('Folder created');
              }}
              sx={{
                color: '#1890ff',
                border: '1px solid #1890ff',
                width: 32,
                height: 32,
                flexShrink: 0
              }}
            >
              <CreateNewFolderIcon sx={{ fontSize: 18 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title="New chat">
            <IconButton
              size="small"
              onClick={() => startNewChat(currentFolderId)}
              sx={{
                color: '#1890ff',
                border: '1px solid #1890ff',
                width: 32,
                height: 32,
                flexShrink: 0
              }}
            >
              <AddCommentIcon sx={{ fontSize: 18 }} />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Search Results or Tree View */}
        {searchQuery && searchResults.length > 0 ? (
          <Box sx={{ flexGrow: 1, overflow: 'auto', pt: 1 }}>
            <Box sx={{ p: 2, borderBottom: isDarkMode ? '1px solid #303030' : '1px solid #e8e8e8' }}>
              <Typography variant="caption" sx={{ color: isDarkMode ? '#888' : '#666' }}>
                Found {searchResults.reduce((acc, r) => acc + r.totalMatches, 0)} matches in {searchResults.length} conversation{searchResults.length !== 1 ? 's' : ''}
                {searchAllProjects
                  ? ' across all projects'
                  : ` in ${currentProject?.name || 'this project'}`}
              </Typography>
            </Box>
            {searchResults.map((result) => (
              <Box
                key={result.conversationId}
                onClick={async () => {
                  try {
                    const firstMatchIndex = result.matches.length > 0 ? result.matches[0].messageIndex : undefined;
                    // If the conversation belongs to a different project, switch first
                    if (result.projectId && result.projectId !== currentProject?.id) {
                      await switchProject(result.projectId);
                      // Small delay to let project switch settle before loading conversation
                      await new Promise(resolve => setTimeout(resolve, 300));
                    }
                    (window as any).__ziyaSearchHighlight = searchQuery;
                    await loadConversationAndScrollToMessage(
                      result.conversationId, firstMatchIndex);
                    setSearchQuery('');
                    setSearchResults([]);
                  } catch (error) {
                    console.error('Error navigating to conversation:', error);
                    message.error('Failed to load conversation');
                  }
                }}
                sx={{
                  p: 2,
                  borderBottom: isDarkMode ? '1px solid #303030' : '1px solid #e8e8e8',
                  cursor: 'pointer',
                  '&:hover': {
                    backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)'
                  }
                }}
              >
                <Typography
                  variant="body2"
                  sx={{ fontWeight: 'bold', mb: 1, display: 'flex', alignItems: 'center', gap: 1 }}
                >
                  <ChatIcon fontSize="small" />
                  {result.conversationTitle}
                  <Typography variant="caption" sx={{ color: isDarkMode ? '#888' : '#666', ml: 'auto', whiteSpace: 'nowrap' }}>
                    ({result.totalMatches} match{result.totalMatches > 1 ? 'es' : ''})
                  </Typography>
                </Typography>
                {/* Show project badge for cross-project results */}
                {result.projectId && result.projectId !== currentProject?.id && result.projectName && (
                  <Typography variant="caption" sx={{
                    color: isDarkMode ? '#b89aff' : '#7c3aed',
                    fontSize: '11px', fontWeight: 500,
                    display: 'flex', alignItems: 'center', gap: '4px', mt: 0.25
                  }}>
                    📁 {result.projectName}
                  </Typography>
                )}
                {result.matches.slice(0, 3).map((match, idx) => (
                  <Box
                    key={idx}
                    sx={{
                      pl: 2,
                      py: 0.5,
                      cursor: 'pointer',
                      '&:hover': {
                        backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)'
                      }
                    }}
                    onClick={async () => {
                      try {
                        // If the conversation belongs to a different project, switch first
                        if (result.projectId && result.projectId !== currentProject?.id) {
                          await switchProject(result.projectId);
                          // Small delay to let project switch settle before loading conversation
                          await new Promise(resolve => setTimeout(resolve, 300));
                        }
                        (window as any).__ziyaSearchHighlight = searchQuery;
                        await loadConversationAndScrollToMessage(
                          result.conversationId, match.messageIndex);
                        setSearchQuery('');
                        setSearchResults([]);
                      } catch (error) {
                        console.error('Error navigating to message:', error);
                        message.error('Failed to navigate to message');
                      }
                    }}
                  >
                    <Typography variant="caption" sx={{ color: isDarkMode ? '#1890ff' : '#1890ff', display: 'block', mb: 0.5 }}>
                      {match.messageRole === 'human' ? '👤 You' : '🤖 AI'} · {new Date(match.timestamp).toLocaleDateString()}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{
                        color: isDarkMode ? '#ccc' : '#555',
                        display: 'block',
                        fontStyle: 'italic',
                        whiteSpace: 'pre-wrap'
                      }}
                    >
                      {highlightSnippet(match.snippet, searchQuery)}
                    </Typography>
                  </Box>
                ))}
                {result.matches.length > 3 && (
                  <Typography variant="caption" sx={{ pl: 2, color: isDarkMode ? '#888' : '#666', display: 'block', mt: 0.5 }}>
                    +{result.matches.length - 3} more match{result.matches.length - 3 > 1 ? 'es' : ''}...
                  </Typography>
                )}
              </Box>
            ))}
          </Box>
        ) : searchQuery && !isSearching ? (
          <Box sx={{ p: 4, textAlign: 'center', color: isDarkMode ? '#888' : '#666' }}>
            <Typography variant="body2">No results found for "{searchQuery}"</Typography>
          </Box>
        ) : searchQuery && isSearching ? (
          <Box sx={{ p: 4, textAlign: 'center' }}>
            <Spin size="small" />
            <Typography variant="caption" sx={{ display: 'block', mt: 1, color: isDarkMode ? '#888' : '#666' }}>
              Searching...
            </Typography>
          </Box>
        ) : (
          <div ref={treeContainerRef} style={{ flexGrow: 1, overflow: 'hidden', paddingTop: 8, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            {/* Root-level drop zone: visible only during drag when first item is a folder */}
            {customDragState.isDragging && flatNodes.length > 0 && flatNodes[0].isFolder && (
              <div
                data-root-drop-zone="true"
                style={{
                  height: 8,
                  flexShrink: 0,
                  marginLeft: 12,
                  marginRight: 12,
                  marginBottom: 2,
                  borderBottom: '2px solid transparent',
                  transition: 'border-color 0.15s, box-shadow 0.15s',
                  borderRadius: 1,
                  cursor: 'default',
                }}
              />
            )}
            <FixedSizeList
              height={treeContainerHeight}
              width="100%"
              ref={virtualListRef}
              itemCount={flatNodes.length}
              itemSize={VIRTUAL_ROW_HEIGHT}
              overscanCount={8}
              className="chat-history-tree"
              itemKey={(index) => flatNodes[index].id}
            >
              {({ index, style: rowStyle }) => {
                const flat = flatNodes[index];
                const node = flat.node;
                const isFolder = flat.isFolder;
                const nodeId = flat.id;

                const taskPlan = isFolder ? node.taskPlan : null;
                const isTaskPlanFolder = Boolean(taskPlan);
                let taskPlanProgress: string | undefined;
                if (isTaskPlanFolder && node.children) {
                  const dels = node.children.filter((c: any) => c.delegateMeta?.role === 'delegate');
                  if (dels.length > 0) {
                    const done = dels.filter((c: any) => c.delegateMeta?.status === 'crystal').length;
                    taskPlanProgress = `${done}/${dels.length}`;
                  }
                }

                const delegateMeta = !isFolder ? node.delegateMeta : null;
                let delegateStatus: DelegateStatus | 'orchestrator' | null = null;
                if (delegateMeta) {
                  delegateStatus = delegateMeta.role === 'orchestrator' ? 'orchestrator' : (delegateMeta.status || null);
                }

                let labelText = node.name || 'Untitled';
                if (delegateStatus && !isFolder) labelText = labelText.replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]\s*/u, '');
                if (isTaskPlanFolder) labelText = labelText.replace(/^⚡\s*/, '');

                const isPinned = isFolder && pinnedFolders.has(nodeId);
                const isCurrentItem = isFolder
                  ? false : nodeId.startsWith('conv-') && nodeId.substring(5) === currentConversationId;
                const isGlobalItem = isFolder ? node.folder?.isGlobal === true : node.conversation?.isGlobal === true;
                const hasUnreadResponse = !isFolder && nodeId.startsWith('conv-') &&
                  node.conversation?.hasUnreadResponse && nodeId.substring(5) !== currentConversationId;
                const convId = !isFolder && nodeId.startsWith('conv-') ? nodeId.substring(5) : null;
                const isStreamingConv = !!(convId && streamingConversations.has(convId));
                const conversationCount = isFolder ? node.conversationCount : 0;
                const isEditingNode = editingId === (isFolder ? nodeId : nodeId.substring(5));

                const handleCustomMouseDown = (e: React.MouseEvent) => {
                  const target = e.target as HTMLElement;
                  if (e.button !== 0 || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.closest('input') || target.closest('.MuiTextField-root')) return;
                  const container = chatHistoryRef.current;
                  if (!container || !container.contains(target)) return;
                  const startX = e.clientX, startY = e.clientY;
                  let down = true;
                  const detect = (me: MouseEvent) => {
                    if (!down) { document.removeEventListener('mousemove', detect); return; }
                    if (Math.abs(me.clientX - startX) > 12 || Math.abs(me.clientY - startY) > 12) {
                      me.preventDefault();
                      startCustomDrag(nodeId, isFolder ? 'folder' : 'conversation', labelText);
                      cleanup();
                    }
                  };
                  const cleanup = () => { down = false; document.removeEventListener('mousemove', detect); document.removeEventListener('mouseup', cleanup); };
                  document.addEventListener('mousemove', detect);
                  document.addEventListener('mouseup', cleanup);
                };

                return (
                  <div style={rowStyle} onClick={(e) => {
                    // Don't navigate on icon button clicks
                    if ((e.target as HTMLElement).closest('button') || (e.target as HTMLElement).closest('.MuiIconButton-root')) return;
                    if (nodeId.startsWith('conv-')) handleConversationClick(nodeId.substring(5));
                    else setCurrentFolderId(nodeId);
                  }}>
                    {/* Indent guide lines aligned with parent chevrons */}
                    {indentGuides[index]?.map((show, d) => show && (
                      <div
                        key={`guide-${d}`}
                        style={{
                          position: 'absolute',
                          left: 22 + d * 20,
                          top: 0,
                          bottom: 0,
                          width: 1,
                          backgroundColor: isDarkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)',
                          pointerEvents: 'none',
                        }}
                      />
                    ))}
                    <ChatTreeItem
                      nodeId={nodeId} labelText={labelText} isFolder={isFolder}
                      isTaskPlanFolder={isTaskPlanFolder} taskPlanProgress={taskPlanProgress}
                      delegateStatus={delegateStatus} isPinned={isPinned}
                      isCurrentItem={isCurrentItem} isGlobalItem={isGlobalItem}
                      isStreaming={isStreamingConv} hasUnreadResponse={hasUnreadResponse}
                      conversationCount={conversationCount}
                      onEdit={handleEdit} onDelete={handleDelete} onAddChat={handleAddChat}
                      onExport={handleExportConversation} onPin={togglePinFolder}
                      onConfigure={handleConfigureFolder} onFork={handleForkConversation}
                      onCompress={handleCompressConversation} onMove={handleMoveConversation}
                      onDelegateRetry={delegateStatus === 'failed' || delegateStatus === 'interrupted' ? handleDelegateRetry : undefined}
                      onDelegateSkip={delegateStatus === 'failed' || delegateStatus === 'interrupted' ? handleDelegateSkip : undefined}
                      onSwarmRecovery={isTaskPlanFolder ? handleSwarmRecovery : undefined}
                      onOpenMoveMenu={handleOpenMoveMenu} onToggleGlobal={handleToggleGlobal}
                      onMoveToProject={handleOpenMoveToProjectMenu}
                      onCopyToProject={handleOpenCopyToProjectMenu}
                      onCreateSubfolder={handleCreateSubfolder}
                      isEditing={isEditingNode} editValue={editValue}
                      onEditChange={handleEditChange} onEditSubmit={handleEditSubmit}
                      onMouseDown={handleCustomMouseDown}
                      depth={flat.depth} isExpanded={flat.isExpanded}
                      hasChildren={flat.hasChildren}
                      onToggleExpand={(id) => {
                        setExpandedNodes(prev => {
                          const s = new Set(prev.map(String));
                          if (s.has(id)) { s.delete(id); } else { s.add(id); }
                          return Array.from(s);
                        });
                      }}
                      style={{
                        cursor: customDragState.isDragging && customDragState.draggedNodeId === nodeId ? 'grabbing' : 'default',
                        opacity: customDragState.isDragging && customDragState.draggedNodeId === nodeId ? 0.6 : 1,
                        ...(isTaskPlanFolder ? { borderLeft: '3px solid #6366f1' } : {}),
                      }}
                    />
                  </div>
                );
              }}
            </FixedSizeList>
            {/* Export/Import — below the list, scrolls with content */}
            <Box sx={{
              display: 'flex',
              justifyContent: 'flex-end',
              p: 1,
              flexShrink: 0,
            }}>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Button
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  onClick={handleExportConversations}
                  size="small"
                >Export</Button>
                <Button
                  variant="outlined"
                  startIcon={<UploadIcon />}
                  onClick={handleImportConversations}
                  size="small"
                >Import</Button>
              </Box>
            </Box>
          </div>
        )}

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

        {/* Move to project menu */}
        <MoveToProjectMenu
          anchorEl={moveToProjectMenuState.anchorEl}
          open={Boolean(moveToProjectMenuState.anchorEl)}
          onClose={() => setMoveToProjectMenuState({ anchorEl: null, nodeId: null, mode: 'move' })}
          projects={projects}
          currentProjectId={currentProject?.id}
          onMoveToProject={handleMoveToProject}
          nodeId={moveToProjectMenuState.nodeId}
        />
      </Box>

      {/* Export modal - only for conversations, not folders */}
      {exportConversationId && (
        <ExportConversationModal visible={showExportModal} onClose={() => { setShowExportModal(false); setExportConversationId(null); }} />
      )}

      {/* Swarm Recovery Modal */}
      {swarmRecoveryFolder && (
        <Modal
          title={`🔧 Swarm Recovery: ${swarmRecoveryFolder.name?.replace(/^⚡\s*/, '')}`}
          open={showSwarmRecovery}
          onCancel={() => { setShowSwarmRecovery(false); setSwarmRecoveryFolderId(null); }}
          footer={null}
          width={480}
        >
          <SwarmRecoveryPanel
            groupId={swarmRecoveryFolder.id}
            planStatus={swarmRecoveryFolder.taskPlan?.status || 'unknown'}
            planName={swarmRecoveryFolder.taskPlan?.name || swarmRecoveryFolder.name || ''}
            delegates={(() => {
              // Build delegate info from conversations in this folder
              const folderConvs = conversations.filter(c => c.folderId === swarmRecoveryFolder.id);
              return folderConvs
                .filter(c => c.delegateMeta?.role === 'delegate')
                .map(c => ({
                  id: c.delegateMeta!.delegate_id || c.id,
                  name: c.title.replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]\s*/u, ''),
                  emoji: (() => {
                    const match = c.title.match(/^([\p{Emoji_Presentation}\p{Extended_Pictographic}])/u);
                    return match ? match[1] : '🔵';
                  })(),
                  status: c.delegateMeta!.status,
                  hasCrystal: c.delegateMeta!.status === 'crystal',
                }));
            })()}
            onActionComplete={() => {
              // Trigger a polling cycle to pick up the status change
              setTimeout(() => {
                setShowSwarmRecovery(false);
                setSwarmRecoveryFolderId(null);
              }, 1000);
            }}
          />
        </Modal>
      )}

      {/* Health Debug Modal */}
      <ConversationHealthDebugModal
        visible={showHealthDebug}
        onClose={() => setShowHealthDebug(false)}
      />
    </>
  );
};

/**
 * Error boundary wrapping MUIChatHistory so sidebar crashes (circular
 * folder references, corrupted data, etc.) show a retry button instead
 * of killing the entire application.
 */
class ChatHistoryErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('💥 Chat sidebar crashed:', error.message, info.componentStack?.slice(0, 500));
  }
  render() {
    if (this.state.hasError) {
      return (
        <Box sx={{ p: 2, textAlign: 'center', color: 'text.secondary' }}>
          <Typography variant="body2" sx={{ mb: 1 }}>
            Sidebar encountered an error
          </Typography>
          <Button size="small" variant="outlined"
            onClick={() => this.setState({ hasError: false, error: null })}>
            Retry
          </Button>
        </Box>
      );
    }
    return this.props.children;
  }
}

export default function SafeMUIChatHistory() {
  return <ChatHistoryErrorBoundary><MUIChatHistory /></ChatHistoryErrorBoundary>;
}
