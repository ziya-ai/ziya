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
