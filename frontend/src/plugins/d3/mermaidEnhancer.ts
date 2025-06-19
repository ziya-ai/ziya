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

  // Add a preprocessor to fix pipe syntax in flowcharts - higher priority
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      // Fix pipe syntax - convert A |label| B to A -->|label| B
      return definition.replace(/(\w+)\s*\|([^|]+)\|\s*(\w+)/g, '$1 -->|$2| $3');
    }, {
    name: 'pipe-syntax-fix',
    priority: 300,
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix multi-line node labels that cause parsing errors
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      // Fix multi-line node labels by replacing newlines with <br> tags
      return definition.replace(/(\w+)\[([^\]]*)\n([^\]]*)\]/g, '$1[$2<br>$3]');
    }, {
    name: 'multiline-node-label-fix',
    priority: 250,
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix flowchart line continuation issues
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' && diagramType !== 'TD' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      // Fix incomplete lines that end abruptly (like "C[Load User Profil" without closing bracket)
      let processedDef = definition;

      // Fix the specific "Load User Profil" error - complete truncated node labels
      processedDef = processedDef.replace(/(\w+)\["?([^"\]]*?)$/gm, (match, nodeId, content) => {
        // If content looks incomplete (no closing quote/bracket), complete it
        return `${nodeId}["${content}"]`;
      });

      processedDef = processedDef.replace(/(\w+)\[([^\]]*?)$/gm, (match, nodeId, content) => {
        return content.trim() ? `${nodeId}["${content}"]` : `${nodeId}[${nodeId}]`;
      });
      return processedDef;
    }, {
    name: 'flowchart-incomplete-line-fix',
    priority: 200,
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix class diagram syntax issues
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'classDiagram' && !definition.trim().startsWith('classDiagram')) {
        return definition;
      }

      let processedDef = definition;

      // Fix class inheritance syntax - replace ">" with "--|>"
      processedDef = processedDef.replace(/(\s+)>\s*(\w+)/g, '$1--|> $2');

      // Fix invalid standalone ">" syntax in class definitions
      processedDef = processedDef.replace(/^\s*>\s*$/gm, '');

      // Fix the specific "} > OrderStatus {" error pattern
      processedDef = processedDef.replace(/}\s*>\s*(\w+)\s*{/g, '}\n    $1 --|> ');

      // Fix class body with standalone ">" - remove it
      processedDef = processedDef.replace(/(class\s+\w+\s*{\s*)>\s*/g, '$1');

      // Fix enum syntax issues
      processedDef = processedDef.replace(/(\w+)\s*{\s*<<enumeration>>/g, '$1 {\n        <<enumeration>>');

      // Fix method syntax with asterisk
      processedDef = processedDef.replace(/\+(\w+)\([^)]*\)\s*(\w+)\*/g, '+$1() $2');

      // Fix invalid ">" syntax in class definitions
      processedDef = processedDef.replace(/}\s*(\w+)\s*{/g, '}\n\n    class $1 {');

      return processedDef;
    }, {
    name: 'class-diagram-syntax-fix',
    priority: 180,
    diagramTypes: ['classDiagram']
  });

  // Add a quote cleanup preprocessor that runs first to fix quote multiplication
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' && !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      const lines = definition.split('\n');
      const fixedLines: string[] = [];

      for (let i = 0; i < lines.length; i++) {
        let currentLine = lines[i];
        // Regex to capture:
        // 1: leading whitespace, 2: nodeId, 3: openBracket (paren or square),
        // 4: opening quote (optional), 5: content, 6: closing quote (matches opening),
        // 7: closeBracket (matches openBracket), 8: any trailing characters on the line

        // Skip subgraph lines - they don't need node termination fixes
        if (currentLine.trim().startsWith('subgraph') || currentLine.trim() === 'end') {
          fixedLines.push(currentLine);
          continue;
        }

        const nodeDefRegex = /^(\s*)(\w+)\s*(\[|\()(["']?)(.*?)(\4)(\]|\))(\s*.*)$/;
        const nodeDefMatch = currentLine.match(nodeDefRegex);

        if (nodeDefMatch) {
          const [,
            leadingSpace,
            nodeId,
            openBracketOrParen,
            openingQuote,
            nodeContent,
            closingQuote, // This should match openingQuote due to \4 in regex
            closeBracketOrParen,
            trailingCharacters // All characters after the closing bracket/paren
          ] = nodeDefMatch;

          let mainPart = `${leadingSpace}${nodeId}${openBracketOrParen}`;
          let contentPart = nodeContent;

          if (openingQuote && closingQuote) { // If it was quoted
            if (contentPart.trim() === "") {
              contentPart = " "; // Replace "" or '' with " "
            }
            mainPart += `${openingQuote}${contentPart}${closingQuote}`;
          } else { // Unquoted content
            mainPart += contentPart;
          }
          mainPart += `${closeBracketOrParen}`;

          let finalLine = mainPart;
          const originalTrailingSyntax = trailingCharacters.trim();

          if (originalTrailingSyntax) {
            finalLine += trailingCharacters;
          }

          const needsTerminationFix = !originalTrailingSyntax ||
            (!originalTrailingSyntax.startsWith(':::') &&
              !originalTrailingSyntax.match(/^(-->|---|~~~|\.-|\.\.-|o--|--o|x--)/));

          if (needsTerminationFix && !originalTrailingSyntax && !currentLine.includes('subgraph')) {
            finalLine += ":::default";
          }
          fixedLines.push(finalLine);
        } else {
          fixedLines.push(currentLine);
        }
      }
      return fixedLines.join('\n');
    }, {
    name: 'flowchart-line-break-fix',
    priority: 185,
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix gitgraph diagrams
  registerPreprocessor((def: string, type: string) => {
    if (!def.trim().startsWith('gitgraph')) {
      return def;
    }

    // Convert gitgraph to git graph (supported syntax)
    let finalDef = def.replace(/^gitgraph/, 'gitGraph');

    return finalDef;
  }, {
    name: 'gitgraph-syntax-fix',
    priority: 140,
    diagramTypes: ['gitgraph', 'gitGraph']
  });

  // Add a preprocessor to fix XYChart excessive quotes
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'xychart' && !def.trim().startsWith('xychart')) {
      return def;
    }

    // Fix excessive quotes in xychart syntax
    return def.replace(/"{2,}/g, '"');
  }, {
    name: 'xychart-quotes-fix',
    priority: 140,
    diagramTypes: ['xychart']
  });

  // Add a preprocessor to fix flowchart line break issues
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      // Fix incomplete node definitions that end with newlines
      let processedDef = definition;

      // Fix nodes that have incomplete label definitions - ensure proper closing
      processedDef = processedDef.replace(/(\w+)\[([^\]]*)\n/g, '$1["$2"]');

      // Ensure proper spacing around arrows
      processedDef = processedDef.replace(/(\w+)-->/g, '$1 -->');
      processedDef = processedDef.replace(/-->(\w+)/g, '--> $1');

      return processedDef;
    }, {
    name: 'flowchart-line-break-fix',
    priority: 190,
    diagramTypes: ['flowchart', 'graph']
  });
  // Add a quote cleanup preprocessor that runs first to fix quote multiplication
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.startsWith('flowchart ') && !def.startsWith('graph ')) {
      return def;
    }

    let finalDef = def;
    console.log('Quote cleanup - before:', finalDef.substring(0, 200));

    // Fix quote multiplication by cleaning up malformed quotes
    finalDef = finalDef.replace(/(\w+)\[([^\]]*)\]/g, (match, nodeId, content) => {
      // Skip if this is already properly quoted
      if (content.match(/^"[^"]*"$/)) {
        return match;
      }

      // If content has multiple quotes, excessive quotes, or malformed quotes, clean it up
      if (content.includes('""') || content.match(/"{2,}/) || content.includes('\\"') || content.match(/^".*".*"/)) {
        // Extract the actual text content by removing all quote variations and trailing backslashes
        const cleanContent = content.replace(/^"+|"+$/g, '').replace(/\\"/g, '"').replace(/"{2,}/g, '"').replace(/\\+$/g, '');
        console.log(`Cleaning quotes for ${nodeId}: "${content}" -> "${cleanContent}"`);
        return `${nodeId}["${cleanContent}"]`;
      }
      return match;
    });

    console.log('Quote cleanup - after:', finalDef.substring(0, 200));
    return finalDef;
  }, {
    name: 'quote-cleanup',
    priority: 200, // High priority to run first
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor specifically for subgraph syntax
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.startsWith('flowchart') && !def.startsWith('graph')) {
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

  registerPreprocessor((def: string, type: string): string => {
    if (type !== 'flowchart' && !def.startsWith('flowchart') && !def.startsWith('graph')) {
      return def;
    }

    let finalDef = def;

    console.log('Mixed node shapes - processing:', finalDef.substring(0, 200));
    finalDef = finalDef.replace(/(\w+)\[([^\]]*)\]/g, (match, nodeId, content) => {
      // Skip if already properly quoted
      if (content.match(/^"[^"]*"$/) || content.match(/^'[^']*'$/)) {
        return match;
      }

      // Only quote if content has special characters and isn't already quoted
      if (/[()\/\n<>\-]/.test(content)) {
        // Escape any existing quotes and wrap in quotes
        const escapedContent = content.replace(/"/g, '\\"');
        return `${nodeId}["${escapedContent}"]`;
      }
      return match;
    });
    finalDef = finalDef.replace(/(\w+)\(([^)]*)\)/g, (match, nodeId, content) => {
      // Skip if already properly quoted
      if (content.match(/^"[^"]*"$/) || content.match(/^'[^']*'$/)) {
        return match;
      }

      if (/[\[\]\/\n<>\-]/.test(content)) {
        const escapedContent = content.replace(/"/g, '\\"');
        return `${nodeId}("${escapedContent}")`;
      }
      return match;
    });


    console.log('Mixed node shapes - result:', finalDef.substring(0, 200));
    console.log('Final processed definition length:', finalDef.length);
    return finalDef;
  }, {
    name: 'special-char-in-node-label-fix',
    priority: 135,
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
    if (type !== 'flowchart' && !def.startsWith('flowchart') && !def.startsWith('graph')) {
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

  // Add a preprocessor to fix Sankey diagram line break issues
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'sankey' && !def.trim().startsWith('sankey-beta') && !def.trim().startsWith('sankey')) {
      return def;
    }

    // Fix line break issues in sankey diagrams
    let lines = def.split('\n');
    let result: string[] = [];

    for (let line of lines) {
      let trimmedLine = line.trim();

      // Skip empty lines
      if (!trimmedLine) {
        continue;
      }

      // Keep the sankey-beta header as is
      if (trimmedLine.startsWith('sankey')) {
        result.push(trimmedLine);
      } else if (trimmedLine.includes(',')) {
        // Ensure each data line has proper comma separation and no extra whitespace
        line = line.replace(/\s+/g, ' ').trim();
        result.push(line);
      }
    }

    return result.join('\n');
  }, {
    name: 'sankey-line-break-fix',
    priority: 115,
    diagramTypes: ['sankey', 'sankey-beta']
  });

  // Add a preprocessor to fix Timeline diagram syntax issues
  registerPreprocessor((def: string, type: string) => {
    if (!def.trim().startsWith('timeline')) {
      return def;
    }

    // Fix timeline diagram syntax issues
    let lines = def.split('\n');
    let result: string[] = [];
    let inSection = false;

    for (let line of lines) {
      let trimmedLine = line.trim();

      // Skip empty lines
      if (!trimmedLine) {
        continue;
      }

      // Handle timeline header
      if (trimmedLine.startsWith('timeline')) {
        result.push(trimmedLine);
        continue;
      }

      // Handle title
      if (trimmedLine.startsWith('title ')) {
        result.push('    ' + trimmedLine);
        continue;
      }

      // Handle sections
      if (trimmedLine.startsWith('section ')) {
        result.push('    ' + trimmedLine);
        inSection = true;
        continue;
      }

      // Handle events within sections
      if (inSection && trimmedLine.includes(' : ')) {
        result.push('        ' + trimmedLine);
        continue;
      }

      // Default case - preserve line with proper indentation
      if (trimmedLine) {
        result.push('    ' + trimmedLine);
      }
    }

    return result.join('\n');
  }, {
    name: 'timeline-syntax-fix',
    priority: 120,
    diagramTypes: ['timeline']
  });

  // Add a preprocessor to fix Gantt diagram date format issues
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'gantt' && !def.trim().startsWith('gantt')) {
      return def;
    }

    // Fix gantt diagram date format issues
    let processedDef = def;

    // Fix date format - convert "50s" style dates to proper format
    processedDef = processedDef.replace(/(\d+)s/g, '$1');

    // Ensure proper date format is set
    if (!processedDef.includes('dateFormat')) {
      processedDef = processedDef.replace(/^gantt/, 'gantt\n    dateFormat YYYY-MM-DD');
    }

    // Fix axis format
    if (!processedDef.includes('axisFormat')) {
      processedDef = processedDef.replace(/dateFormat[^\n]*/, '$&\n    axisFormat %Y-%m-%d');
    }

    return processedDef;
  }, {
    name: 'gantt-date-format-fix',
    priority: 120,
    diagramTypes: ['gantt']
  });

  // Fix for sequence diagram activation/deactivation issues
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'sequenceDiagram' && !def.trim().startsWith('sequenceDiagram')) return def;

    // Track activation state more carefully to prevent deactivating inactive participants
    const lines = def.split('\n');
    const activationState: Record<string, boolean> = {};
    const result: string[] = [];

    for (let line of lines) {
      line = line.trim();

      // Skip empty lines and comments
      if (!line || line.startsWith('%%')) {
        result.push(line);
        continue;
      }

      // Skip lines that are just whitespace or comments
      if (!line || line.startsWith('%%') || line.startsWith('Note')) {
        result.push(line);
        continue;
      }

      // Track activations
      if (line.includes('activate ')) {
        const activateMatch = line.match(/activate\s+(\w+)/);
        const participant = activateMatch ? activateMatch[1] : null;
        if (!participant) continue;

        if (participant) {
          activationState[participant] = true;
        }
        result.push(line);
      }
      // Check deactivations
      else if (line.includes('deactivate ')) {
        const deactivateMatch = line.match(/deactivate\s+(\w+)/);
        const participant = deactivateMatch ? deactivateMatch[1] : null;

        if (participant && activationState[participant]) {
          activationState[participant] = false;
          result.push(line);
        } else if (participant) {
          // Skip this deactivation as the participant is not active
          console.warn(`Skipping deactivation for inactive participant: ${participant}`);
          // Add as comment for debugging
          result.push(`%%${line} (skipped - participant not active)`);
        }
      }
      // Handle activation via message arrows
      else if (line.includes('->>+') || line.includes('-->>+')) {
        const activationMatch = line.match(/.*?->>?\+\s*(\w+)/);
        if (activationMatch && activationMatch[1]) {
          activationState[activationMatch[1]] = true;
        }
        result.push(line);
      }
      // Handle deactivation via message arrows
      else if (line.includes('->>-') || line.includes('-->>-')) {
        const deactivationMatch = line.match(/.*?->>?-\s*(\w+)/);
        if (deactivationMatch && deactivationMatch[1] && activationState[deactivationMatch[1]]) {
          activationState[deactivationMatch[1]] = false;
        }
        result.push(line);
      }

      // Handle lines with "deactivate" at the end but no explicit participant
      else if (line.endsWith('deactivate')) {
        // Extract participant from the message line
        const messageMatch = line.match(/(\w+)->>.*?(\w+).*deactivate$/);
        if (messageMatch) {
          const targetParticipant = messageMatch[2];
          if (targetParticipant && !activationState[targetParticipant]) {
            // Remove the deactivate keyword since the participant isn't active
            const cleanLine = line.replace(/\s*deactivate\s*$/, '');
            result.push(cleanLine);
            console.warn(`Removed deactivation for inactive participant: ${targetParticipant}`);
            continue;
          } else if (targetParticipant) {
            activationState[targetParticipant] = false;
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

  // Add a specific preprocessor to handle the exact parsing errors we're seeing
  registerPreprocessor((def: string, type: string) => {
    // Fix incomplete flowchart connections that cause "NODE_STRING" errors
    let processedDef = def;

    // Fix patterns like "F  H    G  I" (incomplete connections)
    processedDef = processedDef.replace(/(\w+)\s+(\w+)\s+(\w+)\s+(\w+)\s*$/gm,
      '$1 --> $2\n    $2 --> $3\n    $3 --> $4');

    return processedDef;
  }, {
    name: 'incomplete-connection-fix',
    priority: 90,
    diagramTypes: ['flowchart', 'graph']
  });

  registerPreprocessor((def: string, type: string) => {
    // Handle all diagram types that might have parsing issues
    let processedDef = def;

    // Fix the specific "Load User Profil" truncation error
    processedDef = processedDef.replace(/C\["Load User Profil$/m, 'C["Load User Profile"]');
    processedDef = processedDef.replace(/(\w+)\["([^"]*?)$/gm, '$1["$2"]');

    // Fix class diagram ">" dependency syntax
    processedDef = processedDef.replace(/}\s*>\s*(\w+)\s*{/g, '}\n    $1 --|> ');
    processedDef = processedDef.replace(/(class\s+\w+\s*{\s*)>\s*/g, '$1');

    return processedDef;
  }, {
    name: 'parsing-error-fix',
    priority: 85,
    diagramTypes: ['*']
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

  registerPreprocessor((def: string, type: string) => {
    // Add explicit shape definitions for states that might be causing issues
    const lines = def.split('\n');
    const states = new Set<string>();

    // First pass: collect all state names
    for (const line of lines) {
      // Skip if not a state diagram type, but allow processing if it looks like one
      if (type && type !== 'stateDiagram' && type !== 'stateDiagram-v2' && !line.match(/^\s*stateDiagram/i)) {
        continue;
      }
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
      line.trim().match(/^stateDiagram(?:-v2)?/i)
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

  // Register a preprocessor to improve dark mode text visibility by using stroke colors for text
  registerPreprocessor((def: string, type: string) => {
    // Only apply to diagrams that might have classDef or style statements
    if (!def.includes('classDef') && !def.includes('style ')) {
      return def;
    }

    let processedDef = def;

    // Extract classDef statements and modify them for better dark mode visibility
    // Pattern: classDef className fill:#lightcolor,stroke:#darkcolor,stroke-width:2px
    processedDef = processedDef.replace(
      /classDef\s+(\w+)\s+fill:(#[a-fA-F0-9]{6}),stroke:(#[a-fA-F0-9]{6}),stroke-width:(\d+px)/g,
      (match, className, fillColor, strokeColor, strokeWidth) => {
        // Add color property using the stroke color for better text visibility
        return `classDef ${className} fill:${fillColor},stroke:${strokeColor},stroke-width:${strokeWidth},color:${strokeColor}`;
      }
    );

    // Handle style statements for individual nodes
    // Pattern: style NodeName fill:#color,stroke:#color,stroke-width:3px
    processedDef = processedDef.replace(
      /style\s+(\w+)\s+fill:(#[a-fA-F0-9]{6}),stroke:(#[a-fA-F0-9]{6}),stroke-width:(\d+px)/g,
      (match, nodeName, fillColor, strokeColor, strokeWidth) => {
        // Add color property using the stroke color, or a high-contrast color if stroke is too light
        const contrastColor = getContrastColor(strokeColor, fillColor);
        return `style ${nodeName} fill:${fillColor},stroke:${strokeColor},stroke-width:${strokeWidth},color:${contrastColor}`;
      }
    );

    // Handle cases where stroke color might be too light (like #333 which is common)
    // Replace #333 strokes with darker colors for better text contrast
    processedDef = processedDef.replace(
      /(stroke:#333)/g,
      'stroke:#000000'
    );

    return processedDef;
  }, {
    name: 'dark-mode-text-visibility-fix',
    priority: 105,
    diagramTypes: ['*']
  });

  // Default error handler (lowest priority)
  registerErrorHandler((error: Error, context: ErrorContext) => {
    const { container, definition, diagramType } = context;

    // This handler should only run if no other handler has dealt with the error
    // and if a container is provided to display the error.
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
          "><code>${definition.replace(/</g, '<').replace(/>/g, '>')}</code></pre>
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
    priority: 0, // Lowest priority, runs if nothing else handled it
    errorTypes: ['*']
  });

  console.log('Mermaid enhancer initialized with default preprocessors and error handlers');
}

/**
 * Get a high-contrast color for text based on fill and stroke colors
 * @param strokeColor - The stroke color (hex)
 * @param fillColor - The fill color (hex)
 * @returns - A color that provides good contrast
 */
function getContrastColor(strokeColor: string, fillColor: string): string {
  // Convert hex to RGB for luminance calculation
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : null;
  };

  const strokeRgb = hexToRgb(strokeColor);
  if (!strokeRgb) return strokeColor;

  // Calculate relative luminance using proper sRGB formula
  const luminance = (0.299 * strokeRgb.r + 0.587 * strokeRgb.g + 0.114 * strokeRgb.b) / 255;

  // If stroke color is too light (luminance > 0.5), use a darker version or black
  return luminance > 0.5 ? '#000000' : strokeColor;
}

/**
 * Get optimal text color based on background color with special handling for problematic colors
 * @param backgroundColor - The background color (hex)
 * @returns - The best contrasting text color
 */
function getOptimalTextColor(backgroundColor: string): string {
  // Convert hex to RGB
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : null;
  };

  const rgb = hexToRgb(backgroundColor);
  if (!rgb) return '#000000';

  // Special handling for yellow and yellow-ish colors
  // Yellow has high luminance but white text on yellow is terrible
  if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
    return '#000000'; // Always use black on yellow/yellow-ish
  }

  // Special handling for beige/cream colors (high R, G, moderate B)
  if (rgb.r > 220 && rgb.g > 200 && rgb.b > 150) {
    return '#000000'; // Always use black on beige/cream
  }

  // Calculate relative luminance using proper sRGB formula
  const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
  
  // Use a more conservative threshold - prefer black text unless background is quite dark
  return luminance > 0.4 ? '#000000' : '#ffffff';
}

/**
 * Get optimal text color based on background color with special handling for problematic colors
 * @param backgroundColor - The background color (hex)
 * @returns - The best contrasting text color
 */
function getOptimalTextColor(backgroundColor: string): string {
  // Convert hex to RGB
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : null;
  };

  const rgb = hexToRgb(backgroundColor);
  if (!rgb) return '#000000';

  // Special handling for yellow and yellow-ish colors
  // Yellow has high luminance but white text on yellow is terrible
  if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
    return '#000000'; // Always use black on yellow/yellow-ish
  }

  // Special handling for beige/cream colors (high R, G, moderate B)
  if (rgb.r > 220 && rgb.g > 200 && rgb.b > 150) {
    return '#000000'; // Always use black on beige/cream
  }

  // Calculate relative luminance using proper sRGB formula
  const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
  
  // Use a more conservative threshold - prefer black text unless background is quite dark
  return luminance > 0.4 ? '#000000' : '#ffffff';
}

/**
 * Get optimal text color based on background color with special handling for problematic colors
 * @param backgroundColor - The background color (hex)
 * @returns - The best contrasting text color
 */
function getOptimalTextColor(backgroundColor: string): string {
  // Convert hex to RGB
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : null;
  };

  const rgb = hexToRgb(backgroundColor);
  if (!rgb) return '#000000';

  // Special handling for yellow and yellow-ish colors
  // Yellow has high luminance but white text on yellow is terrible
  if (rgb.r > 200 && rgb.g > 200 && rgb.b < 100) {
    return '#000000'; // Always use black on yellow/yellow-ish
  }

  // Special handling for beige/cream colors (high R, G, moderate B)
  if (rgb.r > 220 && rgb.g > 200 && rgb.b > 150) {
    return '#000000'; // Always use black on beige/cream
  }

  // Calculate relative luminance using proper sRGB formula
  const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
  
  // Use a more conservative threshold - prefer black text unless background is quite dark
  return luminance > 0.4 ? '#000000' : '#ffffff';
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
}

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
