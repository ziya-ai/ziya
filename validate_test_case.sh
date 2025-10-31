#!/bin/bash
# Validate a single test case

TEST_NAME=$1
CASE_DIR="tests/diff_test_cases/${TEST_NAME#test_}"

echo "=== Validating: $TEST_NAME ==="
echo

# Check structure
echo "Files in test case:"
ls -1 "$CASE_DIR" 2>/dev/null || echo "  ERROR: Directory not found"
echo

# Check for required files
if [ ! -f "$CASE_DIR/metadata.json" ]; then
    echo "❌ Missing: metadata.json"
fi

if [ ! -f "$CASE_DIR/changes.diff" ]; then
    echo "❌ Missing: changes.diff"
fi

# Check for original file
ORIGINAL=$(ls "$CASE_DIR"/original.* 2>/dev/null | head -1)
if [ -z "$ORIGINAL" ]; then
    echo "❌ Missing: original.* file"
else
    echo "✓ Found: $(basename $ORIGINAL)"
fi

# Check for expected file
EXPECTED=$(ls "$CASE_DIR"/expected.* 2>/dev/null | head -1)
if [ -z "$EXPECTED" ]; then
    echo "❌ Missing: expected.* file"
else
    echo "✓ Found: $(basename $EXPECTED)"
fi

echo
echo "Running test..."
python tests/run_diff_tests.py -k "$TEST_NAME" 2>&1 | grep -E "(PASS|FAIL|ERROR|malformed|ambiguous)" | head -5
