import React, { useState, useEffect } from 'react';
import { Modal, List, Tag, Space, Button, Spin, Alert, Descriptions, Switch, message } from 'antd';
import {
    CheckCircleOutlined,
    CloseCircleOutlined,
    ReloadOutlined,
    ToolOutlined,
    DatabaseOutlined,
    FileTextOutlined
} from '@ant-design/icons';

interface MCPStatusModalProps {
    visible: boolean;
    onClose: () => void;
}

interface MCPServer {
    name: string;
    connected: boolean;
    resources: number;
    tools: number;
    prompts: number;
    capabilities: any;
    builtin?: boolean;
    enabled?: boolean;
}

interface MCPStatus {
    initialized: boolean;
    servers: Record<string, MCPServer>;
    total_servers: number;
    connected_servers: number;
    config_path?: string;
    config_exists?: boolean;
    config_search_paths?: string[];
    server_configs?: Record<string, { enabled: boolean }>;
}

const MCPStatusModal: React.FC<MCPStatusModalProps> = ({ visible, onClose }) => {
    const [status, setStatus] = useState<MCPStatus | null>(null);
    const [loading, setLoading] = useState(false);
    const [toggling, setToggling] = useState<Record<string, boolean>>({});

    useEffect(() => {
        if (visible) {
            fetchMCPStatus();

            // Listen for MCP status changes from other components
            const handleMCPStatusChange = () => {
                setTimeout(() => fetchMCPStatus(), 1000); // Small delay to let server update
            };

            window.addEventListener('mcpStatusChanged', handleMCPStatusChange);
            return () => {
                window.removeEventListener('mcpStatusChanged', handleMCPStatusChange);
            };
        }
    }, [visible]);

    const fetchMCPStatus = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/mcp/status');
            if (response.ok) {
                const data = await response.json();
                setStatus(data);
            }
        } catch (error) {
            console.error('Failed to fetch MCP status:', error);
        } finally {
            setLoading(false);
        }
    };

    const reinitializeMCP = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/mcp/initialize', { method: 'POST' });
            if (response.ok) {
                await fetchMCPStatus();
            }
        } catch (error) {
            console.error('Failed to reinitialize MCP:', error);
        } finally {
            setLoading(false);
        }
    };

    const toggleServer = async (serverName: string, enabled: boolean) => {
        setToggling(prev => ({ ...prev, [serverName]: true }));
        try {
            const response = await fetch('/api/mcp/toggle-server', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    server_name: serverName,
                    enabled: enabled
                }),
            });

            if (response.ok) {
                const result = await response.json();
                if (result.success) {
                    message.success(result.message);
                    await fetchMCPStatus(); // Refresh status
                } else {
                    message.error(result.message || 'Failed to toggle server');
                }
            } else {
                message.error('Failed to toggle server');
            }
        } catch (error) {
            message.error('Failed to toggle server');
            console.error('Toggle error:', error);
        } finally {
            setToggling(prev => ({ ...prev, [serverName]: false }));
        }
    };

    const getServerDisplayName = (serverName: string) => {
        return serverName;
    };

    const getConfigStatusMessage = () => {
        if (!status) return null;

        const getPathDescription = (path: string, index: number) => {
            if (path.includes('/.ziya/')) return '(user\'s home)';
            if (path.endsWith('/mcp_config.json') && !path.includes('/.ziya/')) {
                if (path.includes('/mcp_config.json') && path.split('/').length > 2) {
                    return '(project root)';
                }
                return '(current working directory)';
            }
            return '';
        };

        if (status.config_path && status.config_exists) {
            return `Using config: ${status.config_path}`;
        } else if (status.config_search_paths && status.config_search_paths.length > 0) {
            return (
                <div>
                    <div>No MCP configuration file found in search path.</div>
                    <div style={{ fontSize: '11px', opacity: 0.7, marginTop: '2px' }}>
                        Using built-in server defaults.
                    </div>
                    <div style={{ fontSize: '11px', opacity: 0.7, marginTop: '4px', wordBreak: 'break-all' }}>
                        Search path: {status.config_search_paths.map((path, index) => (
                            <div key={index} style={{ marginTop: index > 0 ? '2px' : '0' }}>
                                {index > 0 && '↓ '}
                                <code style={{
                                    backgroundColor: 'rgba(0,0,0,0.1)',
                                    padding: '1px 3px',
                                    borderRadius: '2px',
                                    fontSize: '10px',
                                    fontFamily: 'monospace'
                                }}>{path}</code>
                                <span style={{ marginLeft: '4px', fontStyle: 'italic' }}>
                                    {getPathDescription(path, index)}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            );
        } else {
            return 'Using built-in server defaults';
        }
    };

    return (
        <Modal
            title="MCP Server Status"
            open={visible}
            onCancel={onClose}
            footer={[
                <Button key="refresh" icon={<ReloadOutlined />} onClick={fetchMCPStatus}>
                    Refresh
                </Button>,
                <Button key="reinit" type="primary" onClick={reinitializeMCP} loading={loading}>
                    Reinitialize
                </Button>,
                <Button key="close" onClick={onClose}>
                    Close
                </Button>
            ]}
            width={700}
        >
            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px' }}>
                    <Spin size="large" />
                </div>
            ) : status ? (
                <Space direction="vertical" style={{ width: '100%' }} size="large">
                    <Alert
                        message={`MCP System ${status.initialized ? 'Initialized' : 'Not Initialized'}`}
                        description={
                            <div>
                                <div>{status.connected_servers}/{status.total_servers} servers connected</div>
                                <div style={{ fontSize: '12px', marginTop: '4px', opacity: 0.8 }}>
                                    {getConfigStatusMessage()}
                                </div>
                            </div>
                        }
                        type={status.initialized && status.connected_servers > 0 ? 'success' : 'warning'}
                        showIcon
                    />

                    <List
                        dataSource={
                            // Show all configured servers, not just connected ones - with proper typing
                            (status.server_configs ?
                                Object.keys(status.server_configs).map<[string, MCPServer]>(name => {
                                    const serverInfo = status.servers[name] || {
                                        connected: false,
                                        resources: 0,
                                        tools: 0,
                                        prompts: 0,
                                        capabilities: {},
                                        enabled: status.server_configs?.[name]?.enabled !== false
                                    };
                                    return [name, serverInfo];
                                }) :
                                Object.entries(status.servers))
                        }
                        renderItem={([name, server]: [string, MCPServer]) => (
                            <List.Item>
                                <Descriptions
                                    title={
                                        <span>
                                            {getServerDisplayName(name)} {server.builtin && <Tag color="blue">built-in</Tag>}
                                        </span>
                                    }
                                    column={1}
                                    size="small"
                                >
                                    <Descriptions.Item label="Status">
                                        <Space align="center">
                                            <Tag
                                                color={
                                                    status.server_configs?.[name]?.enabled === false ? 'default' :
                                                        server.connected ? 'green' : 'red'
                                                }
                                                icon={server.connected ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                                            >
                                                {status.server_configs?.[name]?.enabled === false ? 'Disabled' :
                                                    server.connected ? 'Connected' : 'Disconnected'}
                                            </Tag>
                                            <Switch
                                                checked={status.server_configs?.[name]?.enabled !== false}
                                                onChange={(checked) => toggleServer(name, checked)}
                                                loading={toggling[name]}
                                                size="small"
                                            />
                                            <span style={{ fontSize: '12px', color: '#666' }}>
                                                {status.server_configs?.[name]?.enabled !== false ? 'Enabled' : 'Disabled'}
                                            </span>
                                        </Space>
                                    </Descriptions.Item>
                                    <Descriptions.Item label="Capabilities">
                                        <Space>
                                            <Tag icon={<ToolOutlined />}>{server.tools} tools</Tag>
                                            <Tag icon={<DatabaseOutlined />}>{server.resources} resources</Tag>
                                            <Tag icon={<FileTextOutlined />}>{server.prompts} prompts</Tag>
                                        </Space>
                                    </Descriptions.Item>
                                </Descriptions>
                            </List.Item>
                        )}
                    />
                </Space>
            ) : (
                <Alert message="Failed to load MCP status" type="error" />
            )}
        </Modal>
    );
};

export default MCPStatusModal;
