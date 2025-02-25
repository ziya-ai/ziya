export type DiffTestResult = {
    success: boolean;
    error?: string;
    appliedCleanly?: boolean;
    renderingErrors?: string[];
    syntaxHighlightingErrors?: string[];
    validationErrors?: string[];
};

export type DiffTestCase = {
    id: string;
    name: string;
    description: string;
    category: DiffTestCategory;
    type: DiffTestType;
    diff: string;
    targetFile?: string;
    targetContent?: string;
    expectedResult: DiffTestResult;
    tags: string[];
    metadata?: {
        complexity: 'simple' | 'moderate' | 'complex';
        features: string[];
        regression?: {
            id: string;
            description: string;
            dateIdentified: string;
        };
    };
};

export type DiffTestCategory = 
    | 'display-wellformed'    // Tests for correctly formatted diffs
    | 'display-mangled'       // Tests for malformed but displayable diffs
    | 'apply-wellformed'      // Tests for correctly formatted, applicable diffs
    | 'apply-mangled'         // Tests for malformed diffs that should still apply
    | 'apply-invalid';        // Tests for diffs that should fail to apply

export type DiffTestType = 
    | 'syntax-highlighting'   // Tests specific to syntax highlighting
    | 'whitespace'           // Tests for whitespace handling
    | 'line-endings'         // Tests for line ending variations
    | 'context'              // Tests for context lines
    | 'hunk-headers'         // Tests for hunk header parsing
    | 'file-headers'         // Tests for file header parsing
    | 'binary-files'         // Tests involving binary files
    | 'nested-diffs'         // Tests for diffs within diffs
    | 'regression';          // Tests for specific regression cases

export interface DiffTestSuite {
    name: string;
    description: string;
    cases: DiffTestCase[];
}

export interface DiffTestReport {
    suiteResults: {
        suiteName: string;
        totalTests: number;
        passed: number;
        failed: number;
        skipped: number;
        duration: number;
        cases: {
            caseId: string;
            name: string;
            result: DiffTestResult;
            duration: number;
        }[];
    }[];
    summary: {
        totalSuites: number;
        totalTests: number;
        totalPassed: number;
        totalFailed: number;
        totalSkipped: number;
        totalDuration: number;
    };
    timestamp: string;
    metadata?: {
        environment: string;
        testRunner: string;
    };
}
