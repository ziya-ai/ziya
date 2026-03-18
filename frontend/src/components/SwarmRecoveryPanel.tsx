/**
 * SwarmRecoveryPanel — controls for retrying, skipping, and restarting
 * broken or stalled swarm delegates.
 *
 * Rendered in two places:
 *   1. As a modal from TaskPlan folder rows in the sidebar
 *   2. Inline (compact) in the StreamedContent active-swarm indicator
 *
 * Backend endpoints used:
 *   POST .../retry-delegate     — reset failed/interrupted → re-run
 *   POST .../promote-stub       — skip a failed delegate, unblock downstream
 *   POST .../cancel-delegates   — cancel all running delegates
 */

import React, { useState, useCallback } from 'react';
import { Button, message, Tooltip, Modal, Tag, Space, Divider } from 'antd';
import {
  ReloadOutlined,
  StopOutlined,
  ForwardOutlined,
  ExclamationCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import { useProject } from '../context/ProjectContext';

export interface DelegateInfo {
  id: string;
  name: string;
  emoji: string;
  status: string;
  hasCrystal: boolean;
}

export interface SwarmRecoveryPanelProps {
  groupId: string;
  planStatus: string;
  planName: string;
  delegates: DelegateInfo[];
  onActionComplete?: () => void;
  compact?: boolean;
}

type ActionInFlight = { type: 'retry' | 'skip' | 'cancel'; delegateId?: string } | null;

const STATUS_LABELS: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  crystal:     { label: 'Done',        color: '#52c41a', icon: <CheckCircleOutlined /> },
  running:     { label: 'Running',     color: '#1890ff', icon: <LoadingOutlined spin /> },
  compacting:  { label: 'Compacting',  color: '#1890ff', icon: <LoadingOutlined spin /> },
  failed:      { label: 'Failed',      color: '#ff4d4f', icon: <CloseCircleOutlined /> },
  interrupted: { label: 'Interrupted', color: '#faad14', icon: <ExclamationCircleOutlined /> },
  proposed:    { label: 'Queued',      color: '#8c8c8c', icon: <InfoCircleOutlined /> },
  ready:       { label: 'Ready',       color: '#8c8c8c', icon: <InfoCircleOutlined /> },
  stalled:     { label: 'Stalled',     color: '#fa8c16', icon: <ExclamationCircleOutlined /> },
  blocked:     { label: 'Blocked',     color: '#faad14', icon: <ExclamationCircleOutlined /> },
};

const SwarmRecoveryPanel: React.FC<SwarmRecoveryPanelProps> = ({
  groupId, planStatus, planName, delegates,
  onActionComplete, compact = false,
}) => {
  const { isDarkMode } = useTheme();
  const { currentProject } = useProject();
  const [actionInFlight, setActionInFlight] = useState<ActionInFlight>(null);

  const projectId = currentProject?.id;
  const projectHeaders: Record<string, string> = currentProject?.path
    ? { 'X-Project-Root': currentProject.path } : {};

  const failedDelegates = delegates.filter(d =>
    d.status === 'failed' || d.status === 'interrupted');
  const stalledDelegates = delegates.filter(d => d.status === 'stalled');
  const runningDelegates = delegates.filter(d =>
    d.status === 'running' || d.status === 'compacting');
  const needsAttention = failedDelegates.length > 0 || stalledDelegates.length > 0;
  const isTerminal = planStatus === 'completed' || planStatus === 'cancelled';
  const isPartial = planStatus === 'completed_partial';

  const callApi = useCallback(async (endpoint: string, body?: any) => {
    if (!projectId) return null;
    const url = `/api/v1/projects/${projectId}/groups/${groupId}/${endpoint}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...projectHeaders },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `Request failed: ${res.status}`);
    }
    return res.json();
  }, [projectId, groupId, projectHeaders]);

  const handleRetry = useCallback(async (delegateId: string, delegateName: string) => {
    setActionInFlight({ type: 'retry', delegateId });
    try {
      await callApi('retry-delegate', { delegate_id: delegateId });
      message.success(`🔄 Retrying "${delegateName}"`);
      onActionComplete?.();
    } catch (err: any) {
      message.error(`Retry failed: ${err.message}`);
    } finally {
      setActionInFlight(null);
    }
  }, [callApi, onActionComplete]);

  const handleSkip = useCallback(async (delegateId: string, delegateName: string) => {
    Modal.confirm({
      title: 'Skip delegate?',
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>Creates a stub crystal for <strong>{delegateName}</strong> so
          downstream delegates can proceed.</p>
          <p style={{ color: '#faad14' }}>The delegate's work will be
          marked as incomplete.</p>
        </div>
      ),
      okText: 'Skip & Unblock',
      okType: 'default',
      onOk: async () => {
        setActionInFlight({ type: 'skip', delegateId });
        try {
          await callApi('promote-stub', { delegate_id: delegateId });
          message.success(`⏭️ Skipped "${delegateName}" — downstream unblocked`);
          onActionComplete?.();
        } catch (err: any) {
          message.error(`Skip failed: ${err.message}`);
        } finally {
          setActionInFlight(null);
        }
      },
    });
  }, [callApi, onActionComplete]);

  const handleCancelAll = useCallback(async () => {
    Modal.confirm({
      title: 'Cancel all delegates?',
      icon: <ExclamationCircleOutlined />,
      content: 'Stops all running delegates. Completed crystals are preserved.',
      okText: 'Cancel All',
      okType: 'danger' as const,
      onOk: async () => {
        setActionInFlight({ type: 'cancel' });
        try {
          await callApi('cancel-delegates');
          message.success('🛑 All delegates cancelled');
          onActionComplete?.();
        } catch (err: any) {
          message.error(`Cancel failed: ${err.message}`);
        } finally {
          setActionInFlight(null);
        }
      },
    });
  }, [callApi, onActionComplete]);

  const handleRetryAll = useCallback(async () => {
    setActionInFlight({ type: 'retry' });
    let succeeded = 0;
    for (const d of failedDelegates) {
      try {
        await callApi('retry-delegate', { delegate_id: d.id });
        succeeded++;
      } catch { /* count continues */ }
    }
    setActionInFlight(null);
    if (succeeded > 0) message.success(`🔄 Retried ${succeeded} delegate(s)`);
    onActionComplete?.();
  }, [failedDelegates, callApi, onActionComplete]);

  // ─── Theme ────────────────────────────────────────────────────────
  const t = {
    bg: isDarkMode ? '#1a1a1a' : '#ffffff',
    border: isDarkMode ? '#333' : '#e8e8e8',
    textPrimary: isDarkMode ? '#e0e0e0' : '#1f2937',
    textSecondary: isDarkMode ? '#888' : '#6b7280',
    dangerBg: isDarkMode ? 'rgba(255,77,79,0.08)' : 'rgba(255,77,79,0.06)',
    dangerBorder: isDarkMode ? 'rgba(255,77,79,0.3)' : 'rgba(255,77,79,0.2)',
    rowBg: isDarkMode ? '#222' : '#fafafa',
    rowBorder: isDarkMode ? '#333' : '#f0f0f0',
  };

  // ─── Compact: just inline buttons ─────────────────────────────────
  if (compact) {
    if (!needsAttention && !isPartial && runningDelegates.length === 0) return null;
    return (
      <Space size={4} wrap>
        {failedDelegates.length > 0 && (
          <Tooltip title={`Retry ${failedDelegates.length} failed delegate(s)`}>
            <Button size="small" icon={<ReloadOutlined />}
              onClick={handleRetryAll}
              loading={actionInFlight?.type === 'retry'}
              style={{ fontSize: 12 }}>
              Retry {failedDelegates.length}
            </Button>
          </Tooltip>
        )}
        {runningDelegates.length > 0 && (
          <Tooltip title="Cancel all running delegates">
            <Button size="small" icon={<StopOutlined />}
              onClick={handleCancelAll}
              loading={actionInFlight?.type === 'cancel'}
              danger style={{ fontSize: 12 }}>
              Cancel
            </Button>
          </Tooltip>
        )}
      </Space>
    );
  }

  // ─── Full panel (modal body) ──────────────────────────────────────
  return (
    <div style={{ background: t.bg, fontSize: 13 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontWeight: 600, color: t.textPrimary }}>⚡ {planName}</span>
        <Tag color={
          planStatus === 'running' ? 'processing' :
          planStatus === 'completed' ? 'success' :
          planStatus === 'completed_partial' ? 'warning' :
          'default'
        }>{planStatus}</Tag>
      </div>

      {/* Delegate list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 10 }}>
        {delegates.map(d => {
          const si = STATUS_LABELS[d.status] || STATUS_LABELS.proposed;
          const canAct = d.status === 'failed' || d.status === 'interrupted';
          const isLoading = actionInFlight?.delegateId === d.id;
          return (
            <div key={d.id} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '4px 8px',
              background: canAct ? t.dangerBg : t.rowBg,
              border: `1px solid ${canAct ? t.dangerBorder : t.rowBorder}`,
              borderRadius: 6,
            }}>
              <span style={{ fontSize: 14 }}>{d.emoji}</span>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                whiteSpace: 'nowrap', color: t.textPrimary, fontSize: 12 }}>
                {d.name}
              </span>
              <span style={{ color: si.color, fontSize: 11, display: 'flex', alignItems: 'center', gap: 3 }}>
                {si.icon} {si.label}
              </span>
              {canAct && (
                <Space size={2}>
                  <Tooltip title="Retry">
                    <Button type="text" size="small" icon={<ReloadOutlined />}
                      loading={isLoading && actionInFlight?.type === 'retry'}
                      onClick={e => { e.stopPropagation(); handleRetry(d.id, d.name); }}
                      style={{ padding: '0 4px', height: 22, fontSize: 11 }} />
                  </Tooltip>
                  <Tooltip title="Skip (stub crystal to unblock downstream)">
                    <Button type="text" size="small" icon={<ForwardOutlined />}
                      loading={isLoading && actionInFlight?.type === 'skip'}
                      onClick={e => { e.stopPropagation(); handleSkip(d.id, d.name); }}
                      style={{ padding: '0 4px', height: 22, fontSize: 11 }} />
                  </Tooltip>
                </Space>
              )}
            </div>
          );
        })}
      </div>

      {/* Bulk actions */}
      <Divider style={{ margin: '8px 0', borderColor: t.border }} />
      <Space size={8} wrap>
        {failedDelegates.length > 0 && (
          <Button size="small" icon={<ReloadOutlined />}
            onClick={handleRetryAll}
            loading={actionInFlight?.type === 'retry' && !actionInFlight.delegateId}>
            Retry all failed ({failedDelegates.length})
          </Button>
        )}
        {runningDelegates.length > 0 && (
          <Button size="small" icon={<StopOutlined />}
            onClick={handleCancelAll}
            loading={actionInFlight?.type === 'cancel'} danger>
            Cancel all
          </Button>
        )}
        {isTerminal && failedDelegates.length === 0 && (
          <span style={{ fontSize: 11, color: t.textSecondary }}>
            Plan completed — no actions available
          </span>
        )}
      </Space>
    </div>
  );
};

export default SwarmRecoveryPanel;
