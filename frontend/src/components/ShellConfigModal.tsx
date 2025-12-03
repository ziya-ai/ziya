import React, { useState, useEffect } from 'react';
import { Modal, Switch, Input, Button, List, Tag, Space, message, Divider, Checkbox, Collapse, Alert, Slider } from 'antd';
import { PlusOutlined, DeleteOutlined, WarningOutlined } from '@ant-design/icons';

interface ShellConfigModalProps {
    visible: boolean;
    onClose: () => void;
}

interface ShellConfig {
    enabled: boolean;
    allowedCommands: string[];
    gitOperationsEnabled: boolean;
    safeGitOperations: string[];
    timeout: number;
    persist?: boolean;
}

const { Panel } = Collapse;

const ShellConfigModal: React.FC<ShellConfigModalProps> = ({ visible, onClose }) => {
    const [config, setConfig] = useState<ShellConfig | null>(null);
    const [originalConfig, setOriginalConfig] = useState<ShellConfig | null>(null);
    const [newCommand, setNewCommand] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (visible) {
            fetchShellConfig();
        }
    }, [visible]);

    const fetchShellConfig = async () => {
        try {
            const response = await fetch('/api/mcp/shell-config');
            if (response.ok) {
                const data = await response.json();
                setConfig(data);
                setOriginalConfig(data);
            }
        } catch (error) {
            console.error('Failed to fetch shell config:', error);
            setConfig({
                enabled: true,
                allowedCommands: [],
                gitOperationsEnabled: true,
                safeGitOperations: [],
                timeout: 30
            });
            setOriginalConfig(defaultConfig);
        }
    };

    const syncMCPServerToggle = async (enabled: boolean) => {
        try {
            await fetch('/api/mcp/toggle-server', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    server_name: 'shell',
                    enabled: enabled
                }),
            });
        } catch (error) {
            console.warn('Failed to sync MCP server toggle:', error);
        }
    };

    const saveConfig = async (persist: boolean = false) => {
        if (!config) return;

        setLoading(true);
        try {
            const response = await fetch('/api/mcp/shell-config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ ...config, persist }),
            });

            if (response.ok) {
                const result = await response.json();
                if (result.success) {
                    await syncMCPServerToggle(config.enabled);

                    window.dispatchEvent(new CustomEvent('mcpStatusChanged', {
                        detail: {
                            serverName: 'shell',
                            enabled: config.enabled
                        }
                    }));

                    message.success(result.message || (persist ? 'Configuration saved to file' : 'Configuration applied for this session'));
                } else {
                    message.error(result.message || 'Failed to update shell configuration');
                }
                onClose();
            } else {
                message.error('Failed to save shell configuration');
            }
        } catch (error) {
            message.error('Failed to save shell configuration');
            console.error('Save error:', error);
        } finally {
            setLoading(false);
        }
    };

    const addCommand = () => {
        if (!config) return;

        if (newCommand.trim() && !config.allowedCommands.includes(newCommand.trim())) {
            setConfig(prev => ({
                ...prev!,
                allowedCommands: [...prev!.allowedCommands, newCommand.trim()]
            }));
            setNewCommand('');
        }
    };

    const removeCommand = (command: string) => {
        if (!config) return;

        setConfig(prev => ({
            ...prev!,
            allowedCommands: prev!.allowedCommands.filter(cmd => cmd !== command)
        }));
    };

    const toggleGitOperation = (operation: string) => {
        if (!config) return;

        setConfig(prev => ({
            ...prev!,
            safeGitOperations: prev!.safeGitOperations.includes(operation)
                ? prev!.safeGitOperations.filter(op => op !== operation)
                : [...prev!.safeGitOperations, operation]
        }));
    };

    const hasChanges = (): boolean => {
        if (!config || !originalConfig) return false;
        return JSON.stringify(config) !== JSON.stringify(originalConfig);
    };

    const handleClose = () => {
        if (hasChanges()) {
            Modal.confirm({
                title: 'Unsaved Changes',
                icon: <WarningOutlined style={{ color: '#faad14' }} />,
                content: 'You have unsaved changes. What would you like to do?',
                okText: 'Apply Changes',
                cancelText: 'Discard Changes',
                okButtonProps: {
                    loading: loading
                },
                onOk: async () => {
                    await saveConfig(false);
                },
                onCancel: () => {
                    onClose();
                },
                maskClosable: true,
                closable: true
            });
        } else {
            onClose();
        }
    };

    const dangerousCommands = ['rm', 'rmdir', 'mv', 'cp', 'chmod', 'chown', 'sudo', 'su'];
    const allGitOperations = ['status', 'log', 'show', 'diff', 'branch', 'remote', 'config --get', 'ls-files', 'ls-tree', 'blame', 'tag', 'stash list', 'reflog', 'rev-parse', 'describe', 'shortlog', 'whatchanged'];

    const isDangerous = (command: string) =>
        dangerousCommands.some(dangerous =>
            command.toLowerCase().includes(dangerous.toLowerCase())
        );

    if (!config) {
        return (
            <Modal title="Shell Command Configuration" open={visible} onCancel={onClose} footer={null}>
                <div style={{ textAlign: 'center', padding: '40px' }}>Loading configuration...</div>
            </Modal>
        );
    }

    return (
        <Modal
            title="Shell Command Configuration"
            open={visible}
            onCancel={handleClose}
            width={600}
            footer={[
                <Button key="cancel" onClick={handleClose}>
                    Cancel
                </Button>,
                <Button 
                    key="apply" 
                    loading={loading}
                    onClick={() => saveConfig(false)}
                >
                    Apply
                </Button>,
                <Button
                    key="save"
                    type="primary"
                    loading={loading}
                    onClick={() => saveConfig(true)}
                >
                    Save
                </Button>
            ]}
        >
            <Space direction="vertical" style={{ width: '100%' }} size="large">
                <div>
                    <Space align="center">
                        <Switch
                            checked={config.enabled}
                            onChange={async (checked) => {
                                setConfig(prev => ({ ...prev!, enabled: checked }));
                                await syncMCPServerToggle(checked);
                            }}
                        />
                        <span>Enable shell command execution</span>
                    </Space>
                    <div style={{ marginTop: 8, color: '#666', fontSize: '12px' }}>
                        Enables execution of whitelisted commands for file operations, system inspection, and diagnostics
                    </div>
                </div>

                <Divider />

                <Alert
                    message="Security Notice"
                    description="Only enable commands that are safe for AI execution. Git operations are limited to read-only and safe operations by default."
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                />

                <Collapse defaultActiveKey={['1']} ghost>
                    <Panel header="Basic Configuration" key="1">
                        <div>
                            <h4>Allowed Commands</h4>
                            <div style={{ marginBottom: 12, color: '#666', fontSize: '12px' }}>
                                Commands that the AI agent can execute. These are loaded from the server configuration.
                            </div>

                            <div style={{ marginBottom: 12 }}>
                                <Input.Group compact>
                                    <Input
                                        placeholder="Add additional command"
                                        value={newCommand}
                                        onChange={(e) => setNewCommand(e.target.value)}
                                        onPressEnter={addCommand}
                                        style={{ width: 'calc(100% - 80px)' }}
                                    />
                                    <Button
                                        type="primary"
                                        icon={<PlusOutlined />}
                                        onClick={addCommand}
                                        style={{ width: 80 }}
                                    >
                                        Add
                                    </Button>
                                </Input.Group>
                            </div>

                            {config.allowedCommands && config.allowedCommands.length > 0 && (
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: 16 }}>
                                    {[...config.allowedCommands].sort().map((command) => (
                                        <Tag
                                            key={command}
                                            closable
                                            color={isDangerous(command) ? 'red' : 'blue'}
                                            onClose={() => removeCommand(command)}
                                            style={{ marginBottom: '4px' }}
                                        >
                                            {command}
                                            {isDangerous(command) && (
                                                <WarningOutlined style={{ marginLeft: 4, color: '#ff4d4f' }} />
                                            )}
                                        </Tag>
                                    ))}
                                </div>
                            )}
                        </div>

                        <Divider />

                        <div>
                            <h4>Command Timeout: {config.timeout}s</h4>
                            <Slider
                                min={10}
                                max={600}
                                value={config.timeout}
                                onChange={(value) => setConfig(prev => ({ ...prev!, timeout: value }))}
                                marks={{
                                    10: '10s',
                                    60: '1m',
                                    120: '2m',
                                    300: '5m',
                                    600: '10m'
                                }}
                                style={{ width: '100%', marginBottom: 20 }}
                            />
                        </div>

                        <Divider />

                        <div>
                            <Space align="center" style={{ marginBottom: 12 }}>
                                <Switch
                                    checked={config.gitOperationsEnabled}
                                    onChange={(checked) => setConfig(prev => ({ ...prev!, gitOperationsEnabled: checked }))}
                                />
                                <span>Enable safe Git operations</span>
                            </Space>
                            <div style={{ marginBottom: 12, color: '#666', fontSize: '12px' }}>
                                When enabled, allows read-only and safe Git commands
                            </div>

                            {config.gitOperationsEnabled && (
                                <div style={{ marginLeft: 24 }}>
                                    <h5>Allowed Git Operations:</h5>
                                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                                        {allGitOperations.map(operation => (
                                            <Checkbox
                                                key={operation}
                                                checked={config.safeGitOperations.includes(operation)}
                                                onChange={() => toggleGitOperation(operation)}
                                            >
                                                git {operation}
                                            </Checkbox>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </Panel>

                    <Panel header="Advanced Configuration" key="2">
                        <Alert
                            message="Advanced Configuration"
                            description="This section is reserved for future advanced shell configuration options."
                            type="info"
                            showIcon
                        />
                    </Panel>
                </Collapse>
            </Space>
        </Modal>
    );
};

export default ShellConfigModal;
