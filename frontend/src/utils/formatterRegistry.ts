/**
 * Formatter Registry for dynamic tool output formatting
 * 
 * This extends the existing toolFormatters system to support plugins.
 * 
 * This extends the existing toolFormatters system to support plugins.
 * 
 * Allows plugins to register custom formatters that enhance
 * the display of tool outputs in the UI.
 */

// Legacy formatter type from toolFormatters.ts
export interface FormattedOutput {
    content: string;
    type?: string;
    collapsed?: boolean;
    summary?: string;
}

export interface ToolFormatter {
    formatterId: string;
    priority: number;
    canFormat(toolName: string): boolean;
    format(toolName: string, result: any, options: any): any;
    enhanceHeader?(toolName: string, baseHeader: string, args: any): string | null;
}

// Legacy formatter type from toolFormatters.ts
export interface FormattedOutput {
    content: string;
    type?: string;
    collapsed?: boolean;
    summary?: string;
}

class FormatterRegistry {
    private formatters: ToolFormatter[] = [];

    register(formatter: ToolFormatter): void {
        // Validate formatter before registering
        if (!formatter || !formatter.formatterId) {
            console.warn('Attempted to register invalid formatter:', formatter);
            return;
        }
        this.formatters.push(formatter);
        // Sort by priority (highest first)
        this.formatters.sort((a, b) => b.priority - a.priority);
        console.log(`✅ Registered formatter: ${formatter.formatterId}`);
    }

    getFormatter(toolName: string): ToolFormatter | null {
        return this.formatters.find(f => f.canFormat(toolName)) || null;
    }

    getAllFormatters(): ToolFormatter[] {
        return [...this.formatters];
    }

    clear(): void {
        this.formatters = [];
    }
}

// Global singleton registry
export const formatterRegistry = new FormatterRegistry();

// Expose on window for dynamic formatter scripts to register themselves
declare global {
    interface Window {
        FormatterRegistry: FormatterRegistry;
    }
}

// Make the registry available globally
if (typeof window !== 'undefined') {
    if (window.FormatterRegistry && window.FormatterRegistry.getAllFormatters) {
        // Registry already exists - preserve it and merge our instance
        const existingFormatters = window.FormatterRegistry.getAllFormatters();
        if (existingFormatters.length > 0) {
            console.log(`Preserving ${existingFormatters.length} pre-registered formatters`);
            existingFormatters.forEach(f => formatterRegistry.register(f));
        }
        // Now replace with our instance that has the existing formatters
        window.FormatterRegistry = formatterRegistry;
    } else {
        // First time - just set it
        window.FormatterRegistry = formatterRegistry;
    }
    window.FormatterRegistry = formatterRegistry;
}

export default formatterRegistry;
