/**
 * Skill types
 */

export interface ModelOverrides {
  temperature?: number;
  maxOutputTokens?: number;
  thinkingMode?: boolean;
  model?: string;
}

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
  // Enhanced skill dimensions
  toolIds?: string[];
  files?: string[];
  contextIds?: string[];
  modelOverrides?: ModelOverrides;
  // Discovery metadata
  source?: 'builtin' | 'custom' | 'project' | 'user';
  allowImplicitInvocation?: boolean;
}

/** True if this skill carries more than just a prompt. */
export function isEnhancedSkill(skill: Skill): boolean {
  return !!(
    (skill.toolIds && skill.toolIds.length > 0) ||
    (skill.files && skill.files.length > 0) ||
    (skill.contextIds && skill.contextIds.length > 0) ||
    skill.modelOverrides
  );
}

export interface SkillCreate {
  name: string;
  description: string;
  prompt: string;
  toolIds?: string[];
  files?: string[];
  contextIds?: string[];
  modelOverrides?: ModelOverrides;
  allowImplicitInvocation?: boolean;
}

export interface SkillUpdate {
  name?: string;
  description?: string;
  prompt?: string;
  toolIds?: string[];
  files?: string[];
  contextIds?: string[];
  modelOverrides?: ModelOverrides;
  allowImplicitInvocation?: boolean;
}
