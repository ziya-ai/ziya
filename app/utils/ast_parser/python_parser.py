"""
Python AST Parser for Ziya.

This module provides Python-specific AST parsing capabilities,
converting Python's native AST to Ziya's unified AST format.
"""

import ast
from typing import Dict, List, Optional, Any, Tuple, Set
import os

from .registry import ASTParserPlugin
from .unified_ast import UnifiedAST, SourceLocation


class PythonASTParser(ASTParserPlugin):
    """Parser for Python files."""
    
    def __init__(self):
        """Initialize the Python parser."""
        pass
    
    @classmethod
    def get_file_extensions(cls):
        return ['.py', '.pyi']
    
    def parse(self, file_path: str, file_content: str) -> ast.AST:
        """
        Parse Python file content and return native AST.
        
        Args:
            file_path: Path to the file being parsed
            file_content: Content of the file to parse
            
        Returns:
            Python native AST
        """
        return ast.parse(file_content, filename=file_path)
    
    def to_unified_ast(self, native_ast: ast.AST, file_path: str) -> UnifiedAST:
        """
        Convert Python AST to unified AST format.
        
        Args:
            native_ast: Python native AST
            file_path: Path to the file that was parsed
            
        Returns:
            UnifiedAST representation
        """
        converter = PythonASTConverter(file_path)
        return converter.convert(native_ast)


class PythonASTConverter:
    """Converter from Python AST to unified AST."""
    
    def __init__(self, file_path: str):
        """
        Initialize the converter.
        
        Args:
            file_path: Path to the file being converted
        """
        self.file_path = file_path
        self.unified_ast = UnifiedAST()
        self.unified_ast.set_metadata('language', 'python')
        self.unified_ast.set_metadata('file_path', file_path)
        
        # Keep track of scope stack for parent-child relationships
        self.scope_stack = []
        
        # Track variable definitions and references
        self.definitions = {}
        self.current_function = None
    
    def convert(self, node: ast.AST) -> UnifiedAST:
        """
        Convert Python AST to unified AST.
        
        Args:
            node: Python AST node
            
        Returns:
            Unified AST
        """
        self.visit(node)
        return self.unified_ast
    
    def visit(self, node: ast.AST) -> Optional[str]:
        """
        Visit a node in the Python AST.
        
        Args:
            node: Python AST node
            
        Returns:
            Node ID in the unified AST or None
        """
        method_name = f"visit_{type(node).__name__}"
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)
    
    def generic_visit(self, node: ast.AST) -> Optional[str]:
        """
        Generic visitor for AST nodes.
        
        Args:
            node: Python AST node
            
        Returns:
            None
        """
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        return None
    
    def _get_source_location(self, node: ast.AST) -> SourceLocation:
        """
        Get source location for a node.
        
        Args:
            node: Python AST node
            
        Returns:
            SourceLocation object
        """
        # Python AST line numbers are 1-based
        start_line = getattr(node, 'lineno', 1)
        start_col = getattr(node, 'col_offset', 0) + 1  # Convert to 1-based
        
        # End line and column might not be available in all Python versions
        end_line = getattr(node, 'end_lineno', start_line)
        end_col = getattr(node, 'end_col_offset', start_col)
        if hasattr(node, 'end_col_offset'):
            end_col += 1  # Convert to 1-based and make exclusive
        
        return SourceLocation(
            file_path=self.file_path,
            start_line=start_line,
            start_column=start_col,
            end_line=end_line,
            end_column=end_col
        )
    
    def _add_to_current_scope(self, node_id: str) -> None:
        """
        Add a node to the current scope.
        
        Args:
            node_id: ID of the node to add
        """
        if self.scope_stack:
            parent_id = self.scope_stack[-1]
            self.unified_ast.add_edge(parent_id, node_id, "contains")
    
    def visit_Module(self, node: ast.Module) -> str:
        """
        Visit a module node.
        
        Args:
            node: Module node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        module_name = os.path.basename(self.file_path)
        if module_name.endswith('.py'):
            module_name = module_name[:-3]
        
        node_id = self.unified_ast.add_node(
            node_type="module",
            name=module_name,
            source_location=loc,
            attributes={'docstring': ast.get_docstring(node)}
        )
        
        self.scope_stack.append(node_id)
        for child in node.body:
            self.visit(child)
        self.scope_stack.pop()
        
        return node_id
    
    def visit_ClassDef(self, node: ast.ClassDef) -> str:
        """
        Visit a class definition.
        
        Args:
            node: ClassDef node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        # Extract base classes
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(self._get_attribute_name(base))
        
        # Extract decorators
        decorators = []
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                decorators.append(decorator.id)
            elif isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                decorators.append(decorator.func.id)
        
        node_id = self.unified_ast.add_node(
            node_type="class",
            name=node.name,
            source_location=loc,
            attributes={
                'bases': bases,
                'decorators': decorators,
                'docstring': ast.get_docstring(node)
            }
        )
        
        # Add to current scope
        self._add_to_current_scope(node_id)
        
        # Add to definitions
        self.definitions[node.name] = node_id
        
        # Process class body
        self.scope_stack.append(node_id)
        for child in node.body:
            self.visit(child)
        self.scope_stack.pop()
        
        return node_id
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> str:
        """
        Visit a function definition.
        
        Args:
            node: FunctionDef node
            
        Returns:
            Node ID in the unified AST
        """
        return self._process_function(node)
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> str:
        """
        Visit an async function definition.
        
        Args:
            node: AsyncFunctionDef node
            
        Returns:
            Node ID in the unified AST
        """
        return self._process_function(node, is_async=True)
    
    def _process_function(self, node: ast.FunctionDef, is_async: bool = False) -> str:
        """
        Process a function definition.
        
        Args:
            node: FunctionDef or AsyncFunctionDef node
            is_async: Whether the function is async
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        # Extract parameters
        params = []
        for arg in node.args.args:
            param = {'name': arg.arg}
            if arg.annotation:
                if isinstance(arg.annotation, ast.Name):
                    param['type'] = arg.annotation.id
                elif isinstance(arg.annotation, ast.Attribute):
                    param['type'] = self._get_attribute_name(arg.annotation)
            params.append(param)
        
        # Extract return type
        return_type = None
        if node.returns:
            if isinstance(node.returns, ast.Name):
                return_type = node.returns.id
            elif isinstance(node.returns, ast.Attribute):
                return_type = self._get_attribute_name(node.returns)
        
        # Extract decorators
        decorators = []
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                decorators.append(decorator.id)
            elif isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                decorators.append(decorator.func.id)
        
        node_id = self.unified_ast.add_node(
            node_type="function",
            name=node.name,
            source_location=loc,
            attributes={
                'params': params,
                'return_type': return_type,
                'decorators': decorators,
                'is_async': is_async,
                'docstring': ast.get_docstring(node)
            }
        )
        
        # Add to current scope
        self._add_to_current_scope(node_id)
        
        # Add to definitions
        self.definitions[node.name] = node_id
        
        # Save previous function and set current
        prev_function = self.current_function
        self.current_function = node_id
        
        # Process function body
        self.scope_stack.append(node_id)
        for child in node.body:
            self.visit(child)
        self.scope_stack.pop()
        
        # Restore previous function
        self.current_function = prev_function
        
        return node_id
    
    def visit_Assign(self, node: ast.Assign) -> str:
        """
        Visit an assignment.
        
        Args:
            node: Assign node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        # Process each target
        for target in node.targets:
            if isinstance(target, ast.Name):
                var_name = target.id
                node_id = self.unified_ast.add_node(
                    node_type="variable",
                    name=var_name,
                    source_location=loc,
                    attributes={'kind': 'assignment'}
                )
                
                # Add to current scope
                self._add_to_current_scope(node_id)
                
                # Add to definitions
                self.definitions[var_name] = node_id
                
                # Process the value
                value_id = self.visit(node.value)
                if value_id:
                    self.unified_ast.add_edge(node_id, value_id, "assigned_from")
        
        return None
    
    def visit_AnnAssign(self, node: ast.AnnAssign) -> str:
        """
        Visit an annotated assignment.
        
        Args:
            node: AnnAssign node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
            
            # Get type annotation
            type_annotation = None
            if isinstance(node.annotation, ast.Name):
                type_annotation = node.annotation.id
            elif isinstance(node.annotation, ast.Attribute):
                type_annotation = self._get_attribute_name(node.annotation)
            
            node_id = self.unified_ast.add_node(
                node_type="variable",
                name=var_name,
                source_location=loc,
                attributes={
                    'kind': 'annotated_assignment',
                    'type': type_annotation
                }
            )
            
            # Add to current scope
            self._add_to_current_scope(node_id)
            
            # Add to definitions
            self.definitions[var_name] = node_id
            
            # Process the value if present
            if node.value:
                value_id = self.visit(node.value)
                if value_id:
                    self.unified_ast.add_edge(node_id, value_id, "assigned_from")
        
        return None
    
    def visit_Import(self, node: ast.Import) -> str:
        """
        Visit an import statement.
        
        Args:
            node: Import node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        for name in node.names:
            import_name = name.name
            as_name = name.asname or import_name
            
            node_id = self.unified_ast.add_node(
                node_type="import",
                name=as_name,
                source_location=loc,
                attributes={
                    'module': import_name,
                    'alias': name.asname
                }
            )
            
            # Add to current scope
            self._add_to_current_scope(node_id)
            
            # Add to definitions if it has an alias
            if name.asname:
                self.definitions[name.asname] = node_id
        
        return None
    
    def visit_ImportFrom(self, node: ast.ImportFrom) -> str:
        """
        Visit an import from statement.
        
        Args:
            node: ImportFrom node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        for name in node.names:
            import_name = name.name
            as_name = name.asname or import_name
            
            node_id = self.unified_ast.add_node(
                node_type="import",
                name=as_name,
                source_location=loc,
                attributes={
                    'module': node.module,
                    'name': import_name,
                    'alias': name.asname
                }
            )
            
            # Add to current scope
            self._add_to_current_scope(node_id)
            
            # Add to definitions
            self.definitions[as_name] = node_id
        
        return None
    
    def visit_Call(self, node: ast.Call) -> str:
        """
        Visit a function call.
        
        Args:
            node: Call node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        # Get function name
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = self._get_attribute_name(node.func)
        
        if func_name:
            # Create call node
            node_id = self.unified_ast.add_node(
                node_type="call",
                name=func_name,
                source_location=loc,
                attributes={'args_count': len(node.args)}
            )
            
            # Add to current scope
            self._add_to_current_scope(node_id)
            
            # Link to function definition if available
            if func_name in self.definitions:
                self.unified_ast.add_edge(node_id, self.definitions[func_name], "calls")
            
            # Process arguments
            for i, arg in enumerate(node.args):
                arg_id = self.visit(arg)
                if arg_id:
                    self.unified_ast.add_edge(node_id, arg_id, "argument", {'position': i})
            
            return node_id
        
        return None
    
    def visit_Name(self, node: ast.Name) -> str:
        """
        Visit a name reference.
        
        Args:
            node: Name node
            
        Returns:
            Node ID in the unified AST
        """
        loc = self._get_source_location(node)
        
        # Create reference node
        node_id = self.unified_ast.add_node(
            node_type="reference",
            name=node.id,
            source_location=loc,
            attributes={'context': type(node.ctx).__name__}
        )
        
        # Add to current scope
        self._add_to_current_scope(node_id)
        
        # Link to definition if available
        if node.id in self.definitions:
            self.unified_ast.add_edge(node_id, self.definitions[node.id], "references")
        
        return node_id
    
    def _get_attribute_name(self, node: ast.Attribute) -> str:
        """
        Get the full name of an attribute.
        
        Args:
            node: Attribute node
            
        Returns:
            Full attribute name (e.g., "module.submodule.name")
        """
        parts = [node.attr]
        value = node.value
        
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        
        if isinstance(value, ast.Name):
            parts.append(value.id)
        
        return '.'.join(reversed(parts))
