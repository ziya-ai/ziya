import React, { useState, useEffect } from 'react';
import { Modal, Tag, Space, Button, Spin, Alert, Descriptions, Switch, message, Collapse, Select, Tabs, List, Empty, Tooltip, Statistic, Card, Row, Col, Divider } from 'antd';
import { useTheme } from '../context/ThemeContext';
import MCPRegistryModal from './MCPRegistryModal';
import MarkdownRenderer from './MarkdownRenderer';
import {
    CheckCircleOutlined,
    CloseCircleOutlined,
    ReloadOutlined,
    ToolOutlined,
    DatabaseOutlined,
    FileTextOutlined,
    CloudServerOutlined,
    ExperimentOutlined,
    WarningOutlined,
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

interface BuiltinToolCategory {
    name: string;
    description: string;
    enabled: boolean;
    dependencies_available: boolean;
    available_tools: string[];
    requires_dependencies?: string[];
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
    token_costs?: {
        servers: Record<string, number>;
        total_tool_tokens: number;
        enabled_tool_tokens: number;
        instructions: {
            total_instruction_tokens: number;
            enabled_models: number;
            total_models: number;
            per_model_cost: number;
        };
    };
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
    const { isDarkMode } = useTheme();
    const [builtinTools, setBuiltinTools] = useState<Record<string, BuiltinToolCategory>>({});
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
            
            // Fetch builtin tools status
            const builtinResponse = await fetch('/api/mcp/builtin-tools/status');
            if (builtinResponse.ok) {
                const builtinData = await builtinResponse.json();
                setBuiltinTools(builtinData.categories || {});
            } else {
                console.error('Failed to fetch builtin tools status');
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

    const handleBuiltinToolToggle = async (category: string, enabled: boolean) => {
        try {
            const response = await fetch('/api/mcp/builtin-tools/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category, enabled })
            });
            
            if (response.ok) {
                setBuiltinTools(prev => ({
                    ...prev,
                    [category]: { ...prev[category], enabled }
                }));
                message.success(`${enabled ? 'Enabled' : 'Disabled'} ${category} builtin tools`);
                window.dispatchEvent(new CustomEvent('mcpStatusChanged')); // Notify other components
            }
        } catch (error) {
            message.error('Failed to update builtin tool settings');
        }
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

    const formatTokenCount = (tokens: number): string => {
        if (tokens >= 1000000) {
            return `${(tokens / 1000000).toFixed(1)}M`;
        }
        if (tokens >= 1000) {
            return `${(tokens / 1000).toFixed(1)}K`;
        }
        return tokens.toString();
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
                    <Row gutter={16}>
                        <Col span={12}>
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
                                style={{ height: '100%' }}
                            />
                        </Col>
                        <Col span={12}>
                            {status.token_costs && (() => {
                                const { total_tool_tokens, enabled_tool_tokens, instructions } = status.token_costs;
                                const grandTotal = total_tool_tokens + instructions.per_model_cost;
                                
                                return (
                                    <Alert
                                        message="Total Context Token Usage"
                                        description={
                                            <div>
                                                <div style={{ fontSize: '20px', fontWeight: 'bold', marginBottom: '8px' }}>
                                                    {formatTokenCount(grandTotal)} <span style={{ fontSize: '14px', fontWeight: 'normal', opacity: 0.7 }}>tokens</span>
                                                </div>
                                                <div style={{ fontSize: '12px', opacity: 0.7 }}>
                                                    <div>🔧 MCP Tools: <strong>{formatTokenCount(total_tool_tokens)}</strong> tokens (from {Object.keys(status.token_costs.servers).length} servers)</div>
                                                    <div>📋 Instructions: <strong>{formatTokenCount(instructions.per_model_cost)}</strong> tokens (for current model)</div>
                                                </div>
                                            </div>
                                        }
                                        type="info"
                                        showIcon
                                        style={{ height: '100%' }}
                                    />
                                );
                            })()}
                            {!status.token_costs && (
                                <Alert
                                    message="Token Usage Unavailable"
                                    description="Token cost calculation is not available"
                                    type="warning"
                                    showIcon
                                    style={{ height: '100%' }}
                                />
                            )}
                        </Col>
                    </Row>
                    
                    {/* Builtin Tools Section */}
                    <div>
                        <Divider orientation="left">
                            <Space>
                                <ExperimentOutlined />
                                Builtin Tools
                                {Object.keys(builtinTools).length > 0 && (
                                    <Tag color="blue">
                                        {Object.values(builtinTools).filter((cat: any) => cat.enabled).length} enabled
                                        {/* Also count builtin servers */}
                                        {status.servers && Object.values(status.servers).filter((s: any) => s.builtin).length > 0 && 
                                            ` + ${Object.values(status.servers).filter((s: any) => s.builtin).length} servers`}
                                    </Tag>
                                )}
                            </Space>
                        </Divider>
                        <div style={{ marginBottom: 16 }}>
                            <Alert
                                type="info"
                                message="Builtin Tool Categories"
                                description="Optional tools that run directly within Ziya without external servers. Perfect for enterprise architects and network engineers."
                                showIcon
                            />
                        </div>
                        
                        {/* Render builtin MCP servers (time, shell) */}
                        {status.servers && Object.entries(status.servers)
                            .filter(([name, server]: [string, any]) => server.builtin)
                            .map(([name, server]: [string, any]) => {
                                const isEnabled = status.server_configs?.[name]?.enabled !== false;
                                const tokenCount = status.token_costs?.servers[name] || 0;
                                
                                return (
                                    <div key={name} style={{ 
                                        marginBottom: 12, 
                                        padding: 16, 
                                        border: isDarkMode ? '1px solid #434343' : '1px solid #eee',
                                        borderRadius: 6,
                                        backgroundColor: isEnabled && server.connected ? (isDarkMode ? '#162312' : '#f6ffed') : (isDarkMode ? '#1f1f1f' : '#fafafa')
                                    }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                            <div style={{ flex: 1 }}>
                                                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
                                                    <strong style={{ fontSize: '14px', textTransform: 'capitalize' }}>{name} Server</strong>
                                                    <Tag color={isEnabled && server.connected ? 'green' : (isEnabled ? 'orange' : 'default')} style={{ marginLeft: 8 }}>
                                                        {isEnabled ? (server.connected ? 'Connected' : 'Disconnected') : 'Disabled'}
                                                    </Tag>
                                                    <Tag color="purple" style={{ marginLeft: 4 }}>
                                                        <ExperimentOutlined /> Builtin Server
                                                    </Tag>
                                                    {server.tools > 0 && (
                                                        <Tag color="blue" style={{ marginLeft: 4 }}>
                                                            <ToolOutlined /> {server.tools} tool{server.tools !== 1 ? 's' : ''}
                                                        </Tag>
                                                    )}
                                                    {tokenCount > 0 && (
                                                        <Tag color="cyan" style={{ marginLeft: 4 }}>
                                                            {formatTokenCount(tokenCount)} tokens
                                                        </Tag>
                                                    )}
                                                </div>
                                                <div style={{ fontSize: '12px', color: isDarkMode ? '#a0a0a0' : '#666', marginBottom: 8 }}>
                                                    {name === 'time' ? 'Provides current date and time information' : 
                                                     name === 'shell' ? 'Execute shell commands with configurable safety controls' : 
                                                     'Built-in MCP server'}
                                                </div>
                                                {server.connected && server.tools > 0 && (
                                                    <div style={{ fontSize: '11px', color: isDarkMode ? '#888' : '#999' }}>
                                                        <Space>
                                                            <span>{server.tools} tools</span>
                                                            {server.resources > 0 && <span>• {server.resources} resources</span>}
                                                            {server.prompts > 0 && <span>• {server.prompts} prompts</span>}
                                                        </Space>
                                                    </div>
                                                )}
                                            </div>
                                            <Switch
                                                checked={isEnabled}
                                                onChange={(checked) => toggleServer(name, checked)}
                                                loading={toggling[name]}
                                                size="small"
                                            />
                                        </div>
                                    </div>
                                );
                            })}
                        
                        {Object.entries(builtinTools).map(([category, config]: [string, BuiltinToolCategory]) => (
                            <div key={category} style={{ 
                                marginBottom: 12, 
                                padding: 16, 
                                border: isDarkMode ? '1px solid #434343' : '1px solid #eee',
                                borderRadius: 6,
                                backgroundColor: config.enabled ? (isDarkMode ? '#162312' : '#f6ffed') : (isDarkMode ? '#1f1f1f' : '#fafafa')
                            }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
                                            <strong style={{ fontSize: '14px' }}>{config.name}</strong>
                                            <Tag color={config.dependencies_available ? 'green' : 'orange'} style={{ marginLeft: 8 }}>
                                                {config.dependencies_available ? 'Ready' : 'Dependencies Missing'}
                                            </Tag>
                                            <Tag color="purple" style={{ marginLeft: 4 }}>
                                                <ExperimentOutlined /> Builtin
                                            </Tag>
                                        </div>
                                        <div style={{ fontSize: '12px', color: isDarkMode ? '#a0a0a0' : '#666', marginBottom: 8 }}>
                                            {config.description}
                                        </div>
                                        {config.requires_dependencies && !config.dependencies_available && (
                                            <Alert 
                                                type="warning" 
                                                message="Missing Dependencies"
                                                description={
                                                    <div>
                                                        Install with: <code>pip install {config.requires_dependencies.join(' ')}</code>
                                                    </div>
                                                }
                                                style={{ marginBottom: 8 }}
                                            />
                                        )}
                                        {config.available_tools && config.available_tools.length > 0 && (
                                            <div style={{ fontSize: '11px', color: isDarkMode ? '#888' : '#999' }}>
                                                Available tools: {config.available_tools.join(', ')}
                                            </div>
                                        )}
                                    </div>
                                    <Switch
                                        checked={config.enabled}
                                        onChange={(checked) => handleBuiltinToolToggle(category, checked)}
                                        disabled={!config.dependencies_available}
                                        size="small"
                                    />
                                </div>
                            </div>
                        ))}
                        
                        {Object.keys(builtinTools).length === 0 && (
                            <Empty 
                                description="No builtin tools available" 
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                style={{ margin: '32px 0' }}
                            />
                        )}
                    </div>
                    
                    {/* MCP Servers Section */}
                    {(() => {
                        // Only show MCP Servers section if there are non-builtin servers
                        const nonBuiltinServers = status.servers && status.server_configs ? 
                            Object.keys(status.server_configs).filter(name => !status.servers[name]?.builtin) : [];
                        
                        if (nonBuiltinServers.length === 0) {
                            return null; // Don't render the section if no non-builtin servers
                        }
                        
                        return (
                            <>
                    <Divider orientation="left">
                        <Space>
                            <CloudServerOutlined />
                                        External MCP Servers
                            {status.servers && (
                                <Tag color="blue">
                                                {nonBuiltinServers.length} configured
                                </Tag>
                            )}
                        </Space>
                    </Divider>
                    
                    <Collapse activeKey={expandedKeys} onChange={handlePanelChange}>
                                    {nonBuiltinServers.map(name => {
                            const server = status.servers[name] || { connected: false, resources: 0, tools: 0, prompts: 0, capabilities: {} };
                            const isEnabled = status.server_configs?.[name]?.enabled !== false;
                            const serverPermission = permissions?.servers?.[name]?.permission || permissions?.defaults?.server || 'ask';
                                        
                                        // Skip builtin servers as they're shown in builtin section
                                        if (server.builtin) return null;

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
                                                {status.token_costs?.servers[name] !== undefined && (
                                                    <Tag color={isEnabled && server.connected ? undefined : 'default'} 
                                                         style={isEnabled && server.connected ? {} : { opacity: 0.5 }}>
                                                        {formatTokenCount(status.token_costs.servers[name])} tokens
                                                    </Tag>
                                                )}
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
                                            {status.token_costs?.servers[name] && (
                                                <Descriptions.Item label="Context Cost">
                                                    <Tag color={isEnabled && server.connected ? undefined : 'default'}
                                                         style={isEnabled && server.connected ? {} : { opacity: 0.5 }}>
                                                        {formatTokenCount(status.token_costs.servers[name])} tokens
                                                    </Tag>
                                                </Descriptions.Item>
                                            )}
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
                            </>
                        );
                    })()}
                    
                    {/* Show message if no external servers configured */}
                    {status.servers && Object.keys(status.servers).filter(name => !status.servers[name]?.builtin).length === 0 && (
                        <Alert message="No external MCP servers configured" type="info" showIcon style={{ marginTop: 16 }} />
                    )}
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
