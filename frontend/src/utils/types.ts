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
}

declare global {
    interface Window { 
        enableCodeApply?: string;
        diffDisplayMode?: 'raw' | 'pretty';
        diffViewType?: 'unified' | 'split';
    }
}
