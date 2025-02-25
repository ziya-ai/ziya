import React, { useState, Suspense } from 'react';
import { Card, Tabs, Button, Tooltip, Modal, message, Space, Alert, Typography } from 'antd';
import {
    ExperimentOutlined,
    CodeOutlined,
    ToolOutlined,
    DatabaseOutlined,
    DiffOutlined,
    BugOutlined,
    BulbOutlined
} from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import { db } from '../utils/db';
import { DiffTestRunner } from '../utils/diffTestRunner';
import { diffTestSuites } from '../utils/diffTestCases';
import { DiffTestReport } from '../utils/diffTestTypes';
import { useChatContext } from '../context/ChatContext';
import { useFolderContext } from '../context/FolderContext';

// Lazy load test components
const PrismTest = React.lazy(() => import('./PrismTest'));
const SyntaxTest = React.lazy(() => import('./SyntaxTest'));
const VegaLiteTest = React.lazy(() => import("./VegaLiteTest"));
const ApplyDiffTest = React.lazy(() => import('./ApplyDiffTest'));
const DiffTestView = React.lazy(() => import('./DiffTestView'));
const D3Test = React.lazy(() => import('./D3Test'));

const { TabPane } = Tabs;

export const Debug: React.FC = () => {
    const [activeKey, setActiveKey] = useState('d3');
    const [testReport, setTestReport] = useState<DiffTestReport | null>(null);
    const [isRunningTests, setIsRunningTests] = useState(false);
    const [isRepairing, setIsRepairing] = useState(false);
    const { dbError } = useChatContext();
    const { folders } = useFolderContext();
    const { isDarkMode, toggleTheme } = useTheme();

    const runAllTests = async () => {
        setIsRunningTests(true);
        const testRunner = new DiffTestRunner();
        try {
            const results = await Promise.all(
                diffTestSuites.map(suite => testRunner.runSuite(suite))
            );
            setTestReport(results[0]);
        } finally {
            setIsRunningTests(false);
        }
    };

    const handleForceReset = async () => {
        Modal.confirm({
            title: 'Force Reset Database',
            content: 'This will completely delete and reinitialize the database. All data will be lost. Continue?',
            okText: 'Yes',
            okType: 'danger',
            cancelText: 'No',
            onOk: async () => {
                try {
                    await db.forceReset();
                    message.success('Database reset successfully');
                    window.location.reload(); // Force reload to reinitialize everything
                } catch (error) {
                    message.error('Failed to reset database');
                }
            }
        });
    };

    const checkDatabaseHealth = async () => {
        try {
            const health = await db.checkDatabaseHealth();
            Modal.info({
                title: 'Database Health Check',
                content: (
                    <div>
                        <p>Status: {health.isHealthy ? 'Healthy' : 'Unhealthy'}</p>
                        {health.errors.length > 0 && (
                            <ul>
                                {health.errors.map((error, i) => <li key={i}>{error}</li>)}
                            </ul>
                        )}
                        <p>Can recover: {health.canRecover ? 'Yes' : 'No'}</p>
                    </div>
                )
            });
        } catch (error) {
            message.error('Failed to check database health');
        }
    };

    const handleRepairDatabase = async () => {
        Modal.confirm({
            title: 'Repair Database',
            content: 'This will attempt to repair the conversation database by removing corrupted entries. Continue?',
            okText: 'Yes',
            cancelText: 'No',
            onOk: async () => {
                setIsRepairing(true);
                try {
                    await db.repairDatabase();
                    message.success('Database repair completed successfully');
                } catch (error) {
                    message.error('Failed to repair database');
                    console.error('Database repair error:', error);
                } finally {
                    setIsRepairing(false);
                }
            }
        });
    };

    const handleClearDatabase = () => {
        Modal.confirm({
            title: 'Clear Database',
            content: 'This will permanently delete all conversations. This action cannot be undone. Continue?',
            okText: 'Yes',
            okType: 'danger',
            cancelText: 'No',
            onOk: async () => {
                await db.clearDatabase();
                message.success('Database cleared successfully');
            }
        });
    };

    return (
        <div style={{ 
            padding: '20px',
            minHeight: '100vh',
            backgroundColor: isDarkMode ? '#141414' : '#f0f2f5'
        }}>
            <Card style={{ marginBottom: 16 }}>
                {/* Database Status Section */}
                {dbError && (
                    <Alert
                        message="Database Error"
                        description={dbError}
                        type="error"
                        showIcon
			style={{ marginBottom: 16 }}
                            action={
                                <Button
                                    danger
                                    type="primary"
                                    onClick={async () => {
                                        try {
                                            await db.repairDatabase();
                                            message.success('Database reset complete - reloading page');
                                            window.location.reload();
                                        } catch (error) {
                                            message.error('Reset failed - please reload the page manually');
                                        }
                                    }}
                                >
                                    Nuclear Reset (Emergency Recovery)
                                </Button>
                            }
                        />
                )}
                <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
                    <Typography.Title level={4}>System Status</Typography.Title>
                    <Space wrap>
                        <Button onClick={checkDatabaseHealth}>
                            Check Database Health
                        </Button>
                        <Button type="primary" danger onClick={handleForceReset}>
                            Force Reset Database
                        </Button>
                        <Button 
                            onClick={() => window.localStorage.clear()} 
                            danger
                        >
                            Clear Local Storage
                        </Button>
                        <Alert
                            message={`File Context Status: ${folders ? 'Loaded' : 'Not Loaded'}`}
                            type={folders ? 'success' : 'warning'}
                        />    
		    </Space>
		</Space>
		<div style={{ position: 'relative' }}>
		    <Space style={{
                        position: 'absolute',
                        top: 0,
                        right: 0,
                        padding: '8px'
                    }}>
                        <Tooltip title="Toggle theme">
                            <Button 
                                icon={<BulbOutlined />} 
                                onClick={toggleTheme}
                                type={isDarkMode ? 'default' : 'primary'}
                            />
                        </Tooltip>
                        <Tooltip title="Repair Database">
                            <Button 
                                icon={<ToolOutlined />} 
                                onClick={handleRepairDatabase}
                                loading={isRepairing}
                            />
                        </Tooltip>
                        <Tooltip title="Clear Database">
                            <Button 
                                icon={<DatabaseOutlined />} 
                                onClick={handleClearDatabase}
                                danger
                            />
                        </Tooltip>
                        {activeKey === 'difftest' && (
                            <Button
                                type="primary"
                                onClick={runAllTests}
                                loading={isRunningTests}
                            >
                                Run All Tests
                            </Button>
                        )}
                    </Space>
                    <h2 style={{ 
                        margin: '0 0 16px 0',
                        color: isDarkMode ? '#ffffff' : '#000000'
                    }}>
                        Debug Views
                    </h2>
                </div>
            </Card>

            <Card>
                <Tabs 
                    activeKey={activeKey} 
                    onChange={setActiveKey}
                    type="card"
                >
                    <TabPane 
                        tab={<><ExperimentOutlined /> D3 Visualization</>} 
                        key="d3"
                        forceRender={true}
                    >
                        <Alert
                            message="D3 Visualization Debug"
                            description="Testing D3 rendering capabilities"
                            type="info"
                            showIcon
                            style={{ marginBottom: 16 }}
                        />
                        <Suspense fallback={<div>Loading D3 tests...</div>}>
                            <D3Test />
                        </Suspense>
                    </TabPane>
                    <TabPane 
                        tab={<><CodeOutlined /> Prism Support</> }
                        key="prism"
                    >
                        <Suspense fallback={<div>Loading Prism tests...</div>}>
                            <PrismTest />
                        </Suspense>
                    </TabPane>
                    <TabPane 
                        tab={<><CodeOutlined /> Syntax Tests</>}
                        key="syntax"
                    >
                        <Suspense fallback={<div>Loading syntax tests...</div>}>
                            <SyntaxTest />
                        </Suspense>
                    </TabPane>
		    <TabPane
                         tab={<><ExperimentOutlined /> Vega-Lite Gallery</>}
                         key="vega"
                     >
                         <VegaLiteTest />
                     </TabPane>
                    <TabPane 
                        tab={<><DiffOutlined /> Apply Diff</>}
                        key="applydiff"
                    >
                        <Suspense fallback={<div>Loading diff tests...</div>}>
                            <ApplyDiffTest />
                        </Suspense>
                    </TabPane>
                    <TabPane 
                        tab={<><BugOutlined /> Diff Tests</>}
                        key="difftest"
                    >
                        <Suspense fallback={<div>Loading diff test suite...</div>}>
                            <DiffTestView />
                        </Suspense>
                    </TabPane>
                </Tabs>
            </Card>
        </div>
    );
};

export default Debug;
