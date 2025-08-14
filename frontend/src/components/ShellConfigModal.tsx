import React, { useState, useEffect } from 'react';
import { Modal, Switch, Input, Button, List, Tag, Space, message, Divider, Checkbox, Collapse, Alert } from 'antd';
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
}

const { Panel } = Collapse;

const ShellConfigModal: React.FC<ShellConfigModalProps> = ({ visible, onClose }) => {
    const [config, setConfig] = useState<ShellConfig | null>(null);
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
            }
        } catch (error) {
            console.error('Failed to fetch shell config:', error);
            setConfig({
                enabled: true,
                allowedCommands: [],
                gitOperationsEnabled: true,
                safeGitOperations: [],
                timeout: 10
            });
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

    const saveConfig = async () => {
        if (!config) return;

        setLoading(true);
        try {
            const response = await fetch('/api/mcp/shell-config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(config),
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

                    message.success(result.message || 'Shell configuration updated instantly');
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
            onCancel={onClose}
            onOk={saveConfig}
            confirmLoading={loading}
            width={600}
            okText="Save Configuration"
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
                        When enabled, the AI agent can execute whitelisted shell commands
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
                                    {config.allowedCommands.map((command) => (
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
                            <h4>Command Timeout</h4>
                            <Input
                                type="number"
                                value={config.timeout}
                                onChange={(e) => setConfig(prev => ({ ...prev!, timeout: parseInt(e.target.value) || 10 }))}
                                suffix="seconds"
                                style={{ width: 150 }}
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
