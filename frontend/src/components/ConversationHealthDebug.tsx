import React, { useState, useEffect } from 'react';
import { Card, Descriptions, Tag, Button, Space, Alert, Collapse, Table, Statistic, Row, Col, message, Modal, Tabs } from 'antd';
import {
    CheckCircleOutlined, 
    CloseCircleOutlined, 
    WarningOutlined,
    ReloadOutlined,
    DeleteOutlined,
    ExportOutlined,
    SyncOutlined
} from '@ant-design/icons';
import { useChatContext } from '../context/ChatContext';
import { db } from '../utils/db';
import { Conversation } from '../utils/types';
import { useTheme } from '../context/ThemeContext';

const { TabPane } = Tabs;
const { Panel } = Collapse;

interface HealthReport {
    memoryConversations: Conversation[];
    dbConversations: Conversation[];
    currentConversationId: string;
    currentInMemory: Conversation | undefined;
    currentInDB: Conversation | undefined;
    mismatches: Array<{
        id: string;
        issue: string;
        memoryState: any;
        dbState: any;
    }>;
    inactiveConversations: Conversation[];
    missingInMemory: Conversation[];
    missingInDB: Conversation[];
    backupData: any;
}

const ConversationHealthDebug: React.FC = () => {
    const { conversations, currentConversationId, folders } = useChatContext();
    const { isDarkMode } = useTheme();
    const [healthReport, setHealthReport] = useState<HealthReport | null>(null);
    const [loading, setLoading] = useState(false);

    const runHealthCheck = async () => {
        setLoading(true);
        try {
            // Get data from IndexedDB
            const dbConversations = await db.getConversations();
            
            // Get backup data from localStorage
            let backupData = null;
            try {
                const backup = localStorage.getItem('ZIYA_CONVERSATION_BACKUP');
                backupData = backup ? JSON.parse(backup) : null;
            } catch (e) {
                console.error('Error parsing backup data:', e);
            }
            
            // Find current conversation in both states
            const currentInMemory = conversations.find(c => c.id === currentConversationId);
            const currentInDB = dbConversations.find(c => c.id === currentConversationId);
            
            // Find mismatches
            const mismatches: HealthReport['mismatches'] = [];
            const memoryMap = new Map(conversations.map(c => [c.id, c]));
            const dbMap = new Map(dbConversations.map(c => [c.id, c]));
            
            // Check for conversations with different isActive states
            conversations.forEach(memConv => {
                const dbConv = dbMap.get(memConv.id);
                if (dbConv) {
                    if (memConv.isActive !== dbConv.isActive) {
                        mismatches.push({
                            id: memConv.id,
                            issue: 'isActive mismatch',
                            memoryState: { isActive: memConv.isActive, title: memConv.title },
                            dbState: { isActive: dbConv.isActive, title: dbConv.title }
                        });
                    }
                    if (memConv.folderId !== dbConv.folderId) {
                        mismatches.push({
                            id: memConv.id,
                            issue: 'folderId mismatch',
                            memoryState: { folderId: memConv.folderId, title: memConv.title },
                            dbState: { folderId: dbConv.folderId, title: dbConv.title }
                        });
                    }
                }
            });
            
            // Find conversations that are only in one place
            const missingInMemory = dbConversations.filter(dbConv => 
                !memoryMap.has(dbConv.id) && dbConv.isActive !== false
            );
            const missingInDB = conversations.filter(memConv => 
                !dbMap.has(memConv.id) && memConv.isActive !== false
            );
            
            // Find all inactive conversations
            const inactiveInMemory = conversations.filter(c => c.isActive === false);
            const inactiveInDB = dbConversations.filter(c => c.isActive === false);
            const allInactive = [...new Set([...inactiveInMemory, ...inactiveInDB])];
            
            setHealthReport({
                memoryConversations: conversations,
                dbConversations,
                currentConversationId,
                currentInMemory,
                currentInDB,
                mismatches,
                inactiveConversations: allInactive,
                missingInMemory,
                missingInDB,
                backupData
            });
            
        } catch (error) {
            message.error('Failed to run health check: ' + (error instanceof Error ? error.message : String(error)));
        } finally {
            setLoading(false);
        }
    };

    // Run health check on mount
    useEffect(() => {
        runHealthCheck();
    }, []);

    const handleReloadFromDB = async () => {
        try {
            const dbConversations = await db.getConversations();
            window.location.reload();
            message.success('Reloading from database...');
        } catch (error) {
            message.error('Failed to reload from database');
        }
    };

    const handleRepairDatabase = async () => {
        Modal.confirm({
            title: 'Repair Database',
            content: 'This will validate and repair conversation data. Continue?',
            onOk: async () => {
                try {
                    await db.repairDatabase();
                    await runHealthCheck();
                    message.success('Database repaired successfully');
                } catch (error) {
                    message.error('Failed to repair database');
                }
            }
        });
    };

    const handleExportDebugData = () => {
        const debugData = {
            timestamp: new Date().toISOString(),
            healthReport,
            localStorage: {
                currentConversationId: localStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID'),
                backupTimestamp: localStorage.getItem('ZIYA_BACKUP_TIMESTAMP'),
                dbName: localStorage.getItem('ZIYA_DB_NAME')
            }
        };
        
        const blob = new Blob([JSON.stringify(debugData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ziya-debug-${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const handleForceReset = () => {
        Modal.confirm({
            title: 'Force Reset Database',
            content: 'This will delete ALL conversation data and start fresh. This cannot be undone. Are you sure?',
            okText: 'Yes, Reset Everything',
            okType: 'danger',
            onOk: async () => {
                try {
                    await db.forceReset();
                    message.success('Database reset successfully. Reloading...');
                    setTimeout(() => window.location.reload(), 1000);
                } catch (error) {
                    message.error('Failed to reset database');
                }
            }
        });
    };

    if (!healthReport) {
        return (
            <div style={{ padding: '24px', textAlign: 'center' }}>
                <Button type="primary" loading={loading} onClick={runHealthCheck}>
                    Run Health Check
                </Button>
            </div>
        );
    }

    const isHealthy = 
        healthReport.mismatches.length === 0 &&
        healthReport.missingInMemory.length === 0 &&
        healthReport.missingInDB.length === 0 &&
        !!healthReport.currentInMemory &&
        !!healthReport.currentInDB &&
        healthReport.currentInMemory.isActive !== false;

    const columns = [
        {
            title: 'ID',
            dataIndex: 'id',
            key: 'id',
            render: (id: string) => id.substring(0, 8) + '...'
        },
        {
            title: 'Title',
            dataIndex: 'title',
            key: 'title',
            ellipsis: true
        },
        {
            title: 'isActive',
            dataIndex: 'isActive',
            key: 'isActive',
            render: (isActive: boolean | undefined) => (
                <Tag color={isActive === false ? 'red' : isActive === true ? 'green' : 'orange'}>
                    {isActive === false ? 'FALSE' : isActive === true ? 'TRUE' : 'UNDEFINED'}
                </Tag>
            )
        },
        {
            title: 'Messages',
            dataIndex: 'messages',
            key: 'messages',
            render: (messages: any[]) => messages?.length || 0
        },
        {
            title: 'Folder',
            dataIndex: 'folderId',
            key: 'folderId',
            render: (folderId: string | null) => {
                if (!folderId) return <Tag>Root</Tag>;
                const folder = folders.find(f => f.id === folderId);
                return <Tag color="blue">{folder?.name || folderId.substring(0, 8)}</Tag>;
            }
        }
    ];

    return (
        <div style={{
            height: '100vh',
            display: 'flex',
            flexDirection: 'column',
            backgroundColor: isDarkMode ? '#141414' : '#f0f2f5',
            overflow: 'hidden'
        }}>
            {/* Fixed Header */}
            <div style={{
                padding: '16px 24px',
                borderBottom: `1px solid ${isDarkMode ? '#303030' : '#e8e8e8'}`,
                backgroundColor: isDarkMode ? '#1f1f1f' : '#ffffff',
                flexShrink: 0
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                        <h1 style={{ margin: 0, fontSize: '24px' }}>
                            🔍 Conversation Health Diagnostics
                        </h1>
                        <div style={{ marginTop: '8px' }}>
                            {isHealthy ? (
                                <Tag icon={<CheckCircleOutlined />} color="success" style={{ fontSize: '14px' }}>
                                    System Healthy
                                </Tag>
                            ) : (
                                <Tag icon={<WarningOutlined />} color="warning" style={{ fontSize: '14px' }}>
                                    Issues Detected
                                </Tag>
                            )}
                        </div>
                    </div>
                    
                    <Space wrap>
                        <Button 
                            icon={<ReloadOutlined />} 
                            onClick={runHealthCheck}
                            loading={loading}
                            size="large"
                        >
                            Refresh
                        </Button>
                        <Button 
                            icon={<ExportOutlined />} 
                            onClick={handleExportDebugData}
                            size="large"
                        >
                            Export
                        </Button>
                    </Space>
                </div>

                {/* Summary Stats */}
                <Row gutter={16} style={{ marginTop: '16px' }}>
                    <Col span={6}>
                        <Statistic 
                            title="Memory" 
                            value={healthReport.memoryConversations.length}
                            valueStyle={{ color: '#3f8600', fontSize: '20px' }}
                            prefix="💾"
                        />
                    </Col>
                    <Col span={6}>
                        <Statistic 
                            title="IndexedDB" 
                            value={healthReport.dbConversations.length}
                            valueStyle={{ color: '#1890ff', fontSize: '20px' }}
                            prefix="🗄️"
                        />
                    </Col>
                    <Col span={6}>
                        <Statistic 
                            title="Active" 
                            value={healthReport.memoryConversations.filter(c => c.isActive !== false).length}
                            valueStyle={{ fontSize: '20px' }}
                            prefix="✅"
                        />
                    </Col>
                    <Col span={6}>
                        <Statistic 
                            title="Inactive" 
                            value={healthReport.inactiveConversations.length}
                            valueStyle={{ color: '#cf1322', fontSize: '20px' }}
                            prefix="🗑️"
                        />
                    </Col>
                </Row>
            </div>

            {/* Scrollable Content Area */}
            <div style={{
                flex: 1,
                overflow: 'auto',
                padding: '24px',
                maxWidth: '1400px',
                margin: '0 auto',
                width: '100%'
            }}>
                <Tabs 
                    defaultActiveKey="overview"
                    size="large"
                    style={{ height: '100%' }}
                >
                    {/* Overview Tab */}
                    <TabPane tab="Overview" key="overview">
                        <Space direction="vertical" size="large" style={{ width: '100%' }}>
                            {/* Quick Actions */}
                            <Card title="Quick Actions" size="small">
                                <Space wrap>
                                    <Button 
                                        icon={<SyncOutlined />} 
                                        onClick={handleReloadFromDB}
                                    >
                                        Reload from Database
                                    </Button>
                                    <Button 
                                        icon={<ReloadOutlined />} 
                                        onClick={handleRepairDatabase}
                                        type="primary"
                                    >
                                        Repair Database
                                    </Button>
                                    <Button 
                                        icon={<DeleteOutlined />} 
                                        onClick={handleForceReset}
                                        danger
                                    >
                                        Force Reset (DANGER)
                                    </Button>
                                </Space>
                            </Card>

                            {/* Health Summary */}
                            <Alert
                                message={isHealthy ? 'All Systems Normal' : 'Issues Detected'}
                                description={
                                    isHealthy 
                                        ? 'All conversations are properly synchronized between memory and database.'
                                        : `Found ${healthReport.mismatches.length} mismatches, ${healthReport.missingInMemory.length} missing in memory, ${healthReport.missingInDB.length} missing in DB.`
                                }
                                type={isHealthy ? 'success' : 'error'}
                                showIcon
                            />

                            {/* Current Conversation Quick Status */}
                            <Card 
                                title="Current Conversation Quick Status"
                                size="small"
                                extra={
                                    healthReport.currentInMemory && healthReport.currentInDB ? 
                                        <Tag color="success">✓ OK</Tag> : 
                                        <Tag color="error">✗ ISSUE</Tag>
                                }
                            >
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '16px' }}>
                                    <div>
                                        <strong>ID:</strong> <code style={{ fontSize: '11px' }}>{currentConversationId}</code>
                                    </div>
                                    <div>
                                        <strong>In Memory:</strong> {healthReport.currentInMemory ? 
                                            <Tag color="green">Yes</Tag> : 
                                            <Tag color="red">NO</Tag>}
                                    </div>
                                    <div>
                                        <strong>In Database:</strong> {healthReport.currentInDB ? 
                                            <Tag color="green">Yes</Tag> : 
                                            <Tag color="red">NO</Tag>}
                                    </div>
                                    <div>
                                        <strong>isActive:</strong> {healthReport.currentInMemory ? 
                                            <Tag color={healthReport.currentInMemory.isActive === false ? 'red' : 'green'}>
                                                {String(healthReport.currentInMemory.isActive)}
                                            </Tag> : 'N/A'}
                                    </div>
                                </div>
                            </Card>
                        </Space>
                    </TabPane>

                    {/* Current Conversation Tab */}
                    <TabPane tab="Current Conversation" key="current">
                        <Card 
                            title="Current Conversation Details"
                            extra={
                                <Tag color={healthReport.currentInMemory && healthReport.currentInDB ? 'green' : 'red'}>
                                    {healthReport.currentInMemory && healthReport.currentInDB ? 'OK' : 'ISSUE'}
                                </Tag>
                            }
                        >
                <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label="Current ID">
                        <code>{currentConversationId}</code>
                    </Descriptions.Item>
                    <Descriptions.Item label="Found in Memory">
                        {healthReport.currentInMemory ? (
                            <Tag color="green"><CheckCircleOutlined /> Yes</Tag>
                        ) : (
                            <Tag color="red"><CloseCircleOutlined /> NO - MISSING!</Tag>
                        )}
                    </Descriptions.Item>
                    <Descriptions.Item label="Found in IndexedDB">
                        {healthReport.currentInDB ? (
                            <Tag color="green"><CheckCircleOutlined /> Yes</Tag>
                        ) : (
                            <Tag color="red"><CloseCircleOutlined /> NO - MISSING!</Tag>
                        )}
                    </Descriptions.Item>
                    {healthReport.currentInMemory && (
                            <Descriptions.Item label="State in Memory">
                                <div>
                                    <div>Title: {healthReport.currentInMemory.title}</div>
                                    <div>isActive: <Tag color={healthReport.currentInMemory.isActive === false ? 'red' : 'green'}>
                                        {String(healthReport.currentInMemory.isActive)}
                                    </Tag></div>
                                    <div>Messages: {healthReport.currentInMemory.messages.length}</div>
                                    <div>Folder: {healthReport.currentInMemory.folderId || 'Root'}</div>
                                </div>
                            </Descriptions.Item>
                    )}
                    {healthReport.currentInDB && (
                            <Descriptions.Item label="State in Database">
                                <div>
                                    <div>Title: {healthReport.currentInDB.title}</div>
                                    <div>isActive: <Tag color={healthReport.currentInDB.isActive === false ? 'red' : 'green'}>
                                        {String(healthReport.currentInDB.isActive)}
                                    </Tag></div>
                                    <div>Messages: {healthReport.currentInDB.messages.length}</div>
                                    <div>Folder: {healthReport.currentInDB.folderId || 'Root'}</div>
                                </div>
                            </Descriptions.Item>
                    )}
                </Descriptions>

                {/* Show alert if current conversation is inactive */}
                {(healthReport.currentInMemory?.isActive === false || healthReport.currentInDB?.isActive === false) && (
                    <Alert
                        style={{ marginTop: '16px' }}
                        message="CRITICAL: Current Conversation is Marked Inactive!"
                        description="This conversation has isActive: false, which will cause it to disappear from history on reload."
                        type="error"
                        showIcon
                    />
                )}
            </Card>
                    </TabPane>

                    {/* Issues Tab */}
                    <TabPane 
                        tab={
                            <span>
                                Issues {healthReport.mismatches.length + healthReport.missingInMemory.length + healthReport.missingInDB.length > 0 && 
                                    <Tag color="red">{healthReport.mismatches.length + healthReport.missingInMemory.length + healthReport.missingInDB.length}</Tag>}
                            </span>
                        } 
                        key="issues"
                    >
                        <Space direction="vertical" size="large" style={{ width: '100%' }}>
                            {/* Mismatches */}
                            {healthReport.mismatches.length > 0 && (
                                <Card title="State Mismatches" size="small">
                                    <Alert
                                        message={`Found ${healthReport.mismatches.length} mismatch(es) between memory and database`}
                                        type="warning"
                                        style={{ marginBottom: '16px' }}
                                    />
                                    <div style={{ maxHeight: '400px', overflow: 'auto' }}>
                                        <Table
                                            dataSource={healthReport.mismatches}
                                            columns={[
                                                {
                                                    title: 'ID',
                                                    dataIndex: 'id',
                                                    key: 'id',
                                                    render: (id: string) => <code>{id.substring(0, 12)}...</code>,
                                                    width: 120
                                                },
                                                {
                                                    title: 'Issue',
                                                    dataIndex: 'issue',
                                                    key: 'issue',
                                                    render: (issue: string) => <Tag color="orange">{issue}</Tag>,
                                                    width: 150
                                                },
                                                {
                                                    title: 'Memory',
                                                    dataIndex: 'memoryState',
                                                    key: 'memoryState',
                                                    render: (state: any) => (
                                                        <pre style={{ 
                                                            fontSize: '11px', 
                                                            margin: 0,
                                                            maxHeight: '100px',
                                                            overflow: 'auto'
                                                        }}>
                                                            {JSON.stringify(state, null, 2)}
                                                        </pre>
                                                    )
                                                },
                                                {
                                                    title: 'Database',
                                                    dataIndex: 'dbState',
                                                    key: 'dbState',
                                                    render: (state: any) => (
                                                        <pre style={{ 
                                                            fontSize: '11px', 
                                                            margin: 0,
                                                            maxHeight: '100px',
                                                            overflow: 'auto'
                                                        }}>
                                                            {JSON.stringify(state, null, 2)}
                                                        </pre>
                                                    )
                                                }
                                            ]}
                                            pagination={false}
                                            size="small"
                                            scroll={{ x: true }}
                                        />
                                    </div>
                                </Card>
                            )}

                            {/* Missing Conversations */}
                            {(healthReport.missingInMemory.length > 0 || healthReport.missingInDB.length > 0) && (
                                <Card title="Missing Conversations" size="small">
                                    {healthReport.missingInMemory.length > 0 && (
                                        <div style={{ marginBottom: healthReport.missingInDB.length > 0 ? '24px' : 0 }}>
                            <Alert
                                message={`${healthReport.missingInMemory.length} conversation(s) in Database but NOT in Memory`}
                                description="These conversations exist in IndexedDB but aren't loaded in the app state"
                                type="error"
                                style={{ marginBottom: '16px' }}
                            />
                                            <div style={{ maxHeight: '300px', overflow: 'auto' }}>
                            <Table
                                dataSource={healthReport.missingInMemory}
                                columns={columns}
                                pagination={false}
                                size="small"
                                                scroll={{ x: true }}
                            />
                                            </div>
                                        </div>
                    )}
                    {healthReport.missingInDB.length > 0 && (
                                        <div>
                            <Alert
                                message={`${healthReport.missingInDB.length} conversation(s) in Memory but NOT in Database`}
                                description="These conversations are in app state but not persisted to IndexedDB"
                                type="error"
                                style={{ marginBottom: '16px' }}
                            />
                                            <div style={{ maxHeight: '300px', overflow: 'auto' }}>
                            <Table
                                dataSource={healthReport.missingInDB}
                                columns={columns}
                                pagination={false}
                                size="small"
                                                scroll={{ x: true }}
                            />
                                            </div>
                                        </div>
                    )}
                </Card>
            )}
                        </Space>
                    </TabPane>

                    {/* Inactive Conversations Tab */}
                    <TabPane 
                        tab={
                            <span>
                                Inactive {healthReport.inactiveConversations.length > 0 && 
                                    <Tag color="red">{healthReport.inactiveConversations.length}</Tag>}
                            </span>
                        }
                        key="inactive"
                    >
            {healthReport.inactiveConversations.length > 0 && (
                            <Card title="Soft-Deleted Conversations" size="small">
                    <Alert
                        message={`${healthReport.inactiveConversations.length} conversation(s) marked as inactive (isActive: false)`}
                        description="These conversations are soft-deleted and will not appear in history"
                        type="info"
                        style={{ marginBottom: '16px' }}
                    />
                                <div style={{ maxHeight: '600px', overflow: 'auto' }}>
                    <Table
                        dataSource={healthReport.inactiveConversations}
                        columns={columns}
                        pagination={{ pageSize: 10 }}
                        size="small"
                                        scroll={{ x: true }}
                    />
                                </div>
                </Card>
            )}
                    </TabPane>

                    {/* All Conversations Tab */}
                    <TabPane tab="All Conversations" key="all">
                        <Space direction="vertical" size="large" style={{ width: '100%' }}>
                            <Card title={`Conversations in Memory (${healthReport.memoryConversations.length})`} size="small">
                                <div style={{ maxHeight: '500px', overflow: 'auto' }}>
                    <Table
                        dataSource={healthReport.memoryConversations}
                        columns={columns}
                        pagination={{ pageSize: 20 }}
                        size="small"
                                        scroll={{ x: true }}
                        rowClassName={(record) => record.id === currentConversationId ? 'current-conversation-row' : ''}
                    />
                                </div>
                            </Card>
                            
                            <Card title={`Conversations in IndexedDB (${healthReport.dbConversations.length})`} size="small">
                                <div style={{ maxHeight: '500px', overflow: 'auto' }}>
                    <Table
                        dataSource={healthReport.dbConversations}
                        columns={columns}
                        pagination={{ pageSize: 20 }}
                        size="small"
                                        scroll={{ x: true }}
                        rowClassName={(record) => record.id === currentConversationId ? 'current-conversation-row' : ''}
                    />
                                </div>
                            </Card>
                        </Space>
                    </TabPane>

                    {/* Backup Data Tab */}
                    <TabPane tab="Backup Data" key="backup">
                {healthReport.backupData && (
                            <Card 
                                title={`localStorage Backup (${Array.isArray(healthReport.backupData) ? healthReport.backupData.length : 'N/A'} conversations)`}
                                size="small"
                            >
                        <pre style={{ 
                                    maxHeight: '600px',
                            overflow: 'auto',
                            backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                            padding: '12px',
                                    borderRadius: '4px',
                                    fontSize: '12px'
                        }}>
                            {JSON.stringify(healthReport.backupData, null, 2)}
                        </pre>
                            </Card>
                )}
                    </TabPane>

                    {/* Actions Tab */}
                    <TabPane tab="Actions" key="actions">
                        <Space direction="vertical" size="large" style={{ width: '100%' }}>
                            <Card title="Database Operations" size="small">
                                <Space direction="vertical" style={{ width: '100%' }}>
                                    <Alert
                                        message="Repair Database"
                                        description="Validates conversation data and removes invalid entries. This is safe and recommended if you're experiencing issues."
                                        type="info"
                                        action={
                                            <Button size="small" type="primary" onClick={handleRepairDatabase}>
                                                Repair Now
                                            </Button>
                                        }
                                    />
                                    
                                    <Alert
                                        message="Reload from Database"
                                        description="Reloads all conversation data from IndexedDB. Use this if memory state seems corrupted but database is fine."
                                        type="info"
                                        action={
                                            <Button size="small" onClick={handleReloadFromDB}>
                                                Reload
                                            </Button>
                                        }
                                    />
                                    
                                    <Alert
                                        message="Force Reset"
                                        description="DANGER: Deletes ALL conversation data permanently. Only use this as a last resort."
                                        type="error"
                                        action={
                                            <Button size="small" danger onClick={handleForceReset}>
                                                Reset Everything
                                            </Button>
                                        }
                                    />
                                </Space>
                            </Card>
                        </Space>
                    </TabPane>
                </Tabs>
            </div>

            <style>{`
                .current-conversation-row {
                    background-color: ${isDarkMode ? 'rgba(24, 144, 255, 0.2)' : 'rgba(24, 144, 255, 0.1)'} !important;
                    font-weight: bold;
                }
                
                .ant-tabs-content {
                    height: 100%;
                }
                
                .ant-tabs-tabpane {
                    height: 100%;
                    overflow: visible;
                }
            `}</style>
        </div>
    );
};

// Modal version that can be opened from anywhere
export const ConversationHealthDebugModal: React.FC<{ visible: boolean; onClose: () => void }> = ({ visible, onClose }) => {
    return (
        <Modal
            title="Conversation Health Diagnostics"
            open={visible}
            onCancel={onClose}
            footer={null}
            width="90%"
            style={{ top: 20 }}
        >
            <ConversationHealthDebug />
        </Modal>
    );
};

export default ConversationHealthDebug;
