"""
TypeScript AST Parser for Ziya.

This module provides TypeScript-specific AST parsing capabilities,
converting TypeScript's AST to Ziya's unified AST format.
"""

import json
import subprocess
import os
import shutil
import tempfile
from typing import Dict, List, Optional, Any, Tuple
import logging

from .registry import ASTParserPlugin
from .unified_ast import UnifiedAST, SourceLocation

logger = logging.getLogger(__name__)


class TypeScriptASTParser(ASTParserPlugin):
    """Parser for TypeScript files."""
    
    def __init__(self):
        """Initialize the TypeScript parser."""
        super().__init__()
        self._ensure_parser_script()
    
    @classmethod
    def get_file_extensions(cls):
        return ['.ts', '.tsx', '.js', '.jsx']
    
    def _ensure_parser_script(self):
        """Ensure the TypeScript parser script exists."""
        # Create a directory for the parser script if it doesn't exist
        parser_dir = os.path.join(os.path.dirname(__file__), 'ts_parser')
        os.makedirs(parser_dir, exist_ok=True)
        
        # Check if Node.js is available
        if not shutil.which('node'):
            raise RuntimeError("Node.js is required for TypeScript parsing but is not installed")
        
        # Check if npm is available
        if not shutil.which('npm'):
            raise RuntimeError("npm is required for TypeScript parsing but is not installed")
        
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

    # Install TypeScript dependency if not already installed
        node_modules_path = os.path.join(parser_dir, 'node_modules')
        if not os.path.exists(node_modules_path):
            try:
                # Run npm install in the parser directory
                result = subprocess.run(
                    ['npm', 'install'],
                    cwd=parser_dir,
                    capture_output=True,
                    text=True,
                    check=True
                )
                print(f"Installed TypeScript dependencies: {result.stdout}")
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to install TypeScript dependencies: {e.stderr}")
    
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
            logger.debug(f"Parsing TypeScript file: {file_path}")
            parser_script_path = os.path.join(os.path.dirname(__file__), 'ts_parser', 'parse_typescript.js')
            result = subprocess.run(
                ['node', parser_script_path, temp_file_path],
                capture_output=True,
                text=True,
                check=True
            )
            

            # Parse the JSON output
            parsed_result = json.loads(result.stdout)
            logger.debug(f"Successfully parsed TypeScript file: {file_path}")
            
            # Debug: log the structure of the parsed result
            logger.debug(f"Parsed AST keys: {list(parsed_result.keys())}")
            logger.debug(f"Children count: {len(parsed_result.get('children', []))}")
            return parsed_result
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
        unified_ast = converter.convert(native_ast)
        logger.debug(f"TypeScript converter created {len(unified_ast.nodes)} nodes for {file_path}")
        return unified_ast

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
        logger.debug(f"After process_node call: {len(self.unified_ast.nodes)} nodes created")
        logger.debug(f"TypeScript AST conversion complete: {len(self.unified_ast.nodes)} nodes, {len(self.unified_ast.edges)} edges")
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
        try:
            return self._process_node_impl(node, parent_id)
        except Exception as e:
            logger.error(f"Exception in process_node for {node.get('kind', 'unknown')}: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    def _process_node_impl(self, node: Dict[str, Any], parent_id: Optional[str] = None) -> Optional[str]:
        node_type = node.get('type', node.get('kind', 'unknown'))
        # Use kind if type is not available (common for TypeScript AST nodes)
        if node_type == 'unknown' and 'kind' in node:
            node_type = node['kind']
        name = node.get('name', '')

        # Skip nodes without position information
        if 'pos' not in node:
            logger.debug(f"Node without position: {node_type}, processing children anyway")
            logger.debug(f"Node has 'pos' key: {'pos' in node}")

            # Special case: if this is SourceFile, we need to handle it specially
            if node_type == 'SourceFile':
                logger.debug(f"SourceFile detected without pos, continuing to process")
                # Continue to SourceFile processing below
            else:
                # For other nodes without position, just process children
                for child in node.get('children', []):
                    self.process_node(child, parent_id)
                return None
            
        # Create source location
        if 'pos' in node:
            pos = node['pos']
            source_location = SourceLocation(
                file_path=self.file_path,
                start_line=pos.get('startLine', 1),
                start_column=pos.get('startColumn', 1),
                end_line=pos.get('endLine', 1),
                end_column=pos.get('endColumn', 1)
            )
        else:
            # Default source location for nodes without position (like SourceFile)
            logger.debug(f"Using default source location for {node_type}")
            source_location = SourceLocation(
                file_path=self.file_path,
                start_line=1, start_column=1,
                end_line=1000, end_column=1  # Placeholder for whole file
            )
        
        # Process different node types
        if node_type == 'SourceFile':
            logger.debug(f"Processing SourceFile node type")
            # Create module node
            module_name = os.path.basename(self.file_path)
            node_id = self.unified_ast.add_node(
                node_type='module',
                name=module_name,
                source_location=source_location,
                attributes={
                    'isDeclarationFile': node.get('isDeclarationFile', False)
                }
            )
            
            logger.debug(f"Created SourceFile node with ID: {node_id}")
            # Process children
            logger.debug(f"SourceFile has {len(node.get('children', []))} children to process")
            self.scope_stack.append(node_id)
            for child in node.get('children', []):
                logger.debug(f"Processing SourceFile child: {child.get('kind', child.get('type', 'unknown'))}")
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

        elif node_type in ['FunctionDeclaration', 'MethodDeclaration', 'ArrowFunction']:
            # Create function node for various function types
            func_name = name or 'anonymous'
            attributes = {
                'parameters': node.get('parameters', []),
                'returnType': node.get('returnType'),
                'isArrow': node_type == 'ArrowFunction'
            }
            
            node_id = self.unified_ast.add_node(
                node_type='function',
                name=func_name,
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
            
        elif node_type in ['VariableDeclaration', 'VariableStatement']:
            # Create variable node
            var_name = name or 'variable'
            attributes = {
                'variableType': node.get('variableType'),
                'kind': node.get('kind', 'var')
            }
            
            node_id = self.unified_ast.add_node(
                node_type='variable',
                name=var_name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Add to definitions
            if name:
                self.definitions[name] = node_id
            
            return node_id
            
        elif node_type in ['JsxElement', 'JsxSelfClosingElement']:
            # Create JSX element node
            element_name = self._extract_jsx_element_name(node)
            attributes = {
                'jsx_type': node_type,
                'attributes': self._extract_jsx_attributes(node)
            }
            
            node_id = self.unified_ast.add_node(
                node_type='jsx_element',
                name=element_name,
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
            
        elif node_type in ['InterfaceDeclaration']:
            # Create interface node
            interface_name = name or 'unnamed_interface'
            attributes = {
                'extends': node.get('extends', []),
                'members': []
            }
            
            node_id = self.unified_ast.add_node(
                node_type='interface',
                name=interface_name,
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
            
        elif node_type in ['PropertySignature', 'PropertyDeclaration']:
            # Create property node
            prop_name = name or 'unnamed_property'
            attributes = {
                'propertyType': node.get('type'),
                'optional': node.get('optional', False)
            }
            
            node_id = self.unified_ast.add_node(
                node_type='property',
                name=prop_name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            return node_id
            
        elif node_type in ['InterfaceDeclaration']:
            # Create interface node
            interface_name = name or 'unnamed_interface'
            attributes = {
                'extends': node.get('extends', []),
                'members': []
            }
            
            node_id = self.unified_ast.add_node(
                node_type='interface',
                name=interface_name,
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
            
        elif node_type in ['PropertySignature', 'PropertyDeclaration']:
            # Create property node
            prop_name = name or 'unnamed_property'
            attributes = {
                'propertyType': node.get('type'),
                'optional': node.get('optional', False)
            }
            
            node_id = self.unified_ast.add_node(
                node_type='property',
                name=prop_name,
                source_location=source_location,
                attributes=attributes
            )
            
            # Add to current scope
            if parent_id:
                self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            return node_id
            
        else:
            # Process other node types generically
            # Process children and return None for the node itself
            for child in node.get('children', []):
                self.process_node(child, parent_id)
            
            # Log if we're skipping nodes that might be important
            if node.get('name') or len(node.get('children', [])) > 0:
                logger.debug(f"Skipped TypeScript node with name/children: {node_type}, name={node.get('name')}, children={len(node.get('children', []))}")

            return None

    def _extract_jsx_element_name(self, node: Dict[str, Any]) -> str:
        """Extract the element name from a JSX node."""
        # Look for opening element or self-closing element
        for child in node.get('children', []):
            if child.get('kind') in ['JsxOpeningElement', 'JsxSelfClosingElement']:
                # Find the tag name
                for grandchild in child.get('children', []):
                    if grandchild.get('kind') == 'Identifier':
                        return grandchild.get('name', 'unknown')
        
        # Fallback: look for direct identifier
        if 'name' in node:
            return node['name']
        
        return 'jsx_element'
    
    def _extract_jsx_attributes(self, node: Dict[str, Any]) -> List[str]:
        """Extract attribute names from a JSX node."""
        attributes = []
        for child in node.get('children', []):
            if child.get('kind') == 'JsxAttributes':
                for attr_child in child.get('children', []):
                    if attr_child.get('kind') == 'JsxAttribute' and 'name' in attr_child:
                        attributes.append(attr_child['name'])
        return attributes
