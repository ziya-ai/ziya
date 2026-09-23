"""
Guard: no dict literal in app/config/models_config.py may repeat a key.

Python dict literals silently take the LAST value for a repeated key, so a
half-applied edit that inserts new lines without removing the old ones (seen
2026-09-23: kimi-k3 gained `token_limit: 1048576` above a surviving
`token_limit: 256000`) passes import, passes validate_model_configs, and
yields the stale value. An AST scan is the only thing that catches it.

Known pre-existing duplicate (June 2026, MODEL_FAMILIES['zai-glm'] repeats
`token_limit` with the SAME value) is allow-listed by (key, value) so the
test is red only for a duplicate that changes behaviour or is new.
"""

import ast
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config" / "models_config.py"

# (key, repeated value) pairs that pre-date this guard and are harmless
# because every occurrence carries the same value. Remove an entry when the
# duplicate itself is cleaned up.
_KNOWN_BENIGN = {("token_limit", 1000000)}


def _duplicates():
    tree = ast.parse(CONFIG.read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        seen: dict = {}
        for k, v in zip(node.keys, node.values):
            if not isinstance(k, ast.Constant):
                continue
            if k.value in seen:
                first_v, first_line = seen[k.value]
                same = (isinstance(v, ast.Constant) and isinstance(first_v, ast.Constant)
                        and v.value == first_v.value)
                benign = same and (k.value, getattr(v, "value", None)) in _KNOWN_BENIGN
                out.append((k.value, first_line, k.lineno, same, benign))
            else:
                seen[k.value] = (v, k.lineno)
    return out


def test_no_conflicting_duplicate_keys():
    bad = [d for d in _duplicates() if not d[4]]
    assert bad == [], (
        "duplicate keys in models_config.py (key, first_line, dup_line, "
        f"same_value, benign): {bad} — the LAST value silently wins"
    )


def test_known_benign_list_is_still_accurate():
    # If the pre-existing duplicate is cleaned up, drop it from _KNOWN_BENIGN
    # so the allow-list does not rot into a blanket exemption.
    benign = [d for d in _duplicates() if d[4]]
    assert len(benign) == len(_KNOWN_BENIGN), (
        f"_KNOWN_BENIGN is out of date: allow-listed {_KNOWN_BENIGN}, "
        f"found benign duplicates {benign}"
    )
