import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['ZIYA_USER_CODEBASE_DIR'] = tempfile.gettempdir()

from app.utils.code_util import use_git_to_apply_code_diff

test_name = sys.argv[1] if len(sys.argv) > 1 else 'mcp_registry_test_connection'
test_dir = Path(f'tests/diff_test_cases/{test_name}')

# Find files
original_file = None
for ext in ['.py', '.ts', '.tsx']:
    orig = test_dir / f'original{ext}'
    if orig.exists():
        original_file = orig
        expected_file = test_dir / f'expected{ext}'
        break

original = original_file.read_text()
diff = (test_dir / 'changes.diff').read_text()
expected = expected_file.read_text()

with tempfile.TemporaryDirectory() as tmpdir:
    test_file = Path(tmpdir) / original_file.name.replace('original', 'test')
    test_file.write_text(original)
    
    result = use_git_to_apply_code_diff(diff, str(test_file))
    print("Result keys:", list(result.keys()))
    print("Result:", result)
    
    actual = test_file.read_text()
    print("\nContent match:", actual == expected)
