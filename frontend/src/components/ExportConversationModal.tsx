import React, { useState, useEffect } from 'react';
import { Modal, Radio, Button, message, Space, Typography, Divider, Progress, Switch, Segmented } from 'antd';
import { CopyOutlined, DownloadOutlined, GithubOutlined, CloudOutlined, FileTextOutlined, LinkOutlined, PictureOutlined } from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';
import { useTheme } from '../context/ThemeContext';
import { captureAllVisualizations } from '../utils/visualizationCapture';

const { Text, Paragraph } = Typography;

type ExportMode = 'copy' | 'download' | 'paste';

interface ExportConversationModalProps {
    visible: boolean;
    onClose: () => void;
}

const ExportConversationModal: React.FC<ExportConversationModalProps> = ({ visible, onClose }) => {
    const [exportMode, setExportMode] = useState<ExportMode>('copy');
    const [format, setFormat] = useState<'markdown' | 'html'>('markdown');
    const [embedImages, setEmbedImages] = useState(false);
    const [target, setTarget] = useState<'public' | 'internal'>('public');
    const [isExporting, setIsExporting] = useState(false);
    const [exportedContent, setExportedContent] = useState<string | null>(null);
    const [captureProgress, setCaptureProgress] = useState<number>(0);
    const [captureStatus, setCaptureStatus] = useState<string>('');
    const [availableTargets, setAvailableTargets] = useState<any[]>([
        {
            id: 'public',
            name: 'GitHub Gist',
            url: 'https://gist.github.com',
            icon: 'GithubOutlined',
            description: 'Public paste service with markdown support'
        }
    ]);
    const { currentConversationId, currentMessages } = useChatContext();
    const { isDarkMode } = useTheme();

    useEffect(() => {
        if (!visible) {
            setExportedContent(null);
            setIsExporting(false);
            setCaptureProgress(0);
            setCaptureStatus('');
            setExportMode('copy');
            setFormat('markdown');
            setEmbedImages(false);
            setTarget('public');
        } else {
            loadExportTargets();
        }
    }, [visible]);

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
                    messages: currentMessages,
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
    }, [exportMode, format, embedImages, target]);

    const renderOptions = () => (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
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
                    { label: '🔗 Paste Service', value: 'paste' },
                ]}
                style={{ marginBottom: 20 }}
            />

            {isExporting
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
                        <strong>Includes:</strong> all conversation messages with formatting, code blocks, diffs,
                        {embedImages ? ' embedded rendered visualizations,' : ' visualization source code,'}
                        {' '}and metadata footer.
                    </Paragraph>
                </>
            )}
        </Modal>
    );
};

export default ExportConversationModal;
