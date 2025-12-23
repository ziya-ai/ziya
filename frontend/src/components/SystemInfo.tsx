import React, { useState, useEffect } from 'react';
import { Card, Typography, Tag, Descriptions, Spin } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, WarningOutlined } from '@ant-design/icons';

const { Title, Text, Paragraph } = Typography;

export const SystemInfo: React.FC = () => {
  const [systemInfo, setSystemInfo] = useState<any>(null);
  const [formatterInfo, setFormatterInfo] = useState<any>({ formatters: [] });
  const [mcpInfo, setMcpInfo] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Enable body scrolling for this page
    document.body.classList.add('allow-scroll');

    // Cleanup: remove class when component unmounts
    return () => {
      document.body.classList.remove('allow-scroll');
    };
  }, []);

  useEffect(() => {
    // Fetch system information
    fetch('/api/info')
      .then(response => response.json())
      .then(data => {
        setSystemInfo(data);
        setLoading(false);
      })
      .catch(error => {
        console.error('Error fetching system info:', error);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    // Get formatter info from window registry
    const updateFormatters = () => {
      if ((window as any).FormatterRegistry) {
        const formatters = (window as any).FormatterRegistry.getAllFormatters() || [];
        setFormatterInfo({
          count: formatters.length,
          // Filter out undefined/null entries
          formatters: formatters.filter((f: any) => f && f.formatterId).map((f: any) => ({
            id: f.formatterId,
            priority: f.priority
          }))
        });
      }
    };

    // Try immediately and then after a delay to catch late-loading formatters
    updateFormatters();
    const timer = setTimeout(updateFormatters, 500);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    // Fetch MCP server status
    fetch('/api/mcp/status')
      .then(response => response.json())
      .then(data => {
        setMcpInfo(data);
      })
      .catch(error => {
        console.error('Error fetching MCP status:', error);
      });
  }, []);

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Spin size="large" />
        <div style={{ marginTop: 16 }}>Loading system information...</div>
      </div>
    );
  }

  const getStatusBadge = (status: string) => {
    if (status === 'Valid') return <Tag color="success" icon={<CheckCircleOutlined />}>Valid</Tag>;
    if (status === 'Expired') return <Tag color="error" icon={<CloseCircleOutlined />}>Expired</Tag>;
    return <Tag color="warning" icon={<WarningOutlined />}>{status}</Tag>;
  };

  return (
    <div style={{
      padding: 24,
      maxWidth: 1200,
      margin: '0 auto',
      minHeight: '100vh',
      overflowY: 'auto'
    }}>
      <Title level={1} style={{ marginBottom: 8 }}>🔧 Ziya System Information</Title>
      <Paragraph>
        <Text strong>Edition:</Text> {systemInfo?.version?.edition || 'Unknown'}
        {' • '}
        <Text strong>Version:</Text> {systemInfo?.version?.ziya_version || 'Unknown'}
      </Paragraph>

      {/* Version Information */}
      <Card title="📦 Version Information" style={{ marginBottom: 16 }}>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="Python Version">{systemInfo?.version?.python_version}</Descriptions.Item>
          <Descriptions.Item label="Python Executable">
            <Text code style={{ fontSize: 11 }}>{systemInfo?.version?.python_executable}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Platform">{systemInfo?.version?.platform}</Descriptions.Item>
          {systemInfo?.version?.build_info && (
            <Descriptions.Item label="Build Info">
              <Text code style={{ fontSize: 11 }}>
                {JSON.stringify(systemInfo.version.build_info)}
              </Text>
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {/* Directories */}
      <Card title="📁 Directories" style={{ marginBottom: 16 }}>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="Root Directory">
            <Text code style={{ fontSize: 11 }}>{systemInfo?.directories?.root}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Working Directory">
            <Text code style={{ fontSize: 11 }}>{systemInfo?.directories?.current_working_directory}</Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Model Configuration */}
      <Card title="🤖 Model Configuration" style={{ marginBottom: 16 }}>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="Endpoint">
            <Text strong>{systemInfo?.model?.endpoint}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Model">
            <Text strong>{systemInfo?.model?.model}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Model Alias">
            <Text code>{systemInfo?.model?.current_alias || 'N/A'}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Model ID">
            <Text code style={{ fontSize: 11 }}>{JSON.stringify(systemInfo?.model?.current_id) || 'N/A'}</Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* AWS Configuration (if using Bedrock) */}
      {systemInfo?.model?.endpoint === 'bedrock' && systemInfo?.aws && (
        <Card title="☁️ AWS Configuration" style={{ marginBottom: 16 }}>
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="Profile">{systemInfo.aws.profile}</Descriptions.Item>
            <Descriptions.Item label="Region">{systemInfo.aws.region}</Descriptions.Item>
            <Descriptions.Item label="Account ID">{systemInfo.aws.account_id || 'N/A'}</Descriptions.Item>
            <Descriptions.Item label="Status">
              {getStatusBadge(systemInfo.aws.status)}
            </Descriptions.Item>
            <Descriptions.Item label="Access Key">
              <Text code>{systemInfo.aws.access_key || 'N/A'}</Text>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      {/* Plugins */}
      <Card title="🔌 Plugins" style={{ marginBottom: 16 }}>
        {systemInfo?.plugins?.auth_providers && (
          <div style={{ marginBottom: 16 }}>
            <Text strong>Authentication Providers ({systemInfo.plugins.auth_providers.count})</Text>
            <ul>
              {systemInfo.plugins.auth_providers.providers.map((p: any) => (
                <li key={p.id}>
                  {p.id} {p.active && <Tag color="green">Active</Tag>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {systemInfo?.plugins?.config_providers && (
          <div style={{ marginBottom: 16 }}>
            <Text strong>Configuration Providers ({systemInfo.plugins.config_providers.count})</Text>
            <ul>
              {systemInfo.plugins.config_providers.providers.map((p: string) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        )}

        {systemInfo?.plugins?.registry_providers && (
          <div style={{ marginBottom: 16 }}>
            <Text strong>Registry Providers ({systemInfo.plugins.registry_providers.count})</Text>
            <ul>
              {systemInfo.plugins.registry_providers.providers.map((p: string) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Formatter Providers */}
        <div>
          <Text strong>Formatter Providers ({formatterInfo.count || 0})</Text>
          <ul>
            {formatterInfo.formatters.length > 0 ? (
              formatterInfo.formatters.map((f: any) => (
                <li key={f.id}>
                  {f.id} <Text type="secondary" style={{ fontSize: 11 }}>(priority: {f.priority})</Text>
                </li>
              ))
            ) : (
              <li style={{ opacity: 0.6 }}>No formatters registered</li>
            )}
          </ul>
        </div>
      </Card>

      {/* Feature Flags */}
      <Card title="⚙️ Feature Flags" style={{ marginBottom: 16 }}>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="AST Analysis">
            {systemInfo?.features?.ast_enabled ? '✓ Enabled' : '✗ Disabled'}
          </Descriptions.Item>
          <Descriptions.Item label="MCP Tools">
            {systemInfo?.features?.mcp_enabled ? '✓ Enabled' : '✗ Disabled'}
          </Descriptions.Item>
          <Descriptions.Item label="Ephemeral Mode">
            {systemInfo?.features?.ephemeral_mode ? '✓ Enabled' : '✗ Disabled'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* MCP Servers */}
      <Card title="🔧 MCP Servers" style={{ marginBottom: 16 }}>
        {mcpInfo?.servers ? (
          <>
            <Descriptions column={1} bordered size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="MCP Enabled">
                {mcpInfo.enabled ? '✓ Yes' : '✗ No'}
              </Descriptions.Item>
              <Descriptions.Item label="Connected Servers">
                {Object.values(mcpInfo.servers).filter((s: any) => s.connected).length} / {Object.keys(mcpInfo.servers).length}
              </Descriptions.Item>
              <Descriptions.Item label="Total Tools">
                {Object.values(mcpInfo.servers).reduce((sum: number, s: any) => sum + (s.tools || 0), 0)}
              </Descriptions.Item>
            </Descriptions>

            {Object.entries(mcpInfo.servers).map(([serverName, server]: [string, any]) => (
              <Card
                key={serverName}
                size="small"
                title={serverName}
                style={{ marginBottom: 12 }}
              >
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="Status">
                    {server.connected ? (
                      <Tag color="success" icon={<CheckCircleOutlined />}>Connected</Tag>
                    ) : (
                      <Tag color="error" icon={<CloseCircleOutlined />}>Disconnected</Tag>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label="Tools">
                    {server.tools || 0} tools available
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ))}
          </>
        ) : (
          <Text type="secondary">Loading MCP server information...</Text>
        )}
      </Card>

      {/* Environment Variables */}
      <Card title="🌍 Environment Variables" style={{ marginBottom: 16 }}>
        <div style={{
          fontFamily: 'monospace',
          fontSize: 12,
          background: '#2d2d2d',
          color: '#f8f8f2',
          padding: 15,
          borderRadius: 5,
          maxHeight: 400,
          overflow: 'auto'
        }}>
          {systemInfo?.environment_variables && Object.entries(systemInfo.environment_variables).map(([key, value]) => (
            <div key={key} style={{ marginBottom: 4 }}>
              <span style={{ color: '#66d9ef' }}>{key}</span>=
              <span style={{ color: '#a6e22e' }}>{String(value)}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
