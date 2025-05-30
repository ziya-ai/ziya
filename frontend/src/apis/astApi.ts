/**
 * API for AST-related functionality
 */

export interface AstStatus {
  is_indexing: boolean;
  completion_percentage: number;
  is_complete: boolean;
  indexed_files: number;
  total_files: number;
  elapsed_seconds: number | null;
  error: string | null;
}

/**
 * Fetch the current status of AST indexing
 * @returns Promise with AST indexing status
 */
export const fetchAstStatus = async (): Promise<AstStatus> => {
  try {
    const response = await fetch('/api/ast/status');
    
    if (!response.ok) {
      throw new Error(`HTTP error! Status: ${response.status}`);
    }
    
    return await response.json() as AstStatus;
  } catch (error) {
    console.error('Error fetching AST status:', error);
    // Return a default status object on error
    return {
      is_indexing: false,
      completion_percentage: 0,
      is_complete: false,
      indexed_files: 0,
      total_files: 0,
      elapsed_seconds: null,
      error: error instanceof Error ? error.message : 'Unknown error'
    };
  }
};
