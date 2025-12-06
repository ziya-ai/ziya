import React, { useState, useEffect } from 'react';
import { Modal, Radio, Button, message, Space, Typography, Divider, Progress } from 'antd';
import { CopyOutlined, DownloadOutlined, GithubOutlined, CloudOutlined } from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';
import { useTheme } from '../context/ThemeContext';
import { captureAllVisualizations } from '../utils/visualizationCapture';

const { Text, Paragraph } = Typography;

interface ExportConversationModalProps {
    visible: boolean;
    onClose: () => void;
}

const ExportConversationModal: React.FC<ExportConversationModalProps> = ({ visible, onClose }) => {
    const [format, setFormat] = useState<'markdown' | 'html'>('markdown');
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

    // Reset state when modal closes
    useEffect(() => {
        if (!visible) {
            setExportedContent(null);
            setIsExporting(false);
            setCaptureProgress(0);
            setCaptureStatus('');
            setFormat('markdown');
            setTarget('public');
        } else {
            // Load available targets when modal opens
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

    const handleExport = async () => {
        setIsExporting(true);
        setCaptureProgress(0);
        setCaptureStatus('Capturing visualizations...');
        
        try {
            // Step 1: Capture all visualizations from the DOM
            const capturedDiagrams = await captureAllVisualizations();
            setCaptureProgress(50);
            setCaptureStatus(`Captured ${capturedDiagrams.length} visualization(s). Generating export...`);
            
            // Step 2: Export conversation with captured diagrams
            const response = await fetch('/api/export-conversation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    conversation_id: currentConversationId,
                    messages: currentMessages,
                    format,
                    target,
                    captured_diagrams: capturedDiagrams
                })
            });
            
            setCaptureProgress(100);
            setCaptureStatus('Export complete!');

            if (!response.ok) {
                throw new Error('Export failed');
            }

            const data = await response.json();
            setExportedContent(data.content);
            
            const successMsg = data.diagrams_count > 0 
                ? `Conversation exported with ${data.diagrams_count} embedded visualization(s)!`
                : 'Conversation exported successfully!';
            message.success(successMsg);
        } catch (error) {
            message.error('Failed to export conversation');
            console.error('Export error:', error);
        } finally {
            setIsExporting(false);
            setTimeout(() => {
                setCaptureProgress(0);
                setCaptureStatus('');
            }, 2000);
        }
    };

    const copyToClipboard = async () => {
        if (!exportedContent) return;

        try {
            await navigator.clipboard.writeText(exportedContent);
            message.success('Copied to clipboard!');
        } catch (error) {
            message.error('Failed to copy to clipboard');
        }
    };

    const downloadFile = () => {
        if (!exportedContent) return;

        const blob = new Blob([exportedContent], { 
            type: format === 'html' ? 'text/html' : 'text/markdown' 
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ziya_conversation_${Date.now()}.${format === 'html' ? 'html' : 'md'}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        message.success('Downloaded successfully!');
    };

    const openInNewWindow = () => {
        if (!exportedContent || format !== 'html') return;

        const newWindow = window.open('', '_blank');
        if (newWindow) {
            newWindow.document.write(exportedContent);
            newWindow.document.close();
        }
    };

    const openPasteService = () => {
        const serviceInfo = getPasteServiceInfo();
        const url = serviceInfo?.url || 'https://gist.github.com';
        window.open(url, '_blank');
    };

    const getPasteServiceInfo = () => {
        return availableTargets.find(t => t.id === target) || availableTargets[0];
    };

    const serviceInfo = getPasteServiceInfo();

    return (
        <Modal
            title="Export Conversation"
            open={visible}
            onCancel={onClose}
            width={700}
            footer={[
                <Button key="close" onClick={onClose}>
                    Close
                </Button>,
                exportedContent && (
                    <Button
                        key="copy"
                        icon={<CopyOutlined />}
                        onClick={copyToClipboard}
                    >
                        Copy to Clipboard
                    </Button>
                ),
                exportedContent && (
                    <Button
                        key="download"
                        icon={<DownloadOutlined />}
                        onClick={downloadFile}
                    >
                        Download
                    </Button>
                ),
                exportedContent && format === 'html' && (
                    <Button
                        key="preview"
                        onClick={openInNewWindow}
                    >
                        Preview
                    </Button>
                ),
                exportedContent ? (
                    <Button
                        key="copy-and-open"
                        type="primary"
                        icon={<CopyOutlined />}
                        onClick={() => { copyToClipboard(); message.info('Opening ' + serviceInfo.name + '...'); openPasteService(); }}
                    >
                        Copy & Open {serviceInfo.name}
                    </Button>
                ) : (
                    <Button
                        key="export"
                        type="primary"
                        loading={isExporting}
                        onClick={handleExport}
                        disabled={currentMessages.length === 0}
                    >
                        Generate Export
                    </Button>
                )
            ]}
        >
            {!exportedContent ? (
                isExporting ? (
                    <div style={{ textAlign: 'center', padding: '40px 20px' }}>
                        <Progress 
                            percent={captureProgress} 
                            status={captureProgress === 100 ? 'success' : 'active'}
                        />
                        <p style={{ marginTop: 16, color: '#57606a' }}>
                            {captureStatus}
                        </p>
                    </div>
                ) : (
                <div>
                    <Space direction="vertical" style={{ width: '100%' }} size="large">
                        <div>
                            <Text strong>Target Paste Service</Text>
                            <Radio.Group
                                value={target}
                                onChange={(e) => setTarget(e.target.value)}
                                style={{ marginTop: 8, display: 'block' }}
                            >
                                <Space direction="vertical">
                                    {availableTargets.map(target => (
                                        <Radio key={target.id} value={target.id}>
                                        <Space>
                                                {target.icon === 'GithubOutlined' && <GithubOutlined />}
                                                {target.icon === 'CloudOutlined' && <CloudOutlined />}
                                                <span>{target.name}</span>
                                        </Space>
                                    </Radio>
                                    ))}
                                </Space>
                            </Radio.Group>
                            <Radio.Group
                                value={format}
                                onChange={(e) => setFormat(e.target.value)}
                                style={{ marginTop: 8, display: 'block' }}
                            >
                                <Space direction="vertical">
                                    <Radio value="markdown">
                                        Markdown (.md) - <strong>Recommended for GitHub Gist</strong>, preserves formatting
                                    </Radio>
                                    <Radio value="html">
                                        HTML (.html) - Standalone file with embedded styles
                                    </Radio>
                                </Space>
                            </Radio.Group>
                        </div>
                        
                        {target === 'public' && format === 'markdown' && (
                            <div style={{
                                padding: '8px 12px',
                                background: isDarkMode ? '#1a3a1a' : '#f6ffed',
                                border: `1px solid ${isDarkMode ? '#274d27' : '#b7eb8f'}`,
                                borderRadius: '4px',
                                fontSize: '12px'
                            }}>
                                💡 <strong>Tip:</strong> When creating your Gist, name the file with a <code>.md</code> extension (e.g., <code>conversation.md</code>) for proper markdown rendering.
                            </div>
                        )}

                        <Divider style={{ margin: '12px 0' }} />

                        <div style={{
                            padding: '12px',
                            background: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                            borderRadius: '6px',
                            border: `1px solid ${isDarkMode ? '#30363d' : '#d0d7de'}`
                        }}>
                            <Space>
                                {serviceInfo.icon}
                                <Text strong>{serviceInfo.name}</Text>
                            </Space>
                            <Paragraph style={{ marginTop: 8, marginBottom: 8, fontSize: '13px' }}>
                                {serviceInfo.description}
                            </Paragraph>
                            <Button
                                type="link"
                                size="small"
                                onClick={openPasteService}
                                style={{ padding: 0 }}
                            >
                                Open {serviceInfo.name} →
                            </Button>
                        </div>
                        
                        <Paragraph style={{ fontSize: '12px', color: '#57606a', marginTop: 8 }}>
                            <strong>Note:</strong> This export will include:
                            • All conversation messages with formatting
                            • Embedded rendered visualizations (diagrams, charts)
                            • Source code for all visualizations
                            • Metadata footer with Ziya version and model info
                        </Paragraph>
                    </Space>
                </div>
                )
            ) : (
                <div>
                    <Text type="success" strong>✓ Export Ready</Text>
                    <Paragraph style={{ marginTop: 8 }}>
                        Your conversation has been formatted for <strong>{serviceInfo.name}</strong>.
                        Click "Copy to Clipboard" to copy the content and automatically open {serviceInfo.name}.
                    </Paragraph>
                    
                    <div style={{
                        padding: '12px',
                        background: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                        borderRadius: '6px',
                        border: `1px solid ${isDarkMode ? '#30363d' : '#d0d7de'}`,
                        maxHeight: '300px',
                        overflow: 'auto'
                    }}>
                        <pre style={{ 
                            margin: 0, 
                            fontSize: '11px',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                            fontFamily: 'monospace'
                        }}>
                            {exportedContent.substring(0, 1000)}
                            {exportedContent.length > 1000 && '\n\n... (truncated preview)'}
                        </pre>
                    </div>
                    
                    <Paragraph style={{ marginTop: 12, fontSize: '12px', color: '#57606a' }}>
                        <strong>Size:</strong> {(exportedContent.length / 1024).toFixed(1)} KB • 
                        <strong> Messages:</strong> {currentMessages.filter(m => m.content?.trim()).length}
                    </Paragraph>
                </div>
            )}
        </Modal>
    );
};

export default ExportConversationModal;
