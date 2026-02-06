/**
 * Skill types
 */

export interface Skill {
  id: string;
  name: string;
  description: string;
  prompt: string;
  color: string;
  tokenCount: number;
  isBuiltIn: boolean;
  createdAt: number;
  lastUsedAt: number;
}

export interface SkillCreate {
  name: string;
  description: string;
  prompt: string;
}

export interface SkillUpdate {
  name?: string;
  description?: string;
  prompt?: string;
}
