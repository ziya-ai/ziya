/**
 * Extract diagram definition from YAML-wrapped content
 * Handles cases where diagram specs are wrapped in YAML metadata like:
 * type: mermaid
 * definition: |
 *   graph TD
 *     A --> B
 */
export function extractDefinitionFromYAML(definition: string, diagramType: string): string {
    // Check if this looks like YAML-wrapped content
    const typePattern = `type: ${diagramType}`;
    if (!definition.includes(typePattern) || !definition.includes('definition:')) {
        return definition; // Not YAML-wrapped, return as-is
    }

    console.log(`🔧 Detected YAML-wrapped ${diagramType} definition, extracting content...`);
    const lines = definition.split('\n');
    let inDefinition = false;
    const contentLines: string[] = [];
    
    for (const line of lines) {
        if (line.trim() === 'definition: |' || line.trim().startsWith('definition: |')) {
            inDefinition = true;
            continue;
        }
        if (inDefinition) {
            // Remove the leading spaces that are part of YAML indentation (usually 2 spaces)
            const cleanedLine = line.replace(/^  /, '');
            contentLines.push(cleanedLine);
        }
    }
    
    const extractedContent = contentLines.join('\n').trim();
    console.log(`✅ Extracted ${diagramType} definition (${extractedContent.length} chars):`, extractedContent.substring(0, 200));
    return extractedContent;
}

/**
 * Check if a diagram definition appears to be complete based on the diagram type
 * @param definition - The diagram definition string
 * @param diagramType - The type of diagram (mermaid, graphviz, vega-lite, etc.)
 * @returns boolean indicating if the definition appears complete
 */
export function isDiagramDefinitionComplete(definition: string, diagramType: string): boolean {
    if (!definition || definition.trim().length === 0) return false;

    // Extract actual content if YAML-wrapped
    const actualDefinition = extractDefinitionFromYAML(definition, diagramType);
    
    switch (diagramType.toLowerCase()) {
        case 'mermaid':
            return isMermaidDefinitionComplete(actualDefinition);
        case 'graphviz':
            return isGraphvizDefinitionComplete(actualDefinition);
        case 'vega-lite':
            return isVegaLiteDefinitionComplete(actualDefinition);
        default:
            // Generic check - at least 2 lines and doesn't end with incomplete markers
            const lines = actualDefinition.trim().split('\n');
            return lines.length >= 2 && !actualDefinition.endsWith('```');
    }
}

function isMermaidDefinitionComplete(definition: string): boolean {
    const lines = definition.trim().split('\n');
    if (lines.length < 2) return false;
    
    const firstLine = lines[0].trim().toLowerCase();
    if (firstLine.startsWith('graph') || firstLine.startsWith('flowchart')) {
        // For flowcharts, check for balanced braces if any
        const openBraces = definition.split('{').length - 1;
        const closeBraces = definition.split('}').length - 1;
        return openBraces === closeBraces;
    }
    
    return lines.length >= 3 && !definition.endsWith('```');
}

function isGraphvizDefinitionComplete(definition: string): boolean {
    if (!definition || definition.trim().length === 0) return false;
    
    // Check for balanced braces
    const openBraces = definition.split('{').length - 1;
    const closeBraces = definition.split('}').length - 1;
    
    return openBraces === closeBraces && openBraces > 0 && definition.includes('}');
}

function isVegaLiteDefinitionComplete(definition: string): boolean {
    if (!definition || definition.trim().length === 0) return false;
    
    try {
        const parsed = JSON.parse(definition);
        
        // Basic completeness checks
        if (!parsed || typeof parsed !== 'object') return false;
        
        // Check for required Vega-Lite properties
        const hasData = parsed.data !== undefined;
        const hasVisualization = parsed.mark || parsed.layer || parsed.concat || parsed.facet || parsed.repeat;
        
        return hasData && hasVisualization;
    } catch (error) {
        return false;
    }
}

// Tool-call / function-call sentinel tags an LLM can leak INTO a fenced
// diagram body. None occur in any diagram DSL we render (mermaid, graphviz,
// DOT) nor in valid drawio mxGraph XML, so they are removed by exact tag
// NAME -- never a generic bracketed tag, which would destroy the XML-based
// drawio definition. Attributes and the antml: namespace prefix are tolerated.
const TOOL_CALL_TAG_RE =
    /<\/?(?:antml:)?(?:invoke|parameter|function_calls|function_results|tool_call|tool_use|tool_result)\b[^>]*>/gi;

// The bracketed and angle-bracket forms of the streaming stop sentinel.
const TOOL_SENTINEL_RE = /\[\/?TOOL_SENTINEL\]|<\/?TOOL_SENTINEL>/gi;

// Boundary-anchored forms built from the same alternation so the two cannot
// drift: a sentinel at the very START or END of the block, plus adjacent
// whitespace. Only the boundaries are matched -- see stripToolCallArtifacts.
const _SENTINEL_SRC =
    '(?:' + TOOL_CALL_TAG_RE.source + '|' + TOOL_SENTINEL_RE.source + ')';
const LEADING_SENTINEL_RE = new RegExp('^' + _SENTINEL_SRC + '\\s*', 'i');
const TRAILING_SENTINEL_RE = new RegExp('\\s*' + _SENTINEL_SRC + '\\s*$', 'i');

/**
 * Remove tool-call / function-call sentinel tags that leaked from an LLM into
 * the BOUNDARIES of a fenced diagram body, so the diagram still renders instead
 * of failing the lexer on stray markup.
 *
 * Models occasionally emit their internal tool-invocation markers (an
 * antml:invoke / parameter block, a function_calls wrapper, or a
 * TOOL_SENTINEL marker) inside a mermaid / graphviz / drawio fence -- most
 * often a trailing parameter/invoke close pair. Mermaid then renders an error
 * instead of the picture the user asked for.
 *
 * Boundary-only by design: sentinels are peeled off the END (repeatedly, for a
 * stacked close pair) and off the START (a wrapper's opening tag); the INTERIOR
 * is never scanned, so a tag name that legitimately appears mid-body -- a node
 * label, an embedded snippet -- is left untouched.
 *
 * Pure and idempotent. A clean body is returned byte-for-byte unchanged.
 */
export function stripToolCallArtifacts(definition: string): string {
    if (!definition) return definition;
    // Fast path: nothing that could be a sentinel -- skip the regex and the
    // extra string allocation on the overwhelmingly common clean case.
    if (definition.indexOf('<') === -1 && definition.indexOf('TOOL_SENTINEL') === -1) {
        return definition;
    }
    // Peel from both ends until neither boundary is a sentinel. Each pattern
    // consumes adjacent whitespace, so the interior stays byte-for-byte intact.
    let out = definition;
    let prev: string;
    do {
        prev = out;
        out = out.replace(TRAILING_SENTINEL_RE, '').replace(LEADING_SENTINEL_RE, '');
    } while (out !== prev && out.length > 0);
    return out === definition ? definition : out;
}