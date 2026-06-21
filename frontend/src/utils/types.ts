import { Key } from 'react';
import type { DelegateMeta, TaskPlan } from '../types/delegate';

export interface Folders {
    [key: string]: {
        token_count: number;
        children?: Folders;
    };
}

export type MessageRole = 'human' | 'assistant' | 'system';

export interface ImageAttachment {
    data: string;  // base64 encoded image data (without data URI prefix)
    mediaType: 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp';
    filename?: string;
    size?: number;  // bytes
    width?: number;
    height?: number;
}

export interface DocumentAttachment {
    id?: string;
    filename: string;
    text: string;          // extracted text content
    type: string;          // file extension: 'pdf', 'docx', etc.
    chars: number;
    // For scanned PDFs rendered as page images
    pageImages?: ImageAttachment[];
}

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
    _isToolResult?: boolean;
    _isFeedback?: boolean;          // Flag to indicate this is tool feedback
    _feedbackStatus?: 'pending' | 'acknowledged';  // Status of feedback message
    _feedbackId?: string;           // Unique ID for tracking feedback acknowledgment
    images?: ImageAttachment[];  // Optional array of attached images
    documents?: DocumentAttachment[];  // Optional array of attached documents
};

export interface Conversation {
    id: string;
    projectId?: string;  // Scope conversations to projects
    title: string;
    isGlobal?: boolean;  // When true, visible in all projects
    // When true, this conversation is held in React state only and is
    // never persisted to IndexedDB or synced to the server. Lost on
    // project switch or page reload. Use promoteEphemeralToRetained()
    // to convert to a normal persisted conversation.
    isEphemeral?: boolean;
    messages: Message[];
    lastAccessedAt: number | null;
    hasUnreadResponse?: boolean;
    _version?: number;
    isNew?: boolean;
    isActive: boolean;
    folderId?: string | null;
    _editInProgress?: boolean;
    displayMode?: 'raw' | 'pretty';
    delegateMeta?: DelegateMeta | null;
    // Cheap derived counts for the sidebar open-work indicators.  Populated
    // by the server summary path (ChatSummary) and carried through the sync
    // merge; never authored on the frontend.  openWorkItemCount is currently
    // always 0 — the work-item queue is unbuilt (design/work-primitives-taxonomy.md).
    openBeadCount?: number;
    openWorkItemCount?: number;
    // ── Branch lineage (bead-branching, see design/bead-branching.md) ──
    // Present only on conversations created by splitting from a bead.
    // A parked bead is an un-taken branch point; forkFromBead truncates at
    // the bead's message_index seam and stamps these so the conversation
    // can render its lineage (breadcrumb bar) and the eventual graph panel
    // can reconstruct the branch tree.  All three are authored together at
    // fork time; absent (undefined) on trunk/unbranched conversations.
    branchedFrom?: string;            // parent conversation id
    branchedAtMessageIndex?: number;  // the seam — parent's message index at split
    branchedFromLabel?: string;       // bead content, for display ("microburst drops")
    // Fork-lineage root for shared bead trees (design/bead-branching.md "b2").
    // A plain fork inherits its source's lineageRootId (or, if the source is
    // itself a root, the source's id), so the whole lineage shares one
    // bead tree resolved on the backend.  Absent on root/trunk conversations.
    lineageRootId?: string;
}

export interface ConversationFolder {
    id: string;
    name: string;
    projectId?: string;
    isGlobal?: boolean;  // When true, folder visible in all projects
    parentId?: string | null;
    useGlobalContext?: boolean;
    useGlobalModel?: boolean;
    createdAt: number;
    updatedAt: number;
    taskPlan?: TaskPlan | null;
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

// Search Types
export interface MessageMatch {
    messageIndex: number;
    messageRole: MessageRole;
    snippet: string; // Context around match with search term
    fullContent: string;
    timestamp: number;
    highlightPositions: Array<{ start: number; length: number }>; // For highlighting
}

export interface SearchResult {
    conversationId: string;
    conversationTitle: string;
    folderId?: string | null;
    projectId?: string;
    projectName?: string;
    matches: MessageMatch[];
    totalMatches: number;
    lastAccessedAt: number;
}

export interface SearchOptions {
    caseSensitive?: boolean;
    maxSnippetLength?: number;
    projectId?: string;  // When set, only return results from this project
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
