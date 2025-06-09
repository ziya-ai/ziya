import React, { useState, useEffect } from 'react';
import { Modal, List, Tag, Space, Button, Spin, Alert, Descriptions } from 'antd';
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
}

interface MCPStatus {
    initialized: boolean;
    servers: Record<string, MCPServer>;
    total_servers: number;
    connected_servers: number;
    config_path?: string;
}

const MCPStatusModal: React.FC<MCPStatusModalProps> = ({ visible, onClose }) => {
    const [status, setStatus] = useState<MCPStatus | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (visible) {
            fetchMCPStatus();
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

    const getServerDisplayName = (serverName: string) => {
        // The builtin flag will now come from the server status
        // We'll handle the display in the render function
        return serverName;
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
                                {status.config_path && (
                                    <div style={{ fontSize: '12px', marginTop: '4px', opacity: 0.8 }}>
                                        Config: {status.config_path}
                                    </div>
                                )}
                            </div>
                        }
                        type={status.initialized && status.connected_servers > 0 ? 'success' : 'warning'}
                        showIcon
                    />

                    <List
                        dataSource={Object.entries(status.servers)}
                        renderItem={([name, server]) => (
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
                                        <Tag color={server.connected ? 'green' : 'red'} icon={server.connected ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
                                            {server.connected ? 'Connected' : 'Disconnected'}
                                        </Tag>
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
