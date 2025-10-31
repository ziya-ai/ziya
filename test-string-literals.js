#!/usr/bin/env node

/**
 * Test string literal handling to identify corruption issues
 */

const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, 'frontend/src/apis/chatApi.ts');
const content = fs.readFileSync(filePath, 'utf8');

// Test 1: Check for unmatched backticks in template literals
function testBacktickMatching() {
    console.log('\n=== Test 1: Backtick Matching ===');
    const lines = content.split('\n');
    const issues = [];
    
    for (let i = 900; i < Math.min(1000, lines.length); i++) {
        const line = lines[i];
        // Remove escaped backticks before counting
        const lineWithoutEscaped = line.replace(/\\`/g, '');
        const backticks = (lineWithoutEscaped.match(/`/g) || []).length;
        
        // Only flag if odd number of unescaped backticks AND not in a string literal context
        if (backticks % 2 !== 0 && !line.includes("'\\n```\\n\\n'") && !line.includes('"\\n```\\n\\n"')) {
            issues.push({ line: i + 1, content: line.trim() });
        }
    }
    
    if (issues.length === 0) {
        console.log('✓ No unmatched backticks found');
    } else {
        console.log('✗ Found lines with odd number of backticks:');
        issues.forEach(issue => {
            console.log(`  Line ${issue.line}: ${issue.content}`);
        });
    }
    return issues.length === 0;
}

// Test 2: Check for escaped characters in template literals
function testEscapedCharacters() {
    console.log('\n=== Test 2: Escaped Characters ===');
    const section = content.split('\n').slice(900, 1000).join('\n');
    const patterns = [
        { name: 'Escaped newlines', regex: /\\n/g },
        { name: 'Escaped backticks', regex: /\\`/g },
        { name: 'Escaped quotes', regex: /\\"/g },
        { name: 'Escaped backslashes', regex: /\\\\/g }
    ];
    
    patterns.forEach(({ name, regex }) => {
        const matches = section.match(regex);
        if (matches) {
            console.log(`✓ Found ${matches.length} ${name}`);
        }
    });
    return true;
}

// Test 3: Check for template literal nesting
function testTemplateLiteralNesting() {
    console.log('\n=== Test 3: Template Literal Nesting ===');
    const lines = content.split('\n').slice(900, 1000);
    const issues = [];
    
    lines.forEach((line, idx) => {
        // Check for ${...} inside template literals
        if (line.includes('`') && line.includes('${')) {
            const actualLine = 901 + idx;
            // Check if there are nested template literals
            const templateVars = line.match(/\$\{[^}]*\}/g) || [];
            templateVars.forEach(tv => {
                if (tv.includes('`')) {
                    issues.push({ line: actualLine, content: line.trim(), issue: 'Nested backticks in template variable' });
                }
            });
        }
    });
    
    if (issues.length === 0) {
        console.log('✓ No problematic template literal nesting found');
    } else {
        console.log('✗ Found potential nesting issues:');
        issues.forEach(issue => {
            console.log(`  Line ${issue.line}: ${issue.issue}`);
            console.log(`    ${issue.content}`);
        });
    }
    return issues.length === 0;
}

// Test 4: Extract and validate all template literals in the section
function testExtractTemplateLiterals() {
    console.log('\n=== Test 4: Extract Template Literals ===');
    const lines = content.split('\n').slice(900, 1000);
    const templates = [];
    
    lines.forEach((line, idx) => {
        const actualLine = 901 + idx;
        // Find template literals
        const matches = line.match(/`[^`]*`/g);
        if (matches) {
            matches.forEach(match => {
                templates.push({ line: actualLine, template: match });
            });
        }
    });
    
    console.log(`Found ${templates.length} template literals`);
    
    // Check for problematic patterns
    const problematic = templates.filter(t => 
        t.template.includes('\\n\\`\\`\\`') || 
        t.template.includes('tool:${') ||
        t.template.includes('|${')
    );
    
    if (problematic.length > 0) {
        console.log('\n✗ Found potentially problematic templates:');
        problematic.forEach(p => {
            console.log(`  Line ${p.line}: ${p.template}`);
        });
    } else {
        console.log('✓ All templates look valid');
    }
    
    return problematic.length === 0;
}

// Test 5: Check for string concatenation vs template literals
function testStringConcatenation() {
    console.log('\n=== Test 5: String Concatenation Patterns ===');
    const lines = content.split('\n').slice(900, 1000);
    const concatenations = [];
    
    lines.forEach((line, idx) => {
        const actualLine = 901 + idx;
        // Look for string concatenation with +
        if (line.includes('`') && line.includes('+')) {
            concatenations.push({ line: actualLine, content: line.trim() });
        }
    });
    
    if (concatenations.length > 0) {
        console.log(`Found ${concatenations.length} lines mixing templates and concatenation:`);
        concatenations.forEach(c => {
            console.log(`  Line ${c.line}: ${c.content}`);
        });
    } else {
        console.log('✓ No mixed concatenation patterns found');
    }
    
    return true;
}

// Test 6: Validate specific problematic lines
function testSpecificLines() {
    console.log('\n=== Test 6: Specific Line Validation ===');
    const lines = content.split('\n');
    const checkLines = [951, 952, 953, 954, 958, 959, 960];
    
    checkLines.forEach(lineNum => {
        if (lineNum < lines.length) {
            const line = lines[lineNum - 1];
            console.log(`Line ${lineNum}: ${line.trim()}`);
            
            // Check for common issues
            const backticks = (line.match(/`/g) || []).length;
            const braces = (line.match(/\{/g) || []).length - (line.match(/\}/g) || []).length;
            
            if (backticks % 2 !== 0) {
                console.log(`  ⚠️  Odd number of backticks: ${backticks}`);
            }
            if (braces !== 0) {
                console.log(`  ⚠️  Unbalanced braces: ${braces > 0 ? '+' : ''}${braces}`);
            }
        }
    });
    
    return true;
}

// Run all tests
console.log('Testing string literal handling in chatApi.ts (lines 900-1000)');
console.log('='.repeat(60));

const results = [
    testBacktickMatching(),
    testEscapedCharacters(),
    testTemplateLiteralNesting(),
    testExtractTemplateLiterals(),
    testStringConcatenation(),
    testSpecificLines()
];

console.log('\n' + '='.repeat(60));
console.log(`Summary: ${results.filter(r => r).length}/${results.length} tests passed`);

if (results.every(r => r)) {
    console.log('✓ All tests passed - no obvious corruption issues found');
    process.exit(0);
} else {
    console.log('✗ Some tests failed - potential corruption issues detected');
    process.exit(1);
}
