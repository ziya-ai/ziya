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

with tempfile.TemporaryDirectory() as tmpdir:
    test_file = Path(tmpdir) / 'test.py'
    test_file.write_text(original)
    
    result = use_git_to_apply_code_diff(diff, str(test_file))
    actual = test_file.read_text()
    
    exp_lines = expected.splitlines()
    act_lines = actual.splitlines()
    
    print(f"Expected: {len(exp_lines)} lines")
    print(f"Actual: {len(act_lines)} lines")
    print(f"Difference: {len(act_lines) - len(exp_lines)}")
    
    # Find where they diverge
    print("\nFirst differences:")
    for i, (e, a) in enumerate(zip(exp_lines, act_lines), 1):
        if e != a:
            print(f"\nLine {i}:")
            print(f"  Expected: {e[:80]}")
            print(f"  Actual:   {a[:80]}")
            if i > 5:
                break
    
    # Check for extra lines in actual
    if len(act_lines) > len(exp_lines):
        print(f"\nExtra lines in actual (lines {len(exp_lines)+1}-{len(act_lines)}):")
        for i in range(len(exp_lines), min(len(exp_lines)+5, len(act_lines))):
            print(f"  {i+1}: {act_lines[i][:80]}")
