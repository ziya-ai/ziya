/**
 * DelegateLaunchButton — T27: "Launch Delegates" button in chat.
 *
 * Detects delegate task specifications in assistant message content
 * (```delegate-tasks JSON blocks) and renders a launch button.
 * On click, calls POST .../launch-delegates, syncs new server state
 * to IndexedDB, and broadcasts to update sidebar (T28).
 *
 * Expected format in assistant message:
 *
 * ```delegate-tasks
 * {
 *   "name": "Refactor auth module",
 *   "description": "Split auth into...",
 *   "delegates": [
 *     {
 *       "delegate_id": "auth-core",
 *       "name": "Auth Core",
 *       "emoji": "🔐",
 *       "scope": "Implement token validation",
 *       "files": ["src/auth/token.ts"],
 *       "dependencies": []
 *     }
 *   ]
 * }
 * ```
 */

import React, { useState, useCallback, useMemo } from 'react';
import { Button, message, Modal, Tag, Tooltip } from 'antd';
import { RocketOutlined, CheckCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { useConversationList } from '../context/ConversationListContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { useProject } from '../context/ProjectContext';
import { projectSync } from '../utils/projectSync';
import { useTheme } from '../context/ThemeContext';
import * as syncApi from '../api/conversationSyncApi';
import * as folderSyncApi from '../api/folderSyncApi';
import { db } from '../utils/db';
import type { DelegateSpec } from '../types/delegate';

interface DelegateTaskPlanSpec {
  name: string;
  description: string;
  delegates: Array<{
    delegate_id: string;
    name: string;
    emoji?: string;
    scope: string;
    files?: string[];
    dependencies?: string[];
    skill_id?: string;
    color?: string;
  }>;
}

interface DelegateLaunchButtonProps {
  messageContent: string;
  conversationId?: string;
}

const DELEGATE_TASKS_REGEX = /```delegate-tasks\s*\n([\s\S]*?)```/;

function parseDelegateSpecs(content: string): DelegateTaskPlanSpec | null {
  const match = DELEGATE_TASKS_REGEX.exec(content);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[1]);
    if (!parsed.name || !Array.isArray(parsed.delegates) || parsed.delegates.length === 0) {
      return null;
    }
    return parsed as DelegateTaskPlanSpec;
  } catch {
    return null;
  }
}

const DelegateLaunchButton: React.FC<DelegateLaunchButtonProps> = ({
  messageContent,
  conversationId,
}) => {
  const { currentProject } = useProject();
  const { setConversations, setFolders } = useConversationList();
  const { currentConversationId: contextConversationId } = useActiveChat();
  // Fall back to the active conversation when no explicit conversationId prop is supplied.
  const effectiveConversationId = conversationId ?? contextConversationId ?? null;
  const { isDarkMode } = useTheme();
  const [launching, setLaunching] = useState(false);
  const [launched, setLaunched] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const taskSpec = useMemo(() => parseDelegateSpecs(messageContent), [messageContent]);

  const handleLaunch = useCallback(async () => {
    if (!taskSpec || !currentProject?.id) return;

    setShowPreview(false);
    setLaunching(true);

    try {
      const delegateSpecs: DelegateSpec[] = taskSpec.delegates.map(d => ({
        delegate_id: d.delegate_id,
        name: d.name,
        emoji: d.emoji || '🔵',
        scope: d.scope,
        files: d.files || [],
        dependencies: d.dependencies || [],
        skill_id: d.skill_id || null,
        color: d.color || '',
      }));

      const response = await fetch(
        `/api/v1/projects/${currentProject.id}/groups/new/launch-delegates`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(currentProject?.path ? { 'X-Project-Root': currentProject.path } : {}),
          },
          body: JSON.stringify({
            name: taskSpec.name,
            description: taskSpec.description,
            delegate_specs: delegateSpecs,
            source_conversation_id: effectiveConversationId,
          }),
        }
      );

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }

      const result = await response.json();
      setLaunched(true);
      message.success(
        `🚀 Launched "${taskSpec.name}" with ${taskSpec.delegates.length} delegates`
      );

      // T28: Pull new chats/folders from server → IndexedDB → broadcast
      try {
        const pid = currentProject.id;
        const [serverChats, serverGroups] = await Promise.all([
          syncApi.listChats(pid, true),
          folderSyncApi.listServerFolders(pid),
        ]);
        // Merge new server chats into IndexedDB
        const existing = await db.getConversations();
        const existingIds = new Set(existing.map(c => c.id));
        const newChats = serverChats
          .filter(sc => !existingIds.has(sc.id))
          .map(sc => ({
            id: sc.id,
            title: sc.title || 'Untitled',
            messages: sc.messages || [],
            lastAccessedAt: sc.lastActiveAt || Date.now(),
            isActive: true,
            projectId: pid,
            folderId: sc.groupId || sc.folderId || null,
            delegateMeta: sc.delegateMeta || null,
            hasUnreadResponse: false,
          }));
        if (newChats.length > 0) {
          await db.saveConversations([...existing, ...newChats]);
        }
        // Merge new folders
        const existingFolders = await db.getFolders();
        const existingFolderIds = new Set(existingFolders.map(f => f.id));
        const newFolders = serverGroups
          .filter(g => !existingFolderIds.has(g.id))
          .map(g => ({
            id: g.id,
            name: g.name,
            projectId: pid,
            parentId: null,
            createdAt: g.createdAt || Date.now(),
            updatedAt: g.updatedAt || Date.now(),
            taskPlan: g.taskPlan || null,
          }));
        if (newFolders.length > 0) {
          for (const folder of newFolders) {
            await db.saveFolder(folder);
          }
        }

        // Update React state directly so the current tab sees changes immediately.
        // BroadcastChannel only delivers to OTHER tabs; without this the current
        // tab waits for the 30s server sync poll.
        if (newChats.length > 0) {
          setConversations(prev => [...prev, ...newChats]);
        }
        if (newFolders.length > 0) {
          setFolders(prev => [...prev, ...newFolders]);
        }
      } catch (syncErr) {
        console.warn('Post-launch sync failed (sidebar will update on next poll):', syncErr);
      }

      // Broadcast to update sidebar and other tabs
      projectSync.post('conversations-changed', {
        ids: Object.values(result.conversation_ids || {}),
      });
      projectSync.post('folders-changed');

    } catch (err: any) {
      console.error('Failed to launch delegates:', err);
      message.error(`Launch failed: ${err.message}`);
    } finally {
      setLaunching(false);
    }
  }, [taskSpec, currentProject, effectiveConversationId, setConversations, setFolders]);

  if (!taskSpec) return null;

  if (launched) {
    return (
      <div style={{
        margin: '12px 0', padding: '10px 16px',
        background: 'rgba(82, 196, 26, 0.08)',
        border: '1px solid rgba(82, 196, 26, 0.3)',
        borderRadius: 8, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 18 }} />
        <span style={{ fontWeight: 500 }}>Delegates launched: {taskSpec.name}</span>
        <Tag color="green">{taskSpec.delegates.length} delegates</Tag>
      </div>
    );
  }

  return (
    <>
      <div style={{
        margin: '12px 0', padding: '10px 16px',
        background: isDarkMode ? 'rgba(99, 102, 241, 0.12)' : 'rgba(99, 102, 241, 0.06)',
        border: `1px solid ${isDarkMode ? 'rgba(99, 102, 241, 0.4)' : 'rgba(99, 102, 241, 0.25)'}`,
        borderRadius: 8, fontSize: 15,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <Button
            type="primary"
            icon={launching ? <LoadingOutlined /> : <RocketOutlined />}
            loading={launching}
            onClick={() => setShowPreview(true)}
            size="large"
            style={{ background: '#6366f1', borderColor: '#6366f1', fontWeight: 600 }}
          >
            Launch Delegates
          </Button>
          <span style={{ fontSize: 15, fontWeight: 500 }}>
            {taskSpec.name} — {taskSpec.delegates.length} delegate{taskSpec.delegates.length > 1 ? 's' : ''}
          </span>
        </div>
        <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {taskSpec.delegates.map(d => (
            <Tooltip key={d.delegate_id}
              title={`${d.scope}${d.files?.length ? `\nFiles: ${d.files.join(', ')}` : ''}${d.dependencies?.length ? `\nDepends on: ${d.dependencies.join(', ')}` : ''}`}>
              <Tag style={{
                cursor: 'help', fontSize: 14, padding: '4px 10px', lineHeight: '22px',
                background: isDarkMode ? 'rgba(255,255,255,0.08)' : undefined,
                borderColor: isDarkMode ? 'rgba(255,255,255,0.15)' : undefined,
                color: isDarkMode ? '#e0e0e0' : undefined,
              }}>
                <span style={{ fontSize: 16 }}>{d.emoji || '🔵'}</span> {d.name}
                {d.dependencies && d.dependencies.length > 0 && (
                  <span style={{ opacity: 0.6, marginLeft: 4 }}>
                    ← {d.dependencies.length} dep{d.dependencies.length > 1 ? 's' : ''}
                  </span>
                )}
              </Tag>
            </Tooltip>
          ))}
        </div>
      </div>

      <Modal
        title={`🚀 Launch Delegates: ${taskSpec.name}`}
        open={showPreview} onOk={handleLaunch} onCancel={() => setShowPreview(false)}
        okText="Launch"
        okButtonProps={{ loading: launching, style: { background: '#6366f1', borderColor: '#6366f1' } }}
        width={560}
        className={isDarkMode ? 'delegate-modal-dark' : ''}
        styles={{
          content: { background: isDarkMode ? '#1f1f1f' : '#fff', color: isDarkMode ? '#e0e0e0' : undefined },
          header: { background: isDarkMode ? '#1f1f1f' : '#fff', color: isDarkMode ? '#fff' : undefined },
          body: { background: isDarkMode ? '#1f1f1f' : '#fff' },
        }}
      >
        <p style={{ marginBottom: 12, color: isDarkMode ? '#d0d0d0' : undefined }}>{taskSpec.description}</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {taskSpec.delegates.map(d => (
            <div key={d.delegate_id} style={{
              padding: '10px 14px',
              border: `1px solid ${isDarkMode ? '#404040' : '#e8e8e8'}`,
              borderRadius: 6,
              background: isDarkMode ? '#2a2a2a' : '#fafafa',
              color: isDarkMode ? '#d0d0d0' : undefined,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{d.emoji || '🔵'} {d.name}</div>
              <div style={{ fontSize: 13, opacity: 0.85 }}>{d.scope}</div>
              {d.files && d.files.length > 0 && (
                <div style={{ fontSize: 12, marginTop: 4, opacity: 0.6 }}>Files: {d.files.join(', ')}</div>
              )}
              {d.dependencies && d.dependencies.length > 0 && (
                <div style={{ fontSize: 12, marginTop: 2, opacity: 0.6 }}>Depends on: {d.dependencies.join(', ')}</div>
              )}
            </div>
          ))}
        </div>
      </Modal>
    </>
  );
};

export default DelegateLaunchButton;
export { parseDelegateSpecs };
export type { DelegateTaskPlanSpec };
