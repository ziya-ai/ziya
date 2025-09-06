#!/usr/bin/env python3
"""
Helper script to create new mermaid test cases
"""

import os
import json
import argparse
from pathlib import Path

def create_test_case(name: str, description: str, diagram_type: str, 
                    input_content: str, expected_content: str = None,
                    expected_to_fail: bool = False, expected_errors: list = None,
                    expected_warnings: list = None):
    """Create a new mermaid test case"""
    
    # Create test case directory
    test_dir = Path(__file__).parent / 'mermaid_test_cases' / name
    test_dir.mkdir(parents=True, exist_ok=True)
    
    # Create metadata.json
    metadata = {
        'description': description,
        'diagram_type': diagram_type,
        'expected_to_fail': expected_to_fail
    }
    
    if expected_errors:
        metadata['expected_errors'] = expected_errors
    if expected_warnings:
        metadata['expected_warnings'] = expected_warnings
    
    with open(test_dir / 'metadata.json', 'w') as f:
        json.dump(metadata, f, indent=4)
    
    # Create input.mermaid
    with open(test_dir / 'input.mermaid', 'w') as f:
        f.write(input_content)
    
    # Create expected.mermaid if provided
    if expected_content:
        with open(test_dir / 'expected.mermaid', 'w') as f:
            f.write(expected_content)
    
    print(f"Created test case: {name}")
    print(f"  Directory: {test_dir}")
    print(f"  Files created: metadata.json, input.mermaid" + (", expected.mermaid" if expected_content else ""))

def main():
    parser = argparse.ArgumentParser(description='Create a new mermaid test case')
    parser.add_argument('name', help='Test case name')
    parser.add_argument('-d', '--description', required=True, help='Test description')
    parser.add_argument('-t', '--type', required=True, 
                       choices=['flowchart', 'sequencediagram', 'classdiagram', 'statediagram', 
                               'gantt', 'pie', 'journey', 'gitgraph', 'erdiagram', 'requirementdiagram'],
                       help='Diagram type')
    parser.add_argument('-i', '--input-file', help='File containing input mermaid definition')
    parser.add_argument('-e', '--expected-file', help='File containing expected processed definition')
    parser.add_argument('--input', help='Input mermaid definition as string')
    parser.add_argument('--expected', help='Expected processed definition as string')
    parser.add_argument('--fail', action='store_true', help='Test is expected to fail validation')
    parser.add_argument('--errors', nargs='*', help='Expected error types')
    parser.add_argument('--warnings', nargs='*', help='Expected warning types')
    
    args = parser.parse_args()
    
    # Get input content
    if args.input_file:
        with open(args.input_file, 'r') as f:
            input_content = f.read()
    elif args.input:
        input_content = args.input
    else:
        print("Error: Must provide either --input or --input-file")
        return 1
    
    # Get expected content if provided
    expected_content = None
    if args.expected_file:
        with open(args.expected_file, 'r') as f:
            expected_content = f.read()
    elif args.expected:
        expected_content = args.expected
    
    # Create the test case
    create_test_case(
        name=args.name,
        description=args.description,
        diagram_type=args.type,
        input_content=input_content,
        expected_content=expected_content,
        expected_to_fail=args.fail,
        expected_errors=args.errors,
        expected_warnings=args.warnings
    )

if __name__ == '__main__':
    main()
