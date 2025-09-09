/**
 * Error handling utilities for streaming responses
 */

export interface ThrottlingError {
  error: string;
  detail: string;
  status_code: number;
  retry_after?: string;
  throttle_info?: {
    auto_attempts_exhausted?: boolean;
    total_auto_attempts?: number;
    can_user_retry?: boolean;
    backoff_used?: number[];
  };
  ui_action?: string;
  user_message?: string;
  preserved_content?: string;
}

export const isThrottlingError = (error: any): error is ThrottlingError => {
  return error?.error === 'throttling_error' || 
         error?.status_code === 429 ||
         (typeof error === 'string' && error.includes('ThrottlingException'));
};

export const hasPreservedContent = (error: ThrottlingError): boolean => {
  return !!(error.preserved_content);
};

export const createRetryRequest = (
  originalRequestData: any,
  preservedError?: ThrottlingError
): any => {
  const retryData = { ...originalRequestData };
  
  // If we have preserved content, add it to chat history
  if (preservedError?.preserved_content) {
    if (!retryData.chat_history) retryData.chat_history = [];
    retryData.chat_history.push(['assistant', preservedError.preserved_content]);
    retryData.question = `[Continuing from interrupted response] ${retryData.question}`;
  }
  
  retryData._retry_type = 'user_initiated';
  
  return retryData;
};
