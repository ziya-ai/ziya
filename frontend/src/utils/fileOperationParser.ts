export interface FileOperation {
    type: 'file_operation';
    file?: string;
    find?: string;
    change?: string;
    replace?: string;
    apply?: boolean;
    raw?: string;
    isValid: boolean;
    errors: string[];
    warnings: string[];
}

export interface ParseResult {
    operations: FileOperation[];
    hasValidOperations: boolean;
    totalOperations: number;
    errors: string[];
}

// Common variations and typos we should handle
const TAG_VARIATIONS = {
    file: ['file', 'File', 'FILE', 'filename', 'path'],
    find: ['find', 'Find', 'FIND', 'search', 'locate'],
    change: ['change', 'Change', 'CHANGE', 'modify', 'edit'],
    replace: ['replace', 'Replace', 'REPLACE', 'substitute'],
    apply: ['apply', 'Apply', 'APPLY', 'execute', 'run']
};

// Regex patterns for different syntax variations
const PATTERNS = {
    // Standard: <file> content </file>
    standard: /<(file|find|change|replace|apply)>\s*([\s\S]*?)\s*<\/\1>/gi,

    // Self-closing: <file path="..." />
    selfClosing: /<(file|find|change|replace|apply)(?:\s+[^>]*)?\s*\/>/gi,

    // Attribute style: <file path="...">
    withAttributes: /<(file|find|change|replace|apply)(?:\s+([^>]*))?>([^<]*)<\/\1>/gi,

    // Malformed: missing closing tags, etc.
    malformed: /<(file|find|change|replace|apply)>\s*([\s\S]*?)(?=<(?:file|find|change|replace|apply)|$)/gi
};

export class FileOperationParser {
    private content: string;
    private operations: FileOperation[] = [];
    private errors: string[] = [];

    constructor(content: string) {
        this.content = content;
    }

    parse(): ParseResult {
        this.operations = [];
        this.errors = [];

        // First, try to detect complete operation blocks
        this.parseCompleteOperations();

        // Then, try to parse individual tags that might be malformed
        this.parseMalformedOperations();

        // Validate and clean up operations
        this.validateOperations();

        return {
            operations: this.operations,
            hasValidOperations: this.operations.some(op => op.isValid),
            totalOperations: this.operations.length,
            errors: this.errors
        };
    }

    private parseCompleteOperations(): void {
        // Look for complete operation sequences
        const operationBlocks = this.findOperationBlocks();

        for (const block of operationBlocks) {
            const operation = this.parseOperationBlock(block);
            if (operation) {
                this.operations.push(operation);
            }
        }
    }

    private findOperationBlocks(): string[] {
        const blocks: string[] = [];

    // Pattern to match <apply> blocks specifically
    const blockPattern = /<apply>\s*([\s\S]*?)\s*<\/apply>/gi;

        let match;
        while ((match = blockPattern.exec(this.content)) !== null) {
            blocks.push(match[0]);
        }

    // If no apply blocks found, fall back to individual file operations
        if (blocks.length === 0) {
      const individualPattern = /<file[^>]*>[\s\S]*?<\/file>/gi;
            while ((match = individualPattern.exec(this.content)) !== null) {
                blocks.push(match[0]);
            }
        }

        return blocks;
    }

    private parseOperationBlock(block: string): FileOperation | null {
    // Handle <apply> wrapper blocks
    const applyMatch = block.match(/<apply>\s*([\s\S]*?)\s*<\/apply>/i);
    if (applyMatch) {
      const innerContent = applyMatch[1];
      const operation = this.parseInnerOperation(innerContent);
      if (operation) {
        operation.apply = true;
        operation.raw = block;
        return operation;
      }
    }
    
    // Handle direct operation blocks
    return this.parseInnerOperation(block);
  }

  private parseInnerOperation(content: string): FileOperation | null {
        const operation: FileOperation = {
            type: 'file_operation',
      raw: content,
            isValid: false,
            errors: [],
            warnings: []
        };

        // Extract each tag type from the block
    operation.file = this.extractTagContent(content, 'file');

    // Handle <change> wrapper with <find> and <replace> inside
    const changeContent = this.extractTagContent(content, 'change');
    if (changeContent) {
      operation.find = this.extractTagContent(changeContent, 'find');
      operation.replace = this.extractTagContent(changeContent, 'replace');
    } else {
      // Direct find/replace without change wrapper
      operation.find = this.extractTagContent(content, 'find');
      operation.replace = this.extractTagContent(content, 'replace');
    }
    
    const applyContent = this.extractTagContent(content, 'apply');
        operation.apply = this.parseApplyValue(applyContent);

        // Validate the operation
        this.validateOperation(operation);

        return operation;
    }

    private extractTagContent(content: string, tagName: string): string | undefined {
        // Try different variations of the tag name
        const variations = TAG_VARIATIONS[tagName] || [tagName];

        for (const variation of variations) {
            // Try standard pattern first
            const standardPattern = new RegExp(`<${variation}\\s*>\\s*([\\s\\S]*?)\\s*<\\/${variation}>`, 'i');
            const standardMatch = content.match(standardPattern);
            if (standardMatch) {
                return standardMatch[1].trim();
            }

            // Try self-closing with attributes
            const selfClosingPattern = new RegExp(`<${variation}\\s+([^>]*?)\\s*\\/>`, 'i');
            const selfClosingMatch = content.match(selfClosingPattern);
            if (selfClosingMatch) {
                return this.extractAttributeValue(selfClosingMatch[1]);
            }

            // Try with attributes
            const attrPattern = new RegExp(`<${variation}\\s+([^>]*?)>([^<]*)<\\/${variation}>`, 'i');
            const attrMatch = content.match(attrPattern);
            if (attrMatch) {
                const attrValue = this.extractAttributeValue(attrMatch[1]);
                const textContent = attrMatch[2].trim();
                return attrValue || textContent;
            }
        }

        return undefined;
    }

    private extractAttributeValue(attributes: string): string | undefined {
        // Look for common attribute patterns: path="...", value="...", content="..."
        const patterns = [
            /(?:path|value|content|src|href)\s*=\s*["']([^"']*?)["']/i,
            /["']([^"']*?)["']/  // Any quoted value
        ];

        for (const pattern of patterns) {
            const match = attributes.match(pattern);
            if (match) {
                return match[1];
            }
        }

        return attributes.trim();
    }

    private parseApplyValue(content: string | undefined): boolean {
        if (!content) return false;

        const normalized = content.toLowerCase().trim();
        return ['true', 'yes', '1', 'apply', 'execute', 'run'].includes(normalized);
    }

    private parseMalformedOperations(): void {
        // Look for individual tags that might be part of incomplete operations
        const tags = ['file', 'find', 'change', 'replace', 'apply'];

        for (const tag of tags) {
            const pattern = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)(?=<(?:${tags.join('|')})|$)`, 'gi');
            let match;

            while ((match = pattern.exec(this.content)) !== null) {
                // Check if this tag is already part of a complete operation
                const isPartOfComplete = this.operations.some(op =>
                    op.raw && op.raw.includes(match[0])
                );

                if (!isPartOfComplete) {
                    const operation: FileOperation = {
                        type: 'file_operation',
                        raw: match[0],
                        isValid: false,
                        errors: [`Incomplete operation: found ${tag} tag without complete operation sequence`],
                        warnings: []
                    };

                    operation[tag] = match[1].trim();
                    this.operations.push(operation);
                }
            }
        }
    }

    private validateOperation(operation: FileOperation): void {
        const errors: string[] = [];
        const warnings: string[] = [];

        // Check for required fields
        if (!operation.file) {
            errors.push('Missing file specification');
        }

        // Validate operation type
        const hasFind = !!operation.find;
        const hasChange = !!operation.change;
        const hasReplace = !!operation.replace;

        if (hasFind && hasReplace && !hasChange) {
            // Find and replace operation - valid
        } else if (hasChange && !hasFind && !hasReplace) {
            // Change operation - valid but might need more context
            warnings.push('Change operation without find/replace - ensure the change is clearly specified');
        } else if (hasFind && hasChange && hasReplace) {
            // All three - might be redundant
            warnings.push('Operation has find, change, and replace - this might be redundant');
        } else if (!hasFind && !hasChange && !hasReplace) {
            errors.push('No operation specified - need at least one of: find, change, or replace');
        }

        // Validate file path
        if (operation.file) {
            if (operation.file.includes('..')) {
                errors.push('File path contains ".." - potential security risk');
            }
            if (operation.file.startsWith('/')) {
                warnings.push('Absolute file path detected - ensure this is intended');
            }
        }

        // Check for potentially dangerous operations
        if (operation.find && operation.find.length > 10000) {
            warnings.push('Very large find content - this might cause performance issues');
        }
        if (operation.replace && operation.replace.length > 10000) {
            warnings.push('Very large replace content - this might cause performance issues');
        }

        operation.errors = errors;
        operation.warnings = warnings;
        operation.isValid = errors.length === 0;
    }

    private validateOperations(): void {
        // Cross-validate operations
        const fileOperations = new Map<string, FileOperation[]>();

        // Group operations by file
        for (const op of this.operations) {
            if (op.file) {
                if (!fileOperations.has(op.file)) {
                    fileOperations.set(op.file, []);
                }
                fileOperations.get(op.file)!.push(op);
            }
        }

        // Check for conflicting operations on the same file
        for (const [file, ops] of fileOperations) {
            if (ops.length > 1) {
                for (const op of ops) {
                    op.warnings.push(`Multiple operations detected for file: ${file}`);
                }
            }
        }
    }

    // Static method for quick detection
    static containsFileOperations(content: string): boolean {
    const quickPattern = /<apply>\s*<file[^>]*>/i;
        return quickPattern.test(content);
    }

    // Static method for safe extraction
    static extractSafely(content: string): ParseResult {
        const parser = new FileOperationParser(content);
        return parser.parse();
    }
}

// Utility functions for the markdown renderer
export function detectFileOperationSyntax(content: string): boolean {
  return content.includes('<apply>') && content.includes('<file>');
}

export function parseFileOperations(content: string): ParseResult {
    return FileOperationParser.extractSafely(content);
}

// Safety wrapper for rendering
export function renderFileOperationSafely(content: string): {
    shouldRender: boolean;
    operations: FileOperation[];
    safeContent: string;
    warnings: string[];
} {
    if (!detectFileOperationSyntax(content)) {
        return {
            shouldRender: false,
            operations: [],
            safeContent: content,
            warnings: []
        };
    }

    const parseResult = parseFileOperations(content);
    const warnings: string[] = [];

    // Check for security concerns
    const hasSecurityRisks = parseResult.operations.some(op =>
        op.errors.some(error => error.includes('security risk'))
    );

    if (hasSecurityRisks) {
        warnings.push('Security risks detected in file operations');
    }

    // Create safe content by escaping potentially dangerous operations
    let safeContent = content;
    for (const op of parseResult.operations) {
        if (!op.isValid || op.errors.length > 0) {
            // Escape invalid operations
            safeContent = safeContent.replace(op.raw || '',
                `<!-- INVALID FILE OPERATION: ${op.errors.join(', ')} -->\n\`\`\`\n${op.raw}\n\`\`\``
            );
        }
    }

    return {
        shouldRender: parseResult.hasValidOperations,
        operations: parseResult.operations,
        safeContent,
        warnings: [...parseResult.errors, ...warnings]
    };
}
