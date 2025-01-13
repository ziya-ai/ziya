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
};

export interface Conversation {
    id: string;
    title: string;
    messages: Message[];
    lastAccessedAt: number | null;
    isActive: boolean;
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
