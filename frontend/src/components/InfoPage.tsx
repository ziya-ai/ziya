import React, { useState } from 'react';
import { Tabs, Card } from 'antd';
import {
    InfoCircleOutlined,
    ThunderboltOutlined,
    SettingOutlined
} from '@ant-design/icons';
import { CacheTelemetryDashboard } from './CacheTelemetryDashboard';
import { useTheme } from '../context/ThemeContext';

const { TabPane } = Tabs;

interface SystemInfo {
    version: any;
    directories: any;
    client: any;
    model: any;
    aws?: any;
    google?: any;
    features: any;
    plugins: any;
    formatters: any;
    environment_variables: any;
}

export const InfoPage: React.FC = () => {
    const { isDarkMode } = useTheme();
    const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
    const [activeTab, setActiveTab] = useState('system');

    // Fetch system info
    React.useEffect(() => {
        fetch('/api/info')
            .then(res => res.json())
            .then(setSystemInfo)
            .catch(console.error);
    }, []);

    return (
        <div style={{ 
            padding: '24px', 
            maxWidth: '1400px', 
            margin: '0 auto',
            backgroundColor: isDarkMode ? '#141414' : '#f0f2f5',
            minHeight: '100vh'
        }}>
            <h1 style={{ marginBottom: '24px' }}>
                <InfoCircleOutlined /> System Information
            </h1>

            <Tabs activeKey={activeTab} onChange={setActiveTab} size="large">
                <TabPane
                    tab={
                        <span>
                            <InfoCircleOutlined />
                            System Info
                        </span>
                    }
                    key="system"
                >
                    {systemInfo && (
                        <Card>
                            <pre style={{ 
                                backgroundColor: isDarkMode ? '#1f1f1f' : '#fafafa',
                                padding: '16px',
                                borderRadius: '4px',
                                overflow: 'auto'
                            }}>
                                {JSON.stringify(systemInfo, null, 2)}
                            </pre>
                        </Card>
                    )}
                </TabPane>

                <TabPane
                    tab={
                        <span>
                            <ThunderboltOutlined />
                            Cache Telemetry
                        </span>
                    }
                    key="telemetry"
                >
                    <CacheTelemetryDashboard />
                </TabPane>
            </Tabs>
        </div>
    );
};
