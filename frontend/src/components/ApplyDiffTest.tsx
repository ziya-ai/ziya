import React, { useState, useEffect } from 'react';
import { Card, Table, Button, Space, Modal, Input, Tag, Tooltip, message, Select } from 'antd';
import { PlusOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

type TestStatus = 'pending' | 'success' | 'failed';

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
   const [isModalVisible, setIsModalVisible] = useState(false);
   const [currentTest, setCurrentTest] = useState<TestCase | null>(null);
   const { isDarkMode } = useTheme();
   const [categories, setCategories] = useState<string[]>([]);
   const { TextArea } = Input;

   useEffect(() => {
       loadTestCases();
   }, []);

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

   const addTestCase = () => {
       setCurrentTest({
           id: Date.now().toString(),
           name: '',
           description: '',
           diff: '',
           targetFile: '',
           targetContent: '',
            status: 'pending',
            category: 'custom'  // Default category for manually added test cases
       });
       setIsModalVisible(true);
   };

   const handleSave = () => {
       if (!currentTest) return;
       
       const newCases = currentTest.id 
           ? testCases.map(tc => tc.id === currentTest.id ? currentTest : tc)
           : [...testCases, currentTest];
           
       saveTestCases(newCases);
       setIsModalVisible(false);
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

           const updatedTest = {
               ...testCase,
               status: response.ok ? 'success' : 'failed',
               error: response.ok ? undefined : await response.text(),
               lastRun: new Date().toISOString()
           };

	   // Get error text if response is not ok
           const errorText = !response.ok ? await response.text() : undefined;

           const newCases: TestCase[] = testCases.map(tc => tc.id === testCase.id
               ? {
                    ...tc,
                    status: response.ok ? 'success' as const : 'failed' as const,
		    error: errorText,
                    lastRun: new Date().toISOString()
               } : tc );
           
           saveTestCases(newCases);
           
           message.info(`Test ${response.ok ? 'succeeded' : 'failed'}`);
       } catch (error) {
           const updatedTest = {
               ...testCase,
               status: 'failed',
               error: error instanceof Error ? error.message : 'Unknown error',
               lastRun: new Date().toISOString()
           };
           
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

   const resetAllTests = () => {
       const resetCases = testCases.map(tc => ({
           ...tc,
           status: 'pending' as const,
           error: undefined,
           lastRun: undefined
       }));
       saveTestCases(resetCases);
       message.info('All tests reset');
   };

   const columns = [
       {
           title: 'Name',
           dataIndex: 'name',
           key: 'name',
       },
       {
           title: 'Description',
           dataIndex: 'description',
           key: 'description',
       },
       {
           title: 'Target File',
           dataIndex: 'targetFile',
           key: 'targetFile',
       },
       {
           title: 'Status',
           dataIndex: 'status',
           key: 'status',
           render: (status: string, record: TestCase) => (
               <Tooltip title={record.error}>
                   <Tag color={
                       status === 'success' ? 'success' :
                       status === 'failed' ? 'error' :
                       'default'
                   }>
                       {status.toUpperCase()}
                   </Tag>
               </Tooltip>
           ),
       },
       {
           title: 'Last Run',
           dataIndex: 'lastRun',
           key: 'lastRun',
           render: (date: string) => date ? new Date(date).toLocaleString() : '-'
       },
       {
           title: 'Action',
           key: 'action',
           render: (_, record: TestCase) => (
               <Space>
                   <Button onClick={() => runTest(record)}>Run</Button>
                   <Button onClick={() => {
                       setCurrentTest(record);
                       setIsModalVisible(true);
                   }}>Edit</Button>
               </Space>
           ),
       },
   ];

   return (
       <Card title="Debug View: Apply Diff Test Cases">
       </Card>
   );
};

export default ApplyDiffTest;
