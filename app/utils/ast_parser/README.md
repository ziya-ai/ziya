# Ziya Language-Agnostic AST Parser

This module provides language-agnostic Abstract Syntax Tree (AST) parsing capabilities for Ziya, enabling deeper code understanding across multiple programming languages.

## Overview

The AST parser enhances Ziya's ability to understand code by:

1. Parsing source code into language-specific ASTs
2. Converting these ASTs into a unified representation
3. Providing a query engine for semantic code analysis
4. Enhancing context generation for the LLM

## Supported Languages

Currently, the following languages are supported:

1. **Python** - Using Python's built-in `ast` module
2. **TypeScript/JavaScript** - Using TypeScript compiler API
3. **HTML/CSS** - Using HTML parser and cssutils

## Architecture

The AST parser is built around these core components:

- **Parser Registry**: Manages language-specific parsers
- **Unified AST**: Common representation for all languages
- **Query Engine**: Semantic code analysis capabilities
- **Ziya Enhancer**: Integration with Ziya's context generation

## Usage

### Basic Usage

```python
from app.utils.ast_parser.integration import initialize_ast_capabilities, enhance_context

# Initialize AST capabilities for a codebase
initialize_ast_capabilities(
    codebase_path="/path/to/codebase",
    exclude_patterns=["node_modules", "*.pyc", "__pycache__"],
    max_depth=10
)

# Enhance context for a query
enhanced_context = enhance_context(
    query="How does the authentication system work?",
    file_context="/path/to/codebase/auth.py"
)
```

### Command Line Interface

The module also provides a CLI for testing:

```bash
python -m app.utils.ast_parser --path /path/to/codebase --summary --output summary.json
```

## Integration with Ziya

The AST parser integrates with Ziya by enhancing the context provided to the LLM:

1. When a user asks a question about code, Ziya loads the codebase
2. The AST parser processes relevant files to build semantic understanding
3. The query engine extracts relevant code structures and relationships
4. This semantic information is added to the context sent to the LLM
5. The LLM can provide more accurate and insightful responses

## Extending for New Languages

To add support for a new language:

1. Create a new parser class that inherits from `ASTParserPlugin`
2. Implement the `parse` and `to_unified_ast` methods
3. Register the parser in `ZiyaASTEnhancer._register_parsers`

Example:

```python
class JavaASTParser(ASTParserPlugin):
    file_extensions = ['.java']
    
    def parse(self, file_path, file_content):
        # Parse Java code using appropriate library
        # ...
    
    def to_unified_ast(self, native_ast, file_path):
        # Convert Java AST to unified AST
        # ...
```

## Future Enhancements

Planned enhancements include:

- Support for more languages (Java, Go, Rust, C/C++)
- More advanced semantic analysis features
- Code generation capabilities using AST transformations
- Framework-specific analysis (React, Angular, Django, etc.)
