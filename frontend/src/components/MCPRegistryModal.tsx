import React, { useState, useEffect } from 'react';
import {
    Modal, List, Card, Button, Input, Tag, Space, Tabs, Select,
    message, Spin, Alert, Typography, Divider, Badge, Checkbox
} from 'antd';
import {
    SearchOutlined,
    DownloadOutlined,
    DeleteOutlined,
    InfoCircleOutlined,
    SafetyCertificateOutlined,
    ToolOutlined,
    CloudServerOutlined
} from '@ant-design/icons';

const { Search } = Input;
const { Text, Title, Paragraph } = Typography;
const { Option } = Select;
const { TabPane } = Tabs;

interface MCPRegistryModalProps {
    visible: boolean;
    onClose: () => void;
}

interface RegistryProvider {
    id: string;
    name: string;
    isInternal: boolean;
    supportsSearch: boolean;
}

interface MCPService {
    serviceId: string;
    serviceName: string;
    serviceDescription: string;
    supportLevel: string;
    status: string;
    version: number;
    createdAt: string;
    lastUpdatedAt: string;
    securityReviewLink?: string;
    provider: {
        id: string;
        name: string;
        isInternal: boolean;
    };
    tags?: string[];
    author?: string;
    repositoryUrl?: string;
    instructions: {
        install?: string;
        command: string;
        args?: string[];
    };
}

interface InstalledService {
    serverName: string;
    serviceId: string;
    serviceName: string;
    version?: number;
    supportLevel?: string;
    installedAt?: string;
    enabled: boolean;
}

interface ToolSearchResult {
    service: Partial<MCPService>;
    matchingTools: Array<{
        toolName: string;
        mcpServerId: string;
    }>;
}

const MCPRegistryModal: React.FC<MCPRegistryModalProps> = ({ visible, onClose }) => {
    const [availableServices, setAvailableServices] = useState<MCPService[]>([]);
    const [installedServices, setInstalledServices] = useState<InstalledService[]>([]);
    const [providers, setProviders] = useState<RegistryProvider[]>([]);
    const [toolSearchResults, setToolSearchResults] = useState<ToolSearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [installing, setInstalling] = useState<Record<string, boolean>>({});
    const [searchQuery, setSearchQuery] = useState('');
    const [activeTab, setActiveTab] = useState('browse');
    const [selectedProviders, setSelectedProviders] = useState<string[]>([]);
    const [includeInternal, setIncludeInternal] = useState(true);

    useEffect(() => {
        if (visible) {
            loadProviders();
            loadAvailableServices();
            loadInstalledServices();
        }
    }, [visible]);

    const loadProviders = async () => {
        try {
            const response = await fetch('/api/mcp/registry/providers');
            if (response.ok) {
                const data = await response.json();
                setProviders(data.providers);
                // Select all providers by default
                setSelectedProviders(data.providers.map((p: RegistryProvider) => p.id));
            }
        } catch (error) {
            console.error('Error loading providers:', error);
        }
    };

    const loadAvailableServices = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/mcp/registry/services');
            if (response.ok) {
                const data = await response.json();
                setAvailableServices(data.services);
            } else {
                message.error('Failed to load available services');
            }
        } catch (error) {
            console.error('Error loading services:', error);
            message.error('Error loading services');
        } finally {
            setLoading(false);
        }
    };

    const loadInstalledServices = async () => {
        try {
            const response = await fetch('/api/mcp/registry/services/installed');
            if (response.ok) {
                const data = await response.json();
                setInstalledServices(data.services);
            }
        } catch (error) {
            console.error('Error loading installed services:', error);
        }
    };

    const searchTools = async (query: string) => {
        if (!query.trim()) {
            setToolSearchResults([]);
            return;
        }

        setLoading(true);
        try {
            const response = await fetch('/api/mcp/registry/tools/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query,
                    maxTools: 20,
                    providers: selectedProviders.length > 0 ? selectedProviders : null
                })
            });

            if (response.ok) {
                const data = await response.json();
                setToolSearchResults(data.results);
            } else {
                message.error('Failed to search tools');
            }
        } catch (error) {
            console.error('Error searching tools:', error);
            message.error('Error searching tools');
        } finally {
            setLoading(false);
        }
    };

    const installService = async (serviceId: string) => {
        setInstalling(prev => ({ ...prev, [serviceId]: true }));
        try {
            const response = await fetch('/api/mcp/registry/services/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    service_id: serviceId,
                    provider_id: null // Let the system find the right provider
                })
            });

            if (response.ok) {
                const result = await response.json();
                message.success(`Successfully installed ${result.service_name}`);
                await loadInstalledServices();

                // Refresh MCP status
                window.dispatchEvent(new Event('mcpStatusChanged'));
            } else {
                const error = await response.json();
                message.error(`Installation failed: ${error.detail}`);
            }
        } catch (error) {
            console.error('Installation error:', error);
            message.error('Installation failed');
        } finally {
            setInstalling(prev => ({ ...prev, [serviceId]: false }));
        }
    };

    const uninstallService = async (serverName: string) => {
        try {
            const response = await fetch('/api/mcp/registry/services/uninstall', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ server_name: serverName })
            });

            if (response.ok) {
                message.success('Service uninstalled successfully');
                await loadInstalledServices();

                // Refresh MCP status
                window.dispatchEvent(new Event('mcpStatusChanged'));
            } else {
                const error = await response.json();
                message.error(`Uninstall failed: ${error.detail}`);
            }
        } catch (error) {
            console.error('Uninstall error:', error);
            message.error('Uninstall failed');
        }
    };

    const getSupportLevelColor = (level: string) => {
        switch (level) {
            case 'Recommended': return 'green';
            case 'Supported': return 'blue';
            case 'Under assessment': return 'orange';
            case 'In development': return 'red';
            default: return 'default';
        }
    };

    const isServiceInstalled = (serviceId: string) => {
        return installedServices.some(s => s.serviceId === serviceId);
    };

    const renderServiceCard = (service: MCPService) => (
        <Card
            key={service.serviceId}
            size="small"
            style={{ marginBottom: 16 }}
            actions={[
                <Button
                    key="install"
                    type="primary"
                    icon={<DownloadOutlined />}
                    loading={installing[service.serviceId]}
                    disabled={isServiceInstalled(service.serviceId)}
                    onClick={() => installService(service.serviceId)}
                >
                    {isServiceInstalled(service.serviceId) ? 'Installed' : 'Install'}
                </Button>,
                service.securityReviewLink && (
                    <Button
                        key="security"
                        icon={<SafetyCertificateOutlined />}
                        onClick={() => window.open(service.securityReviewLink, '_blank')}
                    >
                        Security Review
                    </Button>
                )
            ].filter(Boolean)}
        >
            <Card.Meta
                title={
                    <Space>
                        <span>{service.serviceName}</span>
                        <Tag color={getSupportLevelColor(service.supportLevel)}>
                            {service.supportLevel}
                        </Tag>
                        <Tag color={service.provider.isInternal ? 'gold' : 'blue'}>
                            {service.provider.name}
                        </Tag>
                        <Badge count={`v${service.version}`} color="blue" />
                    </Space>
                }
                description={
                    <div>
                        <Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 8 }}>
                            {service.serviceDescription}
                        </Paragraph>
                        <div style={{ marginBottom: 8 }}>
                            {service.tags?.map(tag => (
                                <Tag key={tag}>{tag}</Tag>
                            ))}
                        </div>
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                            ID: {service.serviceId} | Provider: {service.provider.name}
                        </Text>
                    </div>
                }
            />
        </Card>
    );

    const renderInstalledService = (service: InstalledService) => (
        <Card
            key={service.serverName}
            size="small"
            style={{ marginBottom: 16 }}
            actions={[
                <Button
                    key="uninstall"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => uninstallService(service.serverName)}
                >
                    Uninstall
                </Button>
            ]}
        >
            <Card.Meta
                title={
                    <Space>
                        <span>{service.serviceName}</span>
                        {service.supportLevel && (
                            <Tag color={getSupportLevelColor(service.supportLevel)}>
                                {service.supportLevel}
                            </Tag>
                        )}
                        {service.version && (
                            <Badge count={`v${service.version}`} color="blue" />
                        )}
                        <Tag color={service.enabled ? 'green' : 'red'}>
                            {service.enabled ? 'Enabled' : 'Disabled'}
                        </Tag>
                    </Space>
                }
                description={
                    <div>
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                            Server: {service.serverName}
                        </Text>
                        {service.installedAt && (
                            <div>
                                <Text type="secondary" style={{ fontSize: '12px' }}>
                                    Installed: {new Date(service.installedAt).toLocaleDateString()}
                                </Text>
                            </div>
                        )}
                    </div>
                }
            />
        </Card>
    );

    const renderToolSearchResult = (result: ToolSearchResult) => (
        <Card
            key={result.service.serviceId}
            size="small"
            style={{ marginBottom: 16 }}
            actions={[
                <Button
                    key="install"
                    type="primary"
                    icon={<DownloadOutlined />}
                    loading={installing[result.service.serviceId!]}
                    disabled={isServiceInstalled(result.service.serviceId!)}
                    onClick={() => installService(result.service.serviceId!)}
                >
                    {isServiceInstalled(result.service.serviceId!) ? 'Installed' : 'Install'}
                </Button>
            ]}
        >
            <Card.Meta
                title={
                    <Space>
                        <span>{result.service.serviceName}</span>
                        <Tag color={getSupportLevelColor(result.service.supportLevel!)}>
                            {result.service.supportLevel}
                        </Tag>
                    </Space>
                }
                description={
                    <div>
                        <Paragraph ellipsis={{ rows: 1 }} style={{ marginBottom: 8 }}>
                            {result.service.serviceDescription}
                        </Paragraph>
                        <div style={{ marginTop: 8 }}>
                            <Text strong>Matching Tools:</Text>
                            <div style={{ marginTop: 4 }}>
                                {result.matchingTools.map(tool => (
                                    <Tag key={tool.toolName} icon={<ToolOutlined />}>
                                        {tool.toolName}
                                    </Tag>
                                ))}
                            </div>
                        </div>
                    </div>
                }
            />
        </Card>
    );
    return (
        <Modal
            title={
                <Space>
                    <CloudServerOutlined />
                    <span>MCP Registry</span>
                </Space>
            }
            open={visible}
            onCancel={onClose}
            footer={[
                <Button key="close" onClick={onClose}>
                    Close
                </Button>
            ]}
            width={800}
            style={{ top: 20 }}
            bodyStyle={{ height: '70vh', overflow: 'hidden' }}
        >
            <Tabs activeKey={activeTab} onChange={setActiveTab} style={{ height: '100%' }}>
                <TabPane
                    tab={
                        <Space>
                            <SearchOutlined />
                            <span>Browse Services</span>
                        </Space>
                    }
                    key="browse"
                >
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                        <Alert
                            message="Browse and install approved MCP servers"
                            description={
                                <div>
                                    <div>These servers have been reviewed and approved for use.</div>
                                    <div style={{ marginTop: 8 }}>
                                        <Checkbox
                                            checked={includeInternal}
                                            onChange={(e) => {
                                                setIncludeInternal(e.target.checked);
                                                // Reload services when filter changes
                                                setTimeout(loadAvailableServices, 100);
                                            }}
                                        >
                                            Include internal services
                                        </Checkbox>
                                    </div>
                                </div>
                            }
                            type="info"
                            showIcon
                            style={{ marginBottom: 16 }}
                        />

                        <div style={{ flex: 1, overflow: 'auto' }}>
                            {loading ? (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Spin size="large" />
                                </div>
                            ) : (
                                <List
                                    dataSource={availableServices}
                                    renderItem={renderServiceCard}
                                    locale={{ emptyText: 'No services available' }}
                                />
                            )}
                        </div>
                    </div>
                </TabPane>

                <TabPane
                    tab={
                        <Space>
                            <CloudServerOutlined />
                            <span>Installed Services</span>
                            <Badge count={installedServices.length} />
                        </Space>
                    }
                    key="installed"
                >
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                        <Alert
                            message="Manage your installed MCP services"
                            description="These services are currently installed and configured in your system."
                            type="success"
                            showIcon
                            style={{ marginBottom: 16 }}
                        />

                        <div style={{ flex: 1, overflow: 'auto' }}>
                            {installedServices.length === 0 ? (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Text type="secondary">No registry services installed</Text>
                                </div>
                            ) : (
                                <List
                                    dataSource={installedServices}
                                    renderItem={renderInstalledService}
                                />
                            )}
                        </div>
                    </div>
                </TabPane>

                <TabPane
                    tab={
                        <Space>
                            <ToolOutlined />
                            <span>Search Tools</span>
                        </Space>
                    }
                    key="search"
                >
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ marginBottom: 16 }}>
                            <div style={{ marginBottom: 12 }}>
                                <Text strong>Search Providers:</Text>
                                <Select
                                    mode="multiple"
                                    placeholder="Select providers to search"
                                    style={{ width: '100%', marginTop: 4 }}
                                    value={selectedProviders}
                                    onChange={setSelectedProviders}
                                >
                                    {providers.map(provider => (
                                        <Option key={provider.id} value={provider.id}>
                                            {provider.name} {provider.isInternal ? '(Internal)' : ''}
                                        </Option>
                                    ))}
                                </Select>
                            </div>
                            <Search
                                placeholder="Describe what you want to do (e.g., 'file operations', 'database queries')"
                                enterButton="Search Tools"
                                size="large"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                onSearch={searchTools}
                                loading={loading}
                            />
                            <Text type="secondary" style={{ fontSize: '12px', display: 'block', marginTop: 4 }}>
                                Search for MCP servers by describing the tools you need
                            </Text>
                        </div>

                        <div style={{ flex: 1, overflow: 'auto' }}>
                            {loading ? (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Spin size="large" />
                                </div>
                            ) : toolSearchResults.length > 0 ? (
                                <List
                                    dataSource={toolSearchResults}
                                    renderItem={renderToolSearchResult}
                                />
                            ) : searchQuery ? (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Text type="secondary">No tools found for "{searchQuery}"</Text>
                                </div>
                            ) : (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Text type="secondary">Enter a search query to find relevant tools</Text>
                                </div>
                            )}
                        </div>
                    </div>
                </TabPane>
            </Tabs>
        </Modal>
    );
};

export default MCPRegistryModal;
