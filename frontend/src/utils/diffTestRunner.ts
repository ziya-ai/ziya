import { DiffTestSuite, DiffTestCase, DiffTestReport, DiffTestResult } from './diffTestTypes';

export class DiffTestRunner {
    private async validateDiffApplication(testCase: DiffTestCase): Promise<boolean> {
        if (!testCase.targetFile || !testCase.targetContent) {
            return true; // Skip validation if no target specified
        }

        try {
            const response = await fetch('/api/validate-diff', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: testCase.diff,
                    targetFile: testCase.targetFile,
                    targetContent: testCase.targetContent
                })
            });

            const result = await response.json();
            return result.wouldApplyCleanly;
        } catch (error) {
            console.error('Error validating diff:', error);
            return false;
        }
    }

    private async runTestCase(testCase: DiffTestCase): Promise<DiffTestResult> {
        const startTime = performance.now();
        const result: DiffTestResult = {
            success: false,
            renderingErrors: [],
            syntaxHighlightingErrors: [],
            validationErrors: []
        };

        try {
            // Validate diff format
            if (!testCase.diff.startsWith('diff --git')) {
                result.validationErrors?.push('Invalid diff format: Missing git diff header');
            }

            // Check if diff would apply cleanly
            if (testCase.targetFile && testCase.targetContent) {
                result.appliedCleanly = await this.validateDiffApplication(testCase);
            }

            // Compare actual result with expected
            result.success = (
                (!result.validationErrors?.length || 
                 testCase.expectedResult.validationErrors?.length === result.validationErrors?.length) &&
                (result.appliedCleanly === testCase.expectedResult.appliedCleanly)
            );

        } catch (error) {
            result.error = error instanceof Error ? error.message : 'Unknown error';
            result.success = false;
        }

        return result;
    }

    public async runSuite(suite: DiffTestSuite): Promise<DiffTestReport> {
        const startTime = performance.now();
        const results = {
            suiteName: suite.name,
            totalTests: suite.cases.length,
            passed: 0,
            failed: 0,
            skipped: 0,
            duration: 0,
            cases: [] as {
                caseId: string;
                name: string;
                result: DiffTestResult;
                duration: number;
            }[]
        };

        for (const testCase of suite.cases) {
            const caseStartTime = performance.now();
            const result = await this.runTestCase(testCase);
            const duration = performance.now() - caseStartTime;

            if (result.success) {
                results.passed++;
            } else {
                results.failed++;
            }

            results.cases.push({
                caseId: testCase.id,
                name: testCase.name,
                result,
                duration
            });
        }

        results.duration = performance.now() - startTime;

        return {
            suiteResults: [results],
            summary: {
                totalSuites: 1,
                totalTests: results.totalTests,
                totalPassed: results.passed,
                totalFailed: results.failed,
                totalSkipped: results.skipped,
                totalDuration: results.duration
            },
            timestamp: new Date().toISOString(),
            metadata: {
                environment: navigator.userAgent,
                testRunner: 'DiffTestRunner v1.0'
            }
        };
    }
}
