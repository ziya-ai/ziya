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

/*
 * Initialize the Mermaid enhancer with default preprocessors and error handlers
 */

export function initMermaidEnhancer(): void {
  // Register default preprocessors

  // Add a preprocessor to fix naming conflicts between subgraphs and nodes
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      console.log('🔍 NAMING-CONFLICT-FIX: Checking for subgraph/node naming conflicts');

      // Extract subgraph names
      const subgraphNames = new Set<string>();
      const subgraphMatches = definition.matchAll(/subgraph\s+"([^"]+)"/g);
      for (const match of subgraphMatches) {
        subgraphNames.add(match[1]);
      }

      // Extract node IDs
      const nodeIds = new Set<string>();
      const nodeMatches = definition.matchAll(/(\w+)\[/g);
      for (const match of nodeMatches) {
        nodeIds.add(match[1]);
      }

      console.log('🔍 NAMING-CONFLICT-FIX: Found subgraphs:', Array.from(subgraphNames));
      console.log('🔍 NAMING-CONFLICT-FIX: Found node IDs:', Array.from(nodeIds));

      // Check for conflicts and fix them
      let result = definition;
      for (const subgraphName of subgraphNames) {
        // Check if there's a node with the same ID as the subgraph name
        if (nodeIds.has(subgraphName)) {
          console.log(`🔍 NAMING-CONFLICT-FIX: Found conflict - subgraph "${subgraphName}" has node with same ID`);
          
          // Create a unique new node ID
          const newNodeId = `${subgraphName}Node`;
          
          // Replace node definition: TRI[...] -> TRINode[...]
          const nodeDefRegex = new RegExp(`\\b${subgraphName}\\[`, 'g');
          result = result.replace(nodeDefRegex, `${newNodeId}[`);
          
          // Replace all references to this node in connections, but NOT in subgraph declarations
          // This regex matches the node ID when it's used in connections but not in subgraph declarations
          const nodeRefRegex = new RegExp(`\\b${subgraphName}\\b(?!\\s*\\[|"\\s*$)`, 'g');
          result = result.replace(nodeRefRegex, (match, offset) => {
            // Don't replace if this is part of a subgraph declaration
            const beforeMatch = result.substring(Math.max(0, offset - 20), offset);
            return beforeMatch.includes('subgraph') ? match : newNodeId;
          });
          
          console.log(`🔍 NAMING-CONFLICT-FIX: Renamed conflicting node from "${subgraphName}" to "${newNodeId}"`);
        }
      }

      console.log('🔍 NAMING-CONFLICT-FIX: Processing complete');
      return result;
    }, {
    name: 'naming-conflict-fix',
    priority: 480, // Very high priority to run before other fixes
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix numbered list syntax in node labels
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      console.log('🔍 NODE-LABEL-FIX: Processing node labels with numbered lists');

      // Fix node labels that contain numbered list syntax
      // This regex matches node definitions like: NodeId[1. Some text...]
      const result = definition.replace(/(\w+)\[([^\]]*?)\]/gs, (match, nodeId, content) => {
        // Check if content starts with numbered list syntax
        if (content.match(/^\s*\d+\.\s/)) {
          console.log('🔍 NODE-LABEL-FIX: Fixing numbered list in node:', nodeId);

          // Process the content to escape numbered list syntax
          const fixedContent = content
            .split('\n')
            .map(line => {
              // Escape numbered list syntax at the beginning of lines
              return line.replace(/^(\s*)(\d+)\.\s*(.*)$/, '$1$2\\. $3');
            })
            .join('<br/>'); // Also convert newlines to <br/>

          return `${nodeId}[${fixedContent}]`;
        }
        return match;
      });

      console.log('🔍 NODE-LABEL-FIX: Processing complete');
      return result;
    }, {
    name: 'node-label-numbered-list-fix',
    priority: 470, // Higher priority to run before other fixes
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix multi-line node labels
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      console.log('🔍 MULTILINE-NODE-FIX: Processing multi-line node labels');

      // Fix multi-line node labels by replacing newlines with <br/>
      // This regex matches node definitions like: NodeId[text with
      // newlines
      // more text]
      const result = definition.replace(/(\w+)\[([^\]]*?)\]/gs, (match, nodeId, content) => {
        // Check if content contains actual newlines (not <br/> tags)
        if (content.includes('\n') && !content.includes('<br/>')) {
          console.log('🔍 MULTILINE-NODE-FIX: Fixing node:', nodeId);
          // Replace newlines with <br/> tags, preserving leading whitespace as single spaces
          const fixedContent = content
            .split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0)
            .join('<br/>');
          return `${nodeId}[${fixedContent}]`;
        }
        return match;
      });

      console.log('🔍 MULTILINE-NODE-FIX: Processing complete');
      return result;
    }, {
    name: 'multiline-node-fix',
    priority: 460, // High priority to run before linkstyle-fix
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix invalid linkStyle references
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      console.log('🔍 LINKSTYLE-FIX: Checking for invalid linkStyle references');

      // Count the actual number of links in the definition
      const linkPatterns = [
        /-->/g,           // solid arrows
        /---/g,           // solid lines  
        /-\.->/g,         // dashed arrows
        /--[xo]>/g,       // arrows with markers
        /->>|-->>|<--|<<-/g  // other arrow types
      ];

      // More comprehensive approach: find all arrow-like patterns
      const allArrowPattern = /(-->|---|-.->|--[xo]>|->>|-->>|<--|<<-)/g;
      const arrowMatches = definition.match(allArrowPattern);
      const totalLinks = arrowMatches ? arrowMatches.length : 0;

      console.log('🔍 LINKSTYLE-FIX: Found', totalLinks, 'links in definition');
      if (arrowMatches) {
        console.log('🔍 LINKSTYLE-FIX: Arrow types found:', arrowMatches);
      }

      // Process linkStyle commands and remove invalid ones
      const lines = definition.split('\n');
      const processedLines = lines.map(line => {
        const linkStyleMatch = line.match(/^\s*linkStyle\s+(\d+(?:,\d+)*)/);
        if (linkStyleMatch) {
          const linkNumbers = linkStyleMatch[1].split(',').map(n => parseInt(n.trim()));
          const validLinks = linkNumbers.filter(n => n < totalLinks);

          if (validLinks.length !== linkNumbers.length) {
            console.log('🔍 LINKSTYLE-FIX: Removing invalid link references:',
              linkNumbers.filter(n => n >= totalLinks));
          }

          if (validLinks.length === 0) {
            console.log('🔍 LINKSTYLE-FIX: Removing entire linkStyle line (no valid links)');
            return ''; // Remove the entire line
          }

          const newLinkStyle = `linkStyle ${validLinks.join(',')}`;
          return line.replace(/linkStyle\s+\d+(?:,\d+)*/, newLinkStyle);
        }
        return line;
      });

      const result = processedLines.filter(line => line !== '').join('\n');
      console.log('🔍 LINKSTYLE-FIX: Processing complete');
      return result;
    }, {
    name: 'linkstyle-fix',
    priority: 450, // High priority to run early
    diagramTypes: ['flowchart', 'graph']
  });

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

  // Add a preprocessor to fix node IDs containing colons, which are invalid
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.trim().startsWith('flowchart') && !def.trim().startsWith('graph')) {
      return def;
    }

    const idsToQuote = new Set<string>();
    // Find node definitions with unquoted IDs that contain a colon.
    // e.g., `My Node: with details[Label text]`
    const nodeDefRegex = /([a-zA-Z0-9][^\[\(\n]*:[^\[\(\n]*?)(\[|\()/g;
    let match;
    while ((match = nodeDefRegex.exec(def)) !== null) {
      const id = match[1].trim();
      // Only add if it's not already quoted
      if (!id.startsWith('"') && !id.endsWith('"')) {
        idsToQuote.add(id);
      }
    }

    if (idsToQuote.size === 0) {
      return def;
    }

    let newDef = def;
    idsToQuote.forEach(id => {
      // Escape special characters in ID for use in regex
      const escapedId = id.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
      // Regex to find the unquoted ID as a whole "word".
      const findIdRegex = new RegExp(`(?<=^|\\s)(${escapedId})(?=[\\s;\\[\\(]|$)`, 'g');
      newDef = newDef.replace(findIdRegex, `"${id}"`);
    });

    return newDef;
  }, {
    name: 'colon-in-node-id-fix',
    priority: 155, // High priority to fix structural errors first
    diagramTypes: ['flowchart', 'graph']
  });

  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        console.log('🔍 LINK-LABEL-SANITIZER: Skipping non-flowchart diagram type:', diagramType);
        return definition;
      }

      console.log('🔍 LINK-LABEL-SANITIZER: Processing flowchart/graph');
      console.log('🔍 LINK-LABEL-SANITIZER: Input definition (first 500 chars):', definition.substring(0, 500));

      // This regex finds link labels and is non-greedy to handle multiple links on one line.
      const result = definition.replace(/(-->|-\.->|--[xo]>|---|->>|-->>)\s*\|([^|]*?)\|/g, (match, arrow, label) => {
        console.log('🔍 LINK-LABEL-SANITIZER: Found match:', { match, arrow, label });
        let processedLabel = label.trim();

        // If the label is already properly quoted, do nothing.
        if (processedLabel.startsWith('"') && processedLabel.endsWith('"')) {
          console.log('🔍 LINK-LABEL-SANITIZER: Label already quoted, skipping:', processedLabel);
          return match;
        }

        // If the label (cleaned or original) is empty after processing, just return the arrow without a label.
        if (!processedLabel) {
          console.log('🔍 LINK-LABEL-SANITIZER: Empty label, returning arrow only');
          return arrow;
        }

        // Always quote the label to prevent parsing errors with special characters (like "1. ...").
        let newLabel = processedLabel
          .replace(/"/g, '#quot;')
          // CRITICAL: Escape numbered list syntax to prevent Mermaid markdown interpretation
          .replace(/^(\d+)\.\s*(.*)$/, '$1\\. $2')  // Escape the period with backslash
          // Alternative: Replace period with HTML entity
          // .replace(/^(\d+)\.\s*(.*)$/, '$1&#46; $2')
          // Handle bullet points by escaping them too
          .replace(/^[-*]\s*(.*)$/, '\\$1 $2');  // Escape the bullet character

        // Also, replace brackets with parentheses to avoid parsing errors with node-like syntax in labels.
        newLabel = newLabel.replace(/\[/g, '(').replace(/\]/g, ')');
        const finalResult = `${arrow}|"${newLabel}"|`;
        console.log('🔍 LINK-LABEL-SANITIZER: Transformed:', { original: match, result: finalResult });
        return finalResult;
      });

      console.log('🔍 LINK-LABEL-SANITIZER: Processing complete');
      console.log('🔍 LINK-LABEL-SANITIZER: Final result (first 500 chars):', result.substring(0, 500));
      return result;
    }, {
    name: 'link-label-sanitizer',
    priority: 350, // Very high priority to run before other fixes
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to clean arrow characters from edge labels
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'flowchart' && diagramType !== 'graph' &&
        !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
        return definition;
      }

      console.log('🔍 ARROW-LABEL-CLEANER: Processing edge labels with arrow characters');

      // This regex finds edge labels and removes arrow characters from them
      const result = definition.replace(/(==>|-->|-\.->|--[xo]>|---|->>|-->>)\s*\|([^|]*?)\|/g, (match, arrow, label) => {
        console.log('🔍 ARROW-LABEL-CLEANER: Found match:', { match, arrow, label });
        
        let processedLabel = label.trim();
        
        // If the label is already properly quoted, don't add more quotes
        if (processedLabel.startsWith('"') && processedLabel.endsWith('"')) {
          console.log('🔍 ARROW-LABEL-CLEANER: Label already quoted, skipping:', processedLabel);
          return match;
        }
        
        // Clean arrow characters from the label
        let cleanedLabel = processedLabel
          .replace(/-->/g, '')     // Remove -->
          .replace(/<--/g, '')     // Remove <--
          .replace(/==>/g, '')     // Remove ==>
          .replace(/<=/g, '')      // Remove <==
          .replace(/-\.->/g, '')   // Remove -.->
          .replace(/<-\.-/g, '')   // Remove <-.-
          .trim();

        const finalResult = `${arrow}|"${cleanedLabel}"|`;
        console.log('🔍 ARROW-LABEL-CLEANER: Cleaned:', { original: match, result: finalResult });
        return finalResult;
      });

      console.log('🔍 ARROW-LABEL-CLEANER: Processing complete');
      return result;
    }, {
    name: 'arrow-label-cleaner',
    priority: 360, // Higher priority to run before other label fixes
    diagramTypes: ['flowchart', 'graph']
  });

  // Add a preprocessor to fix links where the target is a node definition
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.trim().startsWith('flowchart') && !def.trim().startsWith('graph')) {
      return def;
    }

    // Preserve the original diagram type declaration
    const lines = def.split('\n');
    let diagramTypeLine = '';
    if (lines[0].trim().startsWith('graph') || lines[0].trim().startsWith('flowchart')) {
      diagramTypeLine = lines[0] + '\n';
      lines.shift(); // Remove the diagram type line from processing
    }

    // Enhanced regex to handle links with labels containing brackets
    const linkWithTargetDefRegex = /^(\s*)(\w+)\s*(--+>?)\s*(\|.*?\|)?\s*(\w+)(\[[^\]]*\])(\s*)$/gm;

    // Process remaining lines
    const processedLines = lines.join('\n').replace(linkWithTargetDefRegex, (match, indent, source, link, label, targetId, targetLabel) => {
      // Extract optional label
      const linkLabel = label ? ` ${label}` : '';

      return [
        `${indent}${targetId}${targetLabel}`, // Node definition
        `${indent}${source} ${link}${linkLabel} ${targetId}` // Link statement with label
      ].join('\n');
    });

    // Combine diagram type with processed content
    return diagramTypeLine + processedLines;
  }, {
    name: 'link-target-definition-fix',
    priority: 255, // High priority to run before other link/node fixes
    diagramTypes: ['flowchart', 'graph']
  });

  // // Add a preprocessor to fix multi-line node labels that cause parsing errors
  // registerPreprocessor(
  //   (definition: string, diagramType: string): string => {
  //     if (diagramType !== 'flowchart' && diagramType !== 'graph' && !definition.trim().startsWith('flowchart') && !definition.trim().startsWith('graph')) {
  //       return definition;
  //     }
  //     // This regex finds nodeId[...] and captures the content.
  //     return definition.replace(/(\w+)\[([\s\S]+?)\]/g, (match, nodeId, content) => {
  //       // If content is already properly quoted, do nothing.
  //       if (content.startsWith('"') && content.endsWith('"')) {
  //         return match;
  //       }
  //       // If content contains newlines or quotes that need escaping, quote the whole thing.
  //       if (content.includes('\n') || content.includes('"')) {
  //         const escapedContent = content.replace(/"/g, '#quot;'); // Mermaid's way of escaping quotes inside labels
  //         return `${nodeId}["${escapedContent}"]`;
  //       }
  //       return match;
  //     });
  //   }, {
  //   name: 'multiline-node-label-fix',
  //   priority: 250,
  //   diagramTypes: ['flowchart', 'graph']
  // });

  // Add a preprocessor to fix class diagram syntax issues
  registerPreprocessor(
    (definition: string, diagramType: string): string => {
      if (diagramType !== 'classDiagram' && !definition.trim().startsWith('classDiagram')) {
        return definition;
      }

      let processedDef = definition;

      // Fix invalid inheritance syntax like "User > OrderStatus"
      processedDef = processedDef.replace(/^(\s*)(\w+)\s*>\s*(\w+)\s*$/gm, '$1$2 --|> $3');

      // Fix class body with standalone ">" - remove it
      processedDef = processedDef.replace(/(class\s+\w+\s*{\s*)>\s*/g, '$1');

      // Fix the specific "} > OrderStatus {" error pattern
      processedDef = processedDef.replace(/}\s*>\s*(\w+)\s*{/g, '}\n    $1 --|> ');

      // Fix invalid ">" syntax in class definitions
      processedDef = processedDef.replace(/}\s*(\w+)\s*{/g, '}\n\n    class $1 {');

      return processedDef;
    }, {
    name: 'class-diagram-inheritance-fix',
    priority: 190,
    diagramTypes: ['classDiagram']
  });

  // Add a preprocessor to fix single-quoted link labels that cause SQS errors
  registerPreprocessor((def: string, type: string) => {
    if (type !== 'flowchart' && !def.trim().startsWith('flowchart') && !def.trim().startsWith('graph')) {
      return def;
    }
    // Replaces single-quoted labels with double-quoted ones, e.g., -->|'text'| becomes -->|"text"|
    return def.replace(/\|'([^']*)'\|/g, '|"$1"|');
  }, {
    name: 'link-label-single-quote-fix',
    priority: 145,
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

        const nodeDefRegex = /^(\s*)(\w+)\s*(\[|\()(["']?)([\s\S]*?)(\4)(\]|\))(\s*.*)$/;
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
    // This preprocessor is specific to gitgraph/gitGraph types.
    // We need to replace `gitgraph` with `gitGraph` if it's present after frontmatter.
    const lines = def.split('\n');

    let inFrontmatter = false;
    let diagramTypeLineProcessed = false; // Flag to ensure we only process the first 'gitgraph' after frontmatter

    if (lines.length > 0 && lines[0].trim() === '---') {
      inFrontmatter = true;
    }

    let finalDef = lines.map((line) => {
      if (inFrontmatter) {
        if (line.trim() === '---') {
          inFrontmatter = false; // End of frontmatter
        }
        return line; // Keep frontmatter lines as is
      }

      // After frontmatter (or if no frontmatter)
      if (!diagramTypeLineProcessed && line.trim().startsWith('gitgraph')) {
        diagramTypeLineProcessed = true; // Mark that we've processed the diagram type line
        return line.replace('gitgraph', 'gitGraph');
      }
      return line;
    }).join('\n');
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
      // processedDef = processedDef.replace(/(\w+)\[(?!")([^\]]*)\n/g, '$1["$2"]');

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

    // Fix malformed edge labels that cause parsing errors
    // Pattern: "D --> E{Attempts -->|Yes| F[" should be "D --> E{Attempts < 3?}\n    E -->|Yes| F["
    finalDef = finalDef.replace(/(\w+)\s*-->\s*(\w+)\{([^}]*?)-->\|([^|]+)\|\s*(\w+)\[/g,
      '$1 --> $2{$3}\n    $2 -->|$4| $5[');

    // Fix diamond nodes with embedded arrows: "E{Attempts -->|Yes|" 
    finalDef = finalDef.replace(/(\w+)\{([^}]*?)-->\|([^|]+)\|/g, '$1{$2}\n    $1 -->|$3|');

    // Fix incomplete diamond syntax
    finalDef = finalDef.replace(/(\w+)\{([^}]*?)\s+\|([^|]+)\|\s*(\w+)\[/g, '$1{$2}\n    $1 -->|$3| $4[');

    return finalDef;
  }, {
    name: 'flowchart-edge-label-fix',
    priority: 210,
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
    finalDef = finalDef.replace(/(\w+)\[([\s\S]*?)\]/g, (match, nodeId, content) => {
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
    finalDef = finalDef.replace(/(\w+)(\[|\()([\s\S]*?)(\]|\))/g, (match, nodeId, open, content, close) => {
      // Ensure open and close brackets match
      if ((open === '[' && close !== ']') || (open === '(' && close !== ')')) {
        return match; // Malformed, skip
      }

      // Skip if already properly quoted
      if (content.match(/^"[\s\S]*"$/)) {
        return match;
      }

      // Quote if content has special characters, newlines, or <br>
      if (/[()\/\n<>&:\.,']/.test(content) || content.includes('<br>')) {
        const escapedContent = content.replace(/"/g, '#quot;').replace(/\n/g, '<br/>');
        return `${nodeId}${open}"${escapedContent}"${close}`;
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

    // Fix issues like `class X,Y,Z style --> class`
    // 1. Ensure `class` as a node ID is quoted.
    finalDef = finalDef.replace(/(\s+-->\s+)class(\s*;|\s*\[|\s*$|\s+-->)/gm, '$1"class"$2');
    // 2. Address `class X,Y,Z somenode` potentially being misparsed before `-->`
    // This is complex; a simpler fix is to ensure `class` keyword is not misinterpreted.
    // The main issue seems to be `--> class`.

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

  // Add a preprocessor specifically for sequence diagram note formatting
  registerPreprocessor(
    (definition: string, diagramType: string) => {
      if (diagramType.toLowerCase() !== 'sequencediagram' && !definition.trim().startsWith('sequenceDiagram')) return definition;

      console.log('Running sequence diagram note formatter');

      const lines = definition.split('\n');
      const fixedLines: string[] = [];
      let i = 0;

      while (i < lines.length) {
        const line = lines[i];
        const trimmedLine = line.trim();

        // Check if this is a note line
        if (trimmedLine.startsWith('Note over ') || trimmedLine.startsWith('Note left of ') || trimmedLine.startsWith('Note right of ')) {
          // Extract the note declaration and content
          const noteMatch = trimmedLine.match(/^(Note (?:over|left of|right of) [^:]+):\s*(.*)$/);

          if (noteMatch) {
            const [, noteDeclaration, firstLineContent] = noteMatch;
            let noteContent = firstLineContent;
            let j = i + 1;

            // Collect all subsequent lines that are part of this note (indented or continuation)
            while (j < lines.length) {
              const nextLine = lines[j];
              const nextTrimmed = nextLine.trim();

              // Stop if we hit another Mermaid command or empty line followed by command
              if (nextTrimmed.match(/^(participant|Note|activate|deactivate|\w+->>|\w+-->>|loop|alt|opt|par|and|else|end)/) ||
                nextTrimmed === '' && j + 1 < lines.length && lines[j + 1].trim().match(/^(participant|Note|activate|deactivate|\w+->>|\w+-->>|loop|alt|opt|par|and|else|end)/)) {
                break;
              }

              // If it's not empty, add it to note content
              if (nextTrimmed !== '') {
                noteContent += '<br/>' + nextTrimmed;
              }
              j++;
            }

            // Clean up the note content
            if (noteContent) {
              noteContent = noteContent
                // First, normalize line breaks and clean up whitespace
                .replace(/\r\n/g, '\n')
                .replace(/\r/g, '\n')
                // Split into lines, preserve all lines (including empty ones for spacing)
                .split('\n')
                .map(line => {
                  const trimmed = line.trim();
                  // Keep empty lines for spacing, but convert to a single space
                  return trimmed.length === 0 ? ' ' : trimmed;
                })
                .join('<br/>')
                // Remove problematic HTML tags but keep <br/> for line breaks
                .replace(/<(?!br\/?>)[^>]*>/g, '')
                // Escape only the most problematic characters, keep others readable
                .replace(/"/g, "'")
                .replace(/\{/g, '(')
                .replace(/\}/g, ')')
                // Handle numbered lists
                .replace(/(\d+)\.\s*/g, '$1. ')
                // Handle bullet points
                .replace(/-\s*/g, '• ')
                .trim();
            }

            // Reconstruct the note with proper formatting
            fixedLines.push(`    ${noteDeclaration}: ${noteContent}`);
            i = j;
          } else {
            // Malformed note, just add as-is
            fixedLines.push(line);
            i++;
          }
        } else {
          // Not a note line, add as-is
          fixedLines.push(line);
          i++;
        }
      }

      return fixedLines.join('\n');
    },
    {
      name: 'sequence-diagram-note-formatter',
      priority: 300, // Higher priority to run before other fixes
      diagramTypes: ['sequencediagram']
    }
  );

  // Add a preprocessor for state diagram notes to fix markdown list errors
  registerPreprocessor(
    (definition: string, diagramType: string) => {
      if (!diagramType.toLowerCase().startsWith('statediagram') && !definition.trim().startsWith('stateDiagram')) {
        return definition;
      }

      const lines = definition.split('\n');
      const fixedLines: string[] = [];
      let inNote = false;

      for (const line of lines) {
        const trimmedLine = line.trim();

        if (trimmedLine.match(/^note\s+(right of|left of|over)\s+\w+/)) {
          inNote = true;
          fixedLines.push(line);
          continue;
        }

        if (trimmedLine === 'end note') {
          inNote = false;
          fixedLines.push(line);
          continue;
        }

        if (inNote) {
          // Replace leading hyphens with a bullet to avoid markdown list parsing issues
          const processedLine = line.replace(/^(\s*)-\s+/, '$1• ');
          fixedLines.push(processedLine);
        } else {
          fixedLines.push(line);
        }
      }

      return fixedLines.join('\n');
    },
    {
      name: 'state-diagram-note-fixer',
      priority: 295, // High priority to run before other general fixers
      diagramTypes: ['statediagram', 'statediagram-v2']
    }
  );

  // Add a preprocessor to handle participant names with special characters
  registerPreprocessor(
    (definition: string, diagramType: string) => {
      if (diagramType.toLowerCase() !== 'sequencediagram' && !definition.trim().startsWith('sequenceDiagram')) return definition;

      console.log('Running sequence diagram participant name fixer');

      const lines = definition.split('\n');
      const fixedLines: string[] = [];

      for (const line of lines) {
        let fixedLine = line;
        const trimmedLine = line.trim();

        // Fix participant declarations with special characters
        if (trimmedLine.startsWith('participant ')) {
          const participantMatch = trimmedLine.match(/^participant\s+(.+)$/);
          if (participantMatch) {
            const participantName = participantMatch[1];
            // If participant name contains spaces or special chars, don't quote it
            // Mermaid handles this automatically in most cases
            fixedLine = `    participant ${participantName}`;
          }
        }

        fixedLines.push(fixedLine);
      }

      return fixedLines.join('\n');
    },
    {
      name: 'sequence-diagram-participant-fixer',
      priority: 290,
      diagramTypes: ['sequencediagram']
    });

  // Add preprocessor for beta diagram types
  registerPreprocessor((def: string, type: string) => {
    // Handle beta diagram types that might not be supported
    if (def.trim().startsWith('architecture-beta')) {
      // Convert to a supported diagram type or provide fallback
      return def.replace('architecture-beta', 'graph TD\n    %% Architecture diagram (beta feature not supported)\n    %%');
    }

    if (def.trim().startsWith('packet-beta')) {
      // Convert to a supported diagram type or provide fallback
      return def.replace('packet-beta', 'graph TD\n    %% Packet diagram (beta feature not supported)\n    %%');
    }

    return def;
  }, {
    name: 'beta-diagram-fallback',
    priority: 300,
    diagramTypes: ['*']
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

    // Fix for `OrderStatus --|> PENDING` where PENDING is not a class
    // If Y in X --|> Y is all caps and not defined as a class, comment it out.
    processedDef = processedDef.replace(/^(\s*)(\w+)\s*--\|>\s*([A-Z_]+)\s*$/gm, (match, indent, classX, classY) => {
      if (!processedDef.match(new RegExp(`class\\s+${classY}\\s*\\{`, 'm'))) {
        return `${indent}%% ${match.trim()}`;
      }
      return match;
    });

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
  if (rgb.r > 180 && rgb.g > 180 && rgb.b < 120) {
    return '#000000'; // Always use black on yellow/yellow-ish
  }

  // Special handling for beige/cream colors (high R, G, moderate B)
  if (rgb.r > 200 && rgb.g > 180 && rgb.b > 140) {
    return '#000000'; // Always use black on beige/cream
  }

  // Special handling for light gray colors
  if (Math.abs(rgb.r - rgb.g) < 30 && Math.abs(rgb.g - rgb.b) < 30 && rgb.r > 180) {
    return '#000000'; // Use black on light grays
  }

  // Calculate relative luminance using proper sRGB formula
  const luminance = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;

  // Use a more conservative threshold - prefer black text unless background is quite dark
  return luminance > 0.35 ? '#000000' : '#ffffff';
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
      let diagramType: string;
      const lines = definition.trim().split('\n');
      let typeLine = lines[0]?.trim() || '';
      if (typeLine === '---' && lines.length > 1) {
        let inFrontmatterDetect = true;
        let diagramDeclarationLine = '';
        for (let i = 1; i < lines.length; i++) {
          const currentLineTrimmed = lines[i].trim();
          if (inFrontmatterDetect) {
            if (currentLineTrimmed === '---') {
              inFrontmatterDetect = false;
            }
          } else {
            if (currentLineTrimmed) { // First non-empty line after frontmatter
              diagramDeclarationLine = currentLineTrimmed;
              break;
            }
          }
        }
        typeLine = diagramDeclarationLine || lines[0]?.trim() || ''; // Fallback if no declaration found
      }
      diagramType = typeLine.split(' ')[0].toLowerCase(); // Get the first word as type
      // Preprocess the definition
      const processedDef = preprocessDefinition(definition, diagramType); // Pass the correctly determined diagramType

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
  }

  // Add a preprocessor for class diagram relationship syntax issues
  registerPreprocessor(
    (definition: string, diagramType: string) => {
      if (diagramType.toLowerCase() !== 'classdiagram' && !definition.trim().startsWith('classDiagram')) return definition;

      console.log('Running class diagram relationship fixer');

      const lines = definition.split('\n');
      const fixedLines: string[] = [];
      let inClassDefinition = false;

      for (const line of lines) {
        let fixedLine = line;
        const trimmedLine = line.trim();

        // Check if we're entering or exiting a class definition block
        if (trimmedLine.match(/class\s+\w+\s*{/)) {
          inClassDefinition = true;
        } else if (inClassDefinition && trimmedLine === '}') {
          inClassDefinition = false;
        }

        // Only process relationship lines outside of class definitions
        if (!inClassDefinition) {
          // Fix incorrect relationship syntax (e.g., "User > OrderStatus" should be "User --> OrderStatus")
          const incorrectRelationMatch = trimmedLine.match(/^(\w+)\s+([<>])\s+(\w+)$/);
          if (incorrectRelationMatch) {
            const [, class1, relation, class2] = incorrectRelationMatch;
            const fixedRelation = relation === '>' ? '-->' : '<--';
            fixedLine = `${class1} ${fixedRelation} ${class2}`;
            console.log(`Fixed relationship syntax: "${trimmedLine}" -> "${fixedLine}"`);
          }

          // Fix missing relationship type (e.g., "User -- Order" should be "User --> Order")
          const missingArrowMatch = trimmedLine.match(/^(\w+)\s+--\s+(\w+)$/);
          if (missingArrowMatch) {
            const [, class1, class2] = missingArrowMatch;
            fixedLine = `${class1} --> ${class2}`;
            console.log(`Fixed missing arrow: "${trimmedLine}" -> "${fixedLine}"`);
          }
        }

        fixedLines.push(fixedLine);
      }

      return fixedLines.join('\n');
    },
    {
      name: 'classDiagramRelationshipFixer',
      priority: 85,
      diagramTypes: ['classdiagram']
    }
  );
  // Add a simple preprocessor for sequence diagram activation/deactivation issues
  registerPreprocessor((definition: string, diagramType: string) => {
    if (diagramType.toLowerCase() !== 'sequencediagram' && !definition.trim().startsWith('sequenceDiagram')) {
      return definition;
    }

    // This is a targeted fix for participant names that are also Mermaid keywords (e.g., 'opt', 'loop').
    // It finds message lines where a keyword is used as a participant and quotes it.
    const keywords = ['opt', 'alt', 'loop', 'par', 'and', 'else', 'end'];
    let processedDef = definition;

    keywords.forEach(keyword => {
      // Regex for keyword as target: e.g., A->>opt: message
      const targetRegex = new RegExp(`(->>|-->>|->>\\+|-->>\\+|->>-|-->>-)(\\s*)${keyword}\\b`, 'gi');
      processedDef = processedDef.replace(targetRegex, (match, arrow, space) => `${arrow}${space}"${keyword}"`);

      // Regex for keyword as source: e.g., opt->>A: message
      const sourceRegex = new RegExp(`^(\\s*)${keyword}\\b(\\s*)(->>|-->>|->>\\+|-->>\\+|->>-|-->>-)`, 'gim');
      processedDef = processedDef.replace(sourceRegex, (match, pre, post, arrow) => `${pre}"${keyword}"${post}${arrow}`);
    });

    return processedDef;
  }, {
    name: 'sequence-diagram-keyword-participant-fix',
    priority: 285, // Run after participant declarations are clear but before main parsing
    diagramTypes: ['sequencediagram']
  });
}
