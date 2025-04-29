"""
TypeScript AST Parser for Ziya.

This module provides TypeScript-specific AST parsing capabilities,
converting TypeScript's AST to Ziya's unified AST format.
"""

import json
import subprocess
import os
import tempfile
from typing import Dict, List, Optional, Any, Tuple

from .registry import ASTParserPlugin
from .unified_ast import UnifiedAST, SourceLocation


class TypeScriptASTParser(ASTParserPlugin):
    """Parser for TypeScript files."""
    
    def __init__(self):
        """Initialize the TypeScript parser."""
        super().__init__(file_extensions=['.ts', '.tsx', '.js', '.jsx'])
        self._ensure_parser_script()
    
    def _ensure_parser_script(self):
        """Ensure the TypeScript parser script exists."""
        # Create a directory for the parser script if it doesn't exist
        parser_dir = os.path.join(os.path.dirname(__file__), 'ts_parser')
        os.makedirs(parser_dir, exist_ok=True)
        
        # Create the parser script
        parser_script_path = os.path.join(parser_dir, 'parse_typescript.js')
        if not os.path.exists(parser_script_path):
            with open(parser_script_path, 'w') as f:
                f.write("""
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
                """)
        
        # Create package.json for the parser
        package_json_path = os.path.join(parser_dir, 'package.json')
        if not os.path.exists(package_json_path):
            with open(package_json_path, 'w') as f:
                f.write("""
{
  "name": "ziya-typescript-parser",
  "version": "1.0.0",
  "description": "TypeScript parser for Ziya",
  "main": "parse_typescript.js",
  "dependencies": {
    "typescript": "^4.9.5"
  }
}
                """)
    
    def parse(self, file_path: str, file_content: str) -> Dict[str, Any]:
        """
        Parse TypeScript file content and return native AST.
        
        Args:
            file_path: Path to the file being parsed
            file_content: Content of the file to parse
            
        Returns:
            TypeScript AST as a dictionary
        """
        # Create a temporary file for the content
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file_path)[1], mode='w', delete=False) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        
        try:
            # Run the TypeScript parser script
            parser_script_path = os.path.join(os.path.dirname(__file__), 'ts_parser', 'parse_typescript.js')
            result = subprocess.run(
                ['node', parser_script_path, temp_file_path],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Parse the JSON output
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Error parsing TypeScript file: {e.stderr}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Error decoding TypeScript AST: {str(e)}")
        finally:
            # Clean up the temporary file
            os.unlink(temp_file_path)
    
    def to_unified_ast(self, native_ast: Dict[str, Any], file_path: str) -> UnifiedAST:
        """
        Convert TypeScript AST to unified AST format.
        
        Args:
            native_ast: TypeScript AST as a dictionary
            file_path: Path to the file that was parsed
            
        Returns:
            UnifiedAST representation
        """
        converter = TypeScriptASTConverter(file_path)
        return converter.convert(native_ast)


class TypeScriptASTConverter:
    """Converter from TypeScript AST to unified AST."""
    
    def __init__(self, file_path: str):
        """
        Initialize the converter.
        
        Args:
            file_path: Path to the file being converted
        """
        self.file_path = file_path
        self.unified_ast = UnifiedAST()
        self.unified_ast.set_metadata('language', 'typescript')
        self.unified_ast.set_metadata('file_path', file_path)
        
        # Keep track of scope stack for parent-child relationships
        self.scope_stack = []
        
        # Track definitions
        self.definitions = {}
    
    def convert(self, node: Dict[str, Any]) -> UnifiedAST:
        """
        Convert TypeScript AST to unified AST.
        
        Args:
            node: TypeScript AST node
            
        Returns:
            Unified AST
        """
        self.process_node(node)
        return self.unified_ast
    
    def process_node(self, node: Dict[str, Any], parent_id: Optional[str] = None) -> Optional[str]:
        """
        Process a node in the TypeScript AST.
        
        Args:
            node: TypeScript AST node
            parent_id: ID of the parent node in the unified AST
            
        Returns:
            Node ID in the unified AST or None
        """
        node_type = node.get('type', node.get('kind', 'unknown'))
        name = node.get('name', '')
        
        # Skip nodes without position information
        if 'pos' not in node:
            return None
        
        # Create source location
        pos = node['pos']
        source_location = SourceLocation(
            file_path=self.file_path,
            start_line=pos.get('startLine', 1),
            start_column=pos.get('startColumn', 1),
            end_line=pos.get('endLine', 1),
            end_column=pos.get('endColumn', 1)
        )
        
        # Process different node types
        if node_type == 'SourceFile':
            # Create module node
            module_name = os.path.basename(self.file_path)
            node_id = self.unified_ast.add_node(
                node_type='module',
                name=module_name,
                source_location=source_location,
                attributes={
                    'isDeclarationFile': node.get('isDeclarationFile', False),
                    'languageVariant': node.get('languageVariant', 'Standard')
                }
            )
            
            # Process children
            self.scope_stack.append(node_id)
            for child in node.get('children', []):
                self.process_node(child, node_id)
            self.scope_stack.pop()
            
            return node_id
            
        elif node_type == 'class':
            # Create class node
            attributes = {
                'extends': node.get('extends', []),
                'implements': node.get('implements', [])
            }
            
            node_id = self.unified_ast.add_node(
                node_type='class',
                name=name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Add to definitions
            if name:
                self.definitions[name] = node_id
            
            # Process children
            self.scope_stack.append(node_id)
            for child in node.get('children', []):
                self.process_node(child, node_id)
            self.scope_stack.pop()
            
            return node_id
            
        elif node_type == 'interface':
            # Create interface node
            attributes = {
                'extends': node.get('extends', [])
            }
            
            node_id = self.unified_ast.add_node(
                node_type='interface',
                name=name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Add to definitions
            if name:
                self.definitions[name] = node_id
            
            # Process children
            self.scope_stack.append(node_id)
            for child in node.get('children', []):
                self.process_node(child, node_id)
            self.scope_stack.pop()
            
            return node_id
            
        elif node_type == 'function':
            # Create function node
            attributes = {
                'parameters': node.get('parameters', []),
                'returnType': node.get('returnType')
            }
            
            node_id = self.unified_ast.add_node(
                node_type='function',
                name=name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Add to definitions
            if name:
                self.definitions[name] = node_id
            
            # Process children
            self.scope_stack.append(node_id)
            for child in node.get('children', []):
                self.process_node(child, node_id)
            self.scope_stack.pop()
            
            return node_id
            
        elif node_type == 'property':
            # Create property node
            attributes = {
                'propertyType': node.get('propertyType')
            }
            
            node_id = self.unified_ast.add_node(
                node_type='property',
                name=name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Process children
            for child in node.get('children', []):
                child_id = self.process_node(child, node_id)
                if child_id:
                    self.unified_ast.add_edge(node_id, child_id, 'contains')
            
            return node_id
            
        elif node_type == 'variable':
            # Create variable node
            attributes = {
                'variableType': node.get('variableType')
            }
            
            node_id = self.unified_ast.add_node(
                node_type='variable',
                name=name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Add to definitions
            if name:
                self.definitions[name] = node_id
            
            # Process children
            for child in node.get('children', []):
                child_id = self.process_node(child, node_id)
                if child_id:
                    self.unified_ast.add_edge(node_id, child_id, 'contains')
            
            return node_id
            
        elif node_type == 'import':
            # Create import node
            attributes = {
                'module': node.get('module'),
                'defaultImport': node.get('defaultImport'),
                'namedImports': node.get('namedImports', [])
            }
            
            # Use module name as node name if no specific name
            if not name and attributes['module']:
                name = attributes['module']
            
            node_id = self.unified_ast.add_node(
                node_type='import',
                name=name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Add default import to definitions if present
            if attributes['defaultImport']:
                self.definitions[attributes['defaultImport']] = node_id
            
            # Add named imports to definitions
            for named_import in attributes['namedImports']:
                import_name = named_import.get('as') or named_import.get('name')
                if import_name:
                    self.definitions[import_name] = node_id
            
            return node_id
            
        elif node_type == 'export':
            # Create export node
            attributes = {
                'module': node.get('module')
            }
            
            node_id = self.unified_ast.add_node(
                node_type='export',
                name=name or 'export',
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Process children
            for child in node.get('children', []):
                child_id = self.process_node(child, node_id)
                if child_id:
                    self.unified_ast.add_edge(node_id, child_id, 'contains')
            
            return node_id
            
        else:
            # Process other node types generically
            # Process children and return None for the node itself
            for child in node.get('children', []):
                self.process_node(child, parent_id)
            
            return None
