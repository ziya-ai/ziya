/**
 * Token calculation types
 */

export interface TokenCalculationRequest {
  files?: string[];
  contextIds?: string[];
  skillIds?: string[];
  additionalPrompt?: string;
}

export interface TokenCalculationResponse {
  totalTokens: number;
  fileTokens: Record<string, number>;
  skillTokens: Record<string, number>;
  additionalPromptTokens: number;
  overlappingFiles: string[];
  deduplicatedTokens: number;
}
