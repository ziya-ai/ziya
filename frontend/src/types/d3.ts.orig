import type { Selection, BaseType } from 'd3';
export interface D3RenderPlugin {
    name: string;
    priority: number;  // Higher number = higher priority
    canHandle: (spec: any) => boolean;
    render: (container: HTMLElement, d3: any, spec: any) => void;
}
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
