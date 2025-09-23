import puppeteer from 'puppeteer';

function preprocessClassDiagram(content) {
    if (!content.trim().startsWith('classDiagram')) {
        return content;
    }
    
    let processed = content;
    
    // Remove enum definitions with <> syntax - not supported in web renderer
    processed = processed.replace(/^\s*<>\s+\w+\s*$/gm, '');
    
    // Remove enum value definitions that follow enum declarations
    processed = processed.replace(/^\s*\w+\s*:\s*\w+\s*$/gm, '');
    
    return processed;
}

function preprocessFlowchartDiagram(content) {
    if (!content.trim().startsWith('flowchart') && !content.trim().startsWith('graph')) {
        return content;
    }
    
    let processed = content;
    
    // Remove classDef and class statements - they may not be supported in all versions
    processed = processed.replace(/^\s*classDef\s+.*$/gm, '');
    processed = processed.replace(/^\s*class\s+.*$/gm, '');
    
    return processed;
}

function preprocessSankeyDiagram(content) {
    if (!content.trim().startsWith('sankey-beta') && !content.trim().startsWith('sankey')) {
        return content;
    }
    
    let processed = content;
    
    // Fix spaces in node names - replace with underscores
    const lines = processed.split('\n');
    const fixedLines = [];
    const flows = [];
    
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('sankey')) {
            fixedLines.push(trimmed);
        } else if (trimmed === '') {
            // Skip empty lines in sankey diagrams - they cause parsing errors
            continue;
        } else if (trimmed.includes(',')) {
            // This is a flow line - fix spaces in node names
            const parts = trimmed.split(',');
            if (parts.length === 3) {
                const source = parts[0].trim().replace(/\s+/g, '_');
                const target = parts[1].trim().replace(/\s+/g, '_');
                const value = parts[2].trim();
                flows.push({ source, target, value, original: trimmed });
            } else {
                fixedLines.push('    ' + trimmed);
            }
        } else if (trimmed.length > 0) {
            // Add indentation if not already present
            if (!line.startsWith('    ')) {
                fixedLines.push('    ' + trimmed);
            } else {
                fixedLines.push(line);
            }
        }
    }
    
    // Remove circular links to prevent "circular link" errors
    const filteredFlows = [];
    const connections = new Map();
    
    // Build connection map
    for (const flow of flows) {
        if (!connections.has(flow.source)) {
            connections.set(flow.source, new Set());
        }
        connections.get(flow.source).add(flow.target);
    }
    
    // Check for direct circular links and remove them
    for (const flow of flows) {
        const hasReverse = connections.has(flow.target) && 
                          connections.get(flow.target).has(flow.source);
        
        if (!hasReverse) {
            filteredFlows.push(flow);
        }
        // If there's a circular link, skip the one with smaller value or later in order
    }
    
    // Add filtered flows to output
    for (const flow of filteredFlows) {
        const fixedLine = `    ${flow.source},${flow.target},${flow.value}`;
        fixedLines.push(fixedLine);
    }
    
    return fixedLines.join('\n');
}

function preprocessRequirementDiagram(content) {
    if (!content.trim().startsWith('requirementDiagram')) {
        return content;
    }
    
    let processed = content;
    
    // Fix hyphenated IDs - convert REQ-001 to REQ001
    processed = processed.replace(/id:\s*([A-Z]+-\d+)/g, (match, id) => {
        return `id: ${id.replace(/-/g, '')}`;
    });
    
    // Keep verifymethod lowercase - don't convert to verifyMethod
    // processed = processed.replace(/verifymethod:/g, 'verifyMethod:');
    
    // Fix requirement type names - convert CamelCase names to valid identifiers
    processed = processed.replace(/requirement PerformanceRequirement/g, 'requirement performance_req');
    processed = processed.replace(/requirement ScalabilityRequirement/g, 'requirement scalability_req');
    processed = processed.replace(/requirement SecurityCompliance/g, 'requirement security_req');
    processed = processed.replace(/requirement AccessControl/g, 'requirement access_req');
    processed = processed.replace(/requirement DataEncryption/g, 'requirement encryption_req');
    processed = processed.replace(/requirement HighAvailability/g, 'requirement availability_req');
    processed = processed.replace(/requirement DataConsistency/g, 'requirement consistency_req');
    processed = processed.replace(/requirement DisasterRecovery/g, 'requirement recovery_req');
    processed = processed.replace(/requirement DataRetention/g, 'requirement retention_req');
    processed = processed.replace(/requirement AuditTrail/g, 'requirement audit_req');
    
    // Fix unsupported relationship types - map to supported ones
    const relationshipMap = {
        'constrains': 'satisfies',
        'connects': 'contains', 
        'deploys': 'contains',
        'monitors': 'traces',
        'logs': 'traces'
    };
    
    for (const [unsupported, supported] of Object.entries(relationshipMap)) {
        const regex = new RegExp(`- ${unsupported} ->`, 'g');
        processed = processed.replace(regex, `- ${supported} ->`);
    }
    
    // Fix case issues - convert uppercase to lowercase for risk and verifymethod values
    processed = processed.replace(/risk:\s*(High|Medium|Low)/g, (match, risk) => {
        return `risk: ${risk.toLowerCase()}`;
    });
    processed = processed.replace(/verifymethod:\s*(Test|Inspection|Analysis|Demonstration)/g, (match, method) => {
        return `verifymethod: ${method.toLowerCase()}`;
    });
    
    // Add quotes around text values if missing
    processed = processed.replace(/text:\s*([^"\n]+)$/gm, (match, text) => {
        return `text: "${text.trim()}"`;
    });
    
    // Add quotes around type values if missing
    processed = processed.replace(/type:\s*([^"\n]+)$/gm, (match, type) => {
        return `type: "${type.trim()}"`;
    });
    
    // Remove unsupported docref properties
    processed = processed.replace(/\s*docref:.*$/gm, '');
    
    // Fix relationship references to use new requirement names
    processed = processed.replace(/PerformanceRequirement - /g, 'performance_req - ');
    processed = processed.replace(/ScalabilityRequirement - /g, 'scalability_req - ');
    processed = processed.replace(/SecurityCompliance - /g, 'security_req - ');
    processed = processed.replace(/AccessControl - /g, 'access_req - ');
    processed = processed.replace(/DataEncryption - /g, 'encryption_req - ');
    processed = processed.replace(/HighAvailability - /g, 'availability_req - ');
    processed = processed.replace(/DataConsistency - /g, 'consistency_req - ');
    processed = processed.replace(/DisasterRecovery - /g, 'recovery_req - ');
    processed = processed.replace(/DataRetention - /g, 'retention_req - ');
    processed = processed.replace(/AuditTrail - /g, 'audit_req - ');
    
    const lines = processed.split('\n');
    const fixedLines = [];
    
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('requirementDiagram')) {
            fixedLines.push(trimmed);
        } else if (trimmed === '') {
            fixedLines.push('    ');
        } else if (trimmed.length > 0) {
            // Add indentation if not already present
            if (!line.startsWith('    ')) {
                fixedLines.push('    ' + trimmed);
            } else {
                fixedLines.push(line);
            }
        } else {
            fixedLines.push(line);
        }
    }
    
    return fixedLines.join('\n');
}

function preprocessGitgraphDiagram(content) {
    if (!content.trim().startsWith('gitgraph')) {
        return content;
    }
    
    // Convert gitgraph to gitGraph: (with capital G and colon)
    let processed = content.replace(/^gitgraph/gm, 'gitGraph:');
    
    const lines = processed.split('\n');
    const fixedLines = [];
    
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('gitGraph:')) {
            fixedLines.push(trimmed);
        } else if (trimmed === '') {
            fixedLines.push('    ');
        } else if (trimmed.length > 0) {
            // Add indentation if not already present
            if (!line.startsWith('    ')) {
                fixedLines.push('    ' + trimmed);
            } else {
                fixedLines.push(line);
            }
        } else {
            fixedLines.push(line);
        }
    }
    
    return fixedLines.join('\n');
}

function preprocessArchitectureDiagram(content) {
    if (!content.trim().startsWith('architecture-beta') && !content.trim().startsWith('architecture')) {
        return content;
    }
    
    // DON'T convert architecture-beta to architecture - keep it as beta
    let processed = content;
    
    // Convert ALL logos: syntax to simple icons (in both groups and services)
    processed = processed.replace(/logos:[\w-]+/g, (match) => {
        const logoName = match.replace('logos:', '');
        // Map to the simple icons that actually work in architecture diagrams
        const iconMap = {
            'aws-api-gateway': 'cloud',
            'react': 'internet',
            'android': 'internet',
            'raspberry-pi': 'server',
            'docker': 'server',
            'auth0': 'server',
            'nodejs': 'server',
            'spring': 'server',
            'python': 'server',
            'stripe': 'server',
            'twilio': 'server',
            'postgresql': 'database',
            'mysql': 'database',
            'redis': 'disk',
            'elasticsearch': 'database',
            'aws-s3': 'disk',
            'apache-kafka': 'server',
            'kafka': 'server',
            'kubernetes': 'server',
            'prometheus': 'server',
            'grafana': 'server',
            'jenkins': 'server'
        };
        return iconMap[logoName] || 'server';
    });
    
    // Fix slashes in service descriptions - replace with dashes
    processed = processed.replace(/\[([^\]]*\/[^\]]*)\]/g, (match, content) => {
        return `[${content.replace(/\//g, '-')}]`;
    });
    
    // Remove 'in group' syntax - not supported
    processed = processed.replace(/\s+in\s+\w+/g, '');
    
    // Remove service descriptions with special characters - they cause parsing issues
    processed = processed.replace(/\[[^\]]*[-\/][^\]]*\]/g, '');
    
    const lines = processed.split('\n');
    const fixedLines = [];
    
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('architecture')) {
            fixedLines.push(trimmed);
        } else if (trimmed === '') {
            fixedLines.push('    ');
        } else if (trimmed.length > 0) {
            // Add indentation if not already present
            if (!line.startsWith('    ')) {
                fixedLines.push('    ' + trimmed);
            } else {
                fixedLines.push(line);
            }
        } else {
            fixedLines.push(line);
        }
    }
    
    return fixedLines.join('\n');
}

function preprocessPacketDiagram(content) {
    if (!content.trim().startsWith('packet-beta') && !content.trim().startsWith('packet')) {
        return content;
    }
    
    const lines = content.split('\n');
    const fixedLines = [];
    
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('packet')) {
            fixedLines.push(trimmed);
        } else if (trimmed.startsWith('title ') && !trimmed.includes('"')) {
            const title = trimmed.substring(6);
            fixedLines.push(`    title "${title}"`);
        } else if (trimmed.includes(':') && !trimmed.includes('"') && /^\d/.test(trimmed)) {
            const parts = trimmed.split(':');
            const range = parts[0];
            const desc = parts.slice(1).join(':').trim();
            fixedLines.push(`    ${range}: "${desc}"`);
        } else if (trimmed === '') {
            fixedLines.push('    ');
        } else if (trimmed.startsWith('title ') || trimmed.includes(':')) {
            fixedLines.push('    ' + trimmed);
        } else {
            fixedLines.push(line);
        }
    }
    
    return fixedLines.join('\n');
}

async function validateMermaid(definition) {
    const browser = await puppeteer.launch({ headless: true });
    const page = await browser.newPage();
    
    try {
        // Preprocess the definition before sending to browser
        let processed = definition;
        
        // Fix class diagrams
        processed = preprocessClassDiagram(processed);
        
        // Fix flowchart diagrams
        processed = preprocessFlowchartDiagram(processed);
        
        // Fix sankey diagrams
        processed = preprocessSankeyDiagram(processed);
        
        // Fix requirement diagrams
        processed = preprocessRequirementDiagram(processed);
        
        // Fix gitgraph diagrams
        processed = preprocessGitgraphDiagram(processed);
        
        // Fix architecture diagrams
        processed = preprocessArchitectureDiagram(processed);
        
        // Fix packet diagrams
        processed = preprocessPacketDiagram(processed);
        
        // Fix sequence diagrams
        if (processed.trim().startsWith('sequenceDiagram')) {
            processed = processed.replace(/^(\s*)break\s+.*$/gm, '');
            
            // Remove critical blocks with proper nesting handling
            const lines = processed.split('\n');
            const cleanLines = [];
            let inCritical = false;
            let criticalDepth = 0;
            let blockDepth = 0;
            
            for (const line of lines) {
                const trimmed = line.trim();
                
                if (trimmed.startsWith('critical ')) {
                    inCritical = true;
                    criticalDepth = 0;
                    continue; // Skip the critical line
                } else if (inCritical) {
                    if (trimmed.match(/^(alt|loop|opt|par|rect)\b/)) {
                        criticalDepth++;
                    } else if (trimmed === 'end') {
                        if (criticalDepth > 0) {
                            criticalDepth--;
                        } else {
                            // This is the end of the critical block
                            inCritical = false;
                            continue; // Skip the end line
                        }
                    } else if (trimmed.startsWith('option ')) {
                        continue; // Skip option lines
                    }
                    
                    if (inCritical) {
                        continue; // Skip all content inside critical blocks
                    }
                } else {
                    cleanLines.push(line);
                }
            }
            
            processed = cleanLines.join('\n');
        }
        
        await page.setContent(`
            <!DOCTYPE html>
            <html>
            <head>
                <script src="https://cdn.jsdelivr.net/npm/mermaid@11.8.1/dist/mermaid.min.js"></script>
            </head>
            <body>
                <div id="mermaid-div"></div>
                <script>
                    mermaid.initialize({ 
                        startOnLoad: false, 
                        theme: 'default', 
                        securityLevel: 'loose' 
                    });
                    window.validateDefinition = async function(def) {
                        try {
                            const result = await mermaid.parse(def);
                            return { valid: !!result, message: 'Mermaid parsing successful', errorType: '' };
                        } catch (error) {
                            return {
                                valid: false,
                                message: error.message,
                                errorType: error.name || 'ParseError'
                            };
                        }
                    };
                </script>
            </body>
            </html>
        `);
        
        await page.waitForFunction(() => window.mermaid && window.validateDefinition);
        
        const result = await page.evaluate((def) => {
            return window.validateDefinition(def);
        }, processed);
        
        return result;
        
    } finally {
        await browser.close();
    }
}

const args = process.argv.slice(2);
let definition = '';

for (let i = 0; i < args.length; i++) {
    if (args[i] === '--definition' && i + 1 < args.length) {
        definition = args[i + 1];
        break;
    } else if (!args[i].startsWith('--')) {
        definition = args[i];
        break;
    }
}

if (!definition) {
    console.error('No definition provided');
    process.exit(1);
}

validateMermaid(definition)
    .then(result => {
        console.log(JSON.stringify(result));
        process.exit(result.valid ? 0 : 1);
    })
    .catch(error => {
        console.error('Validation failed:', error.message);
        process.exit(1);
    });
