import React, { useState, useEffect } from 'react';
import { Modal, Tag, Space, Button, Spin, Alert, Descriptions, Switch, message, Collapse, Select, Tabs, List, Empty } from 'antd';
import MCPRegistryModal from './MCPRegistryModal';
import MarkdownRenderer from './MarkdownRenderer';
import {
    CheckCircleOutlined,
    CloseCircleOutlined,
    ReloadOutlined,
    ToolOutlined,
    DatabaseOutlined,
    FileTextOutlined,
    CloudServerOutlined
} from '@ant-design/icons';

const { Panel } = Collapse;
const { Option } = Select;
const { TabPane } = Tabs;

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

interface MCPTool {
    name: string;
    description: string;
    inputSchema: any;
}

interface MCPResource {
    uri: string;
    name: string;
    description?: string;
}

interface MCPPrompt {
    name: string;
    description: string;
    arguments: any[];
}

interface ServerDetails {
    tools: MCPTool[];
    resources: MCPResource[];
    prompts: MCPPrompt[];
}

type PermissionLevel = 'enabled' | 'disabled' | 'ask';

interface MCPPermissions {
    defaults: {
        server: PermissionLevel;
        tool: PermissionLevel;
    };
    servers: Record<string, {
        permission?: PermissionLevel;
        tools?: Record<string, {
            permission: PermissionLevel;
        }>;
    }>;
}

const MCPStatusModal: React.FC<MCPStatusModalProps> = ({ visible, onClose }) => {
    const [status, setStatus] = useState<MCPStatus | null>(null);
    const [loading, setLoading] = useState(false);
    const [showRegistry, setShowRegistry] = useState(false);
    const [toggling, setToggling] = useState<Record<string, boolean>>({});
    const [permissions, setPermissions] = useState<MCPPermissions | null>(null);
    const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
    const [serverDetails, setServerDetails] = useState<Record<string, ServerDetails>>({});
    const [detailsLoading, setDetailsLoading] = useState<Record<string, boolean>>({});

    useEffect(() => {
        if (visible) {
            fetchMCPStatus();

            // Listen for MCP status changes from other components
            const handleMCPStatusChange = () => {
                fetchPermissions();
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
                console.log('MCP Status received:', data);
                console.log('Servers:', data.servers);
                console.log('Server configs:', data.server_configs);
                setStatus(data);
            } else {
                console.error('MCP status fetch failed:', response.status, response.statusText);
            }
        } catch (error) {
            console.error('Failed to fetch MCP status:', error);
        } finally {
            setLoading(false);
        }
    };

    const fetchPermissions = async () => {
        try {
            const response = await fetch('/api/mcp/permissions');
            if (response.ok) {
                const data = await response.json();
                setPermissions(data);
            }
        } catch (error) {
            console.error('Failed to fetch MCP permissions:', error);
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

    const updateServerPermission = async (serverName: string, permission: PermissionLevel) => {
        try {
            const response = await fetch('/api/mcp/permissions/server', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ server_name: serverName, permission }),
            });
            if (response.ok) {
                message.success(`Permission for ${serverName} updated.`);
                fetchPermissions();
            } else {
                message.error('Failed to update permission.');
            }
        } catch (error) {
            message.error('Failed to update permission.');
        }
    };

    const updateToolPermission = async (serverName: string, toolName: string, permission: PermissionLevel) => {
        try {
            const response = await fetch('/api/mcp/permissions/tool', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ server_name: serverName, tool_name: toolName, permission }),
            });
            if (response.ok) {
                message.success(`Permission for ${toolName} updated.`);
                fetchPermissions();
            } else {
                message.error('Failed to update permission.');
            }
        } catch (error) {
            message.error('Failed to update permission.');
        }
    };

    const fetchServerDetails = async (serverName: string) => {
        if (serverDetails[serverName]) return; // Already fetched

        setDetailsLoading(prev => ({ ...prev, [serverName]: true }));
        try {
            const response = await fetch(`/api/mcp/servers/${serverName}/details`);
            if (response.ok) {
                const data = await response.json();
                console.log(`Fetched details for ${serverName}:`, data);
                setServerDetails(prev => ({ ...prev, [serverName]: data }));
            } else {
                console.error(`Failed to fetch details for ${serverName}: ${response.status} ${response.statusText}`);
                // Set empty details to prevent infinite loading
                setServerDetails(prev => ({ ...prev, [serverName]: { tools: [], resources: [], prompts: [] } }));
            }
        } catch (error) {
            console.error(`Failed to fetch details for ${serverName}:`, error);
            // Set empty details to prevent infinite loading
            setServerDetails(prev => ({ ...prev, [serverName]: { tools: [], resources: [], prompts: [] } }));
        } finally {
            setDetailsLoading(prev => ({ ...prev, [serverName]: false }));
        }
    };

    const handlePanelChange = (keys: string | string[]) => {
        const newKeys = Array.isArray(keys) ? keys : [keys];
        setExpandedKeys(newKeys);
        newKeys.forEach(key => {
            if (key) {
                fetchServerDetails(key);
            }
        });
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
                <Button key="registry" icon={<CloudServerOutlined />} onClick={() => setShowRegistry(true)}>
                    Browse Registry
                </Button>,
                <Button key="reinit" type="primary" onClick={reinitializeMCP} loading={loading}>
                    Reinitialize
                </Button>,
                <Button key="close" onClick={onClose}>
                    Close
                </Button>
            ]}
            width={800}
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

                    <Collapse activeKey={expandedKeys} onChange={handlePanelChange}>
                        {(status.server_configs ? Object.keys(status.server_configs) : Object.keys(status.servers)).map(name => {
                            const server = status.servers[name] || { connected: false, resources: 0, tools: 0, prompts: 0, capabilities: {} };
                            const isEnabled = status.server_configs?.[name]?.enabled !== false;
                            const serverPermission = permissions?.servers?.[name]?.permission || permissions?.defaults?.server || 'ask';

                            return (
                                <Panel
                                    key={name}
                                    header={
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                                            <Space>
                                                <Tag color={isEnabled && server.connected ? 'green' : (isEnabled ? 'red' : 'default')} icon={isEnabled && server.connected ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
                                                    {isEnabled ? (server.connected ? 'Connected' : 'Disconnected') : 'Disabled'}
                                                </Tag>
                                                <span>{getServerDisplayName(name)}</span>
                                                {server.builtin && <Tag color="blue">built-in</Tag>}
                                            </Space>
                                            <Space>
                                                <Tag icon={<ToolOutlined />}>{server.tools} tools</Tag>
                                                <Tag icon={<DatabaseOutlined />}>{server.resources} resources</Tag>
                                            </Space>
                                        </div>
                                    }
                                >
                                    <Space direction="vertical" style={{ width: '100%' }}>
                                        <Descriptions bordered size="small" column={1}>
                                            <Descriptions.Item label="Server Process">
                                                <Switch
                                                    checked={isEnabled}
                                                    onChange={(checked) => toggleServer(name, checked)}
                                                    loading={toggling[name]}
                                                    size="small"
                                                />
                                                <span style={{ marginLeft: 8 }}>{isEnabled ? 'Enabled' : 'Disabled'}</span>
                                            </Descriptions.Item>
                                            <Descriptions.Item label="Server Permissions">
                                                <Select value={serverPermission} style={{ width: 120 }} onChange={(value) => updateServerPermission(name, value)}>
                                                    <Option value="enabled">Enabled</Option>
                                                    <Option value="disabled">Disabled</Option>
                                                    <Option value="ask">Ask</Option>
                                                </Select>
                                            </Descriptions.Item>
                                        </Descriptions>
                                        {detailsLoading[name] ? <Spin /> : serverDetails[name] ? (
                                            <Tabs defaultActiveKey="tools">
                                                <TabPane tab={`Tools (${serverDetails[name].tools.length})`} key="tools">
                                                    <List
                                                        dataSource={serverDetails[name].tools}
                                                        renderItem={(tool: MCPTool) => {
                                                            const toolPermission = permissions?.servers?.[name]?.tools?.[tool.name]?.permission || permissions?.defaults?.tool || 'ask';
                                                            return (
                                                                <List.Item>
                                                                    <List.Item.Meta
                                                                        title={tool.name}
                                                                        description={
                                                                            tool.description ? (
                                                                                <div style={{ fontSize: '13px' }}>
                                                                                    <MarkdownRenderer 
                                                                                        markdown={tool.description}
                                                                                        enableCodeApply={false}
                                                                                    />
                                                                                </div>
                                                                            ) : null
                                                                        }
                                                                    />
                                                                    <Select value={toolPermission} style={{ width: 120 }} onChange={(value) => updateToolPermission(name, tool.name, value)}>
                                                                        <Option value="enabled">Enabled</Option>
                                                                        <Option value="disabled">Disabled</Option>
                                                                        <Option value="ask">Ask</Option>
                                                                    </Select>
                                                                </List.Item>
                                                            );
                                                        }}
                                                        locale={{ emptyText: <Empty description="No tools found" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
                                                    />
                                                </TabPane>
                                                <TabPane tab={`Resources (${serverDetails[name].resources.length})`} key="resources">
                                                    <List
                                                        dataSource={serverDetails[name].resources}
                                                        renderItem={(resource: MCPResource) => (
                                                            <List.Item>
                                                                <List.Item.Meta
                                                                    title={resource.name}
                                                                    description={
                                                                        resource.description ? (
                                                                            <div style={{ fontSize: '13px' }}>
                                                                                <MarkdownRenderer 
                                                                                    markdown={resource.description}
                                                                                    enableCodeApply={false}
                                                                                />
                                                                                <div style={{ color: '#999', fontSize: '11px', marginTop: '4px' }}>{resource.uri}</div>
                                                                            </div>
                                                                        ) : resource.uri
                                                                    }
                                                                />
                                                            </List.Item>
                                                        )}
                                                        locale={{ emptyText: <Empty description="No resources found" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
                                                    />
                                                </TabPane>
                                                <TabPane tab={`Prompts (${serverDetails[name].prompts.length})`} key="prompts">
                                                    <List
                                                        dataSource={serverDetails[name].prompts}
                                                        renderItem={(prompt: MCPPrompt) => (
                                                            <List.Item>
                                                                <List.Item.Meta
                                                                    title={prompt.name}
                                                                    description={
                                                                        prompt.description ? (
                                                                            <div style={{ fontSize: '13px' }}>
                                                                                <MarkdownRenderer 
                                                                                    markdown={prompt.description}
                                                                                    enableCodeApply={false}
                                                                                />
                                                                            </div>
                                                                        ) : null
                                                                    }
                                                                />
                                                            </List.Item>
                                                        )}
                                                        locale={{ emptyText: <Empty description="No prompts found" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
                                                    />
                                                </TabPane>
                                            </Tabs>
                                        ) : (
                                            <Alert message="Could not load server details. The server might be disconnected or disabled." type="warning" />
                                        )}
                                    </Space>
                                </Panel>
                            );
                        })}
                    </Collapse>
                </Space>
            ) : (
                <Alert message="Failed to load MCP status" type="error" />
            )}
            
            <MCPRegistryModal
                visible={showRegistry}
                onClose={() => {
                    setShowRegistry(false);
                    fetchMCPStatus(); // Refresh status when registry modal closes
                }}
            />
        </Modal>
    );
};

export default MCPStatusModal;
