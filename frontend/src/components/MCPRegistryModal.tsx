import React, { useState, useEffect, useRef } from 'react';
import {
    Modal, List, Card, Button, Input, Tag, Space, Tabs, Select, Tooltip, Switch, Progress, Form, Collapse,
    message, Spin, Alert, Typography, Divider, Badge, Checkbox, Statistic, Row, Col, Empty, Radio, Descriptions, Popconfirm
} from 'antd';
import MarkdownRenderer from './MarkdownRenderer';
import {
    SearchOutlined,
    DownloadOutlined,
    DeleteOutlined,
    ReloadOutlined,
    InfoCircleOutlined,
    SafetyCertificateOutlined,
    ToolOutlined,
    ExperimentOutlined,
    WarningOutlined,
    CloudServerOutlined,
    GithubOutlined,
    StarOutlined,
    ClockCircleOutlined,
    EyeOutlined,
    HeartOutlined,
    HeartFilled,
    DatabaseOutlined,
    GlobalOutlined,
    PlusOutlined,
    SettingOutlined
} from '@ant-design/icons';

const { Search } = Input;
const { Text, Title, Paragraph } = Typography;
const { Option } = Select;
const { Panel } = Collapse;
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
    enabled?: boolean;
    stats?: RegistryStats;
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
        availableIn?: string[];
    };
    tags?: string[];
    author?: string;
    repositoryUrl?: string;
    installationType?: string;
    downloadCount?: number;
    starCount?: number;
    instructions: {
        install?: string;
        command: string;
        args?: string[];
    };
    _dependencies_available?: boolean;
    _available_tools?: string[];
}

interface InstalledService {
    serverName: string;
    serviceId: string;
    serviceName?: string;
    version?: number;
    serviceDescription?: string;
    installationType?: string;
    repositoryUrl?: string;
    securityReviewLink?: string;
    supportLevel?: string;
    installedAt?: string;
    enabled: boolean;
    provider?: {
        id?: string;
        name?: string;
        isInternal?: boolean;
        availableIn?: string[];
    };
    _dependencies_available?: boolean;
    _available_tools?: string[];
    _manually_configured?: boolean;
    downloadCount?: number;
    starCount?: number;
    lastUpdatedAt?: string;
    author?: string;
    tags?: string[];
}

interface ToolSearchResult {
    service: Partial<MCPService>;
    matchingTools: Array<{
        toolName: string;
        mcpServerId: string;
    }>;
}

interface RegistryStats {
    totalServices: number;
    lastFetched?: string;
    fetchTime?: number;
    errorCount?: number;
}

const MCPRegistryModal: React.FC<MCPRegistryModalProps> = ({ visible, onClose }) => {
    const [availableServices, setAvailableServices] = useState<MCPService[]>([]);
    const [totalAvailableServices, setTotalAvailableServices] = useState<number>(0);
    const [installedServices, setInstalledServices] = useState<InstalledService[]>([]);
    const [providers, setProviders] = useState<RegistryProvider[]>([]);
    const [toolSearchResults, setToolSearchResults] = useState<ToolSearchResult[]>([]);
    const [loading, setLoading] = useState(false);
    const [installing, setInstalling] = useState<Record<string, boolean>>({});
    const [searchQuery, setSearchQuery] = useState('');
    const [activeTab, setActiveTab] = useState('browse');
    const [selectedProviders, setSelectedProviders] = useState<string[]>([]);
    const [expandedServices, setExpandedServices] = useState<Record<string, boolean>>({});
    const [stats, setStats] = useState<any>(null);
    const [filterSupport, setFilterSupport] = useState<string>('all');
    const [filterType, setFilterType] = useState<string>('all');
    const [sortBy, setSortBy] = useState<'name' | 'updated' | 'support'>('name');
    const [previewService, setPreviewService] = useState<any>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [showPreview, setShowPreview] = useState(false);
    const [favorites, setFavorites] = useState<string[]>([]);
    const [showOnlyFavorites, setShowOnlyFavorites] = useState(false);
    const [registryStats, setRegistryStats] = useState<Record<string, RegistryStats>>({});
    const [addingRegistry, setAddingRegistry] = useState(false);
    const [newRegistryForm] = Form.useForm();
    const [expandedRegistries, setExpandedRegistries] = useState<string[]>([]);
    const [registryServiceNames, setRegistryServiceNames] = useState<Record<string, string[]>>({});
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const browseServicesScrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (visible) {
            loadProviders();
            loadAvailableServices();
            loadInstalledServices();
            loadStats();
            loadFavorites();
            loadRegistryStats();
        }
    }, [visible]);

    const loadProviders = async () => {
        try {
            const response = await fetch('/api/mcp/registry/providers');
            if (response.ok) {
                const data = await response.json();

                // Add builtin provider
                const builtinProvider = {
                    id: 'builtin',
                    name: 'Ziya Builtin',
                    isInternal: true,
                    supportsSearch: false,
                    enabled: true,
                    stats: {
                        totalServices: Object.keys(data.builtinTools || {}).length,
                        lastFetched: new Date().toISOString()
                    }
                };

                setProviders([builtinProvider, ...data.providers]);
                // Select all providers by default
                setSelectedProviders([builtinProvider, ...data.providers].map((p: RegistryProvider) => p.id));
                console.log('Loaded providers:', data.providers);
            }
        } catch (error) {
            console.error('Error loading providers:', error);
        }
    };

    const loadRegistryServiceNames = async () => {
        try {
            const response = await fetch('/api/mcp/registry/services?max_results=10000');
            if (response.ok) {
                const data = await response.json();
                
                // Group service names by provider
                const servicesByProvider: Record<string, string[]> = {};
                
                data.services.forEach((service: MCPService) => {
                    // Add to all providers that have this service
                    const providers = service.provider.availableIn || [service.provider.id];
                    providers.forEach((providerId: string) => {
                        if (!servicesByProvider[providerId]) {
                            servicesByProvider[providerId] = [];
                        }
                        servicesByProvider[providerId].push(service.serviceName);
                    });
                });
                
                setRegistryServiceNames(servicesByProvider);
            }
        } catch (error) {
            console.error('Error loading registry service names:', error);
        }
    };

    const loadRegistryStats = async () => {
        // For now, calculate stats from available services
        // In the future, this could be a separate endpoint
        try {
            const response = await fetch('/api/mcp/registry/services');
            if (response.ok) {
                const data = await response.json();
                const servicesByProvider: Record<string, number> = {};

                data.services.forEach((service: MCPService) => {
                    const providerId = service.provider.id;
                    servicesByProvider[providerId] = (servicesByProvider[providerId] || 0) + 1;

                    // Also count services available in multiple providers
                    if (service.provider.availableIn) {
                        service.provider.availableIn.forEach((source: string) => {
                            if (source !== providerId) {
                                servicesByProvider[source] = (servicesByProvider[source] || 0) + 1;
                            }
                        });
                    }
                });

                const stats: Record<string, RegistryStats> = {};
                Object.entries(servicesByProvider).forEach(([providerId, count]) => {
                    stats[providerId] = { totalServices: count, lastFetched: new Date().toISOString() };
                });
                setRegistryStats(stats);
            }
        } catch (error) {
            console.error('Error loading registry stats:', error);
        }
    };

    useEffect(() => {
        if (visible && activeTab === 'registries') {
            loadRegistryServiceNames();
        }
    }, [visible, activeTab]);

    const loadAvailableServices = async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/mcp/registry/services');
            if (response.ok) {
                const data = await response.json();
                setAvailableServices(data.services);
                setTotalAvailableServices(data.services.length);

                // Calculate stats
                const byType: Record<string, number> = {};
                const bySupport: Record<string, number> = {};
                data.services.forEach((s: MCPService) => {
                    byType[s.installationType || 'unknown'] = (byType[s.installationType || 'unknown'] || 0) + 1;
                    bySupport[s.supportLevel] = (bySupport[s.supportLevel] || 0) + 1;
                });

                setStats({
                    total: data.services.length,
                    byType,
                    bySupport
                });
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
        // If query is empty, clear search results and show all services
        if (!query.trim()) {
            setToolSearchResults([]);
            setSearchQuery('');
            return;
        }

        setSearchQuery(query);
        
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
    
    // Clear search when switching away from browse tab
    useEffect(() => {
        if (activeTab !== 'browse' && searchQuery) {
            setSearchQuery('');
            setToolSearchResults([]);
        }
    }, [activeTab]);
    
    // Auto-search as user types (debounced)
    useEffect(() => {
        const timeoutId = setTimeout(() => {
            if (searchQuery.trim() && activeTab === 'browse') {
                searchTools(searchQuery);
            }
        }, 500);
        return () => clearTimeout(timeoutId);
    }, [searchQuery, activeTab]);
    
    const installService = async (serviceId: string) => {
        // Handle builtin services differently
        if (serviceId.startsWith('builtin_')) {
            const category = serviceId.replace('builtin_', '');
            
            // ... builtin handling code ...
            return;
        }
        
        setInstalling(prev => ({ ...prev, [serviceId]: true }));
        try {
            const response = await fetch('/api/mcp/registry/services/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    service_id: serviceId,
                    provider_id: null
                })
            });
            
            if (response.ok) {
                const result = await response.json();
                if (result.status === 'success') {
                    const serviceName = result.server_name || result.service_id || serviceId;
                    message.success(`Successfully installed ${serviceName}`);
                } else {
                    message.error(`Installation failed: ${result.error || 'Unknown error'}`);
                }
                await loadInstalledServices();

                // Refresh MCP status
                window.dispatchEvent(new Event('mcpStatusChanged'));
            } else {
                const error = await response.json();
                message.error(`Installation failed: ${error.error || error.detail || 'Unknown error'}`);
            }
        } catch (error) {
            console.error('Installation error:', error);
            message.error('Installation failed');
        } finally {
            setInstalling(prev => ({ ...prev, [serviceId]: false }));
        }
    };

    const isServiceInstalled = (serviceId: string) => {
        // Check if builtin service is enabled
        if (serviceId?.startsWith('builtin_')) {
            const category = serviceId.replace('builtin_', '');
            
            // Check if it's a builtin MCP server (time, shell)
            if (category === 'time' || category === 'shell') {
                // Check MCP status to see if server is enabled
                // This would require passing MCP status as a prop or fetching it
                // For now, we'll check if it exists in installed services
                return installedServices.some(s => s.serverName === category);
            }
            
            // For builtin tool categories, always show as "Enable" button
            return false;
        }
        return installedServices.some(s => s.serviceId === serviceId);
    };

    const getSupportLevelColor = (level: string) => {
        switch (level) {
            case 'Recommended': return 'green';
            case 'Supported': return 'blue';
            case 'Under assessment': return 'orange';
            case 'In development': return 'red';
            case 'Community': return 'cyan';
            case 'Experimental': return 'purple';
            default: return 'default';
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
                
                // Refresh available services to update totals and filtering
                await loadAvailableServices();
                await loadRegistryStats();

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

    const loadStats = async () => {
        // Stats are calculated when services are loaded
    };

    const loadFavorites = async () => {
        try {
            const response = await fetch('/api/mcp/registry/favorites');
            if (response.ok) {
                const data = await response.json();
                setFavorites(data.favorites || []);
            }
        } catch (error) {
            console.error('Error loading favorites:', error);
        }
    };

    const toggleFavorite = async (serviceId: string) => {
        const newFavorites = favorites.includes(serviceId)
            ? favorites.filter(id => id !== serviceId)
            : [...favorites, serviceId];

        setFavorites(newFavorites);

        try {
            await fetch('/api/mcp/registry/favorites', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ favorites: newFavorites })
            });
        } catch (error) {
            console.error('Error updating favorites:', error);
        }
    };

    const toggleRegistry = async (registryId: string, enabled: boolean) => {
        // Update local state immediately for responsive UI
        setProviders(prev => prev.map(p => 
            p.id === registryId ? { ...p, enabled } : p
        ));

        try {
            // This would be a new endpoint to enable/disable registries
            const response = await fetch('/api/mcp/registry/providers/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider_id: registryId, enabled })
            });

            if (response.ok) {
                message.success(`${enabled ? 'Enabled' : 'Disabled'} registry`);
                // Only reload available services if we're enabling a registry
                if (enabled) {
                    await loadAvailableServices();
                }
            } else {
                // Revert local state on failure
                setProviders(prev => prev.map(p => 
                    p.id === registryId ? { ...p, enabled: !enabled } : p
                ));
                message.error('Failed to toggle registry');
            }
        } catch (error) {
            // Revert local state on error
            setProviders(prev => prev.map(p => 
                p.id === registryId ? { ...p, enabled: !enabled } : p
            ));
            console.error('Error toggling registry:', error);
            message.error('Failed to toggle registry');
        }
    };

    const showServicePreview = async (serviceId: string, providerId?: string) => {
        setPreviewLoading(true);
        setShowPreview(true);

        try {
            const url = providerId
                ? `/api/mcp/registry/services/${encodeURIComponent(serviceId)}/preview?provider_id=${providerId}`
                : `/api/mcp/registry/services/${encodeURIComponent(serviceId)}/preview`;

            const response = await fetch(url);

            if (response.ok) {
                const data = await response.json();
                setPreviewService(data);
            } else {
                message.error('Failed to load service preview');
                setShowPreview(false);
            }
        } catch (error) {
            console.error('Error loading preview:', error);
            message.error('Error loading preview');
            setShowPreview(false);
        } finally {
            setPreviewLoading(false);
        }
    };

    const toggleServiceExpanded = (serviceId: string) => {
        setExpandedServices(prev => ({
            ...prev,
            [serviceId]: !prev[serviceId]
        }));
    };

    const renderEnhancedServiceCard = (service: MCPService, searchResults: ToolSearchResult[] = []) => {
        const isBuiltinService = service.serviceId.startsWith('builtin_');
        
        // Check if this service is installed (even if manually configured)
        const installedInstance = installedServices.find(s => s.serviceId === service.serviceId);
        const isInstalled = !!installedInstance;
        const isManuallyConfigured = installedInstance?._manually_configured === true;

        // Find matching tools for this service if we're in search mode
        const matchingResult = searchResults.find(result => result.service.serviceId === service.serviceId);
        const matchingTools = matchingResult?.matchingTools || (service as any)._matchingTools || [];

        return (
            <Card
                key={service.serviceId}
                style={{ marginBottom: 16 }}
                hoverable
                actions={[
                    !isBuiltinService && (
                        <Button
                            key="preview"
                            icon={<EyeOutlined />}
                            onClick={() => showServicePreview(service.serviceId, service.provider.id)}
                        >
                            Preview
                        </Button>
                    ),
                    <Tooltip title={favorites.includes(service.serviceId) ? "Remove from favorites" : "Add to favorites"} key="favorite">
                        <Button
                            icon={favorites.includes(service.serviceId) ? <HeartFilled style={{ color: '#ff4d4f' }} /> : <HeartOutlined />}
                            onClick={() => toggleFavorite(service.serviceId)}
                        />
                    </Tooltip>,
                    <Button
                        key="install"
                        type={isBuiltinService ? "default" : "primary"}
                        icon={<DownloadOutlined />}
                        loading={installing[service.serviceId]}
                        disabled={isInstalled}
                        onClick={() => installService(service.serviceId)}
                    >
                        {isBuiltinService ? (isInstalled ? 'Enabled' : 'Enable') : (isInstalled ? (isManuallyConfigured ? 'Configured' : 'Installed') : 'Install')}
                    </Button>,
                    !isBuiltinService && service.repositoryUrl && (
                        <Tooltip title="View Repository" key="repo">
                            <Button
                                icon={<GithubOutlined />}
                                onClick={() => window.open(service.repositoryUrl, '_blank')}
                            />
                        </Tooltip>
                    ),
                    !isBuiltinService && service.securityReviewLink && (
                        <Tooltip title="Security Review" key="security">
                            <Button
                                key="security"
                                icon={<SafetyCertificateOutlined />}
                                onClick={() => window.open(service.securityReviewLink, '_blank')}
                            />
                        </Tooltip>
                    ),
                    isInstalled && !isBuiltinService && (
                        <Tooltip title="Uninstall service" key="uninstall">
                            <Button
                                icon={<DeleteOutlined />}
                                danger
                                onClick={() => uninstallService(installedInstance.serverName)}
                            />
                        </Tooltip>
                    ),
                    <Tooltip title={expandedServices[service.serviceId] ? "Show less" : "Show more"} key="info">
                        <Button
                            icon={<InfoCircleOutlined />}
                            onClick={() => toggleServiceExpanded(service.serviceId)}
                        />
                    </Tooltip>
                ].filter(Boolean)}
            >
                <Card.Meta
                    title={
                        <Space wrap>
                            <span>{service.serviceName}</span>
                            <Tag color={getSupportLevelColor(service.supportLevel)}>
                                {service.supportLevel}
                            </Tag>
                            {isManuallyConfigured && (
                                <Tag color="orange" icon={<ToolOutlined />}>
                                    Manually Configured
                                </Tag>
                            )}
                            {isBuiltinService ? (
                                <Tag color="purple">
                                    <ExperimentOutlined /> Builtin
                                </Tag>
                            ) : service.provider.availableIn && service.provider.availableIn.length > 1 ? (
                                <Tooltip title={`Available in: ${service.provider.availableIn.join(', ')}`}>
                                    <Tag color="purple">
                                        {service.provider.availableIn.length} sources
                                    </Tag>
                                </Tooltip>
                            ) : (
                                <Tag color={service.provider.isInternal ? 'gold' : 'blue'}>
                                    {service.provider.name}
                                </Tag>
                            )}
                            {service.installationType && (
                                <Tag color="geekblue">{service.installationType}</Tag>
                            )}
                            {isBuiltinService && service._dependencies_available === false && (
                                <Tag color="orange" icon={<WarningOutlined />}>
                                    Dependencies Required
                                </Tag>
                            )}
                        </Space>
                    }
                    description={
                        <div>
                            <Paragraph
                                ellipsis={expandedServices[service.serviceId] ? false : { rows: 2 }}
                                style={{ marginBottom: 8 }}
                            >
                                {service.serviceDescription}
                            </Paragraph>

                            {/* Show matching tools if in search mode */}
                            {matchingTools && matchingTools.length > 0 && (
                                <div style={{ marginBottom: 8 }}>
                                    <Text strong>Matching Tools: </Text>
                                    {matchingTools.map(tool => (
                                        <Tag key={tool.toolName} icon={<ToolOutlined />} color="blue">
                                            {tool.toolName}
                                        </Tag>
                                    ))}
                                </div>
                            )}

                            {isBuiltinService && service._available_tools && (
                                <div style={{ marginBottom: 8 }}>
                                    <Text strong>Available Tools: </Text>
                                    {service._available_tools.map(toolName => (
                                        <Tag key={toolName} color="cyan">{toolName}</Tag>
                                    ))}
                                </div>
                            )}

                            {isBuiltinService && service._dependencies_available === false && (
                                <Alert
                                    message="Dependencies Required"
                                    description={
                                        <div>
                                            Install with: <code>pip install scapy dpkt</code>
                                        </div>
                                    }
                                    style={{ marginBottom: 8 }}
                                />
                            )}

                            {expandedServices[service.serviceId] && (
                                <div style={{
                                    marginTop: 12,
                                    paddingTop: 12,
                                    borderTop: '1px solid #f0f0f0'
                                }}>
                                    <Row gutter={16} style={{ marginBottom: 12 }}>
                                        {service.downloadCount && (
                                            <Col span={8}>
                                                <Statistic
                                                    title="Downloads"
                                                    value={service.downloadCount}
                                                    prefix={<DownloadOutlined />}
                                                    valueStyle={{ fontSize: '16px' }}
                                                />
                                            </Col>
                                        )}
                                        {service.starCount && (
                                            <Col span={8}>
                                                <Statistic
                                                    title="Stars"
                                                    value={service.starCount}
                                                    prefix={<StarOutlined />}
                                                    valueStyle={{ fontSize: '16px' }}
                                                />
                                            </Col>
                                        )}
                                        <Col span={8}>
                                            <Statistic
                                                title="Updated"
                                                value={new Date(service.lastUpdatedAt).toLocaleDateString()}
                                                prefix={<ClockCircleOutlined />}
                                                valueStyle={{ fontSize: '14px' }}
                                            />
                                        </Col>
                                    </Row>

                                    {service.author && (
                                        <div style={{ marginBottom: 8 }}>
                                            <Text strong>Author: </Text>
                                            <Text>{service.author}</Text>
                                        </div>
                                    )}

                                    {service.provider.availableIn && service.provider.availableIn.length > 1 && (
                                        <div style={{ marginBottom: 8 }}>
                                            <Text strong>Available in: </Text>
                                            {service.provider.availableIn.map(source => (
                                                <Tag key={source} color="blue">{source}</Tag>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}

                            <div style={{ marginBottom: 8 }}>
                                {service.tags?.map(tag => (
                                    <Tag key={tag} color="default">{tag}</Tag>
                                ))}
                            </div>

                            <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                                <span>ID: {service.serviceId}</span>
                                {service.version && <span>v{service.version}</span>}
                            </Text>
                        </div>
                    }
                />
            </Card>
        );
    };

    const renderServiceCard = (service: MCPService) => {
        const isBuiltinService = service.serviceId.startsWith('builtin_');
        
        // Check if this service is installed (even if manually configured)
        const installedInstance = installedServices.find(s => s.serviceId === service.serviceId);
        const isInstalled = !!installedInstance;
        const isManuallyConfigured = installedInstance?._manually_configured === true;

        return (
            <Card
                key={service.serviceId}
                style={{ marginBottom: 16 }}
                hoverable
                actions={[
                    !isBuiltinService && (
                        <Button
                            key="preview"
                            icon={<EyeOutlined />}
                            onClick={() => showServicePreview(service.serviceId, service.provider.id)}
                        >
                            Preview
                        </Button>
                    ),
                    <Tooltip title={favorites.includes(service.serviceId) ? "Remove from favorites" : "Add to favorites"} key="favorite">
                        <Button
                            icon={favorites.includes(service.serviceId) ? <HeartFilled style={{ color: '#ff4d4f' }} /> : <HeartOutlined />}
                            onClick={() => toggleFavorite(service.serviceId)}
                        />
                    </Tooltip>,
                    <Button
                        key="install"
                        type={isBuiltinService ? "default" : "primary"}
                        icon={<DownloadOutlined />}
                        loading={installing[service.serviceId]}
                        disabled={isInstalled}
                        onClick={() => installService(service.serviceId)}
                    >
                        {isBuiltinService ? (isInstalled ? 'Enabled' : 'Enable') : (isInstalled ? (isManuallyConfigured ? 'Configured' : 'Installed') : 'Install')}
                    </Button>,
                    !isBuiltinService && service.repositoryUrl && (
                        <Tooltip title="View Repository" key="repo">
                            <Button
                                icon={<GithubOutlined />}
                                onClick={() => window.open(service.repositoryUrl, '_blank')}
                            />
                        </Tooltip>
                    ),
                    !isBuiltinService && service.securityReviewLink && (
                        <Tooltip title="Security Review" key="security">
                            <Button
                                key="security"
                                icon={<SafetyCertificateOutlined />}
                                onClick={() => window.open(service.securityReviewLink, '_blank')}
                            />
                        </Tooltip>
                    ),
                    <Tooltip title={expandedServices[service.serviceId] ? "Show less" : "Show more"} key="info">
                        <Button
                            icon={<InfoCircleOutlined />}
                            onClick={() => toggleServiceExpanded(service.serviceId)}
                        />
                    </Tooltip>
                ].filter(Boolean)}
            >
                <Card.Meta
                    title={
                        <Space wrap>
                            <span>{service.serviceName}</span>
                            <Tag color={getSupportLevelColor(service.supportLevel)}>
                                {service.supportLevel}
                            </Tag>
                            {isManuallyConfigured && (
                                <Tag color="orange" icon={<ToolOutlined />}>
                                    Manually Configured
                                </Tag>
                            )}
                            {isBuiltinService ? (
                                <Tag color="purple">
                                    <ExperimentOutlined /> Builtin
                                </Tag>
                            ) : service.provider.availableIn && service.provider.availableIn.length > 1 ? (
                                <Tooltip title={`Available in: ${service.provider.availableIn.join(', ')}`}>
                                    <Tag color="purple">
                                        {service.provider.availableIn.length} sources
                                    </Tag>
                                </Tooltip>
                            ) : (
                                <Tag color={service.provider.isInternal ? 'gold' : 'blue'}>
                                    {service.provider.name}
                                </Tag>
                            )}
                            {service.installationType && (
                                <Tag color="geekblue">{service.installationType}</Tag>
                            )}
                            {isBuiltinService && service._dependencies_available === false && (
                                <Tag color="orange" icon={<WarningOutlined />}>
                                    Dependencies Required
                                </Tag>
                            )}
                        </Space>
                    }
                    description={
                        <div>
                            <Paragraph
                                ellipsis={expandedServices[service.serviceId] ? false : { rows: 2 }}
                                style={{ marginBottom: 8 }}
                            >
                                {service.serviceDescription}
                            </Paragraph>

                            {isBuiltinService && service._available_tools && (
                                <div style={{ marginBottom: 8 }}>
                                    <Text strong>Available Tools: </Text>
                                    {service._available_tools.map(toolName => (
                                        <Tag key={toolName} color="cyan">{toolName}</Tag>
                                    ))}
                                </div>
                            )}

                            {isBuiltinService && service._dependencies_available === false && (
                                <Alert
                                    message="Dependencies Required"
                                    description={
                                        <div>
                                            Install with: <code>pip install scapy dpkt</code>
                                        </div>
                                    }
                                    style={{ marginBottom: 8 }}
                                />
                            )}

                            {expandedServices[service.serviceId] && (
                                <div style={{
                                    marginTop: 12,
                                    paddingTop: 12,
                                    borderTop: '1px solid #f0f0f0'
                                }}>
                                    <Row gutter={16} style={{ marginBottom: 12 }}>
                                        {service.downloadCount && (
                                            <Col span={8}>
                                                <Statistic
                                                    title="Downloads"
                                                    value={service.downloadCount}
                                                    prefix={<DownloadOutlined />}
                                                    valueStyle={{ fontSize: '16px' }}
                                                />
                                            </Col>
                                        )}
                                        {service.starCount && (
                                            <Col span={8}>
                                                <Statistic
                                                    title="Stars"
                                                    value={service.starCount}
                                                    prefix={<StarOutlined />}
                                                    valueStyle={{ fontSize: '16px' }}
                                                />
                                            </Col>
                                        )}
                                        <Col span={8}>
                                            <Statistic
                                                title="Updated"
                                                value={new Date(service.lastUpdatedAt).toLocaleDateString()}
                                                prefix={<ClockCircleOutlined />}
                                                valueStyle={{ fontSize: '14px' }}
                                            />
                                        </Col>
                                    </Row>

                                    {service.author && (
                                        <div style={{ marginBottom: 8 }}>
                                            <Text strong>Author: </Text>
                                            <Text>{service.author}</Text>
                                        </div>
                                    )}

                                    {service.provider.availableIn && service.provider.availableIn.length > 1 && (
                                        <div style={{ marginBottom: 8 }}>
                                            <Text strong>Available in: </Text>
                                            {service.provider.availableIn.map(source => (
                                                <Tag key={source} color="blue">{source}</Tag>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}

                            <div style={{ marginBottom: 8 }}>
                                {service.tags?.map(tag => (
                                    <Tag key={tag} color="default">{tag}</Tag>
                                ))}
                            </div>

                            <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                                <span>ID: {service.serviceId}</span>
                                {service.version && <span>v{service.version}</span>}
                            </Text>
                        </div>
                    }
                />
            </Card>
        );
    };

    const renderInstalledService = (service: InstalledService) => {
        if (!service || !service.serviceId) {
            return null; // Skip rendering if service is invalid
        }
        
        const isBuiltinService = service.serviceId?.startsWith('builtin_') || false;
        const isManuallyConfigured = service._manually_configured === true;
        
        return (
            <Card
                key={service.serviceId}
                style={{ marginBottom: 16 }}
                hoverable
                actions={[
                    !isBuiltinService && (
                        <Button
                            key="preview"
                            icon={<EyeOutlined />}
                            onClick={() => showServicePreview(service.serviceId, service.provider?.id)}
                        >
                            Preview
                        </Button>
                    ),
                    <Tooltip title={favorites.includes(service.serviceId) ? "Remove from favorites" : "Add to favorites"} key="favorite">
                        <Button
                            icon={favorites.includes(service.serviceId) ? <HeartFilled style={{ color: '#ff4d4f' }} /> : <HeartOutlined />}
                            onClick={() => toggleFavorite(service.serviceId)}
                        />
                    </Tooltip>,
                    <Button
                        key="install"
                        type={isBuiltinService ? "default" : "primary"}
                        icon={<DownloadOutlined />}
                        loading={installing[service.serviceId]}
                        disabled={isServiceInstalled(service.serviceId)}
                        onClick={() => installService(service.serviceId)}
                    >
                        {isBuiltinService ? (isServiceInstalled(service.serviceId) ? 'Enabled' : 'Enable') : (isServiceInstalled(service.serviceId) ? 'Installed' : 'Install')}
                    </Button>,
                    !isBuiltinService && service.repositoryUrl && (
                        <Tooltip title="View Repository" key="repo">
                            <Button
                                icon={<GithubOutlined />}
                                onClick={() => window.open(service.repositoryUrl, '_blank')}
                            />
                        </Tooltip>
                    ),
                    !isBuiltinService && service.securityReviewLink && (
                        <Tooltip title="Security Review" key="security">
                            <Button
                                key="security"
                                icon={<SafetyCertificateOutlined />}
                                onClick={() => window.open(service.securityReviewLink, '_blank')}
                            />
                        </Tooltip>
                    ),
                    <Tooltip title={expandedServices[service.serviceId] ? "Show less" : "Show more"} key="info">
                        <Button
                            icon={<InfoCircleOutlined />}
                            onClick={() => toggleServiceExpanded(service.serviceId)}
                        />
                    </Tooltip>
                ].filter(Boolean)}
            >
                <Card.Meta
                    title={
                        <Space wrap>
                            <span>{service.serviceName}</span>
                            <Tag color={getSupportLevelColor(service.supportLevel || 'Community')}>
                                {service.supportLevel || 'Community'}
                            </Tag>
                            {isManuallyConfigured && (
                                <Tag color="orange" icon={<ToolOutlined />}>
                                    Manually Configured
                                </Tag>
                            )}
                            {isBuiltinService ? (
                                <Tag color="purple">
                                    <ExperimentOutlined /> Builtin
                                </Tag>
                            ) : service.provider?.availableIn && service.provider.availableIn.length > 1 ? (
                                <Tooltip title={`Available in: ${service.provider.availableIn.join(', ')}`}>
                                    <Tag color="purple">
                                        {service.provider.availableIn.length} sources
                                    </Tag>
                                </Tooltip>
                            ) : (
                                <Tag color={service.provider?.isInternal ? 'gold' : 'blue'}>
                                    {service.provider?.name || 'Unknown'}
                                </Tag>
                            )}
                            {service.installationType && (
                                <Tag color="geekblue">{service.installationType}</Tag>
                            )}
                            {isBuiltinService && service._dependencies_available === false && (
                                <Tag color="orange" icon={<WarningOutlined />}>
                                    Dependencies Required
                                </Tag>
                            )}
                        </Space>
                    }
                    description={
                        <div>
                            <Paragraph
                                ellipsis={expandedServices[service.serviceId] ? false : { rows: 2 }}
                                style={{ marginBottom: 8 }}
                            >
                                {service.serviceDescription || service.serviceName}
                            </Paragraph>
                            
                            {isBuiltinService && service._available_tools && (
                                <div style={{ marginBottom: 8 }}>
                                    <Text strong>Available Tools: </Text>
                                    {service._available_tools.map(toolName => (
                                        <Tag key={toolName} color="cyan">{toolName}</Tag>
                                    ))}
                                </div>
                            )}
                            
                            {isBuiltinService && service._dependencies_available === false && (
                                <Alert
                                    message="Dependencies Required"
                                    description={
                                        <div>
                                            Install with: <code>pip install scapy dpkt</code>
                                        </div>
                                    }
                                    style={{ marginBottom: 8 }}
                                />
                            )}

                            {isBuiltinService && service._available_tools && (
                                <div style={{ marginBottom: 8 }}>
                                    <Text strong>Available Tools: </Text>
                                    {service._available_tools.map(toolName => (
                                        <Tag key={toolName} color="cyan">{toolName}</Tag>
                                    ))}
                                </div>
                            )}

                            {isBuiltinService && service._dependencies_available === false && (
                                <Alert
                                    message="Dependencies Required"
                                    description={
                                        <div>
                                            Install with: <code>pip install scapy dpkt</code>
                                        </div>
                                    }
                                    style={{ marginBottom: 8 }}
                                />
                            )}

                            {expandedServices[service.serviceId] && (
                                <div style={{
                                    marginTop: 12,
                                    paddingTop: 12,
                                    borderTop: '1px solid #f0f0f0'
                                }}>
                                    <Row gutter={16} style={{ marginBottom: 12 }}>
                                        {service.downloadCount && (
                                            <Col span={8}>
                                                <Statistic
                                                    title="Downloads"
                                                    value={service.downloadCount}
                                                    prefix={<DownloadOutlined />}
                                                    valueStyle={{ fontSize: '16px' }}
                                                />
                                            </Col>
                                        )}
                                        {service.starCount && (
                                            <Col span={8}>
                                                <Statistic
                                                    title="Stars"
                                                    value={service.starCount}
                                                    prefix={<StarOutlined />}
                                                    valueStyle={{ fontSize: '16px' }}
                                                />
                                            </Col>
                                        )}
                                        <Col span={8}>
                                            <Statistic
                                                title="Updated"
                                                value={service.lastUpdatedAt ? new Date(service.lastUpdatedAt).toLocaleDateString() : 'N/A'}
                                                prefix={<ClockCircleOutlined />}
                                                valueStyle={{ fontSize: '14px' }}
                                            />
                                        </Col>
                                    </Row>

                                    {service.author && (
                                        <div style={{ marginBottom: 8 }}>
                                            <Text strong>Author: </Text>
                                            <Text>{service.author}</Text>
                                        </div>
                                    )}

                                    {service.provider?.availableIn && service.provider.availableIn.length > 1 && (
                                        <div style={{ marginBottom: 8 }}>
                                            <Text strong>Available in: </Text>
                                            {service.provider.availableIn.map(source => (
                                                <Tag key={source} color="blue">{source}</Tag>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}

                            <div style={{ marginBottom: 8 }}>
                                {service.tags?.map(tag => (
                                    <Tag key={tag} color="default">{tag}</Tag>
                                ))}
                            </div>

                            <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                                <span>ID: {service.serviceId}</span>
                                {service.version && <span>v{service.version}</span>}
                            </Text>
                        </div>
                    }
                />
            </Card>
        );
    };

    const renderToolSearchResult = (result: ToolSearchResult) => (
        <Card
            key={result.service.serviceId}
            style={{ marginBottom: 16 }}
            actions={[
                <Button
                    key="preview"
                    icon={<EyeOutlined />}
                    onClick={() => showServicePreview(result.service.serviceId!, result.service.provider?.id)}
                >
                    Preview
                </Button>,
                result.service.repositoryUrl && (
                    <Tooltip title="View Repository" key="repo">
                        <Button
                            icon={<GithubOutlined />}
                            onClick={() => window.open(result.service.repositoryUrl, '_blank')}
                        />
                    </Tooltip>
                ),
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
            ].filter(Boolean)}
        >
            <Card.Meta
                title={
                    <Space>
                        <span>{result.service.serviceName}</span>
                        <Tag color={getSupportLevelColor(result.service.supportLevel!)}>
                            {result.service.supportLevel}
                        </Tag>
                        {result.service.provider && (
                            <Tag color={result.service.provider.isInternal ? 'gold' : 'blue'}>
                                {result.service.provider.name}
                            </Tag>
                        )}
                    </Space>
                }
                description={
                    <div>
                        <Paragraph ellipsis={{ rows: 1 }} style={{ marginBottom: 8 }}>
                            {result.service.serviceDescription}
                        </Paragraph>
                        <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                            <span>ID: {result.service.serviceId}</span>
                            {result.service.version && <span>v{result.service.version}</span>}
                        </Text>
                        {result.service.provider?.availableIn && result.service.provider.availableIn.length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                                <Text type="secondary" style={{ fontSize: '12px' }}>Available in: </Text>
                                {result.service.provider.availableIn.map((registry: string, index: number) => (
                                    <Tag key={registry} color="default">
                                        {registry}
                                    </Tag>
                                ))}
                            </div>
                        )}
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

    const renderRegistryCard = (provider: RegistryProvider) => {
        const stats = registryStats[provider.id] || { totalServices: 0 };
        const isEnabled = provider.enabled !== false; // Default to enabled if not specified
        const refreshing = loading; // Could track per-provider loading state
        const serviceNames = registryServiceNames[provider.id] || [];

        return (
            <Card
                key={provider.id}
                style={{
                    marginBottom: 16,
                    opacity: isEnabled ? 1 : 0.6,
                    border: provider.isInternal ? '1px solid #faad14' : undefined
                }}
                actions={[
                    <Tooltip title="Refresh registry data" key="refresh">
                        <Button
                            icon={<ReloadOutlined />}
                            onClick={() => refreshRegistry(provider.id)}
                            loading={refreshing}
                            disabled={!isEnabled}
                        />
                    </Tooltip>,
                    <Tooltip title="Registry settings" key="settings">
                        <Button
                            icon={<SettingOutlined />}
                            onClick={() => message.info('Registry settings coming soon')}
                        />
                    </Tooltip>,
                    !provider.isInternal && (
                        <Tooltip title="Remove custom registry" key="remove">
                            <Button
                                icon={<DeleteOutlined />}
                                danger
                                onClick={() => message.info('Remove registry coming soon')}
                            />
                        </Tooltip>
                    )
                ].filter(Boolean)}
                hoverable
            >
                <Card.Meta
                    title={
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Space>
                                <span>{provider.name}</span>
                                {provider.isInternal && (
                                    <Tag color="gold">Internal</Tag>
                                )}
                                {provider.supportsSearch && (
                                    <Tag color="blue">Search</Tag>
                                )}
                                <Tag color="default" style={{ fontSize: '11px' }}>
                                    {provider.id}
                                </Tag>
                                {stats.errorCount && stats.errorCount > 0 && (
                                    <Tag color="red">{stats.errorCount} errors</Tag>
                                )}
                            </Space>
                            <Switch
                                checked={isEnabled}
                                onChange={(checked) => toggleRegistry(provider.id, checked)}
                            />
                        </div>
                    }
                    description={
                        <div>
                            <Space direction="vertical" style={{ width: '100%' }}>
                                <Row gutter={16}>
                                    <Col span={8}>
                                        <Collapse
                                            ghost
                                            size="small"
                                            activeKey={expandedRegistries}
                                            onChange={(keys) => setExpandedRegistries(Array.isArray(keys) ? keys : [keys])}
                                        >
                                            <Panel
                                                key={provider.id}
                                                header={
                                                    <Statistic
                                                        title="Services"
                                                        value={stats.totalServices}
                                                        prefix={<DatabaseOutlined />}
                                                        valueStyle={{ fontSize: '18px', color: isEnabled ? '#1890ff' : '#bfbfbf' }}
                                                    />
                                                }
                                                showArrow={serviceNames.length > 0}
                                            >
                                                {serviceNames.length > 0 ? (
                                                    <div style={{ maxHeight: '200px', overflow: 'auto', paddingLeft: '10px' }}>
                                                        {serviceNames.map(name => (
                                                            <div key={name} style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: '#666' }}>
                                                                • {name}
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : (
                                                    <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>No services available</Text>
                                                )}
                                            </Panel>
                                        </Collapse>
                                    </Col>
                                    <Col span={8}>
                                        <div style={{ textAlign: 'center' }}>
                                            <div style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c', marginBottom: '4px' }}>
                                                Status
                                            </div>
                                            <Tag color={isEnabled ? 'green' : 'default'}>
                                                {isEnabled ? 'Active' : 'Disabled'}
                                            </Tag>
                                        </div>
                                    </Col>
                                    <Col span={8}>
                                        {stats.lastFetched && (
                                            <div style={{ textAlign: 'center' }}>
                                                <div style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c', marginBottom: '4px' }}>
                                                    Last Updated
                                                </div>
                                                <div style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                                                    {new Date(stats.lastFetched).toLocaleTimeString()}
                                                </div>
                                            </div>
                                        )}
                                    </Col>
                                </Row>

                                <div style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c' }}>
                                    <Text code>{provider.id}</Text>
                                    {stats.fetchTime && (
                                        <span style={{ marginLeft: 8 }}>
                                            • Fetch time: {stats.fetchTime}ms
                                        </span>
                                    )}
                                    {stats.errorCount && stats.errorCount > 0 && (
                                        <span style={{ marginLeft: 8, color: '#ff4d4f' }}>
                                            • {stats.errorCount} errors
                                        </span>
                                    )}
                                </div>
                            </Space>
                        </div>
                    }
                />
            </Card>
        );
    };

    const addCustomRegistry = async (values: any) => {
        try {
            // This would be a new endpoint to add custom registries
            const response = await fetch('/api/mcp/registry/providers/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(values)
            });

            if (response.ok) {
                message.success('Custom registry added successfully');
                setAddingRegistry(false);
                newRegistryForm.resetFields();
                await loadProviders();
            } else {
                const error = await response.json();
                message.error(`Failed to add registry: ${error.detail}`);
            }
        } catch (error) {
            console.error('Error adding registry:', error);
            message.error('Failed to add registry');
        }
    };

    const refreshRegistry = async (providerId: string) => {
        try {
            const response = await fetch(`/api/mcp/registry/providers/${providerId}/refresh`, {
                method: 'POST'
            });

            if (response.ok) {
                message.success('Registry refreshed successfully');
                await loadRegistryStats();
                await loadAvailableServices();
            }
        } catch (error) {
            console.error('Error refreshing registry:', error);
            message.error('Failed to refresh registry');
        }
    };

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
            bodyStyle={{ height: '70vh', overflow: 'auto', display: 'flex', flexDirection: 'column' }}
            destroyOnClose={false}
        >
            <Tabs activeKey={activeTab} onChange={setActiveTab} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                <TabPane
                    tab={
                        <Space>
                            <GlobalOutlined />
                            <span>Registries</span>
                            <Badge count={providers.length} />
                        </Space>
                    }
                    key="registries"
                >
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        {/* Registry Overview Stats */}
                        <Card size="small" style={{ marginBottom: 16 }} bodyStyle={{ padding: '12px 16px' }}>
                            <Row gutter={16} align="middle">
                                <Col span={6}>
                                    <div>
                                        <Statistic
                                            title="Registries"
                                            value={providers.length}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#52c41a' }}
                                            prefix={<GlobalOutlined />}
                                        />
                                        <div style={{ marginTop: '8px', paddingLeft: '10px', fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c' }}>
                                            <div>↳ Internal: {providers.filter(p => p.isInternal).length}</div>
                                            <div>↳ External: {providers.filter(p => !p.isInternal).length}</div>
                                        </div>
                                    </div>
                                </Col>
                                <Col span={6}>
                                    <div>
                                        <Statistic
                                            title="Services"
                                            value={totalAvailableServices}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#1890ff' }}
                                        />
                                        <div style={{ marginTop: '8px', paddingLeft: '10px', fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c' }}>
                                            <div>↳ Installed: {installedServices.length}</div>
                                            <div>↳ Available: {totalAvailableServices - installedServices.length}</div>
                                        </div>
                                    </div>
                                </Col>
                                <Col span={6}>
                                    <div>
                                        <Statistic
                                            title="Support Levels"
                                            value={Object.keys(stats?.bySupport || {}).length}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#faad14' }}
                                        />
                                        <div style={{ marginTop: '8px', paddingLeft: '10px', fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c' }}>
                                            {stats && Object.entries(stats.bySupport).slice(0, 2).map(([level, count]) => (
                                                <div key={level}>↳ {level}: {count as number}</div>
                                            ))}
                                        </div>
                                    </div>
                                </Col>
                                <Col span={6}>
                                    <div>
                                        <Statistic
                                            title="Install Types"
                                            value={Object.keys(stats?.byType || {}).length}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#722ed1' }}
                                        />
                                        <div style={{ marginTop: '8px', paddingLeft: '10px', fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c' }}>
                                            {stats && Object.entries(stats.byType).slice(0, 2).map(([type, count]) => (
                                                <div key={type}>↳ {type}: {count as number}</div>
                                            ))}
                                        </div>
                                    </div>
                                </Col>
                            </Row>
                        </Card>

                        {/* Add Custom Registry Card */}
                        <Card
                            style={{ marginBottom: 16 }}
                            title={
                                <Space>
                                    <PlusOutlined />
                                    <span>Add Custom Registry</span>
                                </Space>
                            }
                        >
                            {!addingRegistry ? (
                                <Button
                                    type="dashed"
                                    icon={<PlusOutlined />}
                                    onClick={() => setAddingRegistry(true)}
                                    style={{ width: '100%' }}
                                >
                                    Add Custom Registry Endpoint
                                </Button>
                            ) : (
                                <Form
                                    form={newRegistryForm}
                                    onFinish={addCustomRegistry}
                                    layout="vertical"
                                >
                                    <Row gutter={16}>
                                        <Col span={12}>
                                            <Form.Item
                                                label="Registry Name"
                                                name="name"
                                                rules={[{ required: true, message: 'Please enter a name' }]}
                                            >
                                                <Input placeholder="e.g., Company Internal Registry" />
                                            </Form.Item>
                                        </Col>
                                        <Col span={12}>
                                            <Form.Item
                                                label="Base URL"
                                                name="baseUrl"
                                                rules={[
                                                    { required: true, message: 'Please enter a URL' },
                                                    { type: 'url', message: 'Please enter a valid URL' }
                                                ]}
                                            >
                                                <Input placeholder="https://registry.example.com" />
                                            </Form.Item>
                                        </Col>
                                    </Row>
                                    <Row gutter={16}>
                                        <Col span={12}>
                                            <Form.Item label="Authentication" name="authType">
                                                <Select defaultValue="none">
                                                    <Option value="none">None</Option>
                                                    <Option value="bearer">Bearer Token</Option>
                                                    <Option value="basic">Basic Auth</Option>
                                                </Select>
                                            </Form.Item>
                                        </Col>
                                        <Col span={12}>
                                            <Space>
                                                <Button type="primary" htmlType="submit">Add Registry</Button>
                                                <Button onClick={() => {
                                                    setAddingRegistry(false);
                                                    newRegistryForm.resetFields();
                                                }}>Cancel</Button>
                                            </Space>
                                        </Col>
                                    </Row>
                                </Form>
                            )}
                        </Card>

                        {/* Registry List */}
                        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
                            {loading ? (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Spin size="large" tip="Loading registry providers..." />
                                </div>
                            ) : providers.length === 0 ? (
                                <Empty description="No registry providers found" />
                            ) : (
                                <List
                                    dataSource={providers}
                                    renderItem={renderRegistryCard}
                                />
                            )}
                        </div>
                    </div>
                </TabPane>

                <TabPane
                    tab={
                        <Space>
                            <ToolOutlined />
                            <span>Browse Services</span>
                        </Space>
                    }
                    key="browse"
                >
                    <div ref={browseServicesScrollRef} style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        {/* Summary Stats Card */}
                        {(() => {
                            // Calculate filtered stats
                            let displayServices = searchQuery && toolSearchResults.length > 0 
                                ? toolSearchResults.map(result => result.service).filter(s => s.serviceId) as Partial<MCPService>[]
                                : availableServices;

                            // Apply provider filter to display services
                            if (selectedProviders.length > 0) {
                                displayServices = displayServices.filter(s => 
                                    s.provider && (selectedProviders.includes(s.provider.id) || 
                                    (s.provider.availableIn && s.provider.availableIn.some(p => selectedProviders.includes(p)))));
                            }

                            const filteredTotal = displayServices.length;
                            const isFiltered = filteredTotal !== totalAvailableServices || searchQuery || selectedProviders.length !== providers.length;
                            
                            return isFiltered ? (
                                <Alert
                                    message={`Showing ${filteredTotal} of ${totalAvailableServices} services`}
                                    description={searchQuery ? `Search: "${searchQuery}"` : 'Filters applied'}
                                    type="info"
                                    style={{ marginBottom: 16 }}
                                    showIcon
                                />
                            ) : null;
                        })()}
                        
                        {stats && (
                            <Card
                                style={{ marginBottom: 16 }}
                                bodyStyle={{ padding: '12px 16px' }}
                            >
                                <Row gutter={16} align="middle">
                                    <Col span={6}>
                                        <Statistic
                                            title="Available"
                                            value={stats.total}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#1890ff' }}
                                            prefix={<CloudServerOutlined />}
                                        />
                                    </Col>
                                    <Col span={6}>
                                        <Statistic
                                            title="Registries"
                                            value={providers.length}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#52c41a' }}
                                        />
                                    </Col>
                                    <Col span={6}>
                                        <Statistic
                                            title="Installed"
                                            value={installedServices.length}
                                            valueStyle={{ fontSize: '24px', fontWeight: 'bold', color: '#722ed1' }}
                                        />
                                    </Col>
                                    <Col span={6}>
                                        <div style={{ textAlign: 'left' }}>
                                            <div style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c', marginBottom: '4px' }}>Top Type</div>
                                            <div>
                                                <span style={{ fontSize: '16px', fontWeight: 'bold' }}>
                                                    {(() => {
                                                        const topEntry = Object.entries(stats.byType).sort((a, b) => (b[1] as number) - (a[1] as number))[0];
                                                        return topEntry?.[0] || 'N/A';
                                                    })()}
                                                </span>
                                            </div>
                                            <div>
                                                <span style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between', color: '#8c8c8c' }}>
                                                    {(() => {
                                                        const topEntry = Object.entries(stats.byType).sort((a, b) => (b[1] as number) - (a[1] as number))[0];
                                                        const count = topEntry?.[1] || 0;
                                                        return `(${count} servers)`;
                                                    })()}
                                                </span>
                                            </div>
                                        </div>
                                    </Col>
                                </Row>
                            </Card>
                        )}

                        {/* Filters Card */}
                        <Card size="small" style={{ marginBottom: 16 }}>
                            <div style={{ marginBottom: 12 }}>
                                <Text strong>Search & Filter:</Text>
                                <Search
                                    placeholder="Search services or describe tools you need (e.g., 'file operations')"
                                    enterButton="Search"
                                    size="middle"
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    onSearch={searchTools}
                                    loading={loading}
                                    style={{ marginTop: 4 }}
                                />
                                <Text type="secondary" style={{ fontSize: '12px', display: 'block', marginTop: 4 }}>
                                    Search by name, description, or tool functionality
                                </Text>
                            </div>
                        </Card>

                        {/* Advanced Filters Card */}
                        <Card size="small" style={{ marginBottom: 16 }}>
                            <Row gutter={16}>
                                <Col span={24} style={{ marginBottom: 12 }}>
                                    <Space>
                                        <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                                            {providers.length} registries active
                                        </Text>
                                        <Divider type="vertical" />
                                        <Checkbox
                                            checked={showOnlyFavorites}
                                            onChange={(e) => setShowOnlyFavorites(e.target.checked)}
                                        >
                                            <Space>
                                                <HeartFilled style={{ color: '#ff4d4f' }} />
                                                <span>Favorites only</span>
                                                {favorites.length > 0 && (
                                                    <Badge count={favorites.length} />
                                                )}
                                            </Space>
                                        </Checkbox>
                                    </Space>
                                </Col>
                                <Col span={8}>
                                    <Text strong>Registries: </Text>
                                    <Select
                                        mode="multiple"
                                        placeholder="Select registries"
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
                                </Col>
                                <Col span={8}>
                                    <Text strong>Sort by: </Text>
                                    <Radio.Group
                                        value={sortBy}
                                        onChange={(e) => setSortBy(e.target.value)}
                                        style={{ display: 'block', marginTop: 4 }}
                                    >
                                        <Radio.Button value="name">A-Z</Radio.Button>
                                        <Radio.Button value="updated">Recent</Radio.Button>
                                        <Radio.Button value="support">Quality</Radio.Button>
                                    </Radio.Group>
                                </Col>

                                <Col span={8}>
                                    <Text strong>Support Level: </Text>
                                    <Select
                                        value={filterSupport}
                                        onChange={setFilterSupport}
                                        style={{ width: '100%', marginTop: 4 }}
                                    >
                                        <Option value="all">All Levels</Option>
                                        <Option value="Recommended">⭐ Recommended</Option>
                                        <Option value="Supported">✓ Supported</Option>
                                        <Option value="Community">👥 Community</Option>
                                        <Option value="Under assessment">🔍 Under Review</Option>
                                        <Option value="Experimental">🧪 Experimental</Option>
                                    </Select>
                                </Col>

                                <Col span={8}>
                                    <Text strong>Install Type: </Text>
                                    <Select
                                        value={filterType}
                                        onChange={setFilterType}
                                        style={{ width: '100%', marginTop: 4 }}
                                    >
                                        <Option value="all">All Types</Option>
                                        <Option value="npm">📦 NPM</Option>
                                        <Option value="pypi">🐍 Python</Option>
                                        <Option value="docker">🐳 Docker</Option>
                                        <Option value="remote">☁️ Remote</Option>
                                        <Option value="git">📂 Git Clone</Option>
                                    </Select>
                                </Col>
                            </Row>
                        </Card>

                        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
                            {loading ? (
                                <div style={{ textAlign: 'center', padding: '40px' }}>
                                    <Spin size="large" tip={searchQuery ? "Searching services..." : "Loading MCP servers from all registries..."} />
                                </div>
                            ) : availableServices.length === 0 ? (
                                <Empty
                                    description="No services found. Try adjusting filters or check your connection."
                                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                                />
                            ) : (
                                (() => {
                                    // Apply filters
                                    let filtered = searchQuery && toolSearchResults.length > 0 
                                        ? toolSearchResults.map(result => ({
                                            ...result.service,
                                            // Fill in missing fields from full service data
                                            ...availableServices.find(s => s.serviceId === result.service.serviceId),
                                            _matchingTools: result.matchingTools // Add matching tools info
                                        })).filter(s => s.serviceId) as MCPService[]
                                        : availableServices;

                                    // Filter by selected providers
                                    if (selectedProviders.length > 0) {
                                        filtered = filtered.filter(s => selectedProviders.includes(s.provider.id) || 
                                            (s.provider.availableIn && s.provider.availableIn.some(p => selectedProviders.includes(p))));
                                    }

                                    if (filterSupport !== 'all') {
                                        filtered = filtered.filter(s => s.supportLevel === filterSupport);
                                    }

                                    if (filterType !== 'all') {
                                        filtered = filtered.filter(s => s.installationType === filterType);
                                    }

                                    // Apply favorites filter
                                    if (showOnlyFavorites) {
                                        filtered = filtered.filter(s => favorites.includes(s.serviceId));
                                    }

                                    // Apply sorting
                                    const sorted = [...filtered].sort((a, b) => {
                                        if (sortBy === 'name') {
                                            return a.serviceName.localeCompare(b.serviceName);
                                        } else if (sortBy === 'updated') {
                                            return new Date(b.lastUpdatedAt).getTime() - new Date(a.lastUpdatedAt).getTime();
                                        } else {
                                            const supportOrder = ['Recommended', 'Supported', 'Community', 'Under assessment', 'Experimental'];
                                            return supportOrder.indexOf(a.supportLevel) - supportOrder.indexOf(b.supportLevel);
                                        }
                                    });

                                    return (
                                        <div>
                                            {searchQuery && (
                                                <div style={{ marginBottom: 16, padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                                                    <Text type="secondary">
                                                        {toolSearchResults.length > 0 
                                                            ? `Found ${sorted.length} service${sorted.length !== 1 ? 's' : ''} matching "${searchQuery}"`
                                                            : `Showing ${sorted.length} service${sorted.length !== 1 ? 's' : ''} (no tool matches for "${searchQuery}")`
                                                        }
                                                    </Text>
                                                    {sorted.length !== totalAvailableServices && (
                                                        <Text type="secondary" style={{ marginLeft: 8 }}>
                                                            • Filtered from {totalAvailableServices} total
                                                        </Text>
                                                    )}
                                                </div>
                                            )}
                                        <List
                                            dataSource={sorted}
                                            renderItem={(service) => renderEnhancedServiceCard(service, searchQuery ? toolSearchResults : [])}
                                            pagination={{
                                                current: currentPage,
                                                pageSize: pageSize,
                                                showSizeChanger: true,
                                                showTotal: (total, range) => `${range[0]}-${range[1]} of ${total} filtered (${totalAvailableServices} total servers)`,
                                                onChange: (page, newPageSize) => {
                                                    setCurrentPage(page);
                                                    // Scroll to top of the browse services view
                                                    if (browseServicesScrollRef.current) {
                                                        browseServicesScrollRef.current.scrollTop = 0;
                                                    }
                                                    if (newPageSize !== pageSize) {
                                                        setPageSize(newPageSize);
                                                        setCurrentPage(1); // Reset to first page when page size changes
                                                    }
                                                }
                                            }}
                                        />
                                        </div>
                                    );
                                })()
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
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        <Alert
                            message="Manage your installed MCP services"
                            description="These services are currently installed and configured in your system."
                            type="success"
                            showIcon
                            style={{ marginBottom: 16 }}
                        />

                        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
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
                            <HeartOutlined />
                            <span>Favorites</span>
                            <Badge count={favorites.length} />
                        </Space>
                    }
                    key="favorites"
                >
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        <Alert
                            message="Your favorite MCP servers"
                            description="Quickly access servers you've marked as favorites for easy installation."
                            type="info"
                            showIcon
                            style={{ marginBottom: 16 }}
                        />

                        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
                            {favorites.length === 0 ? (
                                <Empty
                                    description="No favorites yet. Click the heart icon on any server to add it!"
                                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                                />
                            ) : (
                                <List
                                    dataSource={availableServices.filter(s => favorites.includes(s.serviceId))}
                                    renderItem={renderServiceCard}
                                />
                            )}
                        </div>
                    </div>
                </TabPane>
            </Tabs>

            {/* Service Preview Modal */}
            <Modal
                title={
                    <Space>
                        <EyeOutlined />
                        <span>Service Preview</span>
                        {previewService && (
                            <Tag color={getSupportLevelColor(previewService.supportLevel)}>
                                {previewService.supportLevel}
                            </Tag>
                        )}
                    </Space>
                }
                open={showPreview}
                onCancel={() => setShowPreview(false)}
                width={700}
                footer={[
                    <Button
                        key="favorite"
                        icon={previewService && favorites.includes(previewService.serviceId) ? <HeartFilled /> : <HeartOutlined />}
                        onClick={() => previewService && toggleFavorite(previewService.serviceId)}
                    >
                        {previewService && favorites.includes(previewService.serviceId) ? 'Remove from Favorites' : 'Add to Favorites'}
                    </Button>,
                    <Button
                        key="install"
                        type="primary"
                        icon={<DownloadOutlined />}
                        loading={previewService && installing[previewService.serviceId]}
                        disabled={previewService && isServiceInstalled(previewService.serviceId)}
                        onClick={() => previewService && installService(previewService.serviceId)}
                    >
                        {previewService && isServiceInstalled(previewService.serviceId) ? 'Already Installed' : 'Install Now'}
                    </Button>,
                    <Button key="close" onClick={() => setShowPreview(false)}>
                        Close
                    </Button>
                ]}
            >
                {previewLoading ? (
                    <div style={{ textAlign: 'center', padding: '40px' }}>
                        <Spin size="large" tip="Loading service details..." />
                    </div>
                ) : previewService ? (
                    <Space direction="vertical" style={{ width: '100%' }} size="large">
                        {/* Service Info */}
                        <Card size="small" title="Service Information">
                            <Descriptions column={1} size="small">
                                <Descriptions.Item label="Name">{previewService.serviceName}</Descriptions.Item>
                                <Descriptions.Item label="ID">{previewService.serviceId}</Descriptions.Item>
                                <Descriptions.Item label="Type">
                                    <Tag color="geekblue">{previewService.installationType}</Tag>
                                </Descriptions.Item>
                                <Descriptions.Item label="Support Level">
                                    <Tag color={getSupportLevelColor(previewService.supportLevel)}>
                                        {previewService.supportLevel}
                                    </Tag>
                                </Descriptions.Item>
                            </Descriptions>

                            <Divider style={{ margin: '12px 0' }} />

                            <div>
                                <Text strong>Description:</Text>
                                <Paragraph style={{ marginTop: 8 }}>
                                    {previewService.serviceDescription}
                                </Paragraph>
                            </div>

                            {previewService.tags && previewService.tags.length > 0 && (
                                <div style={{ marginTop: 12 }}>
                                    <Text strong>Tags: </Text>
                                    {previewService.tags.map((tag: string) => (
                                        <Tag key={tag}>{tag}</Tag>
                                    ))}
                                </div>
                            )}
                        </Card>

                        {/* Installation Details */}
                        <Card size="small" title="Installation Details">
                            <Descriptions column={1} size="small">
                                <Descriptions.Item label="Method">
                                    <Tag color="blue">{previewService.installationType}</Tag>
                                </Descriptions.Item>

                                {previewService.installationInstructions.package && (
                                    <Descriptions.Item label="Package">
                                        <code>{previewService.installationInstructions.package}</code>
                                    </Descriptions.Item>
                                )}

                                {previewService.installationInstructions.image && (
                                    <Descriptions.Item label="Docker Image">
                                        <code>{previewService.installationInstructions.image}</code>
                                    </Descriptions.Item>
                                )}

                                {previewService.installationInstructions.url && (
                                    <Descriptions.Item label="Remote URL">
                                        <code>{previewService.installationInstructions.url}</code>
                                    </Descriptions.Item>
                                )}
                            </Descriptions>

                            {previewService.requiredEnvVars && previewService.requiredEnvVars.length > 0 && (
                                <div style={{ marginTop: 16 }}>
                                    <Alert
                                        message="Required Environment Variables"
                                        description={
                                            <List
                                                dataSource={previewService.requiredEnvVars}
                                                renderItem={(env: any) => (
                                                    <List.Item>
                                                        <Space direction="vertical" style={{ width: '100%' }}>
                                                            <Text strong>{env.name}</Text>
                                                            <Text type="secondary">{env.description}</Text>
                                                            {env.isRequired && <Tag color="red">Required</Tag>}
                                                            {env.isSecret && <Tag color="orange">Secret</Tag>}
                                                        </Space>
                                                    </List.Item>
                                                )}
                                            />
                                        }
                                        type="warning"
                                        showIcon
                                    />
                                </div>
                            )}
                        </Card>

                        {/* Links */}
                        {(previewService.repositoryUrl || previewService.securityReviewUrl) && (
                            <Card size="small" title="Links">
                                <Space direction="vertical" style={{ width: '100%' }}>
                                    {previewService.repositoryUrl && (
                                        <Button
                                            icon={<GithubOutlined />}
                                            onClick={() => window.open(previewService.repositoryUrl, '_blank')}
                                            block
                                        >
                                            View Repository
                                        </Button>
                                    )}
                                    {previewService.securityReviewUrl && (
                                        <Button
                                            icon={<SafetyCertificateOutlined />}
                                            onClick={() => window.open(previewService.securityReviewUrl, '_blank')}
                                            block
                                        >
                                            Security Review
                                        </Button>
                                    )}
                                </Space>
                            </Card>
                        )}

                        {/* Installation Preview */}
                        {previewService.preview && (
                            <Card size="small" title="What Will Be Installed">
                                <Alert
                                    message={
                                        <div>
                                            <Text>This service will be installed as: </Text>
                                            <Text code>{previewService.preview.service_name}</Text>
                                        </div>
                                    }
                                    description={previewService.preview.description}
                                    type="info"
                                    showIcon
                                />
                            </Card>
                        )}
                    </Space>
                ) : null}
            </Modal>
        </Modal >
    );
};

export default MCPRegistryModal;
