import React, { useState } from 'react';
import { Alert, Button, Card, Collapse, Space, Tag, Typography } from 'antd';
import {
    FileOutlined,
    SearchOutlined,
    EditOutlined,
    SwapOutlined,
    PlayCircleOutlined,
    CheckCircleOutlined,
    CloseCircleOutlined
} from '@ant-design/icons';
import { FileOperation, parseFileOperations } from '../utils/fileOperationParser';
import { useTheme } from '../context/ThemeContext';

const { Panel } = Collapse;
const { Text, Paragraph } = Typography;

interface FileOperationRendererProps {
    content: string;
    enableApply?: boolean;
}

export const FileOperationRenderer: React.FC<FileOperationRendererProps> = ({
    content,
    enableApply = false
}) => {
    const { isDarkMode } = useTheme();
    const [appliedOperations, setAppliedOperations] = useState<Set<string>>(new Set());

    const parseResult = parseFileOperations(content);

    if (!parseResult.hasValidOperations) {
        return (
            <Alert
                type="warning"
                message="Invalid File Operations Detected"
                description={
                    <div>
                        <p>The content contains file operation syntax but has errors:</p>
                        <ul>
                            {parseResult.errors.map((error, index) => (
                                <li key={index}>{error}</li>
                            ))}
                        </ul>
                    </div>
                }
                showIcon
            />
        );
    }

    const handleApplyOperation = async (operation: FileOperation) => {
        if (!operation.file || !enableApply) return;

        try {
            // Convert to diff format for the existing apply-changes API
            const diffContent = createDiffFromOperation(operation);

            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: diffContent,
                    filePath: operation.file
                }),
            });

            if (response.ok) {
                setAppliedOperations(prev => new Set([...prev, operation.raw || '']));
            }
        } catch (error) {
            console.error('Error applying file operation:', error);
        }
    };

    const createDiffFromOperation = (operation: FileOperation): string => {
        // Convert file operation to unified diff format
        if (operation.find && operation.replace) {
            return `--- a/${operation.file}
+++ b/${operation.file}
@@ -1,1 +1,1 @@
-${operation.find}
+${operation.replace}`;
        }

        if (operation.change) {
            return `--- a/${operation.file}
+++ b/${operation.file}
@@ -1,1 +1,1 @@
+${operation.change}`;
        }

        return '';
    };

    const getOperationIcon = (operation: FileOperation) => {
        if (operation.find && operation.replace) return <SwapOutlined />;
        if (operation.find) return <SearchOutlined />;
        if (operation.change) return <EditOutlined />;
        return <FileOutlined />;
    };

    const getOperationTitle = (operation: FileOperation) => {
        if (operation.find && operation.replace) return 'Find & Replace';
        if (operation.find) return 'Find';
        if (operation.change) return 'Change';
        return 'File Operation';
    };

    const renderOperation = (operation: FileOperation, index: number) => {
        const isApplied = appliedOperations.has(operation.raw || '');

        return (
            <Card
                key={index}
                size="small"
                title={
                    <Space>
                        {getOperationIcon(operation)}
                        <Text strong>{getOperationTitle(operation)}</Text>
                        {operation.file && <Tag color="blue">{operation.file}</Tag>}
                        {operation.isValid ? (
                            <CheckCircleOutlined style={{ color: '#52c41a' }} />
                        ) : (
                            <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                        )}
                    </Space>
                }
                extra={
                    enableApply && operation.isValid && !isApplied && (
                        <Button
                            type="primary"
                            size="small"
                            icon={<PlayCircleOutlined />}
                            onClick={() => handleApplyOperation(operation)}
                        >
                            Apply
                        </Button>
                    )
                }
                style={{ marginBottom: 16 }}
            >
                {/* Operation Details */}
                <Collapse ghost>
                    <Panel header="Operation Details" key="details">
                        {operation.find && (
                            <div style={{ marginBottom: 8 }}>
                                <Text strong>Find:</Text>
                                <Paragraph
                                    code
                                    copyable
                                    style={{
                                        backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                                        padding: 8,
                                        marginTop: 4
                                    }}
                                >
                                    {operation.find}
                                </Paragraph>
                            </div>
                        )}

                        {operation.replace && (
                            <div style={{ marginBottom: 8 }}>
                                <Text strong>Replace:</Text>
                                <Paragraph
                                    code
                                    copyable
                                    style={{
                                        backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                                        padding: 8,
                                        marginTop: 4
                                    }}
                                >
                                    {operation.replace}
                                </Paragraph>
                            </div>
                        )}

                        {operation.change && (
                            <div style={{ marginBottom: 8 }}>
                                <Text strong>Change:</Text>
                                <Paragraph
                                    code
                                    copyable
                                    style={{
                                        backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                                        padding: 8,
                                        marginTop: 4
                                    }}
                                >
                                    {operation.change}
                                </Paragraph>
                            </div>
                        )}
                    </Panel>
                </Collapse>

                {/* Warnings and Errors */}
                {operation.warnings.length > 0 && (
                    <Alert
                        type="warning"
                        message="Warnings"
                        description={
                            <ul>
                                {operation.warnings.map((warning, i) => (
                                    <li key={i}>{warning}</li>
                                ))}
                            </ul>
                        }
                        showIcon
                        style={{ marginTop: 8 }}
                    />
                )}

                {operation.errors.length > 0 && (
                    <Alert
                        type="error"
                        message="Errors"
                        description={
                            <ul>
                                {operation.errors.map((error, i) => (
                                    <li key={i}>{error}</li>
                                ))}
                            </ul>
                        }
                        showIcon
                        style={{ marginTop: 8 }}
                    />
                )}

                {isApplied && (
                    <Alert
                        type="success"
                        message="Operation Applied"
                        showIcon
                        style={{ marginTop: 8 }}
                    />
                )}
            </Card>
        );
    };

    return (
        <div className="file-operation-renderer">
            <Alert
                type="info"
                message={`Found ${parseResult.totalOperations} file operation(s)`}
                style={{ marginBottom: 16 }}
                showIcon
            />

            {parseResult.operations.map(renderOperation)}

            {parseResult.errors.length > 0 && (
                <Alert
                    type="error"
                    message="Parser Errors"
                    description={
                        <ul>
                            {parseResult.errors.map((error, i) => (
                                <li key={i}>{error}</li>
                            ))}
                        </ul>
                    }
                    showIcon
                />
            )}
        </div>
    );
};
