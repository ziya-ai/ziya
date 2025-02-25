import React, { useState, useEffect } from 'react';
import { Card, Table, Tag, Button, Space, message } from 'antd';
import { loadPrismLanguage } from '../utils/prismLoader';
import './debug.css';

// Comprehensive list of Prism-supported languages
const PRISM_LANGUAGES = [
    'markup', 'html', 'xml', 'svg', 'mathml', 'ssml', 'atom', 'rss',
    'css',
    'clike',
    'javascript', 'js', 'typescript', 'ts',
    'jsx', 'tsx',
    'bash', 'shell',
    'c', 'cpp', 'cs', 'csharp',
    'python', 'py',
    'java',
    'php',
    'ruby', 'rb',
    'go',
    'rust',
    'dart',
    'kotlin',
    'scala',
    'swift',
    'objectivec',
    'sql',
    'yaml', 'yml',
    'json',
    'markdown', 'md',
    'graphql',
    'regex',
    'dockerfile',
    'git',
    'latex',
    'lua',
    'makefile',
    'perl',
    'r',
    'toml',
    'vim',
    'diff'
];

interface LanguageTestResult {
    language: string;
    status: 'success' | 'failed' | 'pending';
    error?: string;
    loadTime?: number;
}

const PrismTest: React.FC = () => {
    const [testResults, setTestResults] = useState<LanguageTestResult[]>([]);
    const [isTestingAll, setIsTestingAll] = useState(false);

    const testLanguage = async (language: string): Promise<LanguageTestResult> => {
        const start = performance.now();
        try {
            await loadPrismLanguage(language);
            const end = performance.now();
            return {
                language,
                status: 'success',
                loadTime: Math.round(end - start)
            };
        } catch (error) {
            return {
                language,
                status: 'failed',
                error: error instanceof Error ? error.message : 'Unknown error'
            };
        }
    };

    const runAllTests = async () => {
        setIsTestingAll(true);
        setTestResults(PRISM_LANGUAGES.map(lang => ({
            language: lang,
            status: 'pending'
        })));

        const results: LanguageTestResult[] = [];
        for (const language of PRISM_LANGUAGES) {
            const result = await testLanguage(language);
            results.push(result);
            setTestResults([...results]);
        }

        setIsTestingAll(false);
        
        // Calculate statistics
        const successful = results.filter(r => r.status === 'success').length;
        const failed = results.filter(r => r.status === 'failed').length;
        const avgLoadTime = results
            .filter(r => r.loadTime)
            .reduce((acc, curr) => acc + (curr.loadTime || 0), 0) / successful;

        message.info(
            `Test completed: ${successful} succeeded, ${failed} failed. ` +
            `Average load time: ${Math.round(avgLoadTime)}ms`
        );
    };

    const columns = [
        {
            title: 'Language',
            dataIndex: 'language',
            key: 'language',
            render: (text: string) => <code>{text}</code>
        },
        {
            title: 'Status',
            dataIndex: 'status',
            key: 'status',
            render: (status: string) => {
                let color = 'default';
                if (status === 'success') color = 'success';
                if (status === 'failed') color = 'error';
                if (status === 'pending') color = 'processing';
                return <Tag color={color}>{status.toUpperCase()}</Tag>;
            }
        },
        {
            title: 'Load Time',
            dataIndex: 'loadTime',
            key: 'loadTime',
            render: (time?: number) => time ? `${time}ms` : '-'
        },
        {
            title: 'Error',
            dataIndex: 'error',
            key: 'error',
            render: (error?: string) => error ? (
                <code style={{ color: '#ff4d4f' }}>{error}</code>
            ) : '-'
        }
    ];

    const getStatistics = () => {
        const total = testResults.length;
        const successful = testResults.filter(r => r.status === 'success').length;
        const failed = testResults.filter(r => r.status === 'failed').length;
        const pending = testResults.filter(r => r.status === 'pending').length;
        const avgLoadTime = testResults
            .filter(r => r.loadTime)
            .reduce((acc, curr) => acc + (curr.loadTime || 0), 0) / successful || 0;

        return (
            <Space direction="vertical">
                <div>Total languages: {total}</div>
                <div>
                    <Tag color="success">Success: {successful}</Tag>
                    <Tag color="error">Failed: {failed}</Tag>
                    <Tag color="processing">Pending: {pending}</Tag>
                </div>
                {successful > 0 && (
                    <div>Average load time: {Math.round(avgLoadTime)}ms</div>
                )}
            </Space>
        );
    };

    return (
        <Card title="Debug View: Prism Language Support Test">
            <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ marginBottom: 16 }}>
                    <Button
                        type="primary"
                        onClick={runAllTests}
                        loading={isTestingAll}
                        style={{ marginBottom: 16 }}
                    >
                        {isTestingAll ? 'Testing...' : 'Test All Languages'}
                    </Button>
                </div>

                {testResults.length > 0 && (
                    <div style={{ marginBottom: 16 }}>
                        {getStatistics()}
                    </div>
                )}

                <Table
                    dataSource={testResults}
                    columns={columns}
                    rowKey="language"
                    pagination={false}
                    size="small"
                />
            </Space>
        </Card>
    );
};

export default PrismTest;
