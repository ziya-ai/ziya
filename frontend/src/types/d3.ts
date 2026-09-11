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
    /**
     * The spec passed to render() IS the document the plugin renders
     * (a Vega-Lite spec), not an envelope around one. Its `width` and
     * `height` are document properties with their own semantics
     * (`width: 'container'`, an absent height meaning "derive one"), so
     * D3Renderer must not write its container-derived width/height onto
     * them. Renderer geometry is delivered under `containerWidth` instead.
     *
     * Plugins that leave this unset get the legacy behaviour: an absent
     * spec width/height is filled from the renderer's props (600x400).
     */
    ownsSpecDimensions?: boolean;
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
