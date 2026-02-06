/**
 * Token calculation API client
 */
import { api } from './index';
import { TokenCalculationRequest, TokenCalculationResponse } from '../types/token';

export async function calculateTokens(
  projectId: string,
  files?: string[],
  contextIds?: string[],
  skillIds?: string[],
  additionalPrompt?: string
): Promise<TokenCalculationResponse> {
  const request: TokenCalculationRequest = {
    files,
    contextIds,
    skillIds,
    additionalPrompt,
  };
  
  return api.post<TokenCalculationResponse>(
    `/projects/${projectId}/tokens/calculate`,
    request
  );
}
