"""
The shipped model table must pass its own startup validator.

Observed on a fresh toolbox install: every launch logged "Model config
validation: 3 issue(s) found (run with --debug for details)" -- and --debug
showed nothing, because the issues were never logged. The three were
`inline_schema_refs`, a key openai_direct.py reads but _VALID_MODEL_CONFIG_KEYS
never declared. Existing tests only filtered the validator to their own
models, so a table-wide inconsistency greeted users instead of failing CI.
"""
import re
from pathlib import Path

from app.config import models_config as mc

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_model_table_validates_clean():
    assert mc.validate_model_configs() == []


def test_every_key_a_provider_reads_is_declared():
    """A key consumed via model_config.get("x") that is not in the allowlist
    is exactly the drift the validator exists to catch -- catch it here first."""
    read = set()
    for py in (ROOT / "app" / "providers").glob("*.py"):
        read |= set(re.findall(r'model_config\.get\(\s*["\']([a-z_]+)["\']', py.read_text()))
    assert read, "no model_config.get() reads found -- pattern drifted?"
    undeclared = read - mc._VALID_MODEL_CONFIG_KEYS - mc._VALID_FAMILY_KEYS
    assert not undeclared, f"providers read undeclared model-config keys: {sorted(undeclared)}"


def test_main_logs_each_issue_at_debug():
    # Seam: the warning says "run with --debug for details"; main.py must
    # actually emit them at debug level or the pointer is dead.
    src = (ROOT / "app" / "main.py").read_text()
    warn = src.find("Model config validation:")
    assert warn != -1
    block = src[warn: warn + 600]
    assert re.search(r"for issue in issues:\s*\n\s*logger\.debug\(", block), \
        "main.py warns about issues but never logs them at debug level"
