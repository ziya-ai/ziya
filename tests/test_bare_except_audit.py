"""
Static analysis test: ensure no bare `except:` clauses exist in the codebase.

Bare `except:` catches SystemExit and KeyboardInterrupt, preventing clean
process termination and masking real errors.  All exception handlers should
use `except Exception:` (or a more specific type) instead.
"""

import os
import re
import pytest

# Pattern matches `except:` but NOT `except SomeType:` or `except (A, B):`
# Accounts for optional whitespace and trailing comments.
_BARE_EXCEPT_RE = re.compile(r'^\s*except\s*:\s*(#.*)?$')

# Directories to skip (vendored code, caches, etc.)
_SKIP_DIRS = {'__pycache__', '.git', 'node_modules', '.venv', 'venv', '.tox'}


def _find_bare_excepts(root_dir: str):
    """Walk root_dir and return all (filepath, lineno, line) tuples with bare except."""
    hits = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Prune skip dirs in-place so os.walk doesn't descend into them
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith('.py'):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    for lineno, line in enumerate(f, start=1):
                        if _BARE_EXCEPT_RE.match(line):
                            hits.append((fpath, lineno, line.rstrip()))
            except OSError:
                continue
    return hits


class TestNoBareExcepts:
    """Ensure no bare except: clauses exist in production code."""

    def test_no_bare_except_in_app(self):
        """Scan app/ for bare except: clauses."""
        app_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'app')
        hits = _find_bare_excepts(app_dir)
        if hits:
            report = '\n'.join(f'  {path}:{lineno}: {line}' for path, lineno, line in hits)
            pytest.fail(f'Found {len(hits)} bare except: clause(s) — use except Exception: instead:\n{report}')

    def test_no_bare_except_in_tests(self):
        """Scan tests/ for bare except: clauses (they're bad practice in tests too)."""
        tests_dir = os.path.dirname(__file__)
        hits = _find_bare_excepts(tests_dir)
        if hits:
            report = '\n'.join(f'  {path}:{lineno}: {line}' for path, lineno, line in hits)
            pytest.fail(f'Found {len(hits)} bare except: clause(s) in tests:\n{report}')
