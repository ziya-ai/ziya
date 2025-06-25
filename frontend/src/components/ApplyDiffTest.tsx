import React, { useState, useEffect } from 'react';
import { Card, message } from 'antd';

interface TestCase {
    id: string;
    name: string;
    description: string;
    diff: string;
    targetFile: string;
    targetContent: string;
    status: 'pending' | 'success' | 'failed';
    error?: string;
    lastRun?: string;
    category: string;
}

const ApplyDiffTest: React.FC = () => {
    const [isLoading, setIsLoading] = useState(false);
    const [testCases, setTestCases] = useState<TestCase[]>([]);
    const [categories, setCategories] = useState<string[]>([]);

    useEffect(() => {
        loadTestCases();
    });

    const loadTestCases = async () => {
        try {
            // First, scan the testcases directory
            const response = await fetch('/testcases');
            const directories = await response.json();

            setCategories(directories.filter(dir => !dir.startsWith('.')));

            const loadedTests: TestCase[] = [];

            // For each category directory
            for (const category of directories) {
                // Skip hidden directories and files
                if (category.startsWith('.')) continue;

                // Load all test cases in this category
                const categoryResponse = await fetch(`/testcases/${category}`);
                const testFiles = await categoryResponse.json();

                // Look for pairs of .diff and .source files
                const diffFiles = testFiles.filter(f => f.endsWith('.diff'));

                for (const diffFile of diffFiles) {
                    const baseName = diffFile.replace('.diff', '');
                    const sourceFile = `${baseName}.source`;

                    if (testFiles.includes(sourceFile)) {
                        // Load the test case content
                        const [diffContent, sourceContent] = await Promise.all([
                            fetch(`/testcases/${category}/${diffFile}`).then(r => r.text()),
                            fetch(`/testcases/${category}/${sourceFile}`).then(r => r.text())
                        ]);

                        loadedTests.push({
                            id: `${category}-${baseName}`,
                            name: baseName,
                            description: `${category} - ${baseName}`,
                            diff: diffContent,
                            targetContent: sourceContent,
                            targetFile: extractTargetFile(diffContent),
                            status: 'pending',
                            category
                        });
                    }
                }
            }

            setTestCases(loadedTests);
        } catch (error) {
            console.error('Error loading test cases:', error);
            message.error('Failed to load test cases');
        }
    };

    const extractTargetFile = (diff: string): string => {
        // Extract target file from diff header
        const match = diff.match(/^\+\+\+ b\/(.*?)$/m);
        return match ? match[1] : '';
    };

    const saveTestCases = (cases: TestCase[]) => {
        try {
            localStorage.setItem('ZIYA_DIFF_TEST_CASES', JSON.stringify(cases));
            setTestCases(cases);
        } catch (error) {
            console.error('Error saving test cases:', error);
        }
    };

    const runTest = async (testCase: TestCase) => {
        try {
            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: testCase.diff,
                    filePath: testCase.targetFile
                }),
            });

            // Get error text if response is not ok
            const errorText = !response.ok ? await response.text() : undefined;

            const newCases: TestCase[] = testCases.map(tc => tc.id === testCase.id
                ? {
                    ...tc,
                    status: response.ok ? 'success' as const : 'failed' as const,
                    error: errorText,
                    lastRun: new Date().toISOString()
                } : tc);

            saveTestCases(newCases);

            message.info(`Test ${response.ok ? 'succeeded' : 'failed'}`);
        } catch (error) {

            const newCases: TestCase[] = testCases.map(tc =>
                tc.id === testCase.id ? {
                    ...tc,
                    status: 'failed' as const,
                    error: error instanceof Error ? error.message : 'Unknown error',
                    lastRun: new Date().toISOString()
                } : tc
            );

            saveTestCases(newCases);
            message.error('Test failed');
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <Card title="Debug View: Apply Diff Test Cases">
        </Card>
    );
};

export default ApplyDiffTest;
