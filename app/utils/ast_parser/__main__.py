"""
Command-line interface for Ziya AST parser.

This module provides a CLI for testing the AST parser functionality.
"""

import argparse
import os
import sys
import json
import logging

from .ziya_ast_enhancer import ZiyaASTEnhancer


def setup_logging():
    """Set up logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description='Ziya AST Parser CLI')
    
    parser.add_argument('--path', required=True, help='Path to file or directory to parse')
    parser.add_argument('--output', help='Output file for AST (default: stdout)')
    parser.add_argument('--exclude', help='Comma-separated patterns to exclude')
    parser.add_argument('--max-depth', type=int, default=15, help='Maximum directory depth')
    parser.add_argument('--summary', action='store_true', help='Generate code summaries')
    parser.add_argument('--find-refs', help='Find references to a symbol')
    parser.add_argument('--deps', action='store_true', help='Analyze dependencies')
    
    return parser.parse_args()


def main():
    """Main entry point."""
    setup_logging()
    args = parse_args()
    
    # Initialize enhancer
    enhancer = ZiyaASTEnhancer()
    
    # Process path
    path = os.path.abspath(args.path)
    exclude_patterns = args.exclude.split(',') if args.exclude else []
    
    if os.path.isdir(path):
        # Process directory
        enhancer.process_codebase(path, exclude_patterns, args.max_depth)
        
        if args.summary:
            # Generate summaries
            summaries = enhancer.generate_code_summaries()
            output_json(summaries, args.output)
        
        elif args.find_refs:
            # Find references
            references = enhancer.find_references(args.find_refs)
            output_json(references, args.output)
        
        elif args.deps:
            # Analyze dependencies
            dependencies = enhancer.analyze_dependencies()
            output_json(dependencies, args.output)
        
        else:
            # Default: output basic stats
            stats = {
                'files_processed': len(enhancer.ast_cache),
                'file_list': list(enhancer.ast_cache.keys())
            }
            output_json(stats, args.output)
    
    elif os.path.isfile(path):
        # Process single file
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            file_content = f.read()
        
        # Get parser registry
        registry = enhancer.parser_registry
        
        # Get parser for file
        parser_class = registry.get_parser(path)
        if not parser_class:
            print(f"No parser available for file: {path}")
            sys.exit(1)
        
        # Parse file
        parser = parser_class()
        native_ast = parser.parse(path, file_content)
        unified_ast = parser.to_unified_ast(native_ast, path)
        
        # Output AST
        output_json(json.loads(unified_ast.to_json()), args.output)
    
    else:
        print(f"Path not found: {path}")
        sys.exit(1)


def output_json(data, output_path=None):
    """Output JSON data to file or stdout."""
    json_str = json.dumps(data, indent=2)
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json_str)
    else:
        print(json_str)


if __name__ == '__main__':
    main()
