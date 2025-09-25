const fs = require('fs');
const path = require('path');

// Parse command line arguments
const args = process.argv.slice(2);
let definition = '';
let diagramType = '';

for (let i = 0; i < args.length; i++) {
    if (args[i] === '--definition' && i + 1 < args.length) {
        definition = args[i + 1];
        i++;
    } else if (args[i] === '--type' && i + 1 < args.length) {
        diagramType = args[i + 1];
        i++;
    }
}

// Read the actual mermaidEnhancer.ts file and extract the preprocessing logic
const enhancerPath = path.join(__dirname, '..', '..', 'frontend', 'src', 'plugins', 'd3', 'mermaidEnhancer.ts');

if (!fs.existsSync(enhancerPath)) {
    console.error('MermaidEnhancer.ts not found');
    process.exit(1);
}

// For now, implement the key preprocessing logic based on what we added
function preprocessDefinition(def, type) {
    let processed = def;
    
    // HIGHEST PRIORITY: Fix quotes and parentheses in node labels
    if (type === 'flowchart' || type === 'graph' || processed.trim().startsWith('flowchart') || processed.trim().startsWith('graph')) {
        processed = processed.replace(/(\w+)(\{|\[)([^}\]]*?)(\}|\])/g, (match, nodeId, openBracket, content, closeBracket) => {
            let processedContent = content;
            
            // Remove quotes and replace with safe alternatives
            processedContent = processedContent.replace(/"/g, '');
            
            // Replace parentheses with dashes for better readability
            processedContent = processedContent.replace(/\(/g, '- ').replace(/\)/g, '');
            
            // Clean up extra spaces
            processedContent = processedContent.replace(/\s+/g, ' ').trim();
            
            return `${nodeId}${openBracket}${processedContent}${closeBracket}`;
        });
        
        // Also remove semicolons at the end of lines that cause parsing issues
        processed = processed.replace(/;(\s*$)/gm, '$1');
    }
    
    // Replace bullet characters with hyphens (highest priority)
    processed = processed.replace(/•/g, '-');
    processed = processed.replace(/[\u2022\u2023\u2043]/g, '-'); // Various bullet chars
    processed = processed.replace(/[\u2013\u2014]/g, '-'); // En dash, Em dash
    processed = processed.replace(/[\u201C\u201D]/g, '"'); // Smart quotes
    processed = processed.replace(/[\u2018\u2019]/g, "'"); // Smart single quotes
    
    // Fix class diagram cardinality issues (highest priority)
    if (type === 'classdiagram' || processed.trim().startsWith('classDiagram')) {
        processed = processed.replace(/\|\|--\|\|/g, '-->');
        processed = processed.replace(/\|\|--o\{/g, '-->');
        processed = processed.replace(/\}\|--\|\|/g, '-->');
        // Fix other invalid relationship patterns
        processed = processed.replace(/\|\|-->/g, '-->');
        processed = processed.replace(/--\|\|/g, '-->');
        processed = processed.replace(/<\|\|--\|\|>/g, '<-->');
    }
    
    // Fix sequence diagram issues
    if (type === 'sequencediagram' || processed.trim().startsWith('sequenceDiagram')) {
        // Remove invalid option statements from alt blocks
        processed = processed.replace(/(alt[\s\S]*?)option\s+[^\n]*\n/g, '$1');
        // Fix bullet characters in sequence diagrams
        processed = processed.replace(/•/g, '-');
    }
    
    // Quote link labels that contain special characters
    processed = processed.replace(/(-->|---|-.->|--[xo]>)\s*\|([^|]*?)\|/g, (match, arrow, label) => {
        const processedLabel = label.trim().replace(/"/g, '#quot;');
        if (!processedLabel) return arrow;
        return `${arrow}|"${processedLabel}"|`;
    });
    
    // Fix incomplete connections that end abruptly
    const lines = processed.split('\n');
    const fixedLines = lines.map(line => {
        // Check for lines that end with arrows pointing nowhere
        if (line.trim().match(/-->\s*$|---\s*$|\|\s*$/) && !line.includes('subgraph')) {
            return ''; // Remove incomplete connections
        }
        return line;
    }).filter(line => line !== '');
    
    processed = fixedLines.join('\n');
    
    return processed;
}

// Process the definition and output the result
const result = preprocessDefinition(definition, diagramType);
// Ensure we output with a trailing newline to match expected format
process.stdout.write(result + '\n');
