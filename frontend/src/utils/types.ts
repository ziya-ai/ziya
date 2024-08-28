import exp from "node:constants";

export interface Folders {
    [key: string]: {
        token_count: number;
        children?: Folders;
    };
}

export type Message = {
    content: string;
    role: string;
};

export type FolderKeyTitle = { key: string; title: string; };