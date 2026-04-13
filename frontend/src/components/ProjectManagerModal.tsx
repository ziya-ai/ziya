import React, { useState, useEffect, useRef } from 'react';
import { Modal, Button, Input, Tag, Space, message, Divider, Alert, Collapse, Empty, Popconfirm, Select, Tooltip, List, Radio } from 'antd';
import {
    DeleteOutlined, SettingOutlined, MergeCellsOutlined,
    EditOutlined, CheckOutlined, CloseOutlined,
    FolderOutlined, ExclamationCircleOutlined,
    PlusOutlined, FolderAddOutlined
} from '@ant-design/icons';
import { useProject } from '../context/ProjectContext';
import { useTheme } from '../context/ThemeContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { WritePolicy, ContextManagementSettings } from '../types/project';

const { Panel } = Collapse;

/**
 * Reusable editable tag list for glob patterns and path prefixes.
 * Supports add, remove (✕), and inline edit (double-click).
 */
interface EditableTagListProps {
    items: string[];
    onChange: (items: string[]) => void;
    color: string;
    placeholder: string;
    emptyText: string;
    isDarkMode: boolean;
    /** Allow comma-separated input to add multiple at once */
    allowMulti?: boolean;
}

const EditableTagList: React.FC<EditableTagListProps> = ({
    items, onChange, color, placeholder, emptyText, isDarkMode, allowMulti = false,
}) => {
    const [inputValue, setInputValue] = useState('');
    const [editingIndex, setEditingIndex] = useState<number | null>(null);
    const [editingValue, setEditingValue] = useState('');
    const editRef = useRef<any>(null);

    const addItems = () => {
        const raw = inputValue.trim();
        if (!raw) return;
        const parts = allowMulti ? raw.split(',').map(s => s.trim()).filter(Boolean) : [raw];
        const fresh = parts.filter(p => !items.includes(p));
        if (fresh.length > 0) {
            onChange([...items, ...fresh]);
            setInputValue('');
        }
    };

    const removeItem = (value: string) => {
        onChange(items.filter(x => x !== value));
    };

    const startEditing = (index: number) => {
        setEditingIndex(index);
        setEditingValue(items[index]);
        setTimeout(() => editRef.current?.focus(), 0);
    };

    const commitEdit = () => {
        if (editingIndex === null) return;
        const trimmed = editingValue.trim();
        if (!trimmed) {
            // Empty value = remove the entry
            onChange(items.filter((_, i) => i !== editingIndex));
        } else if (trimmed !== items[editingIndex]) {
            // Deduplicate: if the new value already exists elsewhere, just remove the old one
            if (items.some((v, i) => i !== editingIndex && v === trimmed)) {
                onChange(items.filter((_, i) => i !== editingIndex));
            } else {
                onChange(items.map((v, i) => i === editingIndex ? trimmed : v));
            }
        }
        setEditingIndex(null);
    };

    return (
        <>
            <Input.Group compact style={{ marginBottom: 8 }}>
                <Input
                    placeholder={placeholder}
                    value={inputValue}
                    onChange={e => setInputValue(e.target.value)}
                    onPressEnter={addItems}
                    style={{ width: 'calc(100% - 80px)' }}
                />
                <Button type="primary" style={{ width: 80 }} onClick={addItems}>Add</Button>
            </Input.Group>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {items.map((p, idx) => (
                    editingIndex === idx ? (
                        <Input
                            key={`edit-${idx}`}
                            ref={editRef}
                            size="small"
                            value={editingValue}
                            onChange={e => setEditingValue(e.target.value)}
                            onPressEnter={commitEdit}
                            onBlur={commitEdit}
                            onKeyDown={e => { if (e.key === 'Escape') setEditingIndex(null); }}
                            style={{ width: 160, fontSize: 12 }}
                            autoFocus
                        />
                    ) : (
                        <Tag key={p} closable color={color}
                            onClose={() => removeItem(p)}
                            style={{ cursor: 'pointer' }}
                            onDoubleClick={() => startEditing(idx)}
                            title="Double-click to edit"
                        >{p}</Tag>
                    )
                ))}
                {items.length === 0 && (
                    <span style={{ color: '#999', fontSize: 12, fontStyle: 'italic' }}>{emptyText}</span>
                )}
            </div>
        </>
    );
};

interface BrowseEntry {
    name: string;
    path: string;
    is_dir: boolean;
}

interface ProjectManagerModalProps {
    visible: boolean;
    onClose: () => void;
    initialSettingsId?: string | null;
}

const ProjectManagerModal: React.FC<ProjectManagerModalProps> = ({ visible, onClose, initialSettingsId }) => {
    const { currentProject, projects, switchProject, updateProject, deleteProject, mergeProjects, refreshProjects } = useProject();
    const { startNewChat } = useActiveChat();
    const { isDarkMode } = useTheme();

    const [editingId, setEditingId] = useState<string | null>(null);
    const [editValue, setEditValue] = useState('');
    const [settingsId, setSettingsId] = useState<string | null>(initialSettingsId || null);
    const [mergeSourceId, setMergeSourceId] = useState<string | null>(null);
    const [mergeTargetId, setMergeTargetId] = useState<string | null>(null);
    const [isProcessing, setIsProcessing] = useState(false);
    const editInputRef = useRef<any>(null);

    // Write policy state for the project being edited
    const [writePolicy, setWritePolicy] = useState<WritePolicy>({});

    // Context management settings
    const [contextManagement, setContextManagement] = useState<ContextManagementSettings>({ auto_add_diff_files: true });

    // Project root path editing
    const [editProjectPath, setEditProjectPath] = useState('');

    // New project creation state
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [newProjectName, setNewProjectName] = useState('');
    const [newProjectPath, setNewProjectPath] = useState('');
    const [isCreating, setIsCreating] = useState(false);
    const [showBrowseModal, setShowBrowseModal] = useState(false);
    const [browseEntries, setBrowseEntries] = useState<BrowseEntry[]>([]);
    const [browsePath, setBrowsePath] = useState('~');

    useEffect(() => {
        if (visible) refreshProjects();
    }, [visible]);

    // Sync settingsId with initialSettingsId prop when modal opens
    useEffect(() => {
        if (visible && initialSettingsId) {
            setSettingsId(initialSettingsId);
        }
    }, [visible, initialSettingsId]);

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

                // Initialize project path for editing
                try {
                    const proj = projects.find(p => p.id === settingsId);
                    if (proj) setEditProjectPath(proj.path || '');
                } catch {}

                // Load context management settings from project
                try {
                    const proj = projects.find(p => p.id === settingsId);
                    if (proj) {
                        const fullProject = await fetch(`/api/v1/projects/${settingsId}`);
                        if (fullProject.ok) {
                            const projectData = await fullProject.json();
                            setContextManagement(projectData.settings?.contextManagement || { auto_add_diff_files: true });
                        }
                    }
                } catch (error) {
                    console.error('Failed to load context management settings:', error);
                }
            };
            loadPolicy();
        }
    }, [settingsId]);

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

    const handleCreateProject = async () => {
        const name = newProjectName.trim();
        if (!name) {
            message.error('Project name is required');
            return;
        }

        setIsCreating(true);
        try {
            const { createProject } = await import('../context/ProjectContext').then(m => {
                // We already have createProject from useProject hook — use the API directly
                return { createProject: async (path: string, projectName?: string) => {
                    const { api } = await import('../api/index');
                    return api.post<any>('/projects', { path: path || undefined, name: projectName });
                }};
            });

            const path = newProjectPath.trim() || undefined;
            const newProject = await createProject(path || '', name);

            message.success(`Project "${name}" created`);
            setNewProjectName('');
            setNewProjectPath('');
            setShowCreateForm(false);

            // Refresh project list and switch to the new project
            await refreshProjects();

            if (newProject?.id) {
                await switchProject(newProject.id);
            }

            onClose();
        } catch (error: any) {
            message.error(error.message || 'Failed to create project');
        } finally {
            setIsCreating(false);
        }
    };

    const handleBrowse = async (path: string) => {
        try {
            const response = await fetch(`/api/browse-directory?path=${encodeURIComponent(path)}`);
            if (response.ok) {
                const data = await response.json();
                setBrowsePath(data.current_path);
                setBrowseEntries(data.entries.filter((e: BrowseEntry) => e.is_dir));
            }
        } catch (error) {
            console.error('Failed to browse directory:', error);
        }
    };

    const openBrowseModal = () => {
        setShowBrowseModal(true);
        handleBrowse(newProjectPath || '~');
    };

    const selectBrowsePath = (path: string) => {
        if (settingsId) {
            setEditProjectPath(path);
        } else {
            setNewProjectPath(path);
        }
        setShowBrowseModal(false);
    };

    const handleSaveWritePolicy = async () => {
        if (!settingsId) return;
        setIsProcessing(true);
        try {
            // Save project root path if changed
            const currentProj = projects.find(p => p.id === settingsId);
            const trimmedPath = editProjectPath.trim();
            if (currentProj && trimmedPath !== currentProj.path) {
                await updateProject(settingsId, { path: trimmedPath || undefined });
                // If this is the active project, notify the rest of the app
                if (currentProject?.id === settingsId) {
                    (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = trimmedPath;
                    window.dispatchEvent(new CustomEvent('projectSwitched', {
                        detail: { projectId: settingsId, projectPath: trimmedPath, projectName: currentProj.name }
                    }));
                }
            }

            // Save context management settings via project update
            const projectResp = await fetch(`/api/v1/projects/${settingsId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    settings: {
                        contextManagement: contextManagement,
                    }
                })
            });
            if (!projectResp.ok) {
                message.error('Failed to save context management settings');
            }

            const response = await fetch('/api/mcp/write-policy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_id: settingsId,
                    direct_write_mode: writePolicy.direct_write_mode || 'none',
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
                        Save Settings
                    </Button>
                ]}
            >
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                    <Alert
                        message="Project Root"
                        description="The root directory for this project. File context, AST indexing, and shell commands resolve relative to this path."
                        type="info"
                        showIcon
                    />

                    <div>
                        <strong>Project Root Path</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            The absolute filesystem path that serves as the working directory for this project.
                            Leave empty for idea/scratch projects with no directory.
                        </div>
                        <Input.Group compact>
                            <Input
                                placeholder="/path/to/project (optional)"
                                value={editProjectPath}
                                onChange={e => setEditProjectPath(e.target.value)}
                                style={{ width: 'calc(100% - 80px)', fontFamily: 'monospace', fontSize: 12 }}
                            />
                            <Button onClick={() => {
                                setShowBrowseModal(true);
                                handleBrowse(editProjectPath || '~');
                            }} style={{ width: 80 }}>
                                Browse
                            </Button>
                        </Input.Group>
                    </div>

                    <Divider style={{ margin: '8px 0' }} />

                    <Alert
                        message="Automatic Context Management"
                        description="Controls how the AI assistant manages file context when generating code changes."
                        type="info"
                        showIcon
                    />

                    <div>
                        <strong>Auto-add diff files to context</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            When the AI produces a diff referencing files not currently in context,
                            automatically add those files so the AI can see their full content on subsequent turns.
                        </div>
                        <Radio.Group
                            value={contextManagement.auto_add_diff_files !== false}
                            onChange={e => setContextManagement(prev => ({
                                ...prev,
                                auto_add_diff_files: e.target.value
                            }))}
                        >
                            <Radio.Button value={true} style={{ minWidth: 100, textAlign: 'center' }}>
                                Enabled
                            </Radio.Button>
                            <Radio.Button value={false} style={{ minWidth: 100, textAlign: 'center' }}>
                                Disabled
                            </Radio.Button>
                        </Radio.Group>
                    </div>

                    <Divider style={{ margin: '8px 0' }} />

                    <Alert
                        message="Project Write Policy"
                        description="Controls which files the shell can write to within this project. These patterns are merged with the global write policy."
                        type="info"
                        showIcon
                    />

                    <div>
                        <strong>Direct File Write Mode</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            Controls whether the AI can write files directly within this project using <code>file_write</code>,
                            beyond the safe paths and patterns above. Shell write restrictions are unaffected.
                        </div>
                        <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                            {([
                                { value: 'none', label: 'No files', desc: 'Only safe paths + patterns (default)' },
                                { value: 'new_files', label: 'New files', desc: 'Can create new files anywhere in project' },
                                { value: 'all_files', label: 'All files', desc: 'Can create and overwrite any project file' },
                            ] as const).map(opt => {
                                const isActive = (writePolicy.direct_write_mode || 'none') === opt.value;
                                return (
                                    <div
                                        key={opt.value}
                                        onClick={() => setWritePolicy(prev => ({ ...prev, direct_write_mode: opt.value }))}
                                        style={{
                                            flex: 1,
                                            padding: '8px 12px',
                                            borderRadius: 6,
                                            border: `1.5px solid ${isActive ? '#1890ff' : (isDarkMode ? '#303030' : '#d9d9d9')}`,
                                            backgroundColor: isActive ? (isDarkMode ? '#111d2c' : '#e6f7ff') : 'transparent',
                                            cursor: 'pointer',
                                            textAlign: 'center',
                                            transition: 'all 0.2s',
                                        }}
                                    >
                                        <div style={{ fontWeight: isActive ? 600 : 400, fontSize: 13 }}>{opt.label}</div>
                                        <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>{opt.desc}</div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    <Divider style={{ margin: '8px 0' }} />

                    <div>
                        <strong>Allowed Write Patterns</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            Glob patterns for files the shell may write within this project.
                            Examples: <code>*.md</code>, <code>docs/**</code>, <code>tracker/*.json</code>
                        </div>
                        <EditableTagList
                            items={writePolicy.allowed_write_patterns || []}
                            onChange={items => setWritePolicy(prev => ({ ...prev, allowed_write_patterns: items }))}
                            color="purple"
                            placeholder="*.md or docs/**"
                            emptyText="No project-specific patterns — only global policy applies"
                            isDarkMode={isDarkMode}
                            allowMulti
                        />
                    </div>

                    <Divider style={{ margin: '8px 0' }} />

                    <div>
                        <strong>Additional Safe Write Paths</strong>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                            Path prefixes writable within this project (added to global defaults like <code>.ziya/</code>, <code>/tmp/</code>).
                        </div>
                        <EditableTagList
                            items={writePolicy.safe_write_paths || []}
                            onChange={items => setWritePolicy(prev => ({ ...prev, safe_write_paths: items }))}
                            color="green"
                            placeholder="build/ or .cache/"
                            emptyText="Using global defaults only"
                            isDarkMode={isDarkMode}
                        />
                    </div>
                </Space>
            </Modal>
        );
    }

    // Merge sub-view
    if (mergeSourceId) {
        const sourceProject = projects.find(p => p.id === mergeSourceId);
        const sourceConvCount = sourceProject?.conversationCount ?? 0;
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
                        const convCount = project.conversationCount ?? 0;
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

            <Divider style={{ margin: '16px 0 12px' }} />

            {!showCreateForm ? (
                <Button
                    type="dashed"
                    block
                    icon={<PlusOutlined />}
                    onClick={() => setShowCreateForm(true)}
                    style={{ height: 44 }}
                >
                    Create New Project
                </Button>
            ) : (
                <div style={{
                    padding: 16,
                    borderRadius: 8,
                    border: `1px solid ${isDarkMode ? '#177ddc' : '#91d5ff'}`,
                    backgroundColor: isDarkMode ? '#111d2c' : '#f0f8ff',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                        <FolderAddOutlined style={{ fontSize: 16, color: '#1890ff' }} />
                        <strong>New Project</strong>
                    </div>

                    <div style={{ marginBottom: 10 }}>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>Project Name *</div>
                        <Input
                            placeholder="e.g. My Idea, Research Notes, Side Project"
                            value={newProjectName}
                            onChange={e => setNewProjectName(e.target.value)}
                            onPressEnter={handleCreateProject}
                            autoFocus
                        />
                    </div>

                    <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>
                            Directory Path <span style={{ fontStyle: 'italic' }}>(optional — leave empty for idea/scratch projects)</span>
                        </div>
                        <Input.Group compact>
                            <Input
                                placeholder="/path/to/directory (optional)"
                                value={newProjectPath}
                                onChange={e => setNewProjectPath(e.target.value)}
                                onPressEnter={handleCreateProject}
                                style={{ width: 'calc(100% - 80px)' }}
                            />
                            <Button onClick={openBrowseModal} style={{ width: 80 }}>
                                Browse
                            </Button>
                        </Input.Group>
                    </div>

                    <Space>
                        <Button type="primary" icon={<PlusOutlined />} loading={isCreating}
                            onClick={handleCreateProject}
                            disabled={!newProjectName.trim()}
                        >
                            Create
                        </Button>
                        <Button onClick={() => { setShowCreateForm(false); setNewProjectName(''); setNewProjectPath(''); }}>
                            Cancel
                        </Button>
                    </Space>
                </div>
            )}

            {/* Directory browser modal */}
            <Modal
                title="Select Directory"
                open={showBrowseModal}
                onCancel={() => setShowBrowseModal(false)}
                width={500}
                footer={null}
            >
                <div style={{ marginBottom: 12 }}>
                    <strong>Current:</strong> <code style={{ fontSize: 12 }}>{browsePath}</code>
                    <Button size="small" style={{ marginLeft: 8 }}
                        onClick={() => selectBrowsePath(browsePath)}>Select This</Button>
                    <Button size="small" type="link"
                        onClick={() => handleBrowse(browsePath + '/..')}>Up ↑</Button>
                </div>
                <List
                    size="small"
                    dataSource={browseEntries}
                    style={{ maxHeight: 400, overflow: 'auto' }}
                    renderItem={(entry: BrowseEntry) => (
                        <List.Item style={{ cursor: 'pointer', padding: '6px 12px' }}
                            onClick={() => handleBrowse(entry.path)}>
                            <FolderOutlined style={{ marginRight: 8, color: '#faad14' }} />
                            {entry.name}
                            <Button size="small" type="link" style={{ marginLeft: 'auto' }}
                                onClick={(e) => { e.stopPropagation(); selectBrowsePath(entry.path); }}>
                                Select
                            </Button>
                        </List.Item>
                    )}
                />
            </Modal>
        </Modal>
    );
};

export default ProjectManagerModal;
