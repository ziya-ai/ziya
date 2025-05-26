/**
 * Utility functions for working with diagram definitions
 */

/**
 * Checks if a Mermaid diagram definition is complete enough to render
 * @param definition The Mermaid diagram definition string
 * @returns boolean indicating if the definition is complete
 */
export function isMermaidDefinitionComplete(definition: string): boolean {
  if (!definition) return false;
  const trimmedDef = definition.trim();
  if (trimmedDef.length < 10) return false; // Arbitrary minimum length to avoid premature rendering

  const lines = trimmedDef.split('\n');
  if (lines.length < 2 && !trimmedDef.endsWith(';')) return false; // Simple diagrams might be one-liners ending with ;

  const firstLine = lines[0].trim().toLowerCase();
  const knownTypes = ['graph', 'flowchart', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram', 'gantt', 'pie', 'gitGraph', 'mindmap', 'timeline', 'requirement', 'xychart', 'sankey-beta', 'quadrantChart'];

  if (!knownTypes.some(type => firstLine.startsWith(type))) {
    // If it doesn't start with a known type, it's unlikely to be a complete mermaid diagram yet
    // unless it's a very short definition that might be completed soon.
    // Allow very short definitions to pass if they are part of a stream.
    return trimmedDef.length < 50; // Heuristic: if short, assume it might become valid
  }

  // Check for specific diagram types
  if (firstLine.startsWith('graph') || firstLine.startsWith('flowchart')) {
    // For flowcharts, check for at least one link or node definition after the type declaration
    // and that it doesn't end abruptly (e.g., mid-node definition)
    const hasContent = lines.slice(1).some(line => line.includes('-->') || line.includes('---') || line.match(/^\s*\w+/));
    const endsSensibly = !trimmedDef.match(/\[[^\]]*$/) && !trimmedDef.match(/\([^)]*$/); // Doesn't end mid-bracket/paren
    return hasContent && endsSensibly;
  }

  if (firstLine.startsWith('sequencediagram')) {
    // For sequence diagrams, check if there are actual interactions
    const hasActor = lines.some(line => line.trim().startsWith('participant') || line.trim().startsWith('actor'));
    const hasMessage = lines.some(line => line.includes('->') || line.includes('->>'));
    return hasActor && hasMessage;
  }

  if (firstLine.startsWith('classDiagram')) {
    // For class diagrams, check for class definitions
    return lines.some(line => line.includes('class '));
  }

  if (firstLine.startsWith('erDiagram')) {
    // For ER diagrams, check for entity relationships
    return lines.some(line => line.includes('||') || line.includes('|{') || line.includes('}|'));
  }

  // For other diagram types, check if there are at least a few lines
  // and the definition doesn't end with an incomplete code block
  // A common indicator of incompleteness during streaming is ending with an open quote or bracket.
  const lastChar = trimmedDef.slice(-1);
  const potentiallyIncomplete = ['[', '(', '{', '"', "'"].includes(lastChar);
  return lines.length >= 2 && !potentiallyIncomplete;
}

/**
 * Checks if a Graphviz diagram definition is complete enough to render
 * @param definition The Graphviz diagram definition string
 * @returns boolean indicating if the definition is complete
 */
export function isGraphvizDefinitionComplete(definition: string): boolean {
  if (!definition) return false;
  const trimmedDef = definition.trim();
  if (trimmedDef.length < 10) return false; // Arbitrary minimum length

  const firstLine = trimmedDef.split('\n')[0].trim().toLowerCase();
  if (!firstLine.startsWith('digraph') && !firstLine.startsWith('graph') &&
    !firstLine.startsWith('strict digraph') && !firstLine.startsWith('strict graph')) {
    return false; // Must start with a valid Graphviz keyword
  }

  const openBraces = (trimmedDef.match(/{/g) || []).length;
  const closeBraces = (trimmedDef.match(/}/g) || []).length;

  // A complete graphviz definition must start correctly, have at least one pair of braces,
  // have balanced braces, and end with a closing brace.
  return openBraces > 0 && openBraces === closeBraces && trimmedDef.endsWith('}');
}

/**
 * Checks if any diagram definition is complete based on its type
 * @param definition The diagram definition string
 * @param type The type of diagram ('mermaid', 'graphviz', etc.)
 * @returns boolean indicating if the definition is complete
 */
export function isDiagramDefinitionComplete(definition: string, type: string): boolean {
  if (!definition) return false;

  switch (type.toLowerCase()) {
    case 'mermaid':
      return isMermaidDefinitionComplete(definition);

    case 'graphviz':
    case 'dot':
      return isGraphvizDefinitionComplete(definition);

    default:
      // For unknown types, assume complete if not empty
      return definition.trim().length > 0;
  }
}
