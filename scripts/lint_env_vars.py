#!/usr/bin/env python3
"""
Lint: verify all os.environ ZIYA_* references are in the registry.

Usage:
    python scripts/lint_env_vars.py          # check and report
    python scripts/lint_env_vars.py --strict  # exit 1 on violations
"""

import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_PATTERNS = [
    re.compile(r'''os\.environ(?:\.get)?\s*\(\s*['"](ZIYA_[A-Z_]+)['"]'''),
    re.compile(r'''os\.environ\s*\[\s*['"](ZIYA_[A-Z_]+)['"]\s*\]'''),
    re.compile(r'''['"](ZIYA_[A-Z_]+)['"]\s+in\s+os\.environ'''),
    re.compile(r'''os\.environ\.setdefault\s*\(\s*['"](ZIYA_[A-Z_]+)['"]'''),
    re.compile(r'''os\.environ\.pop\s*\(\s*['"](ZIYA_[A-Z_]+)['"]'''),
]


def collect_references():
    """Scan app/ for all ZIYA_* env var references."""
    app_dir = PROJECT_ROOT / "app"
    refs = {}

    for py_file in app_dir.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        rel = str(py_file.relative_to(PROJECT_ROOT))
        for pattern in _PATTERNS:
            for match in pattern.finditer(source):
                var_name = match.group(1)
                refs.setdefault(var_name, []).append(
                    f"{rel}:{match.start()}"
                )

    return refs


def main():
    strict = "--strict" in sys.argv

    from app.config.env_registry import REGISTRY

    refs = collect_references()
    registered = set(REGISTRY.keys())
    found = set(refs.keys())

    unregistered = sorted(found - registered)
    unused = sorted(registered - found)

    print(f"Registry:     {len(registered)} vars declared")
    print(f"Codebase:     {len(found)} unique vars referenced")
    print(f"Unregistered: {len(unregistered)}")
    print(f"Unused:       {len(unused)}")
    print()

    if unregistered:
        print("UNREGISTERED (add to app/config/env_registry.py):")
        for name in unregistered:
            locations = refs[name][:3]
            print(f"  {name}")
            for loc in locations:
                print(f"    -> {loc}")
        print()

    if unused:
        print("REGISTERED BUT NOT FOUND IN app/:")
        for name in unused:
            print(f"  {name}")
        print()

    if unregistered:
        print(f"RESULT: {len(unregistered)} unregistered ZIYA_* variable(s)")
        if strict:
            sys.exit(1)
    else:
        print("All ZIYA_* variables are registered.")


if __name__ == "__main__":
    main()
