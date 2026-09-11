/**
 * ConversationInfoModal — quick read-only info popup for a conversation.
 *
 * Surfaced from the conversation drop-down menu (Info, just above Delete).
 * Shows the conversation id, project id, and various statistics about the
 * conversation and its storage state.
 *
 * The sidebar's in-state conversation may be a lazy-load "shell" (messages
 * truncated), so message statistics are computed from the full IndexedDB
 * record fetched on open, falling back to the in-state record when the
 * conversation isn't persisted yet.
 */
import React, { useEffect, useState } from 'react';
import { Modal, Descriptions, Spin, Tag, Typography, Button, Table, Alert, Collapse, Switch } from 'antd';
import type { Conversation } from '../utils/types';
import { db } from '../utils/db';
import { hydrateConversationMessages } from '../utils/conversationHydration';
import { computeConversationStats, formatBytes } from '../utils/conversationInfo';

const { Text } = Typography;

interface ContextDebugIteration {
    iteration: number;
    timestamp: number;
    fresh_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    total_input_tokens: number;
    effective_limit: number | null;
    pct_of_limit: number | null;
    message_count: number | null;
    note: string | null;
    is_estimated: boolean;
    is_failure: boolean;
}

interface Props {
    visible: boolean;
    conversationId: string | null;
    /** In-state record (may be a shell); source of metadata + shell flags. */
    conversation: Conversation | null;
    projectId?: string;
    onClose: () => void;
}

function formatTs(ts: number | null | undefined): string {
    if (!ts) return '—';
    try {
        return new Date(ts).toLocaleString();
    } catch {
        return String(ts);
    }
}

const ConversationInfoModal: React.FC<Props> = ({
    visible, conversationId, conversation, projectId, onClose,
}) => {
    const [full, setFull] = useState<Conversation | null>(null);
    const [loading, setLoading] = useState(false);
    // null = not yet determined; true/false = present/absent in IndexedDB.
    const [persisted, setPersisted] = useState<boolean | null>(null);
    // Set when hydration could not reach the server for a shell/absent record,
    // so the stats shown may be incomplete.  Surfaced in the render.
    const [hydrationError, setHydrationError] = useState(false);
    // "Submitted context" debug panel: provider-reported per-iteration
    // input token usage for this conversation's recent turns.  Loaded on
    // demand via fetchContextDebug below.
    const [contextDebug, setContextDebug] = useState<ContextDebugIteration[] | null>(null);
    const [contextDebugLoading, setContextDebugLoading] = useState(false);
    const [contextDebugError, setContextDebugError] = useState<string | null>(null);
    // Backend detailed-capture flag (char counts + disk snapshots); null
    // until the first Load reports it.
    const [detailedCapture, setDetailedCapture] = useState<boolean | null>(null);

    useEffect(() => {
        if (!visible || !conversationId) return;
        let cancelled = false;
        setLoading(true);
        setFull(null);
        setPersisted(null);
        setHydrationError(false);
        (async () => {
            // `persisted` reflects genuine IDB presence — independent of
            // whether the record carries message bodies — so read IDB directly
            // for that signal before any server hydration.
            let idbRec: Conversation | null = null;
            try {
                idbRec = await db.getConversation(conversationId);
            } catch { /* treated as absent below */ }
            if (cancelled) return;
            setPersisted(!!idbRec);

            // Resolve full messages (local → IDB → server) for accurate stats.
            const res = await hydrateConversationMessages(conversationId, {
                local: conversation,
                projectId: idbRec?.projectId || projectId,
            });
            if (cancelled) return;
            // Merge resolved messages onto whatever metadata we have (prefer
            // the IDB record's metadata; fall back to the in-state record).
            const base = idbRec || conversation || ({ id: conversationId } as Conversation);
            setFull({ ...base, messages: res.messages } as Conversation);
            setHydrationError(res.source === 'empty' && !!res.error);
            setLoading(false);
        })();
        return () => { cancelled = true; };
    }, [visible, conversationId, projectId, conversation]);

    // Reset the debug panel whenever the modal targets a different
    // conversation so stale data from a previous chat can't linger.
    useEffect(() => {
        setContextDebug(null);
        setContextDebugError(null);
    }, [conversationId]);

    const fetchContextDebug = async () => {
        const pid = full?.projectId || (conversation as any)?.projectId || projectId;
        if (!conversationId || !pid) return;
        setContextDebugLoading(true);
        setContextDebugError(null);
        try {
            const resp = await fetch(
                '/api/v1/projects/' + encodeURIComponent(pid) + '/chats/' + encodeURIComponent(conversationId) + '/context-debug'
            );
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            setContextDebug(data.iterations || []);
            setDetailedCapture(data.detailed_capture_enabled ?? null);
        } catch (e: any) {
            setContextDebugError(e?.message || 'Failed to load');
        } finally {
            setContextDebugLoading(false);
        }
    };

    // Toggle the backend's detailed-capture tier (payload char counts +
    // per-iteration crash-surviving disk snapshots).  The cheap in-memory
    // token tier is always on regardless.
    const toggleDetailedCapture = async (enabled: boolean) => {
        setDetailedCapture(enabled); // optimistic — reverted on failure
        try {
            const resp = await fetch('/api/v1/context-debug/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled }),
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            setDetailedCapture(data.enabled ?? enabled);
        } catch {
            setDetailedCapture(!enabled);
        }
    };

    // Prefer the persisted full record for stats (in-state may be a shell);
    // fall back to the in-state conversation for a not-yet-synced chat.
    const statsSource: Conversation | null = full ?? conversation;
    const stats = statsSource ? computeConversationStats(statsSource) : null;
    const meta: any = conversation ?? full;
    const isShell = !!(conversation as any)?._isShell;
    const fullMessageCount = (conversation as any)?._fullMessageCount;

    return (
        <Modal
            title="Conversation Info"
            open={visible}
            onCancel={onClose}
            footer={null}
            width={540}
        >
            {loading && !stats ? (
                <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
            ) : (
                <Descriptions bordered size="small" column={1}>
                    {hydrationError && (
                        <Descriptions.Item label="⚠️ Stats">
                            <Text type="warning">
                                Could not load full message history from the server —
                                counts below may be incomplete.
                            </Text>
                        </Descriptions.Item>
                    )}
                    <Descriptions.Item label="Conversation ID">
                        <Text copyable code>{conversationId}</Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="Project ID">
                        <Text copyable code>{meta?.projectId || projectId || '—'}</Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="Title">{meta?.title || '—'}</Descriptions.Item>
                    <Descriptions.Item label="Folder ID">
                        {meta?.folderId ? <Text code>{meta.folderId}</Text> : '—'}
                    </Descriptions.Item>
                    {stats && (
                        <Descriptions.Item label="Messages">
                            {stats.messageCount}{' '}
                            <Text type="secondary">
                                ({stats.humanCount} human · {stats.assistantCount} assistant
                                {stats.systemCount ? ` · ${stats.systemCount} system` : ''}
                                {stats.mutedCount ? ` · ${stats.mutedCount} muted` : ''}
                                {stats.toolResultCount ? ` · ${stats.toolResultCount} tool` : ''})
                            </Text>
                        </Descriptions.Item>
                    )}
                    {stats && (
                        <Descriptions.Item label="Total characters">
                            {stats.totalChars.toLocaleString()}
                        </Descriptions.Item>
                    )}
                    {stats && (
                        <Descriptions.Item label="Approx. size">
                            {formatBytes(stats.approxBytes)}
                        </Descriptions.Item>
                    )}
                    <Descriptions.Item label="Last accessed">
                        {formatTs(meta?.lastAccessedAt)}
                    </Descriptions.Item>
                    <Descriptions.Item label="Version">{meta?._version ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="Storage state">
                        {persisted === null
                            ? <Tag>unknown</Tag>
                            : persisted
                                ? <Tag color="green">persisted (IndexedDB)</Tag>
                                : <Tag color="orange">not in IndexedDB</Tag>}
                        {isShell && (
                            <Tag color="blue">
                                shell{typeof fullMessageCount === 'number' ? ` (${fullMessageCount} msgs)` : ''}
                            </Tag>
                        )}
                        {meta?.isEphemeral && <Tag color="red">ephemeral</Tag>}
                        {meta?.isGlobal && <Tag color="purple">global</Tag>}
                        {meta?.isActive === false && <Tag>inactive</Tag>}
                    </Descriptions.Item>
                    {meta?.displayMode && (
                        <Descriptions.Item label="Display mode">{meta.displayMode}</Descriptions.Item>
                    )}
                    {typeof meta?.openBeadCount === 'number' && meta.openBeadCount > 0 && (
                        <Descriptions.Item label="Parked beads">{meta.openBeadCount}</Descriptions.Item>
                    )}
                    {meta?.branchedFrom && (
                        <Descriptions.Item label="Branched from">
                            <Text code>{meta.branchedFrom}</Text>
                            {meta.branchedFromLabel ? ` (${meta.branchedFromLabel})` : ''}
                        </Descriptions.Item>
                    )}
                    {meta?.lineageRootId && (
                        <Descriptions.Item label="Lineage root">
                            <Text code>{meta.lineageRootId}</Text>
                        </Descriptions.Item>
                    )}
                </Descriptions>
            )}
            <Collapse
                style={{ marginTop: 12 }}
                items={[{
                    key: 'context-debug',
                    label: 'Submitted context (debug)',
                    children: (
                        <div>
                            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
                                Actual per-iteration input tokens reported by the model provider
                                for this conversation's recent turns. This can be far larger than
                                the message stats above: a turn's tool-calling loop accumulates
                                tool results that are never saved to the chat record once the
                                turn ends.
                            </Text>
                            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
                                Rows marked <Tag color="red" style={{ marginLeft: 2, marginRight: 2 }}>failed (est.)</Tag>
                                are calls that were rejected before any provider usage event arrived (e.g. a
                                "prompt is too long" error) — their token count is a rough char-based
                                estimate, never the provider's real number, because none exists for a
                                call that never streamed.
                            </Text>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                                <Button size="small" loading={contextDebugLoading} onClick={fetchContextDebug}>
                                    {contextDebug ? 'Refresh' : 'Load'}
                                </Button>
                                {detailedCapture !== null && (
                                    <span style={{ fontSize: 12 }}>
                                        <Switch
                                            size="small"
                                            checked={detailedCapture}
                                            onChange={toggleDetailedCapture}
                                        />{' '}
                                        Detailed capture (payload char counts + crash-surviving disk snapshots)
                                    </span>
                                )}
                            </div>
                            {contextDebugError && (
                                <Alert style={{ marginTop: 8 }} type="error" showIcon
                                    message={'Failed to load: ' + contextDebugError} />
                            )}
                            {contextDebug && contextDebug.length === 0 && !contextDebugError && (
                                <Alert style={{ marginTop: 8 }} type="info" showIcon
                                    message="No iterations recorded for this conversation yet (nothing sent since the server last started, or no turn has run)." />
                            )}
                            {contextDebug && contextDebug.length > 0 && (
                                <Table
                                    style={{ marginTop: 8 }}
                                    size="small"
                                    pagination={false}
                                    rowKey={(r: ContextDebugIteration) => r.iteration + '-' + r.timestamp}
                                    dataSource={contextDebug}
                                    rowClassName={(r: ContextDebugIteration) => r.is_failure ? 'context-debug-failure-row' : ''}
                                    columns={[
                                        { title: 'Iter', dataIndex: 'iteration', width: 50 },
                                        {
                                            title: 'Time', dataIndex: 'timestamp', width: 90,
                                            render: (t: number) => t ? new Date(t * 1000).toLocaleTimeString() : '—',
                                        },
                                        {
                                            title: 'Fresh', dataIndex: 'fresh_tokens',
                                            render: (n: number) => (n ?? 0).toLocaleString(),
                                        },
                                        {
                                            title: 'Cached', dataIndex: 'cache_read_tokens',
                                            render: (n: number) => (n ?? 0).toLocaleString(),
                                        },
                                        {
                                            title: 'Total input', dataIndex: 'total_input_tokens',
                                            render: (n: number, r: ContextDebugIteration) => (
                                                <span>
                                                    <Text strong>{(n ?? 0).toLocaleString()}</Text>
                                                    {r.is_estimated && (
                                                        <Tag color={r.is_failure ? 'red' : 'default'} style={{ marginLeft: 6 }}>
                                                            {r.is_failure ? 'failed (est.)' : 'est.'}
                                                        </Tag>
                                                    )}
                                                </span>
                                            ),
                                        },
                                        { title: 'Note', dataIndex: 'note', render: (n: string | null) => n || '—' },
                                        {
                                            title: '% of limit', dataIndex: 'pct_of_limit',
                                            render: (p: number | null) => p == null ? '—' : (
                                                <Tag color={p >= 95 ? 'red' : p >= 80 ? 'orange' : 'green'}>{p}%</Tag>
                                            ),
                                        },
                                        {
                                            title: 'Msgs', dataIndex: 'message_count',
                                            render: (n: number | null) => n ?? '—',
                                        },
                                    ]}
                                />
                            )}
                            <style>{`
                                .context-debug-failure-row td {
                                    background-color: rgba(255, 77, 79, 0.06) !important;
                                }
                            `}</style>
                        </div>
                    ),
                }]}
            />
        </Modal>
    );
};

export default ConversationInfoModal;
