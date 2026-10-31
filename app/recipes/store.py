"""
RecipeStore — read, write and lifecycle operations on recipe directories.

Layout and file loaders are in app/recipes/layout.py; models in
app/models/recipe.py.  This module owns:

  * the content address (``compute_recipe_sha``): sha256 over the
    canonical signature.json (minus its own ``sha256`` field) and every
    file under procedure/, keyed by relative path.  Prose (SKILL.md) and
    lifecycle state (provenance.json) are deliberately outside it, so a
    rewritten explanation or a probation→active flip does not read as
    drift to the claims that cite ``recipe:<name>@<sha>``.
  * atomic writes (temp + os.replace) of the two JSON files;
  * the lifecycle verbs slice 3 drives: ``set_status``, ``record_use``
    (the cross-conversation promotion signal), ``corroborate``.

Nothing here executes a procedure; that is recipe_run (slice 5), which
goes through the shell tool path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple, Union

from app.models.recipe import (
    KIND_KEY, PROCEDURE_DIR, PROVENANCE_FILE, RECIPE_STATUSES, SIGNATURE_FILE,
    Recipe, RecipeProvenance, RecipeSignature, RecipeTier, RecipeUse,
)
from app.recipes.layout import (
    load_provenance, load_signature, project_recipe_root, user_recipe_root,
)
from app.utils.evidence.store import canonical_json, sha256_file

logger = logging.getLogger(__name__)

# agentskills.io name rule, same as skill_discovery._NAME_RE.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_SKIP_SUFFIXES = (".pyc", ".pyo")


def _now_ms() -> int:
    return int(time.time() * 1000)


def validate_recipe_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name) or len(name) > 64:
        raise ValueError(
            f"invalid recipe name {name!r}: lowercase letters, digits and "
            "hyphens, 1-64 chars, matching the directory name")
    return name


def _procedure_files(recipe_dir: Path) -> List[Path]:
    proc = recipe_dir / PROCEDURE_DIR
    if not proc.is_dir():
        return []
    out = []
    for p in sorted(proc.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(proc).parts):
            continue
        if p.name.endswith(_SKIP_SUFFIXES):
            continue
        out.append(p)
    return out


def compute_recipe_sha(recipe_dir: Union[str, Path]) -> str:
    """Content address: canonical signature (sans ``sha256``) + procedure/.

    Key order and whitespace in signature.json do not matter; the bytes of
    every procedure file and its relative path do.
    """
    d = Path(recipe_dir)
    h = hashlib.sha256()
    sig_path = d / SIGNATURE_FILE
    if sig_path.is_file():
        h.update(b"signature\0")
        try:
            sig = json.loads(sig_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sig = None
        if isinstance(sig, dict):
            h.update(canonical_json({k: v for k, v in sig.items() if k != "sha256"}))
        else:
            h.update(sig_path.read_bytes())
        h.update(b"\0")
    for p in _procedure_files(d):
        h.update(b"file\0")
        h.update(p.relative_to(d).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(p).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def _write_json_atomic(path: Path, obj: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _frontmatter_scalar(value: str) -> str:
    """One-line frontmatter value for the lenient SKILL.md parser."""
    return " ".join(str(value).split())


class RecipeStore:
    """Recipes visible from one project: the project tier shadows the user
    tier by name.  ``project_root=None`` sees the user tier only."""

    def __init__(self, project_root: Optional[str] = None):
        self.project_root = project_root

    # ── roots ──────────────────────────────────────────────────────────

    def roots(self) -> List[Tuple[RecipeTier, Path]]:
        out: List[Tuple[RecipeTier, Path]] = []
        if self.project_root:
            out.append(("project", project_recipe_root(self.project_root)))
        try:
            out.append(("user", user_recipe_root()))
        except Exception as e:  # noqa: BLE001
            logger.debug("No user recipe root: %s", e)
        return out

    def _root_for(self, tier: RecipeTier) -> Path:
        for t, root in self.roots():
            if t == tier:
                return root
        raise ValueError(f"no {tier} recipe root available"
                         + (" (no project root)" if tier == "project" else ""))

    # ── read ───────────────────────────────────────────────────────────

    def load(self, recipe_dir: Union[str, Path], tier: RecipeTier) -> Optional[Recipe]:
        """One directory as a Recipe, or None when it is not a valid recipe.

        The skill view is built by the same parser discovery uses, so a
        recipe the store returns is exactly one discovery would list
        (archived included: the store must reach what discovery hides).
        """
        from app.services.skill_discovery import _skill_from_dir
        from app.services.token_service import TokenService
        d = Path(recipe_dir)
        root = d.parent
        skill = _skill_from_dir(
            d, TokenService(), load_body=True, source=tier,
            id_base=str(root), id_prefix=tier,
            default_kind="recipe", include_archived=True,
        )
        if skill is None or skill.kind != "recipe":
            return None
        signature = load_signature(d)
        if signature is None:
            return None
        return Recipe(
            skill=skill, signature=signature, provenance=load_provenance(d),
            path=str(d), tier=tier, sha256=compute_recipe_sha(d),
        )

    def list(self, status: Optional[Union[str, Iterable[str]]] = None, *,
             include_archived: bool = False) -> List[Recipe]:
        """Recipes by name, project tier shadowing user tier.  Archived
        recipes are omitted unless named by ``status`` or ``include_archived``."""
        wanted = None
        if status is not None:
            wanted = {status} if isinstance(status, str) else set(status)
        seen: set = set()
        out: List[Recipe] = []
        for tier, root in self.roots():
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name in seen:
                    continue
                r = self.load(child, tier)
                if r is None:
                    continue
                seen.add(child.name)
                if wanted is not None:
                    if r.status not in wanted:
                        continue
                elif r.status == "archived" and not include_archived:
                    continue
                out.append(r)
        return out

    def get(self, name: str, tier: Optional[RecipeTier] = None) -> Optional[Recipe]:
        for t, root in self.roots():
            if tier is not None and t != tier:
                continue
            d = root / name
            if d.is_dir():
                r = self.load(d, t)
                if r is not None:
                    return r
        return None

    # ── write ──────────────────────────────────────────────────────────

    def write(self, name: str, *, body: str, signature: RecipeSignature,
              provenance: RecipeProvenance,
              procedure: Mapping[str, Union[str, bytes]],
              description: Optional[str] = None,
              tier: RecipeTier = "project", overwrite: bool = False) -> Recipe:
        """Create a recipe directory and return it as loaded from disk.

        ``procedure`` maps paths relative to ``procedure/`` to contents.
        The signature's ``sha256`` is stamped with the content address of
        what was written.  Refuses to overwrite unless asked.
        """
        validate_recipe_name(name)
        if not procedure:
            raise ValueError("a recipe needs at least one procedure file")
        root = self._root_for(tier)
        d = root / name
        if d.exists() and not overwrite:
            raise FileExistsError(f"recipe {name!r} already exists at {d}")
        proc_root = (d / PROCEDURE_DIR).resolve()
        resolved: Dict[Path, Union[str, bytes]] = {}
        for rel, content in procedure.items():
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts or not rel_path.parts:
                raise ValueError(f"procedure path {rel!r} must be relative and inside procedure/")
            target = (d / PROCEDURE_DIR / rel_path).resolve()
            if proc_root not in target.parents and target != proc_root:
                raise ValueError(f"procedure path {rel!r} escapes procedure/")
            resolved[target] = content

        d.mkdir(parents=True, exist_ok=True)
        for target, content in resolved.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")

        desc = _frontmatter_scalar(description or signature.intent or f"Recipe {name}.")[:1024]
        fm_lines = [
            "---",
            f"name: {name}",
            f"description: {desc}",
            f"{KIND_KEY}: recipe",
            f"recipe_kind: {signature.recipe_kind}",
        ]
        if signature.domain:
            fm_lines.append("domain: " + " ".join(_frontmatter_scalar(t) for t in signature.domain))
        fm_lines.append("metadata: ziya-visibility=model_discoverable")
        fm_lines.append("---")
        (d / "SKILL.md").write_text("\n".join(fm_lines) + "\n" + body.strip() + "\n",
                                    encoding="utf-8")

        _write_json_atomic(d / PROVENANCE_FILE, provenance.model_dump(mode="json"))
        # Write the signature once without the stamp, hash, then stamp:
        # the hash excludes ``sha256`` so the stamped file hashes the same.
        sig = signature.model_dump(mode="json", by_alias=True)
        sig.pop("sha256", None)
        _write_json_atomic(d / SIGNATURE_FILE, sig)
        sig["sha256"] = compute_recipe_sha(d)
        _write_json_atomic(d / SIGNATURE_FILE, sig)

        r = self.load(d, tier)
        if r is None:  # pragma: no cover -- we just wrote a valid recipe
            raise RuntimeError(f"recipe {name!r} did not load back after write")
        return r

    def _save_provenance(self, r: Recipe) -> Recipe:
        _write_json_atomic(Path(r.path) / PROVENANCE_FILE,
                           r.provenance.model_dump(mode="json"))
        return self.load(r.path, r.tier) or r

    def _require(self, name: str, tier: Optional[RecipeTier]) -> Recipe:
        r = self.get(name, tier)
        if r is None:
            raise KeyError(f"no recipe named {name!r}")
        return r

    # ── lifecycle ──────────────────────────────────────────────────────

    def set_status(self, name: str, status: str, *, reason: Optional[str] = None,
                   tier: Optional[RecipeTier] = None) -> Recipe:
        """Flip lifecycle state in place.  Archive never deletes."""
        if status not in RECIPE_STATUSES:
            raise ValueError(f"unknown recipe status {status!r}")
        r = self._require(name, tier)
        p = r.provenance
        p.status = status
        if status == "active":
            p.promoted_at = _now_ms()
            p.archived_at = None
            p.archive_reason = None
        elif status == "archived":
            p.archived_at = _now_ms()
            p.archive_reason = reason
        return self._save_provenance(r)

    def record_use(self, name: str, conversation_id: Optional[str], *,
                   tier: Optional[RecipeTier] = None) -> bool:
        """Record a use by another conversation; the promotion signal.

        Returns False (and records nothing) when the caller is the origin
        conversation or unknown: a recipe reused only by the conversation
        that made it has not proved anything.
        """
        if not conversation_id:
            return False
        r = self._require(name, tier)
        if conversation_id == r.provenance.origin.conversation_id:
            return False
        r.provenance.used_by.append(
            RecipeUse(conversation_id=conversation_id, sha=r.sha256))
        self._save_provenance(r)
        return True

    def corroborate(self, name: str, conversation_id: Optional[str], *,
                    tier: Optional[RecipeTier] = None) -> Recipe:
        """A second harvest produced a near-duplicate: count it once per
        conversation instead of writing a twin."""
        r = self._require(name, tier)
        if conversation_id and conversation_id not in r.provenance.corroborated_by:
            r.provenance.corroborated_by.append(conversation_id)
            r.provenance.corroborations += 1
            return self._save_provenance(r)
        return r
