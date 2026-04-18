/**
 * FileChip — compact clickable indicator for an attached document or image.
 *
 * Renders as a small pill with an icon and filename.  Clicking opens a
 * preview modal showing the full content.
 */
import React, { useState } from 'react';
import { Modal, Button, Tooltip } from 'antd';
import {
    FilePdfOutlined, FileWordOutlined, FileExcelOutlined,
    FilePptOutlined, FileOutlined, DeleteOutlined,
} from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import type { DocumentAttachment, ImageAttachment } from '../utils/types';

function docIcon(type: string) {
    switch (type) {
        case 'pdf': return <FilePdfOutlined style={{ color: '#cf1322', fontSize: 18 }} />;
        case 'doc':
        case 'docx': return <FileWordOutlined style={{ color: '#1677ff', fontSize: 18 }} />;
        case 'xls':
        case 'xlsx': return <FileExcelOutlined style={{ color: '#389e0d', fontSize: 18 }} />;
        case 'ppt':
        case 'pptx': return <FilePptOutlined style={{ color: '#d46b08', fontSize: 18 }} />;
        default: return <FileOutlined style={{ fontSize: 18 }} />;
    }
}

// ── Preview modal ─────────────────────────────────────────────────────────

const FilePreviewModal: React.FC<{
    open: boolean;
    onClose: () => void;
    doc?: DocumentAttachment;
    image?: ImageAttachment;
}> = ({ open, onClose, doc, image }) => {
    const { isDarkMode } = useTheme();
    const title = doc?.filename ?? image?.filename ?? 'Preview';

    return (
        <Modal
            open={open}
            onCancel={onClose}
            title={title}
            footer={<Button onClick={onClose}>Close</Button>}
            width={720}
            styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}
        >
            {doc?.text && (
                <pre style={{
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    fontSize: 13,
                    lineHeight: 1.6,
                    fontFamily: 'monospace',
                    padding: 12,
                    borderRadius: 6,
                    background: isDarkMode ? '#1a1a1a' : '#f5f5f5',
                    color: isDarkMode ? '#d4d4d4' : '#333',
                    maxHeight: '60vh',
                    overflow: 'auto',
                }}>
                    {doc.text}
                </pre>
            )}
            {doc?.pageImages && doc.pageImages.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center' }}>
                    {doc.pageImages.map((img, i) => (
                        <img
                            key={i}
                            src={`data:${img.mediaType};base64,${img.data}`}
                            alt={`Page ${i + 1}`}
                            style={{ maxWidth: '100%', borderRadius: 4, border: '1px solid #d9d9d9' }}
                        />
                    ))}
                </div>
            )}
            {image && (
                <div style={{ textAlign: 'center' }}>
                    <img
                        src={`data:${image.mediaType};base64,${image.data}`}
                        alt={image.filename || 'Image'}
                        style={{ maxWidth: '100%', maxHeight: '65vh', borderRadius: 4 }}
                    />
                </div>
            )}
        </Modal>
    );
};

// ── Document chip ─────────────────────────────────────────────────────────

interface DocChipProps {
    doc: DocumentAttachment;
    onRemove?: () => void;
    inline?: boolean;
}

export const DocumentChip: React.FC<DocChipProps> = ({ doc, onRemove, inline }) => {
    const { isDarkMode } = useTheme();
    const [previewOpen, setPreviewOpen] = useState(false);

    const isScanned = !doc.text && doc.pageImages && doc.pageImages.length > 0;
    const summary = isScanned
        ? `${doc.pageImages!.length} page(s)`
        : `${(doc.chars / 1000).toFixed(1)}k chars`;

    return (
        <>
            <span
                onClick={() => setPreviewOpen(true)}
                style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: inline ? '4px 10px' : '6px 12px',
                    borderRadius: 8,
                    fontSize: 14,
                    background: isDarkMode ? '#1f1f1f' : '#f0f0f0',
                    border: `1px solid ${isDarkMode ? '#333' : '#d9d9d9'}`,
                    cursor: 'pointer',
                    userSelect: 'none',
                    maxWidth: 260,
                    whiteSpace: 'nowrap',
                }}
            >
                {docIcon(doc.type)}
                <span style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    fontWeight: 500,
                }}>{doc.filename}</span>
                <span style={{
                    fontSize: 12,
                    opacity: 0.6,
                    flexShrink: 0,
                }}>{summary}</span>
                {onRemove && !inline && (
                    <Tooltip title="Remove">
                        <DeleteOutlined
                            onClick={(e) => { e.stopPropagation(); onRemove(); }}
                            style={{ fontSize: 12, opacity: 0.5, marginLeft: 2 }}
                        />
                    </Tooltip>
                )}
            </span>
            <FilePreviewModal
                open={previewOpen}
                onClose={() => setPreviewOpen(false)}
                doc={doc}
            />
        </>
    );
};

// ── Image chip ────────────────────────────────────────────────────────────

interface ImageChipProps {
    image: ImageAttachment;
    onRemove?: () => void;
    inline?: boolean;
}

export const ImageChip: React.FC<ImageChipProps> = ({ image, onRemove, inline }) => {
    const { isDarkMode } = useTheme();
    const [previewOpen, setPreviewOpen] = useState(false);

    return (
        <>
            <span
                onClick={() => setPreviewOpen(true)}
                style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: inline ? '2px 8px' : '4px 10px',
                    borderRadius: 6,
                    fontSize: inline ? 12 : 13,
                    background: isDarkMode ? '#1f1f1f' : '#f0f0f0',
                    border: `1px solid ${isDarkMode ? '#333' : '#d9d9d9'}`,
                    cursor: 'pointer',
                    userSelect: 'none',
                    maxWidth: 260,
                    whiteSpace: 'nowrap',
                }}
            >
                <img
                    src={`data:${image.mediaType};base64,${image.data}`}
                    alt=""
                    style={{ width: 18, height: 18, objectFit: 'cover', borderRadius: 3 }}
                />
                <span style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    fontWeight: 500,
                }}>{image.filename || 'image'}</span>
                {onRemove && !inline && (
                    <Tooltip title="Remove">
                        <DeleteOutlined
                            onClick={(e) => { e.stopPropagation(); onRemove(); }}
                            style={{ fontSize: 12, opacity: 0.5, marginLeft: 2 }}
                        />
                    </Tooltip>
                )}
            </span>
            <FilePreviewModal
                open={previewOpen}
                onClose={() => setPreviewOpen(false)}
                image={image}
            />
        </>
    );
};
