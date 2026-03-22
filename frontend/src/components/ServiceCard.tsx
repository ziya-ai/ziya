/**
 * ServiceCard — shared card renderer for MCP registry services.
 *
 * Replaces three near-identical ~200-line render functions:
 *   renderEnhancedServiceCard, renderServiceCard, renderInstalledService
 *
 * Variant-specific behaviour is controlled through props; the card
 * structure, styles, and null-safety are uniform.
 */
import React from 'react';
import {
    Card, Button, Tag, Space, Tooltip, Alert, Typography, Statistic, Row, Col,
} from 'antd';
import {
    DownloadOutlined, DeleteOutlined, InfoCircleOutlined, SafetyCertificateOutlined,
    ToolOutlined, ExperimentOutlined, WarningOutlined, GithubOutlined, StarOutlined,
    ClockCircleOutlined, EyeOutlined, HeartOutlined, HeartFilled,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

/** Minimal shape shared by both MCPService and InstalledService. */
export interface ServiceCardService {
    serviceId: string;
    serviceName?: string;
    serviceDescription?: string;
    supportLevel?: string;
    version?: number | string;
    lastUpdatedAt?: string;
    securityReviewLink?: string;
    provider?: {
        id?: string;
        name?: string;
        isInternal?: boolean;
        availableIn?: string[];
    };
    tags?: string[];
    author?: string;
    repositoryUrl?: string;
    installationType?: string;
    downloadCount?: number;
    starCount?: number;
    _dependencies_available?: boolean;
    _available_tools?: string[];
    _manually_configured?: boolean;
    /** Used by installed-service variant for uninstall. */
    serverName?: string;
}

export interface MatchingTool {
    toolName: string;
    mcpServerId: string;
}

export interface ServiceCardProps {
    service: ServiceCardService;

    /* --- State from parent --- */
    isInstalled: boolean;
    isManuallyConfigured?: boolean;
    isFavorite: boolean;
    isExpanded: boolean;
    isInstalling: boolean;

    /* --- Variant-specific content --- */
    /** Matching tools to highlight (search results mode). */
    matchingTools?: MatchingTool[];
    /** Show uninstall button (enhanced card on browse tab). */
    showUninstall?: boolean;

    /* --- Callbacks --- */
    onInstall: (serviceId: string) => void;
    onUninstall?: (serverName: string) => void;
    onPreview: (serviceId: string, providerId?: string) => void;
    onToggleFavorite: (serviceId: string) => void;
    onToggleExpanded: (serviceId: string) => void;
    getSupportLevelColor: (level: string) => string;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const ServiceCard: React.FC<ServiceCardProps> = ({
    service,
    isInstalled,
    isManuallyConfigured = false,
    isFavorite,
    isExpanded,
    isInstalling,
    matchingTools,
    showUninstall = false,
    onInstall,
    onUninstall,
    onPreview,
    onToggleFavorite,
    onToggleExpanded,
    getSupportLevelColor,
}) => {
    if (!service?.serviceId) return null;

    const isBuiltin = service.serviceId.startsWith('builtin_');
    const supportLevel = service.supportLevel || 'Community';
    const description = service.serviceDescription || service.serviceName || '';

    /* ---- Install button label ---- */
    const installLabel = (() => {
        if (isBuiltin) return isInstalled ? 'Enabled' : 'Enable';
        if (isInstalled) return isManuallyConfigured ? 'Configured' : 'Installed';
        return 'Install';
    })();

    /* ---- Actions ---- */
    const actions = [
        !isBuiltin && (
            <Button key="preview" icon={<EyeOutlined />}
                onClick={() => onPreview(service.serviceId, service.provider?.id)}>
                Preview
            </Button>
        ),
        <Tooltip title={isFavorite ? 'Remove from favorites' : 'Add to favorites'} key="favorite">
            <Button
                icon={isFavorite ? <HeartFilled style={{ color: '#ff4d4f' }} /> : <HeartOutlined />}
                onClick={() => onToggleFavorite(service.serviceId)}
            />
        </Tooltip>,
        <Button key="install" type={isBuiltin ? 'default' : 'primary'}
            icon={<DownloadOutlined />} loading={isInstalling} disabled={isInstalled}
            onClick={() => onInstall(service.serviceId)}>
            {installLabel}
        </Button>,
        !isBuiltin && service.repositoryUrl && (
            <Tooltip title="View Repository" key="repo">
                <Button icon={<GithubOutlined />}
                    onClick={() => window.open(service.repositoryUrl, '_blank')} />
            </Tooltip>
        ),
        !isBuiltin && service.securityReviewLink && (
            <Tooltip title="Security Review" key="security">
                <Button icon={<SafetyCertificateOutlined />}
                    onClick={() => window.open(service.securityReviewLink, '_blank')} />
            </Tooltip>
        ),
        showUninstall && isInstalled && !isBuiltin && service.serverName && onUninstall && (
            <Tooltip title="Uninstall service" key="uninstall">
                <Button icon={<DeleteOutlined />} danger
                    onClick={() => onUninstall(service.serverName!)} />
            </Tooltip>
        ),
        <Tooltip title={isExpanded ? 'Show less' : 'Show more'} key="info">
            <Button icon={<InfoCircleOutlined />}
                onClick={() => onToggleExpanded(service.serviceId)} />
        </Tooltip>,
    ].filter(Boolean);

    /* ---- Title tags ---- */
    const titleContent = (
        <Space wrap>
            <span>{service.serviceName}</span>
            <Tag color={getSupportLevelColor(supportLevel)}>{supportLevel}</Tag>
            {isManuallyConfigured && (
                <Tag color="orange" icon={<ToolOutlined />}>Manually Configured</Tag>
            )}
            {isBuiltin ? (
                <Tag color="purple"><ExperimentOutlined /> Builtin</Tag>
            ) : service.provider?.availableIn && service.provider.availableIn.length > 1 ? (
                <Tooltip title={`Available in: ${service.provider.availableIn.join(', ')}`}>
                    <Tag color="purple">{service.provider.availableIn.length} sources</Tag>
                </Tooltip>
            ) : (
                <Tag color={service.provider?.isInternal ? 'gold' : 'blue'}>
                    {service.provider?.name || 'Unknown'}
                </Tag>
            )}
            {service.installationType && <Tag color="geekblue">{service.installationType}</Tag>}
            {isBuiltin && service._dependencies_available === false && (
                <Tag color="orange" icon={<WarningOutlined />}>Dependencies Required</Tag>
            )}
        </Space>
    );

    /* ---- Description body ---- */
    const descriptionContent = (
        <div>
            <Paragraph ellipsis={isExpanded ? false : { rows: 2 }} style={{ marginBottom: 8 }}>
                {description}
            </Paragraph>

            {matchingTools && matchingTools.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                    <Text strong>Matching Tools: </Text>
                    {matchingTools.map(tool => (
                        <Tag key={tool.toolName} icon={<ToolOutlined />} color="blue">{tool.toolName}</Tag>
                    ))}
                </div>
            )}

            {isBuiltin && service._available_tools && (
                <div style={{ marginBottom: 8 }}>
                    <Text strong>Available Tools: </Text>
                    {service._available_tools.map(toolName => (
                        <Tag key={toolName} color="cyan">{toolName}</Tag>
                    ))}
                </div>
            )}

            {isBuiltin && service._dependencies_available === false && (
                <Alert message="Dependencies Required"
                    description={<div>Install with: <code>pip install scapy dpkt</code></div>}
                    style={{ marginBottom: 8 }} />
            )}

            {isExpanded && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #f0f0f0' }}>
                    <Row gutter={16} style={{ marginBottom: 12 }}>
                        {service.downloadCount != null && (
                            <Col span={8}>
                                <Statistic title="Downloads" value={service.downloadCount}
                                    prefix={<DownloadOutlined />} valueStyle={{ fontSize: '16px' }} />
                            </Col>
                        )}
                        {service.starCount != null && (
                            <Col span={8}>
                                <Statistic title="Stars" value={service.starCount}
                                    prefix={<StarOutlined />} valueStyle={{ fontSize: '16px' }} />
                            </Col>
                        )}
                        <Col span={8}>
                            <Statistic title="Updated"
                                value={service.lastUpdatedAt
                                    ? new Date(service.lastUpdatedAt).toLocaleDateString()
                                    : 'N/A'}
                                prefix={<ClockCircleOutlined />} valueStyle={{ fontSize: '14px' }} />
                        </Col>
                    </Row>
                    {service.author && (
                        <div style={{ marginBottom: 8 }}>
                            <Text strong>Author: </Text><Text>{service.author}</Text>
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
                {service.tags?.map(tag => <Tag key={tag} color="default">{tag}</Tag>)}
            </div>

            <Text type="secondary" style={{ fontSize: '12px', display: 'flex', justifyContent: 'space-between' }}>
                <span>ID: {service.serviceId}</span>
                {service.version && <span>v{service.version}</span>}
            </Text>
        </div>
    );

    return (
        <Card key={service.serviceId} style={{ marginBottom: 16 }} hoverable actions={actions}>
            <Card.Meta title={titleContent} description={descriptionContent} />
        </Card>
    );
};

export default React.memo(ServiceCard);
