import React, { useState, Suspense } from 'react';
import { Card, Tabs, Button, Tooltip, Modal, message, Space } from 'antd';
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

// Lazy load test components
const PrismTest = React.lazy(() => import('./PrismTest'));
const SyntaxTest = React.lazy(() => import('./SyntaxTest'));
const ApplyDiffTest = React.lazy(() => import('./ApplyDiffTest'));
const DiffTestView = React.lazy(() => import('./DiffTestView'));
const D3Test = React.lazy(() => import('./D3Test'));

const { TabPane } = Tabs;

export const Debug: React.FC = () => {
    const [activeKey, setActiveKey] = useState('d3');
    const [testReport, setTestReport] = useState<DiffTestReport | null>(null);
    const [isRunningTests, setIsRunningTests] = useState(false);
    const [isRepairing, setIsRepairing] = useState(false);
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
                    >
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
