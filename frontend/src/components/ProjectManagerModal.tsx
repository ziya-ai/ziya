import React, { useState, useEffect, useRef } from 'react';
import { Modal, Button, Input, Tag, Space, message, Divider, Alert, Collapse, Empty, Popconfirm, Select, Tooltip, List } from 'antd';
import {
    DeleteOutlined, SettingOutlined, MergeCellsOutlined,
    EditOutlined, CheckOutlined, CloseOutlined,
    FolderOutlined, ExclamationCircleOutlined
} from '@ant-design/icons';
import { useProject } from '../context/ProjectContext';
import { useTheme } from '../context/ThemeContext';
import { useChatContext } from '../context/ChatContext';
import { WritePolicy } from '../types/project';

const { Panel } = Collapse;

interface ProjectManagerModalProps {
    visible: boolean;
    onClose: () => void;
}

const ProjectManagerModal: React.FC<ProjectManagerModalProps> = ({ visible, onClose }) => {
    const { currentProject, projects, switchProject, updateProject, deleteProject, mergeProjects, refreshProjects } = useProject();
    const { conversations } = useChatContext();
    const { isDarkMode } = useTheme();

    const [editingId, setEditingId] = useState<string | null>(null);
    const [editValue, setEditValue] = useState('');
    const [settingsId, setSettingsId] = useState<string | null>(null);
    const [mergeSourceId, setMergeSourceId] = useState<string | null>(null);
    const [mergeTargetId, setMergeTargetId] = useState<string | null>(null);
    const [isProcessing, setIsProcessing] = useState(false);
    const editInputRef = useRef<any>(null);

    // Write policy state for the project being edited
    const [writePolicy, setWritePolicy] = useState<WritePolicy>({});
    const [newPattern, setNewPattern] = useState('');
    const [newWritePath, setNewWritePath] = useState('');

    useEffect(() => {
        if (visible) refreshProjects();
    }, [visible]);

    useEffect(() => {
        if (editingId && editInputRef.current) {
            setTimeout(() => editInputRef.current?.focus(), 50);
        }
    }, [editingId]);

    // Load write policy when settings panel opens
    useEffect(() => {
        if (settingsId) {
            const loadPolicy = async () => {
                try {
                    const response = await fetch(`/api/mcp/write-policy/${settingsId}`);
                    if (response.ok) {
                        const data = await response.json();
                        setWritePolicy(data.policy || {});
                    }
                } catch (error) {
                    console.error('Failed to load write policy:', error);
                }
            };
            loadPolicy();
        }
    }, [settingsId]);

    const getConversationCount = (projectId: string): number => {
        return conversations.filter(c => c.projectId === projectId && c.isActive !== false).length;
    };

    const handleRename = async (projectId: string) => {
        if (!editValue.trim()) {
            message.error('Name cannot be empty');
            return;
        }
        try {
            await updateProject(projectId, { name: editValue.trim() });
            message.success('Project renamed');
            setEditingId(null);
        } catch (error) {
            message.error('Failed to rename project');
        }
    };

    const handleDelete = async (projectId: string) => {
        setIsProcessing(true);
        try {
            await deleteProject(projectId);
            message.success('Project deleted');
        } catch (error: any) {
            message.error(error.message || 'Failed to delete project');
        } finally {
            setIsProcessing(false);
        }
    };

    const handleMerge = async () => {
        if (!mergeSourceId || !mergeTargetId) return;
        setIsProcessing(true);
        try {
            await mergeProjects(mergeSourceId, mergeTargetId);
            const targetName = projects.find(p => p.id === mergeTargetId)?.name || 'target';
            message.success(`Merged into "${targetName}"`);
            setMergeSourceId(null);
            setMergeTargetId(null);
        } catch (error: any) {
            message.error(error.message || 'Failed to merge projects');
        } finally {
            setIsProcessing(false);
        }
    };

    const handleSaveWritePolicy = async () => {
        if (!settingsId) return;
        setIsProcessing(true);
        try {
            const response = await fetch('/api/mcp/write-policy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: settingsId,
                    allowed_write_patterns: writePolicy.allowed_write_patterns,
                    safe_write_paths: writePolicy.safe_write_paths,
                })
            });
            if (response.ok) {
                message.success('Write policy saved');
                setSettingsId(null);
            } else {
                message.error('Failed to save write policy');
            }
        } catch (error) {
            message.error('Failed to save write policy');
        } finally {
            setIsProcessing(false);
        }
    };

    const cardStyle = (isActive: boolean) => ({
        padding: 16,
        marginBottom: 12,
        borderRadius: 8,
        border: `1px solid ${isActive
            ? (isDarkMode ? '#177ddc' : '#1890ff')
            : (isDarkMode ? '#303030' : '#e8e8e8')}`,
        backgroundColor: isActive
            ? (isDarkMode ? '#111d2c' : '#e6f7ff')
            : (isDarkMode ? '#1f1f1f' : '#fafafa'),
    });

    // Settings sub-view for a project
    if (settingsId) {
        const project = projects.find(p => p.id === settingsId);
        return (
            <Modal
                title={`Project Settings — ${project?.name || 'Unknown'}`}
                open={visible}
                onCancel={() => setSettingsId(null)}
                width={600}
                footer={[
                    <Button key="cancel" onClick={() => setSettingsId(null)}>Cancel</Button>,
                    <Button key="save" type="primary" loading={isProcessing} onClick={handleSaveWritePolicy}>
                        Save Write Policy
                    </Button>
                ]}
            >
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                    <Alert
                        message="Project Write Policy"
                        description="Controls which files the shell can write to within this project. These patterns are merged with the global write policy."
                        type="info"
                        showIcon
                    />

                    <div>
                        <strong>Allowed Write Patterns</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            Glob patterns for files the shell may write within this project.
                            Examples: <code>*.md</code>, <code>docs/**</code>, <code>tracker/*.json</code>
                        </div>
                        <Input.Group compact style={{ marginBottom: 8 }}>
                            <Input
                                placeholder="*.md or docs/**"
                                value={newPattern}
                                onChange={e => setNewPattern(e.target.value)}
                                onPressEnter={() => {
                                    const v = newPattern.trim();
                                    if (v && !(writePolicy.allowed_write_patterns || []).includes(v)) {
                                        setWritePolicy(prev => ({
                                            ...prev,
                                            allowed_write_patterns: [...(prev.allowed_write_patterns || []), v]
                                        }));
                                        setNewPattern('');
                                    }
                                }}
                                style={{ width: 'calc(100% - 80px)' }}
                            />
                            <Button type="primary" style={{ width: 80 }} onClick={() => {
                                const v = newPattern.trim();
                                if (v && !(writePolicy.allowed_write_patterns || []).includes(v)) {
                                    setWritePolicy(prev => ({
                                        ...prev,
                                        allowed_write_patterns: [...(prev.allowed_write_patterns || []), v]
                                    }));
                                    setNewPattern('');
                                }
                            }}>Add</Button>
                        </Input.Group>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {(writePolicy.allowed_write_patterns || []).map(p => (
                                <Tag key={p} closable color="purple" onClose={() =>
                                    setWritePolicy(prev => ({
                                        ...prev,
                                        allowed_write_patterns: (prev.allowed_write_patterns || []).filter(x => x !== p)
                                    }))
                                }>{p}</Tag>
                            ))}
                            {(writePolicy.allowed_write_patterns || []).length === 0 && (
                                <span style={{ color: '#999', fontSize: 12, fontStyle: 'italic' }}>
                                    No project-specific patterns — only global policy applies
                                </span>
                            )}
                        </div>
                    </div>

                    <Divider style={{ margin: '8px 0' }} />

                    <div>
                        <strong>Additional Safe Write Paths</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            Path prefixes writable within this project (added to global defaults like <code>.ziya/</code>, <code>/tmp/</code>).
                        </div>
                        <Input.Group compact style={{ marginBottom: 8 }}>
                            <Input
                                placeholder="build/ or .cache/"
                                value={newWritePath}
                                onChange={e => setNewWritePath(e.target.value)}
                                onPressEnter={() => {
                                    const v = newWritePath.trim();
                                    if (v && !(writePolicy.safe_write_paths || []).includes(v)) {
                                        setWritePolicy(prev => ({
                                            ...prev,
                                            safe_write_paths: [...(prev.safe_write_paths || []), v]
                                        }));
                                        setNewWritePath('');
                                    }
                                }}
                                style={{ width: 'calc(100% - 80px)' }}
                            />
                            <Button type="primary" style={{ width: 80 }} onClick={() => {
                                const v = newWritePath.trim();
                                if (v && !(writePolicy.safe_write_paths || []).includes(v)) {
                                    setWritePolicy(prev => ({
                                        ...prev,
                                        safe_write_paths: [...(prev.safe_write_paths || []), v]
                                    }));
                                    setNewWritePath('');
                                }
                            }}>Add</Button>
                        </Input.Group>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {(writePolicy.safe_write_paths || []).map(p => (
                                <Tag key={p} closable color="green" onClose={() =>
                                    setWritePolicy(prev => ({
                                        ...prev,
                                        safe_write_paths: (prev.safe_write_paths || []).filter(x => x !== p)
                                    }))
                                }>{p}</Tag>
                            ))}
                            {(writePolicy.safe_write_paths || []).length === 0 && (
                                <span style={{ color: '#999', fontSize: 12, fontStyle: 'italic' }}>
                                    Using global defaults only
                                </span>
                            )}
                        </div>
                    </div>
                </Space>
            </Modal>
        );
    }

    // Merge sub-view
    if (mergeSourceId) {
        const sourceProject = projects.find(p => p.id === mergeSourceId);
        const sourceConvCount = getConversationCount(mergeSourceId);
        return (
            <Modal
                title={`Merge "${sourceProject?.name}" into...`}
                open={visible}
                onCancel={() => { setMergeSourceId(null); setMergeTargetId(null); }}
                width={500}
                footer={[
                    <Button key="cancel" onClick={() => { setMergeSourceId(null); setMergeTargetId(null); }}>Cancel</Button>,
                    <Button key="merge" type="primary" danger loading={isProcessing}
                        disabled={!mergeTargetId}
                        onClick={handleMerge}
                    >
                        Merge & Delete Source
                    </Button>
                ]}
            >
                <Alert
                    message="This will move all conversations to the target project and permanently delete the source project."
                    type="warning"
                    showIcon
                    style={{ marginBottom: 16 }}
                />
                <div style={{ marginBottom: 16 }}>
                    <strong>Source:</strong> {sourceProject?.name}
                    <Tag style={{ marginLeft: 8 }}>{sourceConvCount} conversation{sourceConvCount !== 1 ? 's' : ''}</Tag>
                    <div style={{ fontSize: 12, color: '#888' }}>{sourceProject?.path}</div>
                </div>
                <div>
                    <strong>Merge into:</strong>
                    <Select
                        style={{ width: '100%', marginTop: 8 }}
                        placeholder="Select target project"
                        value={mergeTargetId}
                        onChange={setMergeTargetId}
                    >
                        {projects
                            .filter(p => p.id !== mergeSourceId)
                            .map(p => (
                                <Select.Option key={p.id} value={p.id}>
                                    {p.name} — <span style={{ fontSize: 11, color: '#888' }}>{p.path}</span>
                                </Select.Option>
                            ))}
                    </Select>
                </div>
            </Modal>
        );
    }

    // Main project list view
    return (
        <Modal
            title="Manage Projects"
            open={visible}
            onCancel={onClose}
            width={650}
            footer={<Button onClick={onClose}>Close</Button>}
        >
            {projects.length === 0 ? (
                <Empty description="No projects found" />
            ) : (
                <div>
                    {projects.map(project => {
                        const isActive = project.id === currentProject?.id;
                        const convCount = getConversationCount(project.id);
                        const isEditing = editingId === project.id;

                        return (
                            <div key={project.id} style={cardStyle(isActive)}>
                                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        {isEditing ? (
                                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                                                <Input
                                                    ref={editInputRef}
                                                    value={editValue}
                                                    onChange={e => setEditValue(e.target.value)}
                                                    onPressEnter={() => handleRename(project.id)}
                                                    onBlur={() => handleRename(project.id)}
                                                    size="small"
                                                    style={{ maxWidth: 300 }}
                                                />
                                                <Button size="small" type="text" icon={<CheckOutlined />}
                                                    onClick={() => handleRename(project.id)} />
                                                <Button size="small" type="text" icon={<CloseOutlined />}
                                                    onClick={() => setEditingId(null)} />
                                            </div>
                                        ) : (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                                                <FolderOutlined style={{ color: isActive ? '#1890ff' : '#888' }} />
                                                <strong style={{ fontSize: 14 }}>{project.name}</strong>
                                                {isActive && <Tag color="blue" style={{ fontSize: 10 }}>Active</Tag>}
                                                <Tag style={{ fontSize: 10 }}>{convCount} conv{convCount !== 1 ? 's' : ''}</Tag>
                                            </div>
                                        )}
                                        <div style={{
                                            fontSize: 11,
                                            color: '#888',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                            fontFamily: 'monospace'
                                        }}>
                                            {project.path}
                                        </div>
                                    </div>

                                    <Space size={4}>
                                        <Tooltip title="Rename">
                                            <Button size="small" type="text" icon={<EditOutlined />}
                                                onClick={() => { setEditingId(project.id); setEditValue(project.name); }} />
                                        </Tooltip>
                                        <Tooltip title="Write Policy Settings">
                                            <Button size="small" type="text" icon={<SettingOutlined />}
                                                onClick={() => setSettingsId(project.id)} />
                                        </Tooltip>
                                        <Tooltip title="Merge into another project">
                                            <Button size="small" type="text" icon={<MergeCellsOutlined />}
                                                disabled={projects.length < 2}
                                                onClick={() => { setMergeSourceId(project.id); setMergeTargetId(null); }} />
                                        </Tooltip>
                                        {!isActive ? (
                                            <Popconfirm
                                                title="Delete this project?"
                                                description={
                                                    convCount > 0
                                                        ? `${convCount} conversation${convCount !== 1 ? 's' : ''} will be orphaned. Consider merging first.`
                                                        : 'This project has no conversations.'
                                                }
                                                onConfirm={() => handleDelete(project.id)}
                                                okText="Delete"
                                                okButtonProps={{ danger: true }}
                                                icon={<ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />}
                                            >
                                                <Tooltip title="Delete project">
                                                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                                                </Tooltip>
                                            </Popconfirm>
                                        ) : (
                                            <Tooltip title="Cannot delete active project">
                                                <Button size="small" type="text" icon={<DeleteOutlined />} disabled />
                                            </Tooltip>
                                        )}
                                    </Space>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </Modal>
    );
};

export default ProjectManagerModal;
