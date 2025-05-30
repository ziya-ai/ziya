/**
 * Mermaid Enhancer
 * 
 * A generic enhancement layer for Mermaid diagrams that:
 * 1. Preprocesses diagram definitions to fix common syntax issues
 * 2. Provides error recovery mechanisms for rendering failures
 * 3. Allows for extensible preprocessing rules
 */

// Types for preprocessors and error handlers
interface Preprocessor {
  process: (definition: string, diagramType: string) => string;
  priority: number;
  diagramTypes: string[];
  name: string;
}

interface ErrorHandler {
  handle: (error: Error, context: ErrorContext) => boolean;
  priority: number;
  errorTypes: string[];
  name: string;
}

interface ErrorContext {
  container?: HTMLElement;
  definition: string;
  diagramType: string;
  error: Error;
}

interface PreprocessorOptions {
  name?: string;
  priority?: number;
  diagramTypes?: string[];
}

interface ErrorHandlerOptions {
  name?: string;
  priority?: number;
  errorTypes?: string[];
}

// Registry of preprocessors that can be extended
const preprocessors: Preprocessor[] = [];

// Registry of error handlers that can be extended
const errorHandlers: ErrorHandler[] = [];

/**
 * Register a preprocessor function
 * @param fn - Function that takes diagram text and returns processed text
 * @param options - Options including priority and diagram types to target
 */
export function registerPreprocessor(
  fn: (definition: string, diagramType: string) => string,
  options: PreprocessorOptions = {}
): () => void {
  const processor: Preprocessor = {
    process: fn,
    priority: options.priority || 10,
    diagramTypes: options.diagramTypes || ['*'], // '*' means all diagram types
    name: options.name || `preprocessor-${preprocessors.length}`
  };

  preprocessors.push(processor);
  // Sort by priority (higher numbers run first)
  preprocessors.sort((a, b) => b.priority - a.priority);

  return () => {
    // Return function to unregister this preprocessor
    const index = preprocessors.findIndex(p => p === processor);
    if (index !== -1) {
      preprocessors.splice(index, 1);
    }
  };
}

/**
 * Register an error handler function
 * @param fn - Function that takes error and context and returns boolean (true if handled)
 * @param options - Options including priority and error types to target
 */
export function registerErrorHandler(
  fn: (error: Error, context: ErrorContext) => boolean,
  options: ErrorHandlerOptions = {}
): () => void {
  const handler: ErrorHandler = {
    handle: fn,
    priority: options.priority || 10,
    errorTypes: options.errorTypes || ['*'], // '*' means all error types
    name: options.name || `error-handler-${errorHandlers.length}`
  };

  errorHandlers.push(handler);
  // Sort by priority (higher numbers run first)
  errorHandlers.sort((a, b) => b.priority - a.priority);

  return () => {
    // Return function to unregister this handler
    const index = errorHandlers.findIndex(h => h === handler);
    if (index !== -1) {
      errorHandlers.splice(index, 1);
    }
  };
}

/**
 * Process a diagram definition through all applicable preprocessors
 * @param definition - The original diagram definition
 * @param diagramType - The type of diagram (e.g., 'sequence', 'state')
 * @returns - The processed definition
 */
export function preprocessDefinition(definition: string, diagramType?: string): string {
  let processedDef = definition;

  // Extract diagram type if not provided
  if (!diagramType) {
    const firstLine = definition.trim().split('\n')[0];
    diagramType = firstLine.trim().replace(/^(\w+).*$/, '$1');
  }

  // Apply each preprocessor in order
  for (const processor of preprocessors) {
    if (processor.diagramTypes.includes('*') || processor.diagramTypes.includes(diagramType)) {
      try {
        const result = processor.process(processedDef, diagramType);
        if (result) {
          processedDef = result;
        }
      } catch (error) {
        console.warn(`Preprocessor ${processor.name} failed:`, error);
      }
    }
  }

  return processedDef;
}

/**
 * Handle rendering errors by passing through registered error handlers
 * @param error - The error that occurred
 * @param context - Context including diagram definition, type, and container
 * @returns - True if the error was handled, false otherwise
 */
export function handleRenderError(error: Error, context: ErrorContext): boolean {
  const errorType = error.name || 'Error';
  let handled = false;

  // Try each handler in order until one handles the error
  for (const handler of errorHandlers) {
    if (handler.errorTypes.includes('*') || handler.errorTypes.includes(errorType)) {
      try {
        if (handler.handle(error, context)) {
          handled = true;
          break;
        }
      } catch (handlerError) {
        console.warn(`Error handler ${handler.name} failed:`, handlerError);
      }
    }
  }

  return handled;
}

/**
 * Initialize the Mermaid enhancer with default preprocessors and error handlers
 */
export function initMermaidEnhancer(): void {
  // Register default preprocessors

  // Add a preprocessor specifically for subgraph syntax
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.startsWith('flowchart ') && !def.startsWith('graph ')) {
      return def;
    }

    let finalDef = def;

    // Fix class assignments that use subgraph-* syntax
    // Change from: class ServerStartup subgraph-blue
    // To: class ServerStartup subgraph_blue
    finalDef = finalDef.replace(/class\s+(\w+)\s+subgraph-(\w+)/g, 'class $1 subgraph_$2');

    // Also fix classDef with subgraph prefix
    finalDef = finalDef.replace(/classDef\s+subgraph-(\w+)/g, 'classDef style_$1');

    // Ensure subgraph declarations are properly formatted
    // Make sure there's a space between subgraph and the ID
    finalDef = finalDef.replace(/subgraph(\w+)/g, 'subgraph $1');

    return finalDef;
  }, {
    name: 'subgraph-syntax-fix',
    priority: 130,
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix issues with quoted text in node labels
  registerPreprocessor((def: string, type: string) => {
    let finalDef = def;

    // Fix nodes with "DONE" text by replacing quotes with escaped quotes
    finalDef = finalDef.replace(/\[([^"\]]*)"([^"\]]*)"([^"\]]*)\]/g, (match, before, quoted, after) => {
      // Replace with HTML entity quotes to avoid parsing issues
      return `[${before}"${quoted}"${after}]`;
    });

    return finalDef;
  }, {
    name: 'quoted-text-fix',
    priority: 125,
    diagramTypes: ['*']
  });

  // Add a specific preprocessor for complex flowcharts
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.startsWith('flowchart ') && !def.startsWith('graph ')) {
      return def;
    }

    let finalDef = def;

    // Convert flowchart to graph LR if needed for better compatibility
    finalDef = finalDef.replace(/^flowchart\s+/m, 'graph ');

    // IMPORTANT: Remove any "note on link:" text that causes parsing errors
    finalDef = finalDef.replace(/note\s+on\s+link.*?:/gi, '%%note removed:');

    // Fix "Send DONE Marker" nodes that cause parsing errors
    finalDef = finalDef.replace(/\[Send\s+"DONE"\s+Marker\]/g, '[Send DONE Marker]');


    // Fix node references that cause parsing errors
    finalDef = finalDef.replace(/(\w+)\s*-->\s*(\w+)\[([^\]]+)\]/g, (match, source, target, label) => {
      // If label contains special characters, quote it
      if (/[\/\[\]]/.test(label)) {
        return `${source} --> ${target}["${label}"]`;
      }
      return `${source} --> ${target}[${label}]`;
    });

    // Fix end nodes that cause parsing errors
    finalDef = finalDef.replace(/end\[([^\]]+)\]/g, 'endNode["$1"]');

    // Fix SendDone nodes that cause parsing errors
    finalDef = finalDef.replace(/SendDone\[([^\]]+)\]/g, 'sendDoneNode["$1"]');

    return finalDef;
  }, {
    name: 'flowchart-fix',
    priority: 120,
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor for requirement diagrams
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'requirement') {
      return def;
    }

    // Fix common syntax issues in requirement diagrams
    let lines = def.split('\n');
    let result: string[] = [];

    for (let i = 0; i < lines.length; i++) {
      let line = lines[i].trim();

      // Fix ID format
      if (line.match(/^\s*id:/i)) {
        line = line.replace(/id:\s*([^,]+)/, 'id: "$1"');
      }

      // Fix text format
      if (line.match(/^\s*text:/i)) {
        line = line.replace(/text:\s*([^,]+)/, 'text: "$1"');
      }

      result.push(line);
    }

    return result.join('\n');
  }, {
    name: 'requirement-diagram-fix',
    priority: 110,
    diagramTypes: ['requirement']
  });

  // Add a preprocessor for xychart diagrams
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'xychart') {
      return def;
    }

    // Fix common syntax issues in xychart diagrams
    let lines = def.split('\n');
    let result: string[] = [];

    for (let i = 0; i < lines.length; i++) {
      let line = lines[i].trim();

      // Fix array format
      if (line.includes('[') && line.includes(']')) {
        line = line.replace(/\[(.*?)\]/, '"[$1]"');
      }

      result.push(line);
    }

    return result.join('\n');
  }, {
    name: 'xychart-diagram-fix',
    priority: 110,
    diagramTypes: ['xychart']
  });

  // Fix for sequence diagram activation/deactivation issues
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'sequenceDiagram') return def;

    // Track activation state to prevent deactivating inactive participants
    const lines = def.split('\n');
    const activationState: Record<string, boolean> = {};
    const result: string[] = [];

    for (let line of lines) {
      line = line.trim();

      // Track activations
      if (line.includes('activate ')) {
        const participant = line.replace(/activate\s+(\w+).*/, '$1').trim();
        activationState[participant] = true;
        result.push(line);
      }
      // Check deactivations
      else if (line.includes('deactivate ')) {
        const participant = line.replace(/deactivate\s+(\w+).*/, '$1').trim();
        if (activationState[participant]) {
          activationState[participant] = false;
          result.push(line);
        } else {
          // Skip this deactivation as the participant is not active
          console.warn(`Skipping deactivation for inactive participant: ${participant}`);
          // Add as comment for debugging
          result.push(`%%${line} (skipped - participant not active)`);
        }
      }
      // Handle implicit deactivation via "deactivate" keyword
      else if (line.endsWith('deactivate')) {
        // This is a shorthand deactivation, check if we can determine the participant
        const match = line.match(/(\w+)-->>.*deactivate$/);
        if (match && match[1]) {
          const participant = match[1];
          if (!activationState[participant]) {
            // Convert to a regular line without deactivation
            line = line.replace(/deactivate$/, '');
            console.warn(`Removed deactivation for inactive participant: ${participant}`);
          } else {
            activationState[participant] = false;
          }
        }
        result.push(line);
      }
      else {
        result.push(line);
      }
    }

    return result.join('\n');
  }, {
    name: 'sequence-activation-fix',
    priority: 100,
    diagramTypes: ['sequenceDiagram']
  });

  // Register a preprocessor to handle color values in classDef statements
  registerPreprocessor((def: string, type: string) => {
    // Ensure color values in classDef statements are properly formatted
    return def.replace(/classDef\s+(\w+)\s+([^:]+):([^,]+),([^:]+):([^,\n]+)/g,
      (match, className, attr1, val1, attr2, val2) => `classDef ${className} ${attr1}:${val1},${attr2}:${val2}`);
  }, {
    name: 'classdef-color-fix',
    priority: 100,
    diagramTypes: ['*']
  });

  // Register a preprocessor to fix classDef statements with quotes
  registerPreprocessor((def: string, type: string) => {
    // Fix classDef statements with quoted attributes
    // Change from: classDef primary fill:"#f94144",color:"white"
    // To: classDef primary fill:#f94144,color:white
    return def.replace(/classDef\s+(\w+)\s+((?:[a-zA-Z]+:"[^"]+",?)+)/g, (match, className, attributes) => {
      // Remove quotes from attribute values
      const fixedAttributes = attributes.replace(/:"/g, ':').replace(/"/g, '');
      return `classDef ${className} ${fixedAttributes}`;
    });
  }, {
    name: 'classdef-quotes-fix',
    priority: 110,
    diagramTypes: ['*']
  });

  // Register a preprocessor to handle slashes in node labels
  registerPreprocessor((def: string, type: string) => {
    // Handle slashes in node labels by adding quotes
    let finalDef = def;

    // Fix nodes with slashes by adding quotes
    finalDef = finalDef.replace(/\[([^\]]*\/[^\]]*)\]/g, '["$1"]');

    return finalDef;
  }, {
    name: 'node-slash-fix',
    priority: 95,
    diagramTypes: ['*']
  });


  // Fix for state diagram shape function errors
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'stateDiagram' && type !== 'stateDiagram-v2') return def;

    // Add explicit shape definitions for states that might be causing issues
    const lines = def.split('\n');
    const states = new Set<string>();

    // First pass: collect all state names
    for (const line of lines) {
      // Match state definitions and transitions
      const stateMatches = line.match(/\b(\w+)\b\s*:/g) || [];
      const transitionMatches = line.match(/\b(\w+)\b\s*-->/g) || [];

      stateMatches.forEach(match => {
        const state = match.replace(/\s*:$/, '').trim();
        states.add(state);
      });

      transitionMatches.forEach(match => {
        const state = match.replace(/\s*-->$/, '').trim();
        states.add(state);
      });
    }

    // Second pass: ensure all states have a shape definition
    const result = [...lines];
    const stateDefSection = lines.findIndex(line =>
      line.trim().startsWith('stateDiagram') ||
      line.trim().startsWith('stateDiagram-v2')
    );

    if (stateDefSection !== -1) {
      // Add state style definitions after the diagram type declaration
      const stateStyles = Array.from(states)
        .map(state => `    ${state}: ${state}`)
        .join('\n');

      if (stateStyles) {
        result.splice(stateDefSection + 1, 0, stateStyles);
      }
    }

    return result.join('\n');
  }, {
    name: 'state-shape-fix',
    priority: 90,
    diagramTypes: ['stateDiagram', 'stateDiagram-v2']
  });

  // Generic syntax validator and fixer
  registerPreprocessor((def: string) => {
    // Fix common syntax issues
    let processed = def;

    // Ensure proper line breaks between sections
    processed = processed.replace(/(\w+)(\s*\n\s*[A-Z])/g, '$1\n$2');

    // Fix missing spaces in arrows
    processed = processed.replace(/(\w+)-->/g, '$1 -->');
    processed = processed.replace(/-->(\w+)/g, '--> $1');

    // Fix missing spaces in notes
    processed = processed.replace(/Note(\w+):/g, 'Note $1:');

    // Specifically handle [DONE] text which causes parsing issues
    processed = processed.replace(/\[DONE\]/g, '"DONE"');

    // Fix issues with square brackets in node text
    processed = processed.replace(/(\w+)\[([^\]]+)\]/g, (match, nodeName, nodeText) => {
      // Only add quotes if not already quoted
      return nodeText.includes('"') ? `${nodeName}[${nodeText}]` : `${nodeName}["${nodeText}"]`;
    });

    return processed;
  }, {
    name: 'syntax-fixer',
    priority: 50,
    diagramTypes: ['*']
  });

  // Register default error handlers

  // Generic error handler that provides a fallback rendering
  registerErrorHandler((error: Error, context: ErrorContext) => {
    const { container, definition, diagramType } = context;

    if (!container) return false;

    // Create a fallback visualization
    container.innerHTML = `
      <div class="mermaid-error-recovery" style="
        border: 1px solid #f0ad4e;
        border-radius: 4px;
        padding: 15px;
        margin: 10px 0;
        background-color: #fcf8e3;
      ">
        <div style="margin-bottom: 10px; color: #8a6d3b;">
          <strong>Diagram Rendering Error:</strong> ${error.message || 'Unknown error'}
        </div>
        <div style="margin-bottom: 15px;">
          <button id="toggle-source-${Date.now()}" style="
            background-color: #f0ad4e;
            color: white;
            border: none;
            padding: 5px 10px;
            border-radius: 3px;
            cursor: pointer;
          ">Show Source</button>
        </div>
        <div id="source-container-${Date.now()}" style="display: none;">
          <pre style="
            background-color: #f5f5f5;
            padding: 10px;
            border-radius: 4px;
            overflow: auto;
            max-height: 300px;
          "><code>${definition.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>
        </div>
      </div>
    `;

    // Add toggle functionality
    const toggleBtn = container.querySelector(`#toggle-source-${Date.now()}`) as HTMLButtonElement;
    const sourceContainer = container.querySelector(`#source-container-${Date.now()}`) as HTMLDivElement;

    if (toggleBtn && sourceContainer) {
      toggleBtn.addEventListener('click', () => {
        const isHidden = sourceContainer.style.display === 'none';
        sourceContainer.style.display = isHidden ? 'block' : 'none';
        toggleBtn.textContent = isHidden ? 'Hide Source' : 'Show Source';
      });
    }

    return true;
  }, {
    name: 'fallback-renderer',
    priority: 10,
    errorTypes: ['*']
  });

  console.log('Mermaid enhancer initialized with default preprocessors and error handlers');
}

/**
 * Enhance the mermaid object with preprocessing and error handling
 * @param mermaid - The mermaid library instance
 */
export function enhanceMermaid(mermaid: any): void {
  if (!mermaid) {
    console.error('Mermaid library not provided');
    return;
  }

  // Store the original render function
  const originalRender = mermaid.render;

  // Replace with enhanced version
  mermaid.render = async function(id: string, definition: string, ...args: any[]): Promise<any> {
    try {
      // Determine diagram type
      const firstLine = definition.trim().split('\n')[0];
      const diagramType = firstLine.trim().replace(/^(\w+).*$/, '$1').toLowerCase();

      // Preprocess the definition
      const processedDef = preprocessDefinition(definition, diagramType);

      // Call the original render with processed definition
      const result = await originalRender.call(this, id, processedDef, ...args);

      // Handle case where result doesn't have svg property
      if (!result || typeof result !== 'object' || !result.svg) {
        console.warn('Mermaid render returned unexpected result:', result);
        return { svg: '', bindFunctions: () => { } };
      }

      return result;
    } catch (error: any) {
      console.error('Mermaid rendering error:', error);

      // Try to handle the error
      const container = document.getElementById(id);
      const context: ErrorContext = {
        container: container || undefined,
        definition,
        diagramType: definition.trim().split('\n')[0].trim(),
        error
      };

      const handled = handleRenderError(error, context);

      if (!handled) {
        // If not handled by any handler, rethrow
        throw error;
      }

      // Return a minimal valid result to prevent destructuring errors
      return { svg: '', bindFunctions: () => { } };
    }
  };

  console.log('Mermaid library enhanced with preprocessing and error handling');
}

// Export a function to initialize everything
export default function initMermaidSupport(mermaidInstance?: any): void {
  initMermaidEnhancer();

  if (mermaidInstance) {
    enhanceMermaid(mermaidInstance);
  } else if (typeof window !== 'undefined') {
    // Wait for mermaid to be available on window
    const checkInterval = setInterval(() => {
      if (window.mermaid) {
        enhanceMermaid(window.mermaid);
        clearInterval(checkInterval);
      }
    }, 100);

    // Stop checking after 10 seconds
    setTimeout(() => clearInterval(checkInterval), 10000);
  }
}
