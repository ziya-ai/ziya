"""
Where recipes live, and the two file loaders discovery needs.

Kept free of app.services imports so app/services/skill_discovery.py can
import it; the store (which imports discovery) lives in store.py.

Roots mirror the skill tiers: ``<project>/.ziya/recipes`` shadows
``~/.ziya/recipes`` by name.  Both are ``.ziya`` roots, so they sit at the
top of discover_all_skills' precedence like ``.ziya/skills``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from app.models.recipe import (
    PROVENANCE_FILE, SIGNATURE_FILE, RecipeProvenance, RecipeSignature,
)

logger = logging.getLogger(__name__)

# Project-relative recipe roots, in priority order (one today).
RECIPE_PROJECT_DIR = ".ziya/recipes"
RECIPE_PROJECT_PATHS = [RECIPE_PROJECT_DIR]


def project_recipe_root(project_root: str) -> Path:
    return Path(project_root) / RECIPE_PROJECT_DIR


def user_recipe_root() -> Path:
    """``$ZIYA_HOME/recipes`` (``~/.ziya/recipes`` by default)."""
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "recipes"


def user_recipe_roots() -> List[Tuple[Path, bool]]:
    """User-tier roots as ``(path, is_ziya)``, the shape user_skill_roots()
    returns, so discovery and the read-tool allowlist share one list."""
    try:
        return [(user_recipe_root(), True)]
    except Exception as e:  # noqa: BLE001 -- a missing home must not break discovery
        logger.debug("Could not resolve Ziya home for user recipes: %s", e)
        return []


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Cannot read %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("%s: expected a JSON object", path)
        return None
    return data


def load_signature(recipe_dir: Path) -> Optional[RecipeSignature]:
    """The parsed contract, or None when the file is missing or invalid.

    None is a rejection: a recipe without a parseable signature is not a
    recipe, and discovery skips it with a warning.
    """
    data = _read_json(Path(recipe_dir) / SIGNATURE_FILE)
    if data is None:
        return None
    try:
        return RecipeSignature.model_validate(data)
    except Exception as e:  # noqa: BLE001 -- pydantic ValidationError, reported as a warning
        logger.warning("%s/%s does not validate: %s", recipe_dir, SIGNATURE_FILE, e)
        return None


def load_provenance(recipe_dir: Path) -> RecipeProvenance:
    """The lifecycle record; a missing or unreadable file reads as a fresh
    probationary recipe rather than as an error, so a hand-made recipe
    directory is usable before anything has written provenance."""
    path = Path(recipe_dir) / PROVENANCE_FILE
    if not path.is_file():
        return RecipeProvenance()
    data = _read_json(path)
    if data is None:
        return RecipeProvenance()
    try:
        return RecipeProvenance.model_validate(data)
    except Exception as e:  # noqa: BLE001
        logger.warning("%s does not validate (%s); treating as probationary", path, e)
        return RecipeProvenance()
