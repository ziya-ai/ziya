/**
 * MCP Tool Event Handlers
 * 
 * This module provides specialized handling for specific MCP tools that need
 * custom processing of their tool_start and tool_display events.
 */

export interface ToolEventContext {
  conversationId: string;
  currentContent: { value: string };
  setStreamedContentMap: (updater: (prev: Map<string, string>) => Map<string, string>) => void;
  toolInputsMap: Map<string, any>;
}

export interface ToolHandler {
  handleToolStart?: (jsonData: any, context: ToolEventContext) => boolean; // Return true if handled
  handleToolDisplay?: (jsonData: any, context: ToolEventContext) => boolean; // Return true if handled
}

// Registry of tool handlers
const toolHandlers = new Map<string, ToolHandler>();

// Sequential Thinking Tool Handler
const sequentialThinkingHandler: ToolHandler = {
  handleToolStart: (jsonData: any, context: ToolEventContext): boolean => {
    console.log('🤔 THINKING_START: Received jsonData:', JSON.stringify(jsonData, null, 2));
    
    // Extract the actual thinking content from the args
    const toolInput = jsonData.args?.tool_input || jsonData.args || {};
    
    const thinkingContent = toolInput.thought || jsonData.input?.thought || '';
    const thoughtNumber = toolInput.thoughtNumber || jsonData.input?.thoughtNumber || 1;
    const totalThoughts = toolInput.totalThoughts || jsonData.input?.totalThoughts || 1;
    
    console.log('🤔 THINKING_START: Extracted values:', { thinkingContent: thinkingContent.substring(0, 50), thoughtNumber, totalThoughts });
    
    if (thinkingContent) {
      // Escape any code fences in the thinking content to prevent breaking the outer fence
      const escapedContent = thinkingContent.replace(/```/g, '\\`\\`\\`');
      
      // Create a thinking block display instead of generic tool start
      const thinkingDisplay = `\n\`\`\`thinking:step-${thoughtNumber}\n🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n${escapedContent}\n\`\`\`\n\n`;
      
      context.currentContent.value += thinkingDisplay;
      
      // Update the streamed content map
      context.setStreamedContentMap((prev: Map<string, string>) => {
        const next = new Map(prev);
        next.set(context.conversationId, context.currentContent.value);
        return next;
      });
      
      console.log('🤔 THINKING_START: Added thinking content for step', thoughtNumber);
      return true; // Indicate we handled this event
    }
    
    return false;
  },
  
  handleToolDisplay: (jsonData: any, context: ToolEventContext): boolean => {
    try {
      // Check if this is an error result - suppress it for thinking tools
      if (typeof jsonData.result === 'string' && jsonData.result.startsWith('ERROR:')) {
        console.log('🤔 THINKING_DISPLAY: Suppressing error result for thinking tool');
        return true; // Handled - don't show error
      }
      
      // Handle both JSON and non-JSON results
      let result;
      try {
        result = typeof jsonData.result === 'string' ? JSON.parse(jsonData.result) : jsonData.result;
      } catch (parseError) {
        // If result isn't JSON (e.g., an error), let default handler show it
        console.log('🤔 THINKING_DISPLAY: Result is not JSON, letting default handler display:', jsonData.result);
        return false; // Return false to allow default handler to run
      }
      
      const thoughtNumber = result.thoughtNumber || 1;
      const totalThoughts = result.totalThoughts || 1;
      const nextThoughtNeeded = result.nextThoughtNeeded;
      
      // Update the thinking block to show completion status
      const stepPattern = new RegExp(`\`\`\`thinking:step-${thoughtNumber}\\n🤔 \\*\\*Thought ${thoughtNumber}/${totalThoughts}\\*\\*\\n\\n([\\s\\S]*?)\\n\`\`\``, 'g');
      const match = context.currentContent.value.match(stepPattern);
      
      if (match) {
        const statusSuffix = nextThoughtNeeded ? '\n\n_Continuing..._' : '\n\n_✅ Complete._';
        context.currentContent.value = context.currentContent.value.replace(stepPattern, 
          `\`\`\`thinking:step-${thoughtNumber}\n🤔 **Thought ${thoughtNumber}/${totalThoughts}**\n\n$1${statusSuffix}\n\`\`\``
        );
        
        context.setStreamedContentMap((prev: Map<string, string>) => {
          const next = new Map(prev);
          next.set(context.conversationId, context.currentContent.value);
          return next;
        });
        
        console.log('🤔 THINKING_DISPLAY: Updated thinking completion status for step', thoughtNumber);
        return true;
      }
      
    } catch (e) {
      console.error('Error handling sequential thinking display:', e);
    }
    
    return false;
  }
};

// Register tool handlers - support both mcp_ prefixed and unprefixed variants
toolHandlers.set('mcp_sequentialthinking', sequentialThinkingHandler);
toolHandlers.set('sequentialthinking', sequentialThinkingHandler);

// Export the main handler functions
export function handleToolStart(toolName: string, jsonData: any, context: ToolEventContext): boolean {
  const handler = toolHandlers.get(toolName);
  return handler?.handleToolStart?.(jsonData, context) || false;
}

export function handleToolDisplay(toolName: string, jsonData: any, context: ToolEventContext): boolean {
  const handler = toolHandlers.get(toolName);
  return handler?.handleToolDisplay?.(jsonData, context) || false;
}
