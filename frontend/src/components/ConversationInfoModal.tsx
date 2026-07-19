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
import { Modal, Descriptions, Spin, Tag, Typography } from 'antd';
import type { Conversation } from '../utils/types';
import { db } from '../utils/db';
import { hydrateConversationMessages } from '../utils/conversationHydration';
import { computeConversationStats, formatBytes } from '../utils/conversationInfo';

const { Text } = Typography;

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
                        <Descriptions.Item label="Open beads">{meta.openBeadCount}</Descriptions.Item>
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
        </Modal>
    );
};

export default ConversationInfoModal;
