/**
 * Context types
 */

export interface Context {
  id: string;
  name: string;
  files: string[];
  color: string;
  tokenCount: number;
  tokenCountUpdatedAt: number;
  createdAt: number;
  lastUsedAt: number;
}

export interface ContextCreate {
  name: string;
  files: string[];
}

export interface ContextUpdate {
  name?: string;
  files?: string[];
}
