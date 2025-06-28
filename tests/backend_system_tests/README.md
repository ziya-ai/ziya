# Ziya Backend System Tests

This directory contains validated regression tests for Ziya's backend systems, organized by category.

## Directory Structure

```
backend_system_tests/
├── core/           # Core functionality tests
├── diff/           # Diff application and patch handling tests  
├── token/          # Token counting and estimation tests
├── performance/    # Performance and timeout tests
├── integration/    # Integration and API tests
└── README.md       # This file
```

## Test Categories

### Core (`core/`)
Tests for fundamental Ziya functionality:
- Directory reading and file detection
- File processing and filtering
- Basic I/O operations
- Configuration handling

### Diff (`diff/`)
Tests for diff application and patch handling:
- Patch parsing and application
- Diff format handling
- Conflict resolution
- Multi-hunk patches

### Token (`token/`)
Tests for token counting and estimation:
- Fast token estimation
- Accurate token counting (tiktoken)
- File type multipliers
- Document extraction

### Performance (`performance/`)
Tests for performance characteristics:
- Timeout behavior
- Large file handling
- Memory usage
- Caching effectiveness

### Integration (`integration/`)
Tests for system integration:
- API endpoint testing
- Frontend/backend communication
- External service integration
- End-to-end workflows

## Running Tests

Use the test runner to execute tests:

```bash
# Run all tests
python run_backend_system_tests.py

# Run specific category
python run_backend_system_tests.py --category core

# List available tests
python run_backend_system_tests.py --list

# Fast mode (skip slow tests)
python run_backend_system_tests.py --fast

# Verbose output
python run_backend_system_tests.py --verbose

# Generate detailed report
python run_backend_system_tests.py --report
```

## Test Guidelines

### Naming Convention
- Test files: `test_<functionality>.py`
- Test classes: `Test<Functionality>`
- Test methods: `test_<specific_behavior>`

### Documentation
Each test file should include:
- Module docstring describing the test purpose
- Individual test method docstrings
- Comments explaining complex test logic

### Categories
Tests should be placed in the appropriate category directory:
- **Core**: Fundamental functionality that other systems depend on
- **Diff**: Anything related to patch/diff processing
- **Token**: Token counting, estimation, and related functionality
- **Performance**: Tests that measure timing, memory, or resource usage
- **Integration**: Tests that involve multiple systems or external dependencies

### Test Quality
- Tests should be deterministic and repeatable
- Use temporary directories/files for file system tests
- Clean up resources in tearDown methods
- Include both positive and negative test cases
- Test edge cases and error conditions

### Performance Tests
Performance tests should:
- Be marked with `# SLOW_TEST` comment for fast mode filtering
- Have reasonable timeout expectations
- Test both normal and edge case performance
- Include memory usage validation where appropriate

## Adding New Tests

1. Choose the appropriate category directory
2. Create a new test file following naming conventions
3. Implement test class inheriting from `unittest.TestCase`
4. Add comprehensive docstrings
5. Test the new test file with the runner
6. Update this README if adding new categories

## Maintenance

- Review and update tests when functionality changes
- Remove obsolete tests that no longer apply
- Refactor common test utilities into shared modules
- Keep test execution time reasonable
- Monitor test flakiness and fix unstable tests
