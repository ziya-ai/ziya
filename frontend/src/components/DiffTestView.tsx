import React, { useEffect, useState } from 'react';
import { Card, Tag, Space, Typography, Alert, Divider, Tabs, Button, Spin, message, Radio } from 'antd';
import { useTheme } from '../context/ThemeContext';
import MarkdownRenderer from './MarkdownRenderer';
import { RenderPath } from './MarkdownRenderer';
import { DiffTestRunner } from '../utils/diffTestRunner';
import { diffTestSuites } from '../utils/diffTestCases';
import { DiffTestReport } from '../utils/diffTestTypes';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

const renderPathOptions = [
    { label: 'Full Pipeline', value: 'full' },
    { label: 'Prism Only', value: 'prismOnly' },
    { label: 'Diff Only', value: 'diffOnly' },
    { label: 'Raw', value: 'raw' }
];

interface TestCase {
    id: string;
    name: string;
    category: string;
    subcategory: string;
    description: string;
    tags: string[];
    diff: string;
    sourceFile?: string;
    sourceContent?: string;
    expectedResult: {
        shouldApplyCleanly: boolean;
        expectedErrors?: string[];
    };
}

const DiffTestView: React.FC = () => {
    const { isDarkMode } = useTheme();
    const [testCases, setTestCases] = useState<TestCase[]>([]);
    const [selectedCategory, setSelectedCategory] = useState<string>('all');
    const [loading, setLoading] = useState(true);
    const [validationResults, setValidationResults] = useState<Record<string, boolean>>({});
    const [testReport, setTestReport] = useState<DiffTestReport | null>(null);
    const [isRunningTests, setIsRunningTests] = useState(false);
    const [renderPath, setRenderPath] = useState<RenderPath>('full');

    const runAllTests = async () => {
        setIsRunningTests(true);
        const testRunner = new DiffTestRunner();
        try {
            const results = await Promise.all(
                diffTestSuites.map(suite => testRunner.runSuite(suite))
            );
            setTestReport(results[0]);
            message.success('Test suite completed');
        } catch (error) {
            message.error('Failed to run tests');
            console.error('Test error:', error);
        } finally {
            setIsRunningTests(false);
        }
    };

    useEffect(() => {
        async function loadCases() {
            setLoading(true);
            try {
                const basePath = `${process.env.PUBLIC_URL || ''}/testcases`;
                console.log('Loading test cases from:', basePath);

                const response = await fetch(`${basePath}/index.json`);
                if (!response.ok) {
                    throw new Error(`Failed to load index.json: ${response.statusText}`);
                }
                const index = await response.json();
                console.log('Index loaded:', index);

                const allCases = await Promise.all(
                    Object.entries(index.testSets || {}).map(async ([category, setInfo]: [string, any]) => {
                        return Promise.all(setInfo.cases.map(async (caseId: string) => {
                            const [metaResponse, diffResponse] = await Promise.all([
                                fetch(`${basePath}/${setInfo.path}/${caseId}.meta.json`),
                                fetch(`${basePath}/${setInfo.path}/${caseId}.diff`)
                            ]);

                            const metadata = await metaResponse.json();
                            const diff = await diffResponse.text();

                            return { ...metadata, id: caseId, category, diff };
                        }));
                    })
                );

                const validCases = allCases.flat().filter((testCase): testCase is TestCase =>
                    Boolean(testCase) && Boolean(testCase.id) && Boolean(testCase.name) &&
                    Array.isArray(testCase.tags));
                setTestCases(validCases);
            } catch (error) {
                console.error('Error loading test cases:', error);
                message.error('Failed to load test cases');
            } finally {
                setLoading(false);
            }
        }
        loadCases();
    }, []);

    const validateDiff = async (testCase: TestCase) => {
        try {
            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: testCase.diff,
                    filePath: testCase.sourceFile,
                    content: testCase.sourceContent,
                    validate: true
                })
            });

            const success = response.ok;
            setValidationResults(prev => ({
                ...prev,
                [testCase.id]: success
            }));

            message.success(success ? 'Diff validation successful' : 'Diff validation failed');
            return success;
        } catch (error) {
            console.error('Validation error:', error);
            message.error('Validation error occurred');
            return false;
        }
    };

    const categories = [...new Set(testCases.map(tc => tc.category))];
    const filteredCases = selectedCategory === 'all'
        ? testCases
        : testCases.filter(tc => tc.category === selectedCategory);

    const gridStyle = {
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(600px, 1fr))',
        gap: '16px',
        padding: '16px'
    };

    return (
        <div>
            <Space direction="vertical" style={{ width: '100%', marginBottom: '16px' }}>
                <Title level={3}>Diff Rendering Test Cases</Title>
                <Space>
                    <Button
                        type="primary"
                        onClick={runAllTests}
                        loading={isRunningTests}
                    >
                        Run All Tests
                    </Button>
                    <Button
                        onClick={() => setValidationResults({})}
                        disabled={Object.keys(validationResults).length === 0}
                    >
                        Clear Results
                    </Button>
                </Space>
            </Space>

            {loading ? (
                <div style={{ textAlign: 'center', padding: '40px' }}>
                    <Spin size="large" />
                    <div style={{ marginTop: '16px' }}>Loading test cases...</div>
                </div>
            ) : (
                <>
                    <Alert
                        message="Visual Verification Suite"
                        description={`${testCases.length} test cases loaded across ${categories.length} categories.
                                    Each case can be validated against its source file when provided.`}
                        type="info"
                        showIcon
                        style={{ margin: '16px' }}
                    />

                    <Card style={{ margin: '16px' }}>
                        <Space direction="vertical">
                            <Text strong>Render Pipeline Control</Text>
                            <Radio.Group 
                                options={renderPathOptions} 
                                onChange={e => setRenderPath(e.target.value)} 
                                value={renderPath}
                                optionType="button"
                                buttonStyle="solid"
                            />
                        </Space>
                    </Card>

                    {testReport && (
                        <Alert
                            message="Test Results"
                            description={
                                <Space direction="vertical" size="small">
                                    <Title level={5}>Summary</Title>
                                    <Space direction="vertical">
                                        <Text>Total Suites: {testReport.summary.totalSuites}</Text>
                                        <Text>Total Tests: {testReport.summary.totalTests}</Text>
                                        <Text>Passed: {testReport.summary.totalPassed}</Text>
                                        <Text>Failed: {testReport.summary.totalFailed}</Text>
                                        <Text>Skipped: {testReport.summary.totalSkipped}</Text>
                                        <Text>Duration: {testReport.summary.totalDuration}ms</Text>
                                    </Space>

                                    <Divider />

                                    <Title level={5}>Suite Details</Title>
                                    {testReport.suiteResults.map((suite, index) => (
                                        <Card size="small" key={index} style={{ marginBottom: 8 }}>
                                            <Space direction="vertical">
                                                <Text strong>{suite.suiteName}</Text>
                                                <Space>
                                                    <Tag color="blue">{suite.totalTests} tests</Tag>
                                                    <Tag color="success">{suite.passed} passed</Tag>
                                                    {suite.failed > 0 && <Tag color="error">{suite.failed} failed</Tag>}
                                                    {suite.skipped > 0 && <Tag color="warning">{suite.skipped} skipped</Tag>}
                                                </Space>
                                            </Space>
                                        </Card>
                                    ))}
                                </Space>
                            }
                            type={testReport.summary.totalFailed === 0 ? "success" : "warning"}
                            showIcon
                            style={{ margin: '16px' }}
                        />
                    )}

                    <Tabs
                        activeKey={selectedCategory}
                        onChange={setSelectedCategory}
                        style={{ margin: '0 16px' }}
                    >
                        <TabPane tab="All Cases" key="all" />
                        {categories.map(category => (
                            <TabPane tab={category} key={category} />
                        ))}
                    </Tabs>

                    <div style={gridStyle}>
                        {filteredCases.map(testCase => (
                            <Card
                                key={testCase.id}
                                title={testCase.name}
                                extra={
                                    <Space>
                                        <Tag color="blue">{testCase.id}</Tag>
                                        <Tag color="purple">{testCase.category}</Tag>
                                        {testCase.subcategory && (
                                            <Tag color="cyan">{testCase.subcategory}</Tag>
                                        )}
                                        {validationResults[testCase.id] !== undefined && (
                                            <Tag color={validationResults[testCase.id] ? 'success' : 'error'}>
                                                {validationResults[testCase.id] ? 'Valid' : 'Invalid'}
                                            </Tag>
                                        )}
                                    </Space>
                                }
                            >
                                <Space direction="vertical" style={{ width: '100%' }}>
                                    <Text type="secondary">{testCase.description}</Text>
                                    {testCase.tags && testCase.tags.length > 0 && (
                                        <Space wrap>
                                            {testCase.tags.map(tag => (
                                                <Tag key={tag}>{tag}</Tag>
                                            ))}
                                        </Space>
                                    )}
                                    <div className="diff-container">
                                        <MarkdownRenderer
                                            markdown={`\`\`\`diff\n${testCase.diff}\n\`\`\``}
                                            enableCodeApply={false}
                                            renderPath={renderPath}
                                        />
                                    </div>
                                    {testCase.sourceContent && (
                                        <>
                                            <Divider>Source File</Divider>
                                            <pre style={{
                                                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                                                padding: '16px',
                                                borderRadius: '6px',
                                                overflow: 'auto'
                                            }}>
                                                <code>{testCase.sourceContent}</code>
                                            </pre>
                                        </>
                                    )}
                                    <Button
                                        onClick={() => validateDiff(testCase)}
                                        style={{ marginTop: '8px' }}
                                    >
                                        Validate Diff
                                    </Button>
                                </Space>
                            </Card>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
};

export default DiffTestView;
