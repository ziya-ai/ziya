import { DiffNormalizerOptions, DiffType, NormalizationRule } from './types';

export class DiffNormalizer {
    private rules: NormalizationRule[] = [];
    private options: DiffNormalizerOptions;

    constructor(options: Partial<DiffNormalizerOptions> = {}) {
        this.options = {
            preserveWhitespace: false,
            debug: false,
            ...options
        };

        // Register default rules
        this.registerDefaultRules();
    }

    private registerDefaultRules() {
        // Rule for new file creation
        this.addRule({
            name: 'new-file-creation',
            test: (diff: string) => {
                return diff.startsWith('diff --git') &&
                    (diff.includes('/dev/null') || diff.includes('new file mode'));
            },
            normalize: (diff: string) => {
                const lines = diff.split('\n');
                const filePath = this.extractNewFilePath(lines[0]);
                if (!filePath) return diff;

                const contentStart = this.findContentStart(lines);
                const content = lines.slice(contentStart)
                    .filter(line => line.startsWith('+'))
                    .map(line => line.substring(1));

                return [
                    `diff --git a/dev/null b/${filePath}`,
                    'new file mode 100644',
                    '--- /dev/null',
                    `+++ b/${filePath}`,
                    `@@ -0,0 +1,${content.length} @@`,
                    ...content.map(line => `+${line}`)
                ].join('\n');
            }
        });

        // Rule for file deletion
        this.addRule({
            name: 'file-deletion',
            test: (diff: string) => {
                return diff.startsWith('diff --git') &&
                    diff.includes('deleted file mode');
            },
            normalize: (diff: string) => {
                const lines = diff.split('\n');
                const filePath = this.extractDeletedFilePath(lines[0]);
                if (!filePath) return diff;

                return [
                    `diff --git a/${filePath} b/dev/null`,
                    'deleted file mode 100644',
                    `--- a/${filePath}`,
                    '+++ /dev/null'
                ].join('\n');
            }
        });

        // Rule for fixing hunk headers
        this.addRule({
            name: 'hunk-headers',
            test: (diff: string) => {
                return diff.includes('@@ ');
            },
            normalize: (diff: string) => {
                return this.normalizeHunkHeaders(diff);
            }
        });

        // Rule for cleaning up line endings
        this.addRule({
            name: 'line-endings',
            test: () => true, // Always apply
            normalize: (diff: string) => {
                return diff.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
            }
        });
    }

    public addRule(rule: NormalizationRule): void {
        this.rules.push(rule);
    }

    public normalize(diff: string): string {
        if (!diff) return '';

        let normalizedDiff = diff;
        for (const rule of this.rules) {
            if (rule.test(normalizedDiff)) {
                const before = normalizedDiff;
                normalizedDiff = rule.normalize(normalizedDiff);

                if (this.options.debug && before !== normalizedDiff) {
                    console.log(`Rule '${rule.name}' modified the diff`);
                }
            }
        }

        // Ensure diff ends with newline
        if (!normalizedDiff.endsWith('\n')) {
            normalizedDiff += '\n';
        }

        return normalizedDiff;
    }

    private extractNewFilePath(diffLine: string): string | null {
        const match = diffLine.match(/^diff --git (?:a\/)?(\S+) (?:b\/)?(\S+)/);
        return match ? match[2] : null;
    }

    private extractDeletedFilePath(diffLine: string): string | null {
        const match = diffLine.match(/^diff --git (a\/)?(\S+) /);
        return match ? match[2] : null;
    }

    private findContentStart(lines: string[]): number {
        for (let i = 0; i < lines.length; i++) {
            if (lines[i].startsWith('@@')) {
                return i + 1;
            }
        }
        return 0;
    }

    private normalizeHunkHeaders(diff: string): string {
        const lines = diff.split('\n');
        const normalizedLines: string[] = [];
        let currentHunk: string[] = [];
        let inHunk = false;

        for (const line of lines) {
            if (line.startsWith('@@')) {
                if (inHunk) {
                    normalizedLines.push(this.normalizeHunk(currentHunk));
                    currentHunk = [];
                }
                inHunk = true;
                currentHunk = [line];
            } else if (inHunk) {
                currentHunk.push(line);
            } else {
                normalizedLines.push(line);
            }
        }

        if (inHunk && currentHunk.length > 0) {
            normalizedLines.push(this.normalizeHunk(currentHunk));
        }

        return normalizedLines.join('\n');
    }

    private normalizeHunk(hunkLines: string[]): string {
        if (hunkLines.length === 0) return '';

        const headerMatch = hunkLines[0].match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
        if (!headerMatch) return hunkLines.join('\n');

        let [, oldStart, oldCount = '1', newStart, newCount = '1'] = headerMatch;

        let actualOldCount = 0;
        let actualNewCount = 0;

        // Count actual changes
        for (let i = 1; i < hunkLines.length; i++) {
            const line = hunkLines[i];
            if (line.startsWith('-')) {
                actualOldCount++;
            } else if (line.startsWith('+')) {
                actualNewCount++;
            } else if (!line.startsWith('\\')) {
                actualOldCount++;
                actualNewCount++;
            }
        }

        // Format the new header
        const oldPart = actualOldCount === 1 ? `-${oldStart}` : `-${oldStart},${actualOldCount}`;
        const newPart = actualNewCount === 1 ? `+${newStart}` : `+${newStart},${actualNewCount}`;
        const newHeader = `@@ ${oldPart} ${newPart} @@`;

        return [newHeader, ...hunkLines.slice(1)].join('\n');
    }

    public getDiffType(diff: string): DiffType {
        if (!diff) return 'invalid';

        if (diff.includes('new file mode')) {
            return 'create';
        }
        if (diff.includes('deleted file mode')) {
            return 'delete';
        }
        if (diff.startsWith('diff --git')) {
            return 'modify';
        }

        return 'invalid';
    }

    public validateDiff(diff: string): boolean {
        if (!diff) return false;

        const lines = diff.split('\n');
        const type = this.getDiffType(diff);

        switch (type) {
            case 'create':
                return this.validateNewFileDiff(lines);
            case 'delete':
                return this.validateDeletedFileDiff(lines);
            case 'modify':
                return this.validateModifyDiff(lines);
            default:
                return false;
        }
    }

    private validateNewFileDiff(lines: string[]): boolean {
        return lines[0].startsWith('diff --git') &&
               lines.some(line => line.startsWith('new file mode')) &&
               lines.some(line => line === '--- /dev/null') &&
               lines.some(line => line.startsWith('+++ b/'));
    }

    private validateDeletedFileDiff(lines: string[]): boolean {
        return lines[0].startsWith('diff --git') &&
               lines.some(line => line.startsWith('deleted file mode')) &&
               lines.some(line => line.startsWith('--- a/')) &&
               lines.some(line => line === '+++ /dev/null');
    }

    private validateModifyDiff(lines: string[]): boolean {
        return lines[0].startsWith('diff --git') &&
               lines.some(line => line.startsWith('--- a/')) &&
               lines.some(line => line.startsWith('+++ b/')) &&
               lines.some(line => line.startsWith('@@'));
    }
}
