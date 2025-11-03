import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
os.environ['ZIYA_USER_CODEBASE_DIR'] = tempfile.gettempdir()

from app.utils.code_util import use_git_to_apply_code_diff

test_dir = Path('tests/diff_test_cases/MRE_incorrect_hunk_offsets')
original = (test_dir / 'original.py').read_text()
diff = (test_dir / 'changes.diff').read_text()
expected = (test_dir / 'expected.py').read_text()

print("ORIGINAL FILE:")
print("="*80)
for i, line in enumerate(original.splitlines(), 1):
    print(f"{i:3}: {line}")

print("\n\nDIFF:")
print("="*80)
print(diff)

print("\n\nEXPECTED FILE:")
print("="*80)
for i, line in enumerate(expected.splitlines(), 1):
    print(f"{i:3}: {line}")

with tempfile.TemporaryDirectory() as tmpdir:
    test_file = Path(tmpdir) / 'test.py'
    test_file.write_text(original)
    
    result = use_git_to_apply_code_diff(diff, str(test_file))
    actual = test_file.read_text()
    
    print("\n\nACTUAL RESULT:")
    print("="*80)
    for i, line in enumerate(actual.splitlines(), 1):
        print(f"{i:3}: {line}")
    
    print("\n\nCOMPARISON:")
    print("="*80)
    print(f"Expected: {len(expected.splitlines())} lines")
    print(f"Actual: {len(actual.splitlines())} lines")
    print(f"Match: {actual == expected}")
