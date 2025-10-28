import { Key } from 'react';

export interface Folders {
    [key: string]: {
        token_count: number;
        children?: Folders;
    };
}

export type MessageRole = 'human' | 'assistant' | 'system';

export interface ModelChange {
    from: string;
    to: string;
    changeKey?: string;
}

// Updated Message type to include 'system' role and modelChange property
export type Message = {
    id?: string;
    content: string;
    muted?: boolean;
    _edited?: boolean;
    _truncatedAfter?: boolean;
    role: MessageRole;
    modelChange?: {
        _edited?: boolean;      // Flag to indicate this message was edited
        _truncatedAfter?: boolean; // Flag to indicate conversation was truncated after this message
        from: string;
        to: string;
        changeKey?: string;
    };
    _timestamp?: number;
    _version?: number;
    isComplete?: boolean;
};

export interface ConversationFolder {
    id: string;
    name: string;
    parentId?: string | null; // For nested folders
    useGlobalContext: boolean; // Whether to use global file context
    useGlobalModel: boolean; // Whether to use global model config
    systemInstructions?: string; // Additional system instructions
    createdAt: number;
    updatedAt: number;
}


export interface Conversation {
    id: string;
    title: string;
    messages: Message[];
    lastAccessedAt: number | null;
    hasUnreadResponse?: boolean;
    _version?: number;  // Optional version field for tracking changes
    isNew?: boolean;    // Flag for newly created conversations
    isActive: boolean;
    folderId?: string | null;
    _editInProgress?: boolean; // Flag to indicate an edit operation is in progress
    displayMode?: 'raw' | 'pretty';  // Store display mode per conversation
}

// Add _edited and _truncatedAfter to Message type

export interface DiffNormalizerOptions {
    preserveWhitespace: boolean;
    debug: boolean;
}

export interface NormalizationRule {
    name: string;
    test: (diff: string) => boolean;
    normalize: (diff: string) => string;
    priority?: number;
}

export type DiffType = 'create' | 'delete' | 'modify' | 'invalid';

export interface DiffValidationResult {
    normalizedDiff?: string;
}

export const convertKeysToStrings = (keys: Key[]): string[] => {
    return keys.map(key => String(key));
}

// D3 Visualization Types
interface BaseChartOptions {
    width?: number;
    height?: number;
    margin?: {
        top?: number;
        right?: number;
        bottom?: number;
        left?: number;
    };
    theme?: 'light' | 'dark';
}

export interface NetworkChartOptions extends BaseChartOptions {
    nodeSize?: number;
    linkStrength?: number;
    chargeStrength?: number;
    nodeColor?: string;
    linkColor?: string;
    directed?: boolean;
}

export interface TreeChartOptions extends BaseChartOptions {
    nodeSize?: number;
    nodeColor?: string;
    linkColor?: string;
    orientation?: 'vertical' | 'horizontal';
}

export interface ForceChartOptions extends BaseChartOptions {
    nodeSize?: number;
    linkDistance?: number;
    charge?: number;
    nodeColor?: string;
    linkColor?: string;
}

export type ChartOptions = NetworkChartOptions | TreeChartOptions | ForceChartOptions;

export interface D3CustomSpec {
    type: 'custom';
    renderer: 'd3';
    render: (
        container: SVGSVGElement,
        width: number,
        height: number,
        isDarkMode: boolean,
        d3: typeof import('d3')
    ) => void;
    options?: BaseChartOptions;
}

export interface D3ChartSpec {
    type: 'chart';
    renderer: 'd3';
    chartType: 'network' | 'tree' | 'force' | 'custom';
    data: any;
    options?: ChartOptions;
}

export type D3Spec = D3CustomSpec | D3ChartSpec;

// Vega-Lite Types
export interface VegaLiteSpec {
    $schema?: string;
    data?: {
        values?: any[];
        url?: string;
        name?: string;
    };
    mark?: string | {
        type: string;
        [key: string]: any;
    };
    encoding?: {
        [key: string]: {
            field?: string;
            type?: string;
            scale?: any;
            axis?: any;
            title?: string;
            [key: string]: any;
        };
    };
    width?: number | 'container';
    height?: number;
    title?: string;
    transform?: Array<{
        filter?: any;
        calculate?: string;
        aggregate?: any;
        [key: string]: any;
    }>;
    config?: any;
    layer?: VegaLiteSpec[];
    [key: string]: any;
}

declare global {
    interface Window {
        enableCodeApply?: string;
        diffDisplayMode?: 'raw' | 'pretty';
        diffViewType?: 'unified' | 'split';
    }
}
