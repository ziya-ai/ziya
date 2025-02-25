export interface TestCase {
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

export interface TestCaseMetadata {
    name: string;
    description: string;
    tags: string[];
    expectedResult: {
        shouldApplyCleanly: boolean;
        expectedErrors?: string[];
    };
}

export async function loadTestCases(): Promise<TestCase[]> {
    try {
        // First load the index file
        const indexResponse = await fetch('/testcases/index.json');
        const index = await indexResponse.json();
        
        const testCases: TestCase[] = [];
        
        // Load test cases for each category
        for (const category of index.categories) {
            const testSets = index.testSets[category] || [];
            
            for (const testSet of testSets) {
                // Load the test set index
                const testSetResponse = await fetch(`/testcases/${category}/${testSet}/index.json`);
                const testSetIndex = await testSetResponse.json();
                
                // Load each test case in the set
                for (const testId of testSetIndex.cases) {
                    const [metadataResponse, diffResponse] = await Promise.all([
                        fetch(`/testcases/${category}/${testSet}/${testId}.meta.json`),
                        fetch(`/testcases/${category}/${testSet}/${testId}.diff`)
                    ]);
                    
                    const metadata = await metadataResponse.json();
                    const diff = await diffResponse.text();
                    
                    // Try to load source file if it exists
                    let sourceContent: string | undefined;
                    try {
                        const sourceResponse = await fetch(`/testcases/${category}/${testSet}/${testId}.source`);
                        if (sourceResponse.ok) {
                            sourceContent = await sourceResponse.text();
                        }
                    } catch (e) {
                        // Source file is optional
                    }
                    
                    testCases.push({
                        id: testId,
                        category,
                        subcategory: testSet,
                        name: metadata.name,
                        description: metadata.description,
                        tags: metadata.tags,
                        diff,
                        sourceFile: sourceContent ? `${category}/${testSet}/${testId}` : undefined,
                        sourceContent,
                        expectedResult: metadata.expectedResult
                    });
                }
            }
        }
        
        return testCases;
    } catch (e) {
        console.error('Error loading test cases:', e);
        return [];
    }
}
