
const ts = require('typescript');
const fs = require('fs');

// Get file path from command line arguments
const filePath = process.argv[2];
const fileContent = process.argv[3] || fs.readFileSync(filePath, 'utf8');

// Parse the TypeScript file
const sourceFile = ts.createSourceFile(
    filePath,
    fileContent,
    ts.ScriptTarget.Latest,
    /*setParentNodes*/ true
);

// Helper function to get text position
function getTextPosition(node) {
    const { line, character } = sourceFile.getLineAndCharacterOfPosition(node.getStart());
    const endPosition = sourceFile.getLineAndCharacterOfPosition(node.getEnd());
    return {
        startLine: line + 1,  // Convert to 1-based
        startColumn: character + 1,  // Convert to 1-based
        endLine: endPosition.line + 1,
        endColumn: endPosition.character + 1
    };
}

// Helper function to convert node to JSON
function nodeToJson(node) {
    const result = {
        kind: ts.SyntaxKind[node.kind],
        pos: getTextPosition(node),
        children: []
    };
    
    // Add name if available
    if (node.name && node.name.text) {
        result.name = node.name.text;
    }
    
    // Add specific properties based on node kind
    switch (node.kind) {
        case ts.SyntaxKind.ClassDeclaration:
            result.type = 'class';
            if (node.heritageClauses) {
                result.extends = [];
                result.implements = [];
                node.heritageClauses.forEach(clause => {
                    if (clause.token === ts.SyntaxKind.ExtendsKeyword) {
                        clause.types.forEach(type => {
                            result.extends.push(type.expression.text);
                        });
                    } else if (clause.token === ts.SyntaxKind.ImplementsKeyword) {
                        clause.types.forEach(type => {
                            result.implements.push(type.expression.text);
                        });
                    }
                });
            }
            break;
            
        case ts.SyntaxKind.InterfaceDeclaration:
            result.type = 'interface';
            if (node.heritageClauses) {
                result.extends = [];
                node.heritageClauses.forEach(clause => {
                    if (clause.token === ts.SyntaxKind.ExtendsKeyword) {
                        clause.types.forEach(type => {
                            result.extends.push(type.expression.text);
                        });
                    }
                });
            }
            break;
            
        case ts.SyntaxKind.FunctionDeclaration:
        case ts.SyntaxKind.MethodDeclaration:
        case ts.SyntaxKind.Constructor:
            result.type = 'function';
            result.parameters = [];
            if (node.parameters) {
                node.parameters.forEach(param => {
                    const parameter = {
                        name: param.name.text
                    };
                    if (param.type) {
                        parameter.type = param.type.getText(sourceFile);
                    }
                    result.parameters.push(parameter);
                });
            }
            if (node.type) {
                result.returnType = node.type.getText(sourceFile);
            }
            break;
            
        case ts.SyntaxKind.PropertyDeclaration:
        case ts.SyntaxKind.PropertySignature:
            result.type = 'property';
            if (node.type) {
                result.propertyType = node.type.getText(sourceFile);
            }
            break;
            
        case ts.SyntaxKind.VariableDeclaration:
            result.type = 'variable';
            if (node.type) {
                result.variableType = node.type.getText(sourceFile);
            }
            break;
            
        case ts.SyntaxKind.ImportDeclaration:
            result.type = 'import';
            if (node.moduleSpecifier) {
                result.module = node.moduleSpecifier.text;
            }
            if (node.importClause) {
                if (node.importClause.name) {
                    result.defaultImport = node.importClause.name.text;
                }
                if (node.importClause.namedBindings) {
                    result.namedImports = [];
                    if (node.importClause.namedBindings.elements) {
                        node.importClause.namedBindings.elements.forEach(element => {
                            const namedImport = {
                                name: element.name.text
                            };
                            if (element.propertyName) {
                                namedImport.as = element.name.text;
                                namedImport.name = element.propertyName.text;
                            }
                            result.namedImports.push(namedImport);
                        });
                    }
                }
            }
            break;
            
        case ts.SyntaxKind.ExportDeclaration:
            result.type = 'export';
            if (node.moduleSpecifier) {
                result.module = node.moduleSpecifier.text;
            }
            break;
    }
    
    // Process children
    ts.forEachChild(node, child => {
        result.children.push(nodeToJson(child));
    });
    
    return result;
}

// Convert the AST to JSON
const ast = nodeToJson(sourceFile);

// Add source file information
ast.fileName = sourceFile.fileName;
ast.isDeclarationFile = sourceFile.isDeclarationFile;
ast.languageVariant = ts.LanguageVariant[sourceFile.languageVariant];

// Output the JSON
console.log(JSON.stringify(ast));
                