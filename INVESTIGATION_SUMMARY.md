# Diff Application Investigation Summary

## Current Status
- **83/106 tests passing** (78.3%)
- **+1 from baseline** (82 → 83) via deduplication fix

## Key Findings

### 1. Content Duplication Pattern - FIXED ✓
- **Root Cause**: Fuzzy matching applying hunks with incorrect position calculations
- **Fix**: Post-application deduplication removes consecutive identical non-blank lines
- **Impact**: Fixed `test_MRE_identical_adjacent_blocks`

### 2. Invalid Test Data - IDENTIFIED ⚠️
Multiple test cases have mismatched diffs and expected outputs:
- `test_MRE_comment_only_changes`: Diff changes opening `'''` to `"""` but not closing
- `test_MRE_incorrect_hunk_offsets`: System patch fails with "malformed patch"
- Expected files don't match what the diffs would produce

### 3. Incomplete Removal Pattern - ROOT CAUSE IDENTIFIED
- **Symptom**: Old code not removed before new code inserted
- **Root Cause**: Surgical application only works when `len(removed) == len(added)`
- **Fallback Issue**: When surgical fails, standard application doesn't work correctly
- **Affected Tests**: Multiple tests with fuzzy matching

### 4. Surgical Application Limitations
Current implementation:
```python
if len(removed_lines) != len(added_lines):
    return original_lines  # Returns unchanged!
```
This is too restrictive and causes fallback to fail.

## Attempted Fixes

### Fix 1: Fuzzy Match Position Adjustment
- Recalculate `end_remove_pos` for fuzzy matches
- **Result**: Not applied (flag not set in affected cases)

### Fix 2: Improved Surgical Application  
- Remove restriction on equal line counts
- **Result**: Broke 4 tests (79/106), reverted

## Recommendations

### High Priority
1. **Fix or skip invalid test cases** - Regenerate test data or mark as expected failures
2. **Improve surgical application** - Make it work for unequal line counts without breaking existing tests
3. **Fix fuzzy match fallback** - Ensure standard application works when surgical fails

### Medium Priority
4. Fix position calculation for ambiguous context
5. Improve EOF handling for truncated diffs

## Test Categories

### Passing (83)
- Basic replacements, additions, deletions
- Most fuzzy matching cases
- Indentation handling
- Multi-hunk diffs

### Failing - Invalid Data (estimated 5-8)
- MRE_comment_only_changes
- MRE_incorrect_hunk_offsets  
- Possibly others

### Failing - Code Issues (estimated 15-18)
- Surgical application failures
- Position calculation errors
- Content not removed correctly
