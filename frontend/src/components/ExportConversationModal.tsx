import React, { useState, useEffect } from 'react';
import { Modal, Radio, Button, message, Space, Typography, Divider, Progress, Switch, Segmented, InputNumber, Select } from 'antd';
import { CopyOutlined, DownloadOutlined, GithubOutlined, CloudOutlined, FileTextOutlined, LinkOutlined, PictureOutlined, CheckCircleOutlined, FilePdfOutlined, MessageOutlined, FilterOutlined, EyeOutlined } from '@ant-design/icons';
import { useActiveChat } from '../context/ActiveChatContext';
import { useTheme } from '../context/ThemeContext';
import { captureAllVisualizations } from '../utils/visualizationCapture';
// NOTE: the legacy client-side exportConversationAsPdf (utils/pdfExport.ts) is
// retired from the PDF path in Stage 2 — handlePdfExport now POSTs to
// /api/export/pdf and downloads real PDF bytes. The util file itself is left
// in place (its import removed here) until Stage 7 deletes it.
import { useProject } from '../context/ProjectContext';
import { hydrateConversationMessages } from '../utils/conversationHydration';
import type { Message } from '../utils/types';

const { Text, Paragraph } = Typography;

type ExportMode = 'copy' | 'download' | 'pdf' | 'paste';

interface ExportConversationModalProps {
    visible: boolean;
    onClose: () => void;
    /**
     * Conversation to export.  When omitted (or equal to the active chat),
     * the modal exports the active conversation.  When it names a different
     * conversation (e.g. a row picked from the history list), that
     * conversation's messages are loaded from IndexedDB and exported
     * instead of the active chat's.
     */
    conversationId?: string;
}

const ExportConversationModal: React.FC<ExportConversationModalProps> = ({ visible, onClose, conversationId }) => {
    const [exportMode, setExportMode] = useState<ExportMode>('copy');
    const [format, setFormat] = useState<'markdown' | 'html'>('markdown');
    const [embedImages, setEmbedImages] = useState(false);
    const [target, setTarget] = useState<'public' | 'internal'>('public');
    const [isExporting, setIsExporting] = useState(false);
    const [exportedContent, setExportedContent] = useState<string | null>(null);
    const [isPdfExporting, setIsPdfExporting] = useState(false);
    const [pasteUrl, setPasteUrl] = useState<string | null>(null);
    const [captureProgress, setCaptureProgress] = useState<number>(0);
    const [captureStatus, setCaptureStatus] = useState<string>('');
    const [roundLimit, setRoundLimit] = useState<number | null>(null); // null = all rounds
    const [includeHuman, setIncludeHuman] = useState(true);
    const [includeCollapsed, setIncludeCollapsed] = useState(true);
    const [availableTargets, setAvailableTargets] = useState<any[]>([
        {
            id: 'public',
            name: 'GitHub Gist',
            url: 'https://gist.github.com',
            icon: 'GithubOutlined',
            description: 'Public paste service with markdown support'
        }
    ]);
    const { currentConversationId: activeConversationId, currentMessages: activeMessages } = useActiveChat();
    const { currentProject } = useProject();
    // When an explicit target conversation is supplied and it is NOT the
    // active chat, export that conversation's messages (loaded from IDB)
    // rather than the active chat's.  Downstream code keeps using
    // currentConversationId / currentMessages unchanged — these locals
    // shadow the active-chat values and resolve to the correct source.
    const [loadedConv, setLoadedConv] = useState<{ id: string; messages: Message[] } | null>(null);
    const [loadError, setLoadError] = useState(false);
    const useExplicitTarget = !!conversationId && conversationId !== activeConversationId;
    const currentConversationId = useExplicitTarget ? conversationId! : activeConversationId;
    const currentMessages: Message[] = useExplicitTarget
        ? (loadedConv?.id === conversationId ? loadedConv.messages : [])
        : activeMessages;

    // Load the target conversation's messages when it differs from the active
    // chat, via the shared hydration helper (local → IDB → server).  A
    // conversation the user has never opened lives only on the server (its IDB
    // entry is a metadata-only shell), which previously exported as "0 rounds".
    // Cleared on close so a stale target can't leak into a later active-chat
    // export.
    useEffect(() => {
        if (!visible || !useExplicitTarget) { setLoadedConv(null); setLoadError(false); return; }
        let cancelled = false;
        (async () => {
            const res = await hydrateConversationMessages(conversationId!, {
                projectId: currentProject?.id,
            });
            if (cancelled) return;
            setLoadedConv({ id: conversationId!, messages: res.messages });
            setLoadError(res.source === 'empty' && !!res.error);
        })();
        return () => { cancelled = true; };
    }, [visible, useExplicitTarget, conversationId, currentProject?.id]);
    const { isDarkMode } = useTheme();

    useEffect(() => {
        if (!visible) {
            setExportedContent(null);
            setIsExporting(false);
            setCaptureProgress(0);
            setPasteUrl(null);
            setCaptureStatus('');
            setExportMode('copy');
            setFormat('markdown');
            setIsPdfExporting(false);
            setEmbedImages(false);
            setTarget('public');
            setRoundLimit(null);
            setIncludeHuman(true);
            setIncludeCollapsed(true);
        } else {
            loadExportTargets();
        }
    }, [visible]);

    /**
     * Compute the total number of conversation rounds (human→assistant pairs).
     */
    const totalRounds = React.useMemo(() => {
        let rounds = 0;
        for (const m of currentMessages) {
            if (m.role === 'human') rounds++;
        }
        return rounds;
    }, [currentMessages]);

    /**
     * Apply scope & content filters to the raw message list.
     *
     *  - roundLimit: keep only the last N human→assistant exchanges
     *  - includeHuman: when false, strip the user's prompts (keep AI only)
     *  - includeCollapsed: when false, remove content inside
     *    <details>…</details> blocks (tool output, reasoning steps, etc.)
     */
    const filteredMessages = React.useMemo(() => {
        let msgs = [...currentMessages];

        // Scope to last N rounds (a "round" = one human + following assistant msgs)
        if (roundLimit !== null && roundLimit > 0) {
            const humanIndices = msgs.reduce<number[]>((acc, m, i) => {
                if (m.role === 'human') acc.push(i);
                return acc;
            }, []);
            const startFrom = humanIndices[Math.max(0, humanIndices.length - roundLimit)];
            if (startFrom !== undefined) {
                msgs = msgs.slice(startFrom);
            }
        }

        // Optionally exclude human messages
        if (!includeHuman) {
            msgs = msgs.filter(m => m.role !== 'human');
        }

        // Optionally strip collapsed / details content
        if (!includeCollapsed) {
            msgs = msgs.map(m => ({
                ...m,
                content: m.content
                    ? m.content.replace(/<details[\s\S]*?<\/details>/gi, '')
                             .replace(/```thinking:step-\d+\n[\s\S]*?```/g, '')
                    : m.content
            }));
        }

        return msgs;
    }, [currentMessages, roundLimit, includeHuman, includeCollapsed]);

    const handlePdfExport = async () => {
        setIsPdfExporting(true);
        setCaptureProgress(0);
        setCaptureStatus('Preparing PDF…');
        // Progress plumbing preserved from the old client-side path. A single
        // server round-trip does not stream granular progress, so we report
        // coarse milestones through the SAME onProgress/captureProgress state
        // the modal already renders.
        const onProgress = (pct: number, status: string) => {
            setCaptureProgress(pct);
            setCaptureStatus(status);
        };
        try {
            onProgress(15, 'Rendering conversation server-side…');
            // Send the RAW messages plus option knobs; the server-side /print
            // route performs the roundLimit/includeHuman/includeCollapsed
            // filtering (single source of truth shared by PDF & HTML exports),
            // so we must NOT pre-filter here.
            const response = await fetch('/api/export/pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    conversation_id: currentConversationId,
                    project_id: currentProject?.id,
                    messages: currentMessages,
                    title: 'Ziya Session Transcript',
                    roundLimit,
                    includeHuman,
                    includeCollapsed,
                    includeFooter: true,
                }),
            });

            if (!response.ok) {
                // Surface server-side failure as a REAL error instead of the
                // old "print dialog opened" silent success.
                let detail = `PDF export failed (HTTP ${response.status})`;
                try {
                    const errBody = await response.json();
                    if (errBody?.error) detail = errBody.error;
                } catch { /* non-JSON body */ }
                throw new Error(detail);
            }

            onProgress(80, 'Downloading PDF…');
            const blob = await response.blob();

            // Derive a filename from the Content-Disposition header when present.
            let filename = 'Ziya_Session_Transcript.pdf';
            const disposition = response.headers.get('Content-Disposition');
            const match = disposition && /filename="?([^"]+)"?/.exec(disposition);
            if (match && match[1]) filename = match[1];

            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);

            onProgress(100, 'PDF downloaded');
            message.success('PDF downloaded.');
        } catch (err: any) {
            message.error(err?.message || 'PDF export failed');
        } finally {
            setIsPdfExporting(false);
            setTimeout(() => { setCaptureProgress(0); setCaptureStatus(''); }, 2000);
        }
    };

    const loadExportTargets = async () => {
        try {
            const response = await fetch('/api/export/targets');
            if (response.ok) {
                const data = await response.json();
                setAvailableTargets(data.targets);
            }
        } catch (error) {
            console.error('Error loading export targets:', error);
        }
    };

    const generateExport = async (opts?: { formatOverride?: string }): Promise<string | null> => {
        setIsExporting(true);
        setCaptureProgress(0);

        try {
            let capturedDiagrams: any[] = [];

            if (embedImages) {
                setCaptureStatus('Capturing visualizations...');
                capturedDiagrams = await captureAllVisualizations();
                setCaptureProgress(50);
                setCaptureStatus(`Captured ${capturedDiagrams.length} visualization(s). Generating export...`);
            } else {
                setCaptureProgress(30);
                setCaptureStatus('Generating export...');
            }

            const effectiveFormat = opts?.formatOverride || format;

            const response = await fetch('/api/export-conversation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    conversation_id: currentConversationId,
                    messages: filteredMessages,
                    format: effectiveFormat,
                    target,
                    captured_diagrams: capturedDiagrams
                })
            });

            setCaptureProgress(100);
            setCaptureStatus('Export complete!');

            if (!response.ok) throw new Error('Export failed');

            const data = await response.json();
            setExportedContent(data.content);
            return data.content;
        } catch (error) {
            message.error('Failed to export conversation');
            console.error('Export error:', error);
            return null;
        } finally {
            setIsExporting(false);
            setTimeout(() => {
                setCaptureProgress(0);
                setCaptureStatus('');
            }, 2000);
        }
    };

    const handleCopyToClipboard = async () => {
        // Always generate markdown for clipboard copy
        const content = exportedContent || await generateExport({ formatOverride: 'markdown' });
        if (!content) return;

        try {
            await navigator.clipboard.writeText(content);
            message.success('Copied to clipboard!');
        } catch (error) {
            message.error('Failed to copy to clipboard');
        }
    };

    const handleDownloadFile = async () => {
        const content = exportedContent || await generateExport();
        if (!content) return;

        const ext = format === 'html' ? 'html' : 'md';
        const mimeType = format === 'html' ? 'text/html' : 'text/markdown';
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ziya_conversation_${Date.now()}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        message.success('Downloaded successfully!');
    };

    const handlePasteExport = async () => {
        const content = exportedContent || await generateExport();
        if (!content) return;

        try {
            await navigator.clipboard.writeText(content);
            const serviceInfo = getPasteServiceInfo();
            message.info(`Opening ${serviceInfo.name}...`);
            window.open(serviceInfo.url || 'https://gist.github.com', '_blank');
        } catch (error) {
            message.error('Failed to copy to clipboard');
        }
    };

    const handlePreviewHtml = () => {
        if (!exportedContent || format !== 'html') return;
        const newWindow = window.open('', '_blank');
        if (newWindow) {
            newWindow.document.write(exportedContent);
            newWindow.document.close();
        }
    };

    const getPasteServiceInfo = () => {
        return availableTargets.find(t => t.id === target) || availableTargets[0];
    };

    const serviceInfo = getPasteServiceInfo();

    // Mode-specific footer buttons
    const getFooterButtons = () => {
        const buttons: React.ReactNode[] = [
            <Button key="close" onClick={onClose}>Close</Button>
        ];

        if (isExporting) return buttons;

        if (exportMode === 'copy') {
            if (exportedContent) {
                buttons.push(
                    <Button key="copy-again" icon={<CopyOutlined />} onClick={handleCopyToClipboard}>
                        Copy Again
                    </Button>
                );
            } else {
                buttons.push(
                    <Button
                        key="copy"
                        type="primary"
                        icon={<CopyOutlined />}
                        onClick={handleCopyToClipboard}
                        disabled={currentMessages.length === 0}
                    >
                        Copy Markdown to Clipboard
                    </Button>
                );
            }
        } else if (exportMode === 'pdf') {
            buttons.push(
                <Button key="pdf" type="primary" icon={<FilePdfOutlined />}
                    onClick={handlePdfExport} disabled={currentMessages.length === 0} loading={isPdfExporting}>
                    Export as PDF
                </Button>
            );
        } else if (exportMode === 'download') {
            if (exportedContent) {
                buttons.push(
                    <Button key="download-again" icon={<DownloadOutlined />} onClick={handleDownloadFile}>
                        Download Again
                    </Button>
                );
                if (format === 'html') {
                    buttons.push(
                        <Button key="preview" onClick={handlePreviewHtml}>Preview</Button>
                    );
                }
            } else {
                buttons.push(
                    <Button
                        key="download"
                        type="primary"
                        icon={<DownloadOutlined />}
                        onClick={handleDownloadFile}
                        disabled={currentMessages.length === 0}
                    >
                        Download .{format === 'html' ? 'html' : 'md'} File
                    </Button>
                );
            }
        } else {
            // paste mode
            if (exportedContent) {
                buttons.push(
                    <Button key="copy" icon={<CopyOutlined />} onClick={handleCopyToClipboard}>
                        Copy to Clipboard
                    </Button>,
                    <Button key="download" icon={<DownloadOutlined />} onClick={handleDownloadFile}>
                        Download
                    </Button>,
                    <Button
                        key="copy-open"
                        type="primary"
                        icon={<CopyOutlined />}
                        onClick={handlePasteExport}
                    >
                        Copy & Open {serviceInfo?.name}
                    </Button>
                );
            } else {
                buttons.push(
                    <Button
                        key="generate"
                        type="primary"
                        onClick={() => generateExport()}
                        disabled={currentMessages.length === 0}
                    >
                        Generate Export
                    </Button>
                );
            }
        }

        return buttons;
    };

    // Clear generated content when switching modes / format / options
    useEffect(() => {
        setExportedContent(null);
        setPasteUrl(null);
    }, [exportMode, format, embedImages, target, roundLimit, includeHuman, includeCollapsed]);

    const renderOptions = () => (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {/* Hydration failure: the target conversation's history could not
                be loaded from the server (shell record + server unreachable). */}
            {loadError && useExplicitTarget && (
                <div style={{
                    padding: '8px 12px',
                    background: isDarkMode ? '#3a1a1a' : '#fff2f0',
                    border: `1px solid ${isDarkMode ? '#5c2626' : '#ffccc7'}`,
                    borderRadius: 4,
                    fontSize: 12,
                    color: isDarkMode ? '#ff7875' : '#cf1322',
                }}>
                    ⚠️ Could not load this conversation's history from the server.
                    It may export empty — open the conversation first, then retry.
                </div>
            )}
            {/* ── Scope & Content ────────────────────────────── */}
            <div style={{
                padding: '10px 14px',
                background: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                borderRadius: 6,
                border: `1px solid ${isDarkMode ? '#30363d' : '#d0d7de'}`
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    <FilterOutlined style={{ fontSize: 16, color: '#8c8c8c' }} />
                    <Text strong>Scope & Content</Text>
                </div>

                {/* Round limit */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
                    <MessageOutlined style={{ fontSize: 14, color: '#8c8c8c' }} />
                    <div style={{ flex: 1 }}>
                        <Text style={{ fontSize: 13 }}>Conversation range</Text>
                    </div>
                    <Select
                        size="small"
                        value={roundLimit === null ? 'all' : String(roundLimit)}
                        onChange={(val) => setRoundLimit(val === 'all' ? null : Number(val))}
                        style={{ width: 160 }}
                        options={[
                            { label: `All ${totalRounds} round${totalRounds !== 1 ? 's' : ''}`, value: 'all' },
                            ...[1, 3, 5, 10, 20].filter(n => n < totalRounds).map(n => ({
                                label: `Last ${n} round${n !== 1 ? 's' : ''}`,
                                value: String(n)
                            }))
                        ]}
                    />
                </div>

                {/* Include human messages */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
                    <span style={{ fontSize: 14, width: 14, textAlign: 'center' }}>👤</span>
                    <div style={{ flex: 1 }}>
                        <Text style={{ fontSize: 13 }}>Include your prompts</Text>
                        <div style={{ fontSize: 11, color: '#8c8c8c' }}>
                            {includeHuman
                                ? 'Full conversation — both your messages and AI responses'
                                : 'AI responses only — your prompts will be omitted'}
                        </div>
                    </div>
                    <Switch size="small" checked={includeHuman} onChange={setIncludeHuman} />
                </div>

                {/* Include collapsed content */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <EyeOutlined style={{ fontSize: 14, color: '#8c8c8c' }} />
                    <div style={{ flex: 1 }}>
                        <Text style={{ fontSize: 13 }}>Include collapsed sections</Text>
                        <div style={{ fontSize: 11, color: '#8c8c8c' }}>
                            {includeCollapsed
                                ? 'Everything exported — tool output, reasoning steps, and all details'
                                : 'Collapsed content stripped — only what\'s visible on screen'}
                        </div>
                    </div>
                    <Switch size="small" checked={includeCollapsed} onChange={setIncludeCollapsed} />
                </div>
            </div>

            {/* Image embedding toggle — shown for all modes */}
            <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 14px',
                background: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                borderRadius: 6,
                border: `1px solid ${isDarkMode ? '#30363d' : '#d0d7de'}`
            }}>
                <PictureOutlined style={{ fontSize: 18, color: embedImages ? '#1890ff' : '#8c8c8c' }} />
                <div style={{ flex: 1 }}>
                    <Text strong>Embed rendered images</Text>
                    <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                        {embedImages
                            ? 'Visualizations will be captured from the page and embedded as images'
                            : 'Visualizations will be exported as source code blocks (mermaid, graphviz, etc.)'}
                    </div>
                </div>
                <Switch checked={embedImages} onChange={setEmbedImages} />
            </div>

            {/* Mode-specific options */}
            {exportMode === 'copy' && (
                <Paragraph style={{ fontSize: 12, color: '#8c8c8c', margin: 0 }}>
                    Generates raw Markdown and copies it to your clipboard. Paste into any editor, README, wiki, or document.
                </Paragraph>
            )}

            {exportMode === 'pdf' && (
                <div style={{
                    padding: '12px 14px',
                    background: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    borderRadius: 6,
                    border: `1px solid ${isDarkMode ? '#30363d' : '#d0d7de'}`
                }}>
                    <Text strong>📄 PDF Export</Text>
                    <Paragraph style={{ fontSize: 12, color: '#8c8c8c', margin: '8px 0 0' }}>
                        Captures the conversation exactly as rendered on screen — including syntax highlighting,
                        diagrams, images, math, and diffs — and opens your browser's print dialog.
                        Choose <strong>"Save as PDF"</strong> as the destination to get a PDF file.
                    </Paragraph>
                </div>
            )}

            {exportMode === 'download' && (
                <div>
                    <Text strong style={{ display: 'block', marginBottom: 8 }}>File Format</Text>
                    <Radio.Group value={format} onChange={(e) => setFormat(e.target.value)}>
                        <Space direction="vertical">
                            <Radio value="markdown">
                                Markdown (.md) — portable, editable, works in GitHub / editors
                            </Radio>
                            <Radio value="html">
                                HTML (.html) — standalone file with embedded styles, open in browser
                            </Radio>
                        </Space>
                    </Radio.Group>
                </div>
            )}

            {exportMode === 'paste' && (
                <>
                    <div>
                        <Text strong style={{ display: 'block', marginBottom: 8 }}>Paste Service</Text>
                        <Radio.Group value={target} onChange={(e) => setTarget(e.target.value)} style={{ display: 'block' }}>
                            <Space direction="vertical">
                                {availableTargets.map(t => (
                                    <Radio key={t.id} value={t.id}>
                                        <Space>
                                            {t.icon === 'GithubOutlined' && <GithubOutlined />}
                                            {t.icon === 'CloudOutlined' && <CloudOutlined />}
                                            <span>{t.name}</span>
                                        </Space>
                                    </Radio>
                                ))}
                            </Space>
                        </Radio.Group>
                    </div>
                    <div>
                        <Text strong style={{ display: 'block', marginBottom: 8 }}>Format</Text>
                        <Radio.Group value={format} onChange={(e) => setFormat(e.target.value)}>
                            <Space direction="vertical">
                                <Radio value="markdown">
                                    Markdown (.md) — <strong>Recommended for Gist</strong>
                                </Radio>
                                <Radio value="html">
                                    HTML (.html) — standalone with embedded styles
                                </Radio>
                            </Space>
                        </Radio.Group>
                    </div>
                    {target === 'public' && format === 'markdown' && (
                        <div style={{
                            padding: '8px 12px',
                            background: isDarkMode ? '#1a3a1a' : '#f6ffed',
                            border: `1px solid ${isDarkMode ? '#274d27' : '#b7eb8f'}`,
                            borderRadius: 4,
                            fontSize: 12
                        }}>
                            💡 <strong>Tip:</strong> Name your Gist file with a <code>.md</code> extension for proper rendering.
                        </div>
                    )}
                </>
            )}
        </Space>
    );

    const renderExportedPreview = () => (
        <div>
            <Text type="success" strong>✓ Export Ready</Text>
            <Paragraph style={{ marginTop: 8 }}>
                {exportMode === 'copy' && 'Markdown has been copied to your clipboard.'}
                {exportMode === 'download' && 'Your file has been downloaded.'}
                {exportMode === 'paste' && `Content is ready for ${serviceInfo?.name}. Click "Copy & Open" to proceed.`}
            </Paragraph>

            <div style={{
                padding: 12,
                background: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                borderRadius: 6,
                border: `1px solid ${isDarkMode ? '#30363d' : '#d0d7de'}`,
                maxHeight: 250,
                overflow: 'auto'
            }}>
                <pre style={{
                    margin: 0,
                    fontSize: 11,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    fontFamily: 'monospace'
                }}>
                    {exportedContent!.substring(0, 1500)}
                    {exportedContent!.length > 1500 && '\n\n... (truncated preview)'}
                </pre>
            </div>

            <Paragraph style={{ marginTop: 12, fontSize: 12, color: '#8c8c8c' }}>
                <strong>Size:</strong> {(exportedContent!.length / 1024).toFixed(1)} KB •
                <strong> Messages:</strong> {currentMessages.filter(m => m.content?.trim()).length}
                {embedImages && <> • <strong>Images:</strong> embedded</>}
            </Paragraph>
        </div>
    );

    const renderProgress = () => (
        <div style={{ textAlign: 'center', padding: '40px 20px' }}>
            <Progress
                percent={captureProgress}
                status={captureProgress === 100 ? 'success' : 'active'}
            />
            <p style={{ marginTop: 16, color: '#8c8c8c' }}>{captureStatus}</p>
        </div>
    );

    return (
        <Modal
            title="Export Conversation"
            open={visible}
            onCancel={onClose}
            width={700}
            footer={getFooterButtons()}
        >
            {/* Mode selector */}
            <Segmented
                block
                value={exportMode}
                onChange={(val) => setExportMode(val as ExportMode)}
                options={[
                    { label: '📋 Copy to Clipboard', value: 'copy' },
                    { label: '💾 Download File', value: 'download' },
                    { label: '📄 Export as PDF', value: 'pdf' },
                    { label: '🔗 Paste Service', value: 'paste' },
                ]}
                style={{ marginBottom: 20 }}
            />

            {isExporting || isPdfExporting
                ? renderProgress()
                : exportedContent
                    ? renderExportedPreview()
                    : renderOptions()
            }

            {/* Note about what's included */}
            {!exportedContent && !isExporting && (
                <>
                    <Divider style={{ margin: '16px 0 12px' }} />
                    <Paragraph style={{ fontSize: 12, color: '#8c8c8c', margin: 0 }}>
                        <strong>Includes:</strong>{' '}
                        {roundLimit !== null ? `last ${roundLimit} round${roundLimit !== 1 ? 's' : ''}` : 'all rounds'}
                        {!includeHuman && ' (AI responses only)'}
                        {' '}with formatting, code blocks, diffs,
                        {embedImages ? ' embedded rendered visualizations,' : ' visualization source code,'}
                        {!includeCollapsed && ' excluding collapsed sections,'}
                        {' '}and metadata footer.
                    </Paragraph>
                </>
            )}
        </Modal>
    );
};

export default ExportConversationModal;
