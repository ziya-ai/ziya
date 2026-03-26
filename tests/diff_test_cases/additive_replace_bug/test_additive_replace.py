"""
Standalone MRE test for the additive-replace bug class.

Bug description:
    When a multi-hunk diff is applied, and an earlier hunk adds lines
    (shifting line numbers), subsequent hunks that perform single-line
    replacements may be applied *additively* — meaning both the old line
    and the new line appear in the file, instead of the old line being
    replaced by the new one.

    Real-world trigger: Diff 6 of the stableTokenKey change replaced
        <Alert key={index} ...>
    with
        <Alert key={sk} ...>
    but the file ended up with BOTH lines.

How to run:
    python -m pytest tests/diff_test_cases/additive_replace_bug/test_additive_replace.py -v
    
    Or from project root:
    python -m pytest tests/ -k additive_replace -v
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import re

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, project_root)


class TestAdditiveReplaceBug(unittest.TestCase):
    """
    Minimal reproduction for the additive-replace bug where a diff hunk
    that should replace a single line instead inserts the new line
    alongside the old one.
    """

    CASE_DIR = os.path.dirname(__file__)

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='additive_replace_test_')
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_fixture(self, name: str) -> str:
        with open(os.path.join(self.CASE_DIR, name), 'r') as f:
            return f.read()

    def _setup_file(self, content: str, rel_path: str) -> str:
        """Write *content* into temp_dir/<rel_path> and return the full path."""
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def _read_file(self, rel_path: str) -> str:
        with open(os.path.join(self.temp_dir, rel_path), 'r') as f:
            return f.read()

    def _count_pattern(self, text: str, pattern: str) -> int:
        """Count occurrences of *pattern* in *text*."""
        return len(re.findall(re.escape(pattern), text))

    # ------------------------------------------------------------------
    # Core test: apply the full multi-hunk diff
    # ------------------------------------------------------------------
    def test_full_diff_no_duplicate_lines(self):
        """
        Apply a multi-hunk diff (adds lines + replaces key={index} → key={sk})
        and verify no line appears twice (the additive-replace symptom).
        """
        from app.utils.code_util import use_git_to_apply_code_diff

        metadata = json.loads(self._load_fixture('metadata.json'))
        original = self._load_fixture('original.tsx')
        diff = self._load_fixture('changes.diff')
        expected = self._load_fixture('expected.tsx')
        target = metadata['target_file']

        self._setup_file(original, target)
        use_git_to_apply_code_diff(diff, target)
        result = self._read_file(target)

        # PRIMARY CHECK: no line should appear as both key={index} AND key={sk}
        # This is the exact symptom of the additive-replace bug.
        has_old_key = 'key={index}' in result
        has_new_key = 'key={sk}' in result

        if has_old_key and has_new_key:
            # Extract the offending lines for diagnosis
            lines = result.split('\n')
            duplicated = [(i+1, l.strip()) for i, l in enumerate(lines)
                          if 'key={index}' in l or 'key={sk}' in l]
            self.fail(
                f"ADDITIVE-REPLACE BUG: Both key={{index}} and key={{sk}} "
                f"found in result — a replacement hunk was applied additively.\n"
                f"Offending lines:\n" +
                '\n'.join(f"  L{n}: {l}" for n, l in duplicated)
            )

        # SECONDARY CHECK: result matches expected output exactly
        self.assertEqual(result, expected,
                         "Diff application did not produce expected result")

    def test_no_key_index_remains(self):
        """
        After applying the diff, NO key={index} should remain in the file.
        All instances should have been replaced with key={sk}.
        """
        from app.utils.code_util import use_git_to_apply_code_diff

        metadata = json.loads(self._load_fixture('metadata.json'))
        original = self._load_fixture('original.tsx')
        diff = self._load_fixture('changes.diff')
        target = metadata['target_file']

        self._setup_file(original, target)
        use_git_to_apply_code_diff(diff, target)
        result = self._read_file(target)

        old_count = self._count_pattern(result, 'key={index}')
        self.assertEqual(old_count, 0,
                         f"Found {old_count} leftover key={{index}} occurrences — "
                         f"replacement hunks were not applied")

    def test_key_sk_count_matches(self):
        """
        The number of key={sk} in the result should match the number of
        key={index} in the original — every instance should be replaced.
        """
        from app.utils.code_util import use_git_to_apply_code_diff

        metadata = json.loads(self._load_fixture('metadata.json'))
        original = self._load_fixture('original.tsx')
        diff = self._load_fixture('changes.diff')
        target = metadata['target_file']

        original_count = self._count_pattern(original, 'key={index}')
        self._setup_file(original, target)
        use_git_to_apply_code_diff(diff, target)
        result = self._read_file(target)

        new_count = self._count_pattern(result, 'key={sk}')
        self.assertEqual(new_count, original_count,
                         f"Expected {original_count} key={{sk}} replacements, "
                         f"found {new_count}")

    def test_no_adjacent_duplicate_jsx_elements(self):
        """
        Detect the exact JSX compilation error that triggered this investigation:
        two adjacent JSX elements without a wrapper, caused by both old and new
        lines being present.

        Pattern detected:
            <Alert key={index} ... />
            <Alert key={sk} ... />
        or:
            <ThinkingBlock key={index} ...>
            <ThinkingBlock key={sk} ...>
        """
        from app.utils.code_util import use_git_to_apply_code_diff

        metadata = json.loads(self._load_fixture('metadata.json'))
        original = self._load_fixture('original.tsx')
        diff = self._load_fixture('changes.diff')
        target = metadata['target_file']

        self._setup_file(original, target)
        use_git_to_apply_code_diff(diff, target)
        result = self._read_file(target)

        lines = result.split('\n')
        for i in range(len(lines) - 1):
            line_a = lines[i].strip()
            line_b = lines[i + 1].strip()

            # Check for adjacent lines that are the same element with different keys
            if not line_a or not line_b:
                continue

            # Extract tag name and key from JSX-like patterns
            match_a = re.match(r'<(\w+)\s+key=\{(\w+)\}', line_a)
            match_b = re.match(r'<(\w+)\s+key=\{(\w+)\}', line_b)

            if match_a and match_b:
                tag_a, key_a = match_a.groups()
                tag_b, key_b = match_b.groups()
                if tag_a == tag_b and key_a != key_b:
                    self.fail(
                        f"Adjacent duplicate JSX elements detected at lines "
                        f"{i+1}-{i+2} — additive-replace bug:\n"
                        f"  L{i+1}: {line_a}\n"
                        f"  L{i+2}: {line_b}\n"
                        f"The second line should have REPLACED the first, "
                        f"not been added alongside it."
                    )

    def test_line_count_is_correct(self):
        """
        Verify total line count matches expected.  An additive replacement
        adds extra lines, so a count mismatch is a strong signal.
        """
        from app.utils.code_util import use_git_to_apply_code_diff

        metadata = json.loads(self._load_fixture('metadata.json'))
        original = self._load_fixture('original.tsx')
        diff = self._load_fixture('changes.diff')
        expected = self._load_fixture('expected.tsx')
        target = metadata['target_file']

        self._setup_file(original, target)
        use_git_to_apply_code_diff(diff, target)
        result = self._read_file(target)

        expected_lines = len(expected.split('\n'))
        result_lines = len(result.split('\n'))

        self.assertEqual(result_lines, expected_lines,
                         f"Line count mismatch: got {result_lines}, "
                         f"expected {expected_lines}. "
                         f"Delta of {result_lines - expected_lines} lines "
                         f"suggests {'additive insertion' if result_lines > expected_lines else 'missing content'}.")


if __name__ == '__main__':
    unittest.main()
