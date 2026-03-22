"""
Tests for the centralized ZIYA_* environment variable registry.

Validates:
1. Registry integrity — no duplicate names, all have required fields
2. ziya_env() accessor — type coercion, defaults, unknown-key errors
3. Coverage — every os.environ.get("ZIYA_…") in the codebase has a registry entry
"""

import os
import re
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.config.env_registry import (
    REGISTRY, EnvVar, EnvCategory, ziya_env,
    get_user_facing_vars, get_vars_by_category,
)


class TestRegistryIntegrity:
    """Ensure the registry itself is well-formed."""

    def test_no_duplicate_names(self):
        """Every env var name should appear exactly once."""
        from app.config.env_registry import _VARS
        names = [v.name for v in _VARS]
        duplicates = [n for n in names if names.count(n) > 1]
        assert not duplicates, f"Duplicate registry entries: {set(duplicates)}"

    def test_all_start_with_ziya(self):
        """All registered names must use the ZIYA_ prefix."""
        for name, spec in REGISTRY.items():
            assert name.startswith("ZIYA_"), f"{name} does not start with ZIYA_"

    def test_all_have_description(self):
        """Every entry must have a non-empty description."""
        for name, spec in REGISTRY.items():
            assert spec.description, f"{name} is missing a description"

    def test_type_is_valid(self):
        """Type must be str, int, float, or bool."""
        valid_types = {str, int, float, bool}
        for name, spec in REGISTRY.items():
            assert spec.type in valid_types, (
                f"{name} has invalid type {spec.type}"
            )

    def test_deprecated_vars_point_to_existing(self):
        """If deprecated_by is set, the target must exist in the registry."""
        for name, spec in REGISTRY.items():
            if spec.deprecated_by:
                assert spec.deprecated_by in REGISTRY, (
                    f"{name} deprecated_by '{spec.deprecated_by}' not in registry"
                )

    def test_category_enum_coverage(self):
        """Every EnvCategory value should have at least one entry."""
        used = {spec.category for spec in REGISTRY.values()}
        for cat in EnvCategory:
            assert cat in used, f"Category {cat.value} has no entries"


class TestZiyaEnvAccessor:
    """Test the ziya_env() type-coercing accessor."""

    def test_string_default(self):
        """Unset string var returns registry default."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_THEME", None)
            assert ziya_env("ZIYA_THEME") == "light"

    def test_string_override(self):
        """Set string var returns the value."""
        with patch.dict(os.environ, {"ZIYA_THEME": "dark"}):
            assert ziya_env("ZIYA_THEME") == "dark"

    def test_int_coercion(self):
        """Int vars are coerced from string."""
        with patch.dict(os.environ, {"ZIYA_PORT": "8080"}):
            result = ziya_env("ZIYA_PORT")
            assert result == 8080
            assert isinstance(result, int)

    def test_int_bad_value_returns_default(self):
        """Invalid int falls back to default."""
        with patch.dict(os.environ, {"ZIYA_PORT": "not_a_number"}):
            assert ziya_env("ZIYA_PORT") == 6969

    def test_float_coercion(self):
        """Float vars are coerced from string."""
        with patch.dict(os.environ, {"ZIYA_TEMPERATURE": "0.7"}):
            result = ziya_env("ZIYA_TEMPERATURE")
            assert result == 0.7
            assert isinstance(result, float)

    def test_bool_true_variants(self):
        """Bool vars accept 'true', '1', 'yes' (case-insensitive)."""
        for truthy in ("true", "1", "yes", "True", "YES", "  true  "):
            with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": truthy}):
                assert ziya_env("ZIYA_ENABLE_MCP") is True, f"Failed for '{truthy}'"

    def test_bool_false_variants(self):
        """Non-truthy strings resolve to False."""
        for falsy in ("false", "0", "no", ""):
            with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": falsy}):
                assert ziya_env("ZIYA_ENABLE_MCP") is False, f"Failed for '{falsy}'"

    def test_bool_default_when_unset(self):
        """Bool vars use registry default when not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_ENABLE_MCP", None)
            # ZIYA_ENABLE_MCP defaults to True
            assert ziya_env("ZIYA_ENABLE_MCP") is True

    def test_caller_default_overrides_registry(self):
        """Passing an explicit default overrides the registry's default."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_PORT", None)
            assert ziya_env("ZIYA_PORT", default=9999) == 9999

    def test_unknown_key_raises(self):
        """Requesting an unregistered key raises KeyError."""
        with pytest.raises(KeyError, match="not declared"):
            ziya_env("ZIYA_NONEXISTENT_VAR_12345")

    def test_none_default_when_unset(self):
        """Vars with default=None return None when unset."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
            assert ziya_env("ZIYA_USER_CODEBASE_DIR") is None


class TestFilterFunctions:
    """Test the query/filter helpers."""

    def test_get_user_facing_excludes_internal(self):
        """Internal vars should be excluded from user-facing list."""
        user_vars = get_user_facing_vars()
        internal_names = {v.name for v in get_vars_by_category(EnvCategory.INTERNAL)
                          if not v.user_facing}
        user_names = {v.name for v in user_vars}
        overlap = user_names & internal_names
        assert not overlap, f"Internal vars leaked into user-facing: {overlap}"

    def test_get_vars_by_category(self):
        """Category filter returns non-empty lists for known categories."""
        for cat in [EnvCategory.CORE, EnvCategory.MODEL, EnvCategory.MCP]:
            vars_in_cat = get_vars_by_category(cat)
            assert len(vars_in_cat) > 0, f"Category {cat.value} returned empty"


class TestCodebaseCoverage:
    """Verify that os.environ.get("ZIYA_…") calls in the codebase
    reference variables that exist in the registry.

    This is the static-analysis check that catches undocumented vars.
    """

    # Pattern to match os.environ.get("ZIYA_FOO" or os.environ["ZIYA_FOO"
    _ENV_PATTERN = re.compile(
        r'''os\.environ(?:\.get)?\s*\(\s*['"](ZIYA_[A-Z_]+)['"]'''
    )
    # Also match os.environ["ZIYA_FOO"] = ... and "ZIYA_FOO" in os.environ
    _ENV_SET_PATTERN = re.compile(
        r'''os\.environ\s*\[\s*['"](ZIYA_[A-Z_]+)['"]\s*\]'''
    )
    _ENV_IN_PATTERN = re.compile(
        r'''['"](ZIYA_[A-Z_]+)['"]\s+in\s+os\.environ'''
    )

    def _collect_env_vars_from_source(self) -> dict[str, set[str]]:
        """Walk the app/ tree and collect all ZIYA_* references.

        Returns: {var_name: {file1, file2, ...}}
        """
        app_dir = project_root / "app"
        var_files: dict[str, set[str]] = {}

        for py_file in app_dir.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for pattern in (self._ENV_PATTERN, self._ENV_SET_PATTERN, self._ENV_IN_PATTERN):
                for match in pattern.finditer(source):
                    var_name = match.group(1)
                    rel = str(py_file.relative_to(project_root))
                    var_files.setdefault(var_name, set()).add(rel)

        return var_files

    def test_all_env_vars_registered(self):
        """Every ZIYA_* var used via os.environ must be in the registry."""
        var_files = self._collect_env_vars_from_source()
        unregistered = {
            name: files
            for name, files in sorted(var_files.items())
            if name not in REGISTRY
        }
        if unregistered:
            lines = ["Unregistered ZIYA_* env vars found:"]
            for name, files in unregistered.items():
                lines.append(f"  {name}")
                for f in sorted(files)[:3]:
                    lines.append(f"    → {f}")
            pytest.fail("\n".join(lines))

    def test_registry_vars_not_phantoms(self):
        """Warn (don't fail) about registry entries not found in code.

        This catches stale entries after a var is removed.
        """
        var_files = self._collect_env_vars_from_source()
        unused = [
            name for name in REGISTRY
            if name not in var_files
        ]
        if unused:
            # Soft warning — some vars may only be used in plugins or tests
            import warnings
            warnings.warn(
                f"Registry entries with no os.environ reference in app/: "
                f"{unused}",
                stacklevel=1,
            )
