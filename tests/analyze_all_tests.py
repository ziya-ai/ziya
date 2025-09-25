#!/usr/bin/env python3
"""
Comprehensive test analysis for the Ziya tests directory.

This script analyzes all test files in the tests directory to categorize them
and identify which ones should be integrated into the backend system tests.
"""

import os
import glob
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any

def analyze_test_file(file_path: str) -> Dict[str, Any]:
    """Analyze a test file to determine its purpose, quality, and integration potential."""
    analysis = {
        'file_path': file_path,
        'file_name': os.path.basename(file_path),
        'line_count': 0,
        'has_unittest': False,
        'has_pytest': False,
        'has_docstring': False,
        'has_main': False,
        'test_methods': 0,
        'test_functions': 0,
        'imports_ziya': False,
        'imports_app': False,
        'purpose': 'unknown',
        'category': 'unknown',
        'quality_score': 0,
        'integration_potential': 'low',
        'key_functionality': [],
        'dependencies': [],
        'issues': []
    }
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            lines = content.split('\n')
            
        analysis['line_count'] = len(lines)
        
        # Basic structure analysis
        analysis['has_unittest'] = 'unittest' in content
        analysis['has_pytest'] = 'pytest' in content or '@pytest' in content
        analysis['has_docstring'] = '"""' in content[:1000]  # Check first 1000 chars
        analysis['has_main'] = 'if __name__ == "__main__"' in content
        analysis['test_methods'] = content.count('def test_')
        analysis['test_functions'] = len(re.findall(r'^def test_\w+', content, re.MULTILINE))
        
        # Import analysis
        analysis['imports_ziya'] = 'import ziya' in content or 'from ziya' in content
        analysis['imports_app'] = 'app.utils' in content or 'from app' in content
        
        # Dependency analysis
        if 'import requests' in content or 'from requests' in content:
            analysis['dependencies'].append('requests')
        if 'import boto3' in content or 'from boto3' in content:
            analysis['dependencies'].append('boto3')
        if 'import psutil' in content or 'from psutil' in content:
            analysis['dependencies'].append('psutil')
        if 'import tiktoken' in content or 'from tiktoken' in content:
            analysis['dependencies'].append('tiktoken')
        
        # Functionality analysis
        functionality_keywords = {
            'diff': ['diff', 'patch', 'hunk', 'apply'],
            'model': ['model', 'llm', 'bedrock', 'nova', 'claude'],
            'streaming': ['stream', 'sse', 'async'],
            'middleware': ['middleware', 'pipeline'],
            'directory': ['directory', 'folder', 'file'],
            'token': ['token', 'count', 'tiktoken'],
            'api': ['api', 'endpoint', 'server'],
            'integration': ['integration', 'e2e', 'end-to-end'],
            'performance': ['performance', 'benchmark', 'timeout'],
            'auth': ['auth', 'credential', 'aws']
        }
        
        content_lower = content.lower()
        for category, keywords in functionality_keywords.items():
            if any(keyword in content_lower for keyword in keywords):
                analysis['key_functionality'].append(category)
        
        # Purpose determination
        if 'regression' in content_lower:
            analysis['purpose'] = 'regression_test'
        elif 'integration' in content_lower:
            analysis['purpose'] = 'integration_test'
        elif 'performance' in content_lower or 'benchmark' in content_lower:
            analysis['purpose'] = 'performance_test'
        elif analysis['test_methods'] > 10 or analysis['test_functions'] > 10:
            analysis['purpose'] = 'comprehensive_test'
        elif analysis['test_methods'] > 0 or analysis['test_functions'] > 0:
            analysis['purpose'] = 'unit_test'
        elif 'debug' in content_lower or 'mre' in content_lower:
            analysis['purpose'] = 'debug_tool'
        else:
            analysis['purpose'] = 'script'
        
        # Category determination
        if analysis['key_functionality']:
            # Use the most prominent functionality as category
            analysis['category'] = analysis['key_functionality'][0]
        
        # Quality scoring (0-10)
        quality_score = 0
        if analysis['has_unittest'] or analysis['has_pytest']:
            quality_score += 2
        if analysis['has_docstring']:
            quality_score += 1
        if analysis['test_methods'] > 0 or analysis['test_functions'] > 0:
            quality_score += 2
        if analysis['line_count'] > 50:
            quality_score += 1
        if analysis['imports_app']:
            quality_score += 2
        if analysis['purpose'] in ['regression_test', 'integration_test', 'comprehensive_test']:
            quality_score += 2
        
        analysis['quality_score'] = min(quality_score, 10)
        
        # Integration potential
        if analysis['quality_score'] >= 7:
            analysis['integration_potential'] = 'high'
        elif analysis['quality_score'] >= 4:
            analysis['integration_potential'] = 'medium'
        else:
            analysis['integration_potential'] = 'low'
        
        # Issue detection
        if analysis['line_count'] < 20:
            analysis['issues'].append('very_short')
        if analysis['test_methods'] == 0 and analysis['test_functions'] == 0:
            analysis['issues'].append('no_tests')
        if not analysis['imports_app'] and not analysis['imports_ziya']:
            analysis['issues'].append('no_ziya_imports')
        if 'TODO' in content or 'FIXME' in content:
            analysis['issues'].append('has_todos')
            
    except Exception as e:
        analysis['error'] = str(e)
        analysis['issues'].append('read_error')
    
    return analysis

def categorize_tests() -> Dict[str, List[Dict[str, Any]]]:
    """Categorize all test files in the tests directory."""
    categories = {
        'high_value_integration': [],
        'medium_value_integration': [],
        'diff_tests': [],
        'model_tests': [],
        'streaming_tests': [],
        'api_tests': [],
        'performance_tests': [],
        'utility_tests': [],
        'low_value': [],
        'problematic': []
    }
    
    # Find all Python files that look like tests
    test_patterns = [
        'test_*.py',
        '*_test.py',
        'run_*.py',
        'diff_*.py'
    ]
    
    all_files = []
    for pattern in test_patterns:
        all_files.extend(glob.glob(pattern))
    
    # Also check subdirectories
    for subdir in ['backend_system_tests', 'diff_test_cases', 'test_cases']:
        if os.path.exists(subdir):
            for pattern in test_patterns:
                all_files.extend(glob.glob(f"{subdir}/**/{pattern}", recursive=True))
    
    # Remove duplicates
    all_files = list(set(all_files))
    
    for file_path in all_files:
        if os.path.isfile(file_path):
            analysis = analyze_test_file(file_path)
            
            # Categorize based on analysis
            if analysis['integration_potential'] == 'high' and analysis['quality_score'] >= 7:
                categories['high_value_integration'].append(analysis)
            elif analysis['integration_potential'] == 'medium' and analysis['quality_score'] >= 4:
                categories['medium_value_integration'].append(analysis)
            elif 'diff' in analysis['key_functionality']:
                categories['diff_tests'].append(analysis)
            elif 'model' in analysis['key_functionality']:
                categories['model_tests'].append(analysis)
            elif 'streaming' in analysis['key_functionality']:
                categories['streaming_tests'].append(analysis)
            elif 'api' in analysis['key_functionality']:
                categories['api_tests'].append(analysis)
            elif 'performance' in analysis['key_functionality']:
                categories['performance_tests'].append(analysis)
            elif analysis['purpose'] in ['unit_test', 'integration_test']:
                categories['utility_tests'].append(analysis)
            elif len(analysis['issues']) > 2:
                categories['problematic'].append(analysis)
            else:
                categories['low_value'].append(analysis)
    
    return categories

def print_analysis_report():
    """Print a comprehensive analysis report."""
    print("=" * 80)
    print("ZIYA TESTS DIRECTORY COMPREHENSIVE ANALYSIS")
    print("=" * 80)
    
    categories = categorize_tests()
    
    # Summary statistics
    total_files = sum(len(tests) for tests in categories.values())
    print(f"\n📊 SUMMARY STATISTICS")
    print(f"Total test files analyzed: {total_files}")
    
    for category, tests in categories.items():
        if tests:
            print(f"{category.replace('_', ' ').title()}: {len(tests)}")
    
    # High value integration candidates
    print(f"\n🌟 HIGH VALUE INTEGRATION CANDIDATES ({len(categories['high_value_integration'])})")
    print("These tests should definitely be integrated into backend_system_tests:")
    
    for test in sorted(categories['high_value_integration'], key=lambda x: x['quality_score'], reverse=True):
        print(f"  ⭐ {test['file_name']}")
        print(f"     Quality: {test['quality_score']}/10, Purpose: {test['purpose']}")
        print(f"     Functionality: {', '.join(test['key_functionality'])}")
        print(f"     Tests: {test['test_methods']} methods, {test['line_count']} lines")
        if test['dependencies']:
            print(f"     Dependencies: {', '.join(test['dependencies'])}")
        print()
    
    # Medium value integration candidates
    print(f"\n🔍 MEDIUM VALUE INTEGRATION CANDIDATES ({len(categories['medium_value_integration'])})")
    print("These tests could be valuable with some cleanup:")
    
    for test in sorted(categories['medium_value_integration'], key=lambda x: x['quality_score'], reverse=True):
        print(f"  📋 {test['file_name']}")
        print(f"     Quality: {test['quality_score']}/10, Purpose: {test['purpose']}")
        print(f"     Functionality: {', '.join(test['key_functionality'])}")
        print(f"     Issues: {', '.join(test['issues']) if test['issues'] else 'None'}")
        print()
    
    # Specialized test categories
    specialized_categories = ['diff_tests', 'model_tests', 'streaming_tests', 'api_tests', 'performance_tests']
    
    for category in specialized_categories:
        tests = categories[category]
        if tests:
            print(f"\n🔧 {category.replace('_', ' ').upper()} ({len(tests)})")
            print(f"Specialized tests for {category.replace('_', ' ')}:")
            
            for test in sorted(tests, key=lambda x: x['quality_score'], reverse=True)[:10]:  # Show top 10
                print(f"  🧪 {test['file_name']} (Quality: {test['quality_score']}/10)")
            
            if len(tests) > 10:
                print(f"  ... and {len(tests) - 10} more")
            print()
    
    # Problematic tests
    if categories['problematic']:
        print(f"\n⚠️  PROBLEMATIC TESTS ({len(categories['problematic'])})")
        print("These tests have issues that need attention:")
        
        for test in categories['problematic']:
            print(f"  ⚠️  {test['file_name']}")
            print(f"     Issues: {', '.join(test['issues'])}")
            print(f"     Quality: {test['quality_score']}/10")
            print()
    
    # Integration recommendations
    print(f"\n💡 INTEGRATION RECOMMENDATIONS")
    
    high_value_count = len(categories['high_value_integration'])
    medium_value_count = len(categories['medium_value_integration'])
    diff_count = len(categories['diff_tests'])
    
    print(f"1. IMMEDIATE INTEGRATION ({high_value_count} tests):")
    print(f"   Move high-value tests to appropriate backend_system_tests/ categories")
    
    print(f"2. DIFF TESTS INTEGRATION ({diff_count} tests):")
    print(f"   Create backend_system_tests/diff/ and integrate diff-related tests")
    
    print(f"3. MEDIUM VALUE REVIEW ({medium_value_count} tests):")
    print(f"   Review and clean up medium-value tests before integration")
    
    print(f"4. SPECIALIZED CATEGORIES:")
    for category in specialized_categories:
        count = len(categories[category])
        if count > 0:
            print(f"   {category}: {count} tests - consider dedicated category")
    
    # Generate integration script
    generate_integration_script(categories)

def generate_integration_script(categories: Dict[str, List[Dict[str, Any]]]):
    """Generate a script to help with test integration."""
    
    script_content = """#!/bin/bash
# Generated test integration script for Ziya
# Review this script before running!

echo "Ziya Test Integration Script"
echo "============================"
echo "This script will organize tests into backend_system_tests categories."
echo "Please review each action before proceeding."
echo ""

# Create additional categories if needed
mkdir -p backend_system_tests/diff
mkdir -p backend_system_tests/model
mkdir -p backend_system_tests/streaming
mkdir -p backend_system_tests/auth

"""
    
    # High value integrations
    if categories['high_value_integration']:
        script_content += "# High value integrations - move immediately\n"
        script_content += "echo \"Moving high-value tests...\"\n"
        
        for test in categories['high_value_integration']:
            # Determine target category
            if 'diff' in test['key_functionality']:
                target = 'backend_system_tests/diff/'
            elif 'model' in test['key_functionality']:
                target = 'backend_system_tests/model/'
            elif 'api' in test['key_functionality'] or 'integration' in test['key_functionality']:
                target = 'backend_system_tests/integration/'
            elif 'performance' in test['key_functionality']:
                target = 'backend_system_tests/performance/'
            elif 'token' in test['key_functionality'] or 'directory' in test['key_functionality']:
                target = 'backend_system_tests/core/'
            else:
                target = 'backend_system_tests/integration/'
            
            script_content += f"mv '{test['file_name']}' {target}  # Quality: {test['quality_score']}/10\n"
        
        script_content += "\n"
    
    # Diff tests
    if categories['diff_tests']:
        script_content += "# Diff tests - move to diff category\n"
        script_content += "echo \"Moving diff tests...\"\n"
        
        for test in categories['diff_tests'][:10]:  # Top 10 diff tests
            script_content += f"mv '{test['file_name']}' backend_system_tests/diff/  # Quality: {test['quality_score']}/10\n"
        
        script_content += "\n"
    
    script_content += """
echo "Integration complete!"
echo "Next steps:"
echo "1. Review moved tests for compatibility"
echo "2. Update import paths if needed"
echo "3. Run test suite: python run_backend_system_tests.py"
echo "4. Update documentation"
"""
    
    with open('integrate_tests.sh', 'w') as f:
        f.write(script_content)
    
    print(f"📝 Generated integration script: integrate_tests.sh")

def main():
    """Main function."""
    print_analysis_report()

if __name__ == "__main__":
    main()
