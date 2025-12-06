/**
 * Tool Formatter Registry
 * 
 * Allows plugins to register custom formatters for tool outputs.
 */

export interface ToolFormatter {
    formatterId: string;
    priority: number;
    
    canFormat(toolName: string): boolean;
    format(toolName: string, result: any, options: any): FormattedOutput | null;
    enhanceHeader?(toolName: string, baseHeader: string, args: any): string | null;
}

export interface FormattedOutput {
    title?: string;
    content: string;
    language?: string;
    sections?: Array<{
        title: string;
        content: string;
        language?: string;
    }>;
}

class FormatterRegistryClass {
    private formatters: ToolFormatter[] = [];
    
    register(formatter: ToolFormatter): void {
        this.formatters.push(formatter);
        // Sort by priority (higher first)
        this.formatters.sort((a, b) => b.priority - a.priority);
        console.log(`Registered formatter: ${formatter.formatterId} (priority: ${formatter.priority})`);
    }
    
    format(toolName: string, result: any, options: any = {}): FormattedOutput {
        // Try each registered formatter in priority order
        for (const formatter of this.formatters) {
            if (formatter.canFormat(toolName)) {
                const output = formatter.format(toolName, result, options);
                if (output) {
                    return output;
                }
            }
        }
        
        // Fall back to generic formatting
        return this.genericFormat(result);
    }
    
    enhanceHeader(toolName: string, baseHeader: string, args: any): string {
        // Try each formatter
        for (const formatter of this.formatters) {
            if (formatter.canFormat(toolName) && formatter.enhanceHeader) {
                const enhanced = formatter.enhanceHeader(toolName, baseHeader, args);
                if (enhanced) {
                    return enhanced;
                }
            }
        }
        return baseHeader;
    }
    
    private genericFormat(result: any): FormattedOutput {
        return {
            content: typeof result === 'string' ? result : JSON.stringify(result, null, 2),
            language: 'text'
        };
    }
}

// Global singleton
export const FormatterRegistry = new FormatterRegistryClass();

// Make available globally for plugin scripts
(window as any).FormatterRegistry = FormatterRegistry;
