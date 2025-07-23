import type { Selection, BaseType } from 'd3';

export interface PluginSizingConfig {
    // How the plugin handles sizing
    sizingStrategy: 'fixed' | 'responsive' | 'content-driven' | 'auto-expand';
    // Whether the plugin needs dynamic height adjustment
    needsDynamicHeight: boolean;
    // Whether the plugin needs overflow: visible
    needsOverflowVisible: boolean;
    // Minimum dimensions
    minWidth?: number;
    minHeight?: number;
    // Whether to observe size changes
    observeResize: boolean;
    // Custom container styles
    containerStyles?: React.CSSProperties;
}

export interface D3RenderPlugin {
    name: string;
    priority: number;  // Higher number = higher priority
    sizingConfig?: PluginSizingConfig;  // Optional sizing configuration
    canHandle: (spec: any) => boolean;
    isDefinitionComplete?: (definition: string) => boolean;  // Optional method to check if a diagram definition is complete
    render: (container: HTMLElement, d3: any, spec: any, isDarkMode: boolean) => void | (() => void) | Promise<void | (() => void)>;
}
// Common types used across D3 visualizations
// Common types used across D3 visualizations
export interface D3Node {
    id: string;
    x: number;
    y: number;
    label?: string;
    group?: string;
    [key: string]: any;
}
export interface D3Link {
    source: string;
    target: string;
    type?: string;
    color?: string;
    dashed?: boolean;
    [key: string]: any;
}
export interface D3Style {
    fill?: string;
    stroke?: string;
    [key: string]: any;
}
