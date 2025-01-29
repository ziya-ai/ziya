import { Key } from 'react';

export interface Folders {
    [key: string]: {
        token_count: number;
        children?: Folders;
    };
}

export type Message = {
    content: string;
    role: 'human' | 'assistant';
    _timestamp?: number;
    _version?: number;
};

export interface Conversation {
    id: string;
    title: string;
    messages: Message[];
    lastAccessedAt: number | null;
    hasUnreadResponse?: boolean;
    _version?: number;  // Optional version field for tracking changes
    isNew?: boolean;    // Flag for newly created conversations
    isActive: boolean;
}

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
    isValid: boolean;
    errors: string[];
    warnings: string[];
    type: DiffType;
    normalizedDiff?: string;
}

export const convertKeysToStrings = (keys: Key[]): string[] => {
    return keys.map(key => String(key));
};

declare global {
    interface Window {
        enableCodeApply?: string;
        diffDisplayMode?: 'raw' | 'pretty';
        diffViewType?: 'unified' | 'split';
    }
}
