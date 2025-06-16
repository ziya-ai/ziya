import React, { useState, useEffect } from 'react';
import { Modal, Switch, Input, Button, List, Tag, Space, message, Divider } from 'antd';
import { PlusOutlined, DeleteOutlined, WarningOutlined } from '@ant-design/icons';

interface ShellConfigModalProps {
    visible: boolean;
    onClose: () => void;
}

interface ShellConfig {
    enabled: boolean;
    allowedCommands: string[];
    timeout: number;
}

const ShellConfigModal: React.FC<ShellConfigModalProps> = ({ visible, onClose }) => {
    const [config, setConfig] = useState<ShellConfig>({
        enabled: true,
        allowedCommands: ['ls', 'cat', 'pwd', 'grep', 'wc', 'touch', 'find', 'date'],
        timeout: 10
    });
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
            // Use default config if fetch fails
        }
    };

    const saveConfig = async () => {
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
        if (newCommand.trim() && !config.allowedCommands.includes(newCommand.trim())) {
            setConfig(prev => ({
                ...prev,
                allowedCommands: [...prev.allowedCommands, newCommand.trim()]
            }));
            setNewCommand('');
        }
    };

    const removeCommand = (command: string) => {
        setConfig(prev => ({
            ...prev,
            allowedCommands: prev.allowedCommands.filter(cmd => cmd !== command)
        }));
    };

    const dangerousCommands = ['rm', 'rmdir', 'mv', 'cp', 'chmod', 'chown', 'sudo', 'su'];
    const isDangerous = (command: string) => dangerousCommands.some(dangerous => 
        command.toLowerCase().includes(dangerous.toLowerCase())
    );

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
                            onChange={(checked) => setConfig(prev => ({ ...prev, enabled: checked }))}
                        />
                        <span>Enable shell command execution</span>
                    </Space>
                    <div style={{ marginTop: 8, color: '#666', fontSize: '12px' }}>
                        When enabled, the AI agent can execute whitelisted shell commands
                    </div>
                </div>

                <Divider />

                <div>
                    <h4>Command Timeout</h4>
                    <Input
                        type="number"
                        value={config.timeout}
                        onChange={(e) => setConfig(prev => ({ ...prev, timeout: parseInt(e.target.value) || 10 }))}
                        suffix="seconds"
                        style={{ width: 150 }}
                    />
                </div>

                <div>
                    <h4>Allowed Commands Whitelist</h4>
                    <div style={{ marginBottom: 12 }}>
                        <Input.Group compact>
                            <Input
                                placeholder="Enter command name"
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

                    <List
                        size="small"
                        dataSource={config.allowedCommands}
                        renderItem={(command) => (
                            <List.Item
                                actions={[
                                    <Button
                                        type="text"
                                        size="small"
                                        icon={<DeleteOutlined />}
                                        onClick={() => removeCommand(command)}
                                        danger
                                    />
                                ]}
                            >
                                <Space>
                                    <Tag color={isDangerous(command) ? 'red' : 'blue'}>
                                        {command}
                                    </Tag>
                                    {isDangerous(command) && (
                                        <WarningOutlined style={{ color: '#ff4d4f' }} />
                                    )}
                                </Space>
                            </List.Item>
                        )}
                        style={{ maxHeight: 200, overflow: 'auto' }}
                    />
                </div>
            </Space>
        </Modal>
    );
};

export default ShellConfigModal;
