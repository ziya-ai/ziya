/**
 * FileChip — compact clickable indicator for an attached document or image.
 *
 * Renders as a small pill with an icon and filename.  Clicking opens a
 * preview modal showing the full content.
 */
import React, { useState } from 'react';
import { Modal, Button, Tooltip } from 'antd';
import {
    FileTextOutlined,
    FilePdfOutlined, FileWordOutlined, FileExcelOutlined, FileImageOutlined,
    FilePptOutlined, FileOutlined, DeleteOutlined, ConsoleSqlOutlined,
} from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import type { DocumentAttachment, ImageAttachment } from '../utils/types';
import { rtfToHtml } from '../utils/rtfToHtml';

// ── Language badge ────────────────────────────────────────────────────────
// Colored 2–3 letter badges for common source file types.

const LANG_BADGE: Record<string, { label: string; bg: string }> = {
    py:    { label: 'PY',  bg: '#3776AB' },
    js:    { label: 'JS',  bg: '#F7DF1E' },
    mjs:   { label: 'JS',  bg: '#F7DF1E' },
    cjs:   { label: 'JS',  bg: '#F7DF1E' },
    jsx:   { label: 'JSX', bg: '#61DAFB' },
    ts:    { label: 'TS',  bg: '#3178C6' },
    tsx:   { label: 'TSX', bg: '#3178C6' },
    go:    { label: 'GO',  bg: '#00ADD8' },
    rs:    { label: 'RS',  bg: '#DEA584' },
    rb:    { label: 'RB',  bg: '#CC342D' },
    java:  { label: 'JV',  bg: '#B07219' },
    kt:    { label: 'KT',  bg: '#A97BFF' },
    kts:   { label: 'KT',  bg: '#A97BFF' },
    scala: { label: 'SC',  bg: '#DC322F' },
    swift: { label: 'SW',  bg: '#F05138' },
    c:     { label: 'C',   bg: '#555555' },
    cpp:   { label: 'C+',  bg: '#00599C' },
    cc:    { label: 'C+',  bg: '#00599C' },
    cxx:   { label: 'C+',  bg: '#00599C' },
    h:     { label: 'H',   bg: '#6A737D' },
    hpp:   { label: 'H+',  bg: '#00599C' },
    cs:    { label: 'C#',  bg: '#68217A' },
    php:   { label: 'PHP', bg: '#777BB4' },
    lua:   { label: 'LUA', bg: '#000080' },
    r:     { label: 'R',   bg: '#276DC3' },
    pl:    { label: 'PL',  bg: '#39457E' },
    ex:    { label: 'EX',  bg: '#6E4A7E' },
    exs:   { label: 'EX',  bg: '#6E4A7E' },
    hs:    { label: 'HS',  bg: '#5D4F85' },
    html:  { label: '</>',  bg: '#E34F26' },
    htm:   { label: '</>',  bg: '#E34F26' },
    css:   { label: 'CSS', bg: '#1572B6' },
    scss:  { label: 'CSS', bg: '#CD6799' },
    sass:  { label: 'CSS', bg: '#CD6799' },
    less:  { label: 'CSS', bg: '#1D365D' },
    json:  { label: '{ }', bg: '#5B5B5B' },
    yaml:  { label: 'YML', bg: '#CB171E' },
    yml:   { label: 'YML', bg: '#CB171E' },
    toml:  { label: 'TML', bg: '#9C4121' },
    md:    { label: 'MD',  bg: '#083FA1' },
    mdx:   { label: 'MD',  bg: '#083FA1' },
    sql:   { label: 'SQL', bg: '#336791' },
    sh:    { label: '$_',  bg: '#4EAA25' },
    bash:  { label: '$_',  bg: '#4EAA25' },
    zsh:   { label: '$_',  bg: '#4EAA25' },
    fish:  { label: '$_',  bg: '#4EAA25' },
    xml:   { label: '</>',  bg: '#0060AC' },
    svg:   { label: 'SVG', bg: '#FFB13B' },
    tf:    { label: 'TF',  bg: '#623CE4' },
    hcl:   { label: 'HCL', bg: '#623CE4' },
    proto: { label: 'PB',  bg: '#4285F4' },
    graphql: { label: 'GQL', bg: '#E535AB' },
    gql:   { label: 'GQL', bg: '#E535AB' },
    rtf:   { label: 'RTF', bg: '#185ABD' },
};

const LangBadge: React.FC<{ label: string; bg: string }> = ({ label, bg }) => {
    const dark = label === 'JS' || label === 'SVG'; // dark text on bright backgrounds
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 22, height: 16, borderRadius: 3, fontSize: 9, fontWeight: 700,
            fontFamily: 'system-ui, sans-serif', letterSpacing: -0.3,
            background: bg, color: dark ? '#1a1a1a' : '#fff', flexShrink: 0,
        }}>{label}</span>
    );
};

function docIcon(type: string) {
    switch (type) {
        case 'pdf': return <FilePdfOutlined style={{ color: '#cf1322', fontSize: 18 }} />;
        case 'doc':
        case 'docx': return <FileWordOutlined style={{ color: '#1677ff', fontSize: 18 }} />;
        case 'xls':
        case 'xlsx': return <FileExcelOutlined style={{ color: '#389e0d', fontSize: 18 }} />;
        case 'ppt':
        case 'pptx': return <FilePptOutlined style={{ color: '#d46b08', fontSize: 18 }} />;
        case 'rtf': return <FileTextOutlined style={{ color: '#185ABD', fontSize: 18 }} />;
        case 'png': case 'jpg': case 'jpeg': case 'gif': case 'webp': case 'bmp':
            return <FileImageOutlined style={{ color: '#722ed1', fontSize: 18 }} />;
        default: {
            const badge = LANG_BADGE[type];
            if (badge) return <LangBadge label={badge.label} bg={badge.bg} />;
            return <FileOutlined style={{ fontSize: 18 }} />;
        }
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
                doc.type === 'rtf' ? (
                <div
                    dangerouslySetInnerHTML={{ __html: rtfToHtml(doc.text) }}
                    style={{
                        fontSize: 13,
                        lineHeight: 1.6,
                        fontFamily: 'Georgia, "Times New Roman", serif',
                        padding: 16,
                        borderRadius: 6,
                        background: isDarkMode ? '#1a1a1a' : '#fff',
                        color: isDarkMode ? '#d4d4d4' : '#333',
                        maxHeight: '60vh',
                        overflow: 'auto',
                        border: `1px solid ${isDarkMode ? '#333' : '#e8e8e8'}`,
                    }}
                />
                ) : (
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
                )
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
