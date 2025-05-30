"""
HTML/CSS Parser for Ziya.

This module provides HTML and CSS parsing capabilities,
converting HTML DOM and CSS AST to Ziya's unified AST format.
"""

import os
from typing import Dict, List, Optional, Any, Tuple, Union
import re
import html.parser
import cssutils
import logging

from .registry import ASTParserPlugin
from .unified_ast import UnifiedAST, SourceLocation

# Suppress cssutils warnings
cssutils.log.setLevel(logging.CRITICAL)


class HTMLCSSParser(ASTParserPlugin):
    """Parser for HTML and CSS files."""
    
    def __init__(self):
        """Initialize the HTML/CSS parser."""
        pass
    
    @classmethod
    def get_file_extensions(cls):
        return ['.html', '.htm', '.css']
    
    def parse(self, file_path: str, file_content: str) -> Dict[str, Any]:
        """
        Parse HTML or CSS file content.
        
        Args:
            file_path: Path to the file being parsed
            file_content: Content of the file to parse
            
        Returns:
            Parsed representation of the file
        """
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        
        if ext in ['.html', '.htm']:
            parser = HTMLParser(file_path)
            parser.feed(file_content)
            return {
                'type': 'html',
                'dom': parser.get_dom(),
                'embedded_css': parser.get_embedded_css(),
                'embedded_js': parser.get_embedded_js()
            }
        elif ext == '.css':
            return {
                'type': 'css',
                'stylesheet': self._parse_css(file_content)
            }
        else:
            raise ValueError(f"Unsupported file extension: {ext}")
    
    def _parse_css(self, content: str) -> Dict[str, Any]:
        """
        Parse CSS content.
        
        Args:
            content: CSS content to parse
            
        Returns:
            Parsed CSS representation
        """
        stylesheet = cssutils.parseString(content)
        result = {
            'rules': []
        }
        
        for rule in stylesheet:
            if rule.type == rule.STYLE_RULE:
                style_rule = {
                    'type': 'style',
                    'selector': rule.selectorText,
                    'properties': []
                }
                
                for prop in rule.style:
                    style_rule['properties'].append({
                        'name': prop.name,
                        'value': prop.value,
                        'priority': prop.priority
                    })
                
                result['rules'].append(style_rule)
            
            elif rule.type == rule.MEDIA_RULE:
                media_rule = {
                    'type': 'media',
                    'media': rule.media.mediaText,
                    'rules': []
                }
                
                for subrule in rule:
                    if subrule.type == subrule.STYLE_RULE:
                        style_rule = {
                            'type': 'style',
                            'selector': subrule.selectorText,
                            'properties': []
                        }
                        
                        for prop in subrule.style:
                            style_rule['properties'].append({
                                'name': prop.name,
                                'value': prop.value,
                                'priority': prop.priority
                            })
                        
                        media_rule['rules'].append(style_rule)
                
                result['rules'].append(media_rule)
            
            elif rule.type == rule.IMPORT_RULE:
                result['rules'].append({
                    'type': 'import',
                    'href': rule.href,
                    'media': rule.media.mediaText if rule.media else ''
                })
        
        return result
    
    def to_unified_ast(self, native_ast: Dict[str, Any], file_path: str) -> UnifiedAST:
        """
        Convert HTML/CSS AST to unified AST format.
        
        Args:
            native_ast: Native AST from the parser
            file_path: Path to the file that was parsed
            
        Returns:
            UnifiedAST representation
        """
        if native_ast['type'] == 'html':
            converter = HTMLConverter(file_path)
            unified_ast = converter.convert(native_ast['dom'])
            
            # Process embedded CSS if present
            if native_ast['embedded_css']:
                for css_content in native_ast['embedded_css']:
                    css_ast = self._parse_css(css_content)
                    css_converter = CSSConverter(file_path)
                    css_unified_ast = css_converter.convert(css_ast)
                    unified_ast.merge(css_unified_ast)
            
            return unified_ast
        
        elif native_ast['type'] == 'css':
            converter = CSSConverter(file_path)
            return converter.convert(native_ast['stylesheet'])
        
        else:
            raise ValueError(f"Unsupported AST type: {native_ast['type']}")


class HTMLParser(html.parser.HTMLParser):
    """Custom HTML parser to build a DOM representation."""
    
    def __init__(self, file_path: str):
        """
        Initialize the HTML parser.
        
        Args:
            file_path: Path to the file being parsed
        """
        super().__init__()
        self.file_path = file_path
        self.dom = {
            'type': 'document',
            'children': []
        }
        self.stack = [self.dom]
        self.line_offsets = None
        self.embedded_css = []
        self.embedded_js = []
        self.in_style = False
        self.in_script = False
        self.current_style = ""
        self.current_script = ""
    
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """
        Handle start tag.
        
        Args:
            tag: Tag name
            attrs: List of attribute tuples
        """
        if tag == 'style':
            self.in_style = True
            self.current_style = ""
        
        elif tag == 'script':
            self.in_script = True
            self.current_script = ""
        
        element = {
            'type': 'element',
            'tag': tag,
            'attributes': dict(attrs),
            'children': [],
            'line': self.getpos()[0],
            'column': self.getpos()[1]
        }
        
        self.stack[-1]['children'].append(element)
        self.stack.append(element)
    
    def handle_endtag(self, tag: str) -> None:
        """
        Handle end tag.
        
        Args:
            tag: Tag name
        """
        if tag == 'style' and self.in_style:
            self.in_style = False
            self.embedded_css.append(self.current_style)
        
        elif tag == 'script' and self.in_script:
            self.in_script = False
            self.embedded_js.append(self.current_script)
        
        # Pop elements from stack until we find matching tag
        while len(self.stack) > 1 and self.stack[-1]['tag'] != tag:
            self.stack.pop()
        
        if len(self.stack) > 1:
            self.stack.pop()
    
    def handle_data(self, data: str) -> None:
        """
        Handle text data.
        
        Args:
            data: Text data
        """
        if self.in_style:
            self.current_style += data
            return
        
        if self.in_script:
            self.current_script += data
            return
        
        if data.strip():
            text_node = {
                'type': 'text',
                'content': data,
                'line': self.getpos()[0],
                'column': self.getpos()[1]
            }
            self.stack[-1]['children'].append(text_node)
    
    def get_dom(self) -> Dict[str, Any]:
        """
        Get the parsed DOM.
        
        Returns:
            DOM representation
        """
        return self.dom
    
    def get_embedded_css(self) -> List[str]:
        """
        Get embedded CSS content.
        
        Returns:
            List of CSS content strings
        """
        return self.embedded_css
    
    def get_embedded_js(self) -> List[str]:
        """
        Get embedded JavaScript content.
        
        Returns:
            List of JavaScript content strings
        """
        return self.embedded_js


class HTMLConverter:
    """Converter from HTML DOM to unified AST."""
    
    def __init__(self, file_path: str):
        """
        Initialize the converter.
        
        Args:
            file_path: Path to the file being converted
        """
        self.file_path = file_path
        self.unified_ast = UnifiedAST()
        self.unified_ast.set_metadata('language', 'html')
        self.unified_ast.set_metadata('file_path', file_path)
        
        # Keep track of element IDs
        self.element_ids = {}
    
    def convert(self, dom: Dict[str, Any]) -> UnifiedAST:
        """
        Convert HTML DOM to unified AST.
        
        Args:
            dom: HTML DOM representation
            
        Returns:
            Unified AST
        """
        # Create document node
        doc_loc = SourceLocation(
            file_path=self.file_path,
            start_line=1,
            start_column=1,
            end_line=1000,  # Placeholder
            end_column=1
        )
        
        doc_id = self.unified_ast.add_node(
            node_type='document',
            name=os.path.basename(self.file_path),
            source_location=doc_loc,
            attributes={'doctype': 'html'}
        )
        
        # Process children
        self._process_node(dom, doc_id)
        
        return self.unified_ast
    
    def _process_node(self, node: Dict[str, Any], parent_id: str) -> Optional[str]:
        """
        Process a node in the HTML DOM.
        
        Args:
            node: HTML DOM node
            parent_id: ID of the parent node in the unified AST
            
        Returns:
            Node ID in the unified AST or None
        """
        if node['type'] == 'element':
            # Create source location
            loc = SourceLocation(
                file_path=self.file_path,
                start_line=node.get('line', 1),
                start_column=node.get('column', 1),
                end_line=node.get('line', 1) + 1,  # Approximate
                end_column=1
            )
            
            # Get element ID or class for name
            name = node['tag']
            if 'id' in node['attributes']:
                name = f"{name}#{node['attributes']['id']}"
            elif 'class' in node['attributes']:
                name = f"{name}.{node['attributes']['class'].split()[0]}"
            
            # Create element node
            node_id = self.unified_ast.add_node(
                node_type='element',
                name=name,
                source_location=loc,
                attributes={
                    'tag': node['tag'],
                    'attributes': node['attributes']
                }
            )
            
            # Add to parent
            self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Store element ID for references
            if 'id' in node['attributes']:
                self.element_ids[node['attributes']['id']] = node_id
            
            # Process children
            for child in node.get('children', []):
                self._process_node(child, node_id)
            
            return node_id
            
        elif node['type'] == 'text':
            # Create source location
            loc = SourceLocation(
                file_path=self.file_path,
                start_line=node.get('line', 1),
                start_column=node.get('column', 1),
                end_line=node.get('line', 1),
                end_column=node.get('column', 1) + len(node['content'])
            )
            
            # Create text node
            node_id = self.unified_ast.add_node(
                node_type='text',
                name='text',
                source_location=loc,
                attributes={'content': node['content']}
            )
            
            # Add to parent
            self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            return node_id
            
        elif node['type'] == 'document':
            # Process children
            for child in node.get('children', []):
                self._process_node(child, parent_id)
            
            return None
        
        return None


class CSSConverter:
    """Converter from CSS AST to unified AST."""
    
    def __init__(self, file_path: str):
        """
        Initialize the converter.
        
        Args:
            file_path: Path to the file being converted
        """
        self.file_path = file_path
        self.unified_ast = UnifiedAST()
        self.unified_ast.set_metadata('language', 'css')
        self.unified_ast.set_metadata('file_path', file_path)
    
    def convert(self, stylesheet: Dict[str, Any]) -> UnifiedAST:
        """
        Convert CSS stylesheet to unified AST.
        
        Args:
            stylesheet: CSS stylesheet representation
            
        Returns:
            Unified AST
        """
        # Create stylesheet node
        sheet_loc = SourceLocation(
            file_path=self.file_path,
            start_line=1,
            start_column=1,
            end_line=1000,  # Placeholder
            end_column=1
        )
        
        sheet_id = self.unified_ast.add_node(
            node_type='stylesheet',
            name=os.path.basename(self.file_path),
            source_location=sheet_loc,
            attributes={}
        )
        
        # Process rules
        for i, rule in enumerate(stylesheet.get('rules', [])):
            self._process_rule(rule, sheet_id, i + 1)
        
        return self.unified_ast
    
    def _process_rule(self, rule: Dict[str, Any], parent_id: str, line_offset: int) -> Optional[str]:
        """
        Process a CSS rule.
        
        Args:
            rule: CSS rule
            parent_id: ID of the parent node in the unified AST
            line_offset: Approximate line offset for the rule
            
        Returns:
            Node ID in the unified AST or None
        """
        if rule['type'] == 'style':
            # Create source location (approximate)
            loc = SourceLocation(
                file_path=self.file_path,
                start_line=line_offset,
                start_column=1,
                end_line=line_offset + 1,
                end_column=1
            )
            
            # Create rule node
            node_id = self.unified_ast.add_node(
                node_type='rule',
                name=rule['selector'],
                source_location=loc,
                attributes={
                    'selector': rule['selector'],
                    'properties': rule['properties']
                }
            )
            
            # Add to parent
            self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            return node_id
            
        elif rule['type'] == 'media':
            # Create source location (approximate)
            loc = SourceLocation(
                file_path=self.file_path,
                start_line=line_offset,
                start_column=1,
                end_line=line_offset + len(rule.get('rules', [])) + 1,
                end_column=1
            )
            
            # Create media rule node
            node_id = self.unified_ast.add_node(
                node_type='media',
                name=f"@media {rule['media']}",
                source_location=loc,
                attributes={'media': rule['media']}
            )
            
            # Add to parent
            self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            # Process child rules
            for i, subrule in enumerate(rule.get('rules', [])):
                self._process_rule(subrule, node_id, line_offset + i + 1)
            
            return node_id
            
        elif rule['type'] == 'import':
            # Create source location (approximate)
            loc = SourceLocation(
                file_path=self.file_path,
                start_line=line_offset,
                start_column=1,
                end_line=line_offset,
                end_column=50  # Approximate
            )
            
            # Create import rule node
            node_id = self.unified_ast.add_node(
                node_type='import',
                name=f"@import {rule['href']}",
                source_location=loc,
                attributes={
                    'href': rule['href'],
                    'media': rule['media']
                }
            )
            
            # Add to parent
            self.unified_ast.add_edge(parent_id, node_id, 'contains')
            
            return node_id
        
        return None
