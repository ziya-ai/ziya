import React, { useState, useEffect } from 'react';
import { Card, Table, Tag, Progress, Space, Statistic, Row, Col, Alert, Tooltip } from 'antd';
import {
    CheckCircleOutlined,
    CloseCircleOutlined,
    WarningOutlined,
    ThunderboltOutlined,
    DollarOutlined,
    FireOutlined
} from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

interface ConversationMetrics {
    conversation_id: string;
    iteration_count: number;
    fresh_tokens: number;
    cached_tokens: number;
    output_tokens: number;
    cache_created: number;
    throttle_count: number;
    cache_efficiency: number;
    has_cache_issue: boolean;
    timestamp: number;
}

interface GlobalStats {
    total_conversations: number;
    total_fresh_tokens: number;
    total_cached_tokens: number;
    total_output_tokens: number;
    total_throttle_events: number;
    conversations_with_cache_issues: number;
    overall_cache_efficiency: number;
    estimated_cost_savings_pct: number;
}

interface TelemetryData {
    status: string;
    timestamp: number;
    global_stats: GlobalStats;
    conversations: ConversationMetrics[];
    health_summary: {
        cache_working: boolean;
        issues_detected: number;
        throttle_pressure: 'low' | 'medium' | 'high';
    };
}

export const CacheTelemetryDashboard: React.FC = () => {
    const { isDarkMode } = useTheme();
    const [telemetry, setTelemetry] = useState<TelemetryData | null>(null);
    const [loading, setLoading] = useState(true);
    const [autoRefresh, setAutoRefresh] = useState(true);

    // Fetch telemetry data
    const fetchTelemetry = async () => {
        try {
            const response = await fetch('/api/telemetry/cache-health');
            const data = await response.json();
            setTelemetry(data);
        } catch (error) {
            console.error('Error fetching telemetry:', error);
        } finally {
            setLoading(false);
        }
    };

    // Auto-refresh every 5 seconds
    useEffect(() => {
        fetchTelemetry();
        
        if (autoRefresh) {
            const interval = setInterval(fetchTelemetry, 5000);
            return () => clearInterval(interval);
        }
    }, [autoRefresh]);

    if (loading || !telemetry) {
        return <div>Loading telemetry...</div>;
    }

    const { global_stats, conversations, health_summary } = telemetry;

    // Calculate derived metrics
    const totalBillableTokens = global_stats.total_fresh_tokens + global_stats.total_output_tokens;
    const totalPotentialTokens = totalBillableTokens + global_stats.total_cached_tokens;

    // Table columns for conversation details
    const columns = [
        {
            title: 'Conversation',
            dataIndex: 'conversation_id',
            key: 'conversation_id',
            render: (id: string) => (
                <Tooltip title={id}>
                    <code style={{ fontSize: '11px' }}>{id.substring(0, 12)}...</code>
                </Tooltip>
            ),
            width: 120
        },
        {
            title: 'Iterations',
            dataIndex: 'iteration_count',
            key: 'iteration_count',
            align: 'center' as const,
            width: 80
        },
        {
            title: 'Fresh Tokens',
            dataIndex: 'fresh_tokens',
            key: 'fresh_tokens',
            render: (tokens: number) => tokens.toLocaleString(),
            align: 'right' as const,
            width: 120
        },
        {
            title: 'Cached Tokens',
            dataIndex: 'cached_tokens',
            key: 'cached_tokens',
            render: (tokens: number, record: ConversationMetrics) => (
                <Space>
                    <span style={{ color: tokens > 0 ? '#52c41a' : '#ff4d4f' }}>
                        {tokens.toLocaleString()}
                    </span>
                    {record.has_cache_issue && (
                        <Tooltip title="Cache issue detected">
                            <WarningOutlined style={{ color: '#ff4d4f' }} />
                        </Tooltip>
                    )}
                </Space>
            ),
            align: 'right' as const,
            width: 140
        },
        {
            title: 'Cache Efficiency',
            dataIndex: 'cache_efficiency',
            key: 'cache_efficiency',
            render: (efficiency: number) => (
                <Progress
                    percent={Math.round(efficiency)}
                    size="small"
                    status={efficiency > 50 ? 'success' : efficiency > 20 ? 'normal' : 'exception'}
                    format={(percent) => `${percent}%`}
                />
            ),
            width: 150
        },
        {
            title: 'Throttles',
            dataIndex: 'throttle_count',
            key: 'throttle_count',
            render: (count: number) => (
                <Tag color={count === 0 ? 'success' : count < 3 ? 'warning' : 'error'}>
                    {count > 0 ? `${count}x` : 'None'}
                </Tag>
            ),
            align: 'center' as const,
            width: 100
        }
    ];

    // Get throttle pressure color
    const getThrottlePressureColor = (pressure: string) => {
        switch (pressure) {
            case 'low': return '#52c41a';
            case 'medium': return '#faad14';
            case 'high': return '#ff4d4f';
            default: return '#8c8c8c';
        }
    };

    return (
        <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
            <h2 style={{ marginBottom: '24px' }}>
                <ThunderboltOutlined /> Cache & Throttling Telemetry
            </h2>

            {/* Health Alert */}
            {!health_summary.cache_working && (
                <Alert
                    message="🚨 Cache Issues Detected"
                    description={`${health_summary.issues_detected} conversation(s) with cache problems. Caching may be disabled or broken, leading to increased throttling.`}
                    type="error"
                    showIcon
                    style={{ marginBottom: '24px' }}
                />
            )}

            {/* Global Statistics */}
            <Row gutter={16} style={{ marginBottom: '24px' }}>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="Overall Cache Efficiency"
                            value={global_stats.overall_cache_efficiency}
                            precision={1}
                            suffix="%"
                            valueStyle={{ 
                                color: global_stats.overall_cache_efficiency > 50 ? '#52c41a' : '#ff4d4f' 
                            }}
                            prefix={
                                global_stats.overall_cache_efficiency > 50 ? 
                                <CheckCircleOutlined /> : <CloseCircleOutlined />
                            }
                        />
                    </Card>
                </Col>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="Cost Savings"
                            value={global_stats.estimated_cost_savings_pct}
                            precision={1}
                            suffix="%"
                            prefix={<DollarOutlined />}
                            valueStyle={{ color: '#52c41a' }}
                        />
                        <div style={{ fontSize: '12px', color: '#8c8c8c', marginTop: '8px' }}>
                            {global_stats.total_cached_tokens.toLocaleString()} tokens cached
                        </div>
                    </Card>
                </Col>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="Throttle Events"
                            value={global_stats.total_throttle_events}
                            prefix={<FireOutlined />}
                            valueStyle={{ 
                                color: getThrottlePressureColor(health_summary.throttle_pressure)
                            }}
                        />
                        <div style={{ fontSize: '12px', color: '#8c8c8c', marginTop: '8px' }}>
                            Pressure: <Tag color={getThrottlePressureColor(health_summary.throttle_pressure)}>
                                {health_summary.throttle_pressure.toUpperCase()}
                            </Tag>
                        </div>
                    </Card>
                </Col>
                <Col span={6}>
                    <Card>
                        <Statistic
                            title="Active Conversations"
                            value={global_stats.total_conversations}
                            suffix={
                                health_summary.issues_detected > 0 ? 
                                <span style={{ fontSize: '14px', color: '#ff4d4f' }}>
                                    ({health_summary.issues_detected} issues)
                                </span> : null
                            }
                        />
                    </Card>
                </Col>
            </Row>

            {/* Token Usage Breakdown */}
            <Card 
                title="Token Usage Breakdown" 
                style={{ marginBottom: '24px' }}
                extra={
                    <Space>
                        <span style={{ fontSize: '12px', color: '#8c8c8c' }}>
                            Auto-refresh: {autoRefresh ? 'ON' : 'OFF'}
                        </span>
                        <a onClick={() => setAutoRefresh(!autoRefresh)}>Toggle</a>
                    </Space>
                }
            >
                <Row gutter={16}>
                    <Col span={8}>
                        <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#1890ff' }}>
                                {global_stats.total_fresh_tokens.toLocaleString()}
                            </div>
                            <div style={{ fontSize: '12px', color: '#8c8c8c' }}>Fresh Tokens (Billable)</div>
                        </div>
                    </Col>
                    <Col span={8}>
                        <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#52c41a' }}>
                                {global_stats.total_cached_tokens.toLocaleString()}
                            </div>
                            <div style={{ fontSize: '12px', color: '#8c8c8c' }}>Cached Tokens (FREE)</div>
                        </div>
                    </Col>
                    <Col span={8}>
                        <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#722ed1' }}>
                                {global_stats.total_output_tokens.toLocaleString()}
                            </div>
                            <div style={{ fontSize: '12px', color: '#8c8c8c' }}>Output Tokens</div>
                        </div>
                    </Col>
                </Row>
            </Card>

            {/* Conversation Details */}
            <Card title="Recent Conversations">
                <Table
                    dataSource={conversations}
                    columns={columns}
                    rowKey="conversation_id"
                    size="small"
                    pagination={{ pageSize: 10 }}
                    rowClassName={(record) => record.has_cache_issue ? 'cache-issue-row' : ''}
                />
            </Card>

            <style>{`
                .cache-issue-row {
                    background-color: ${isDarkMode ? 'rgba(255, 77, 79, 0.1)' : 'rgba(255, 77, 79, 0.05)'} !important;
                }
            `}</style>
        </div>
    );
};
