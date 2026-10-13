"""
Bundled-resource disclosure for file-backed skills (agentskills.io).

A skill directory may carry files beyond SKILL.md -- by convention
``scripts/`` (executable helpers), ``references/`` (docs loaded on demand)
and ``assets/`` (templates, data).  The skill body refers to them by paths
relative to the skill root ("run scripts/extract.py").  The model can only
follow those references if it is told where the root is.

The agentskills.io integration guide ("Structured wrapping" / "Listing
bundled resources") prescribes what an activation result carries:

    Skill directory: /home/user/.agents/skills/pdf-processing
    Relative paths in this skill are relative to the skill directory.

    <skill_resources>
      <file>scripts/extract.py</file>
      <file>references/pdf-spec-summary.md</file>
    </skill_resources>

Files are enumerated, never read: resources are tier 3 of progressive
disclosure and are loaded by the model on demand.  Large listings are
capped with a note that the list is incomplete.

Every path that hands a skill body to the model (get_skill_details, bench
"always" placement, task cards, delegates, and the client-assembled prompt
via ``Skill.resourceManifest``) goes through ``skill_activation_body`` /
``skill_resource_manifest`` so the disclosure is identical however a skill
is activated.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional, Tuple
from xml.sax.saxutils import escape

# Cap on listed files; beyond this the manifest says how many were omitted.
MAX_LISTED_FILES = 50
# Directory depth walked below the skill root.  The spec asks for references
# one level deep; scripts/lib/... is still reasonable, deeper is noise.
_MAX_DEPTH = 4
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv"}
_SKIP_SUFFIXES = (".pyc", ".pyo")
_SKILL_FILE = "SKILL.md"


def _skill_dir(path: Optional[str]) -> Optional[Path]:
    """Absolute (not symlink-resolved) skill directory, or None.

    Not resolving symlinks keeps a project skill's path inside the project
    tree, where the file tools already grant reads, and keeps the path the
    user recognises from their own layout.
    """
    if not path:
        return None
    p = Path(os.path.abspath(os.path.expanduser(path)))
    return p if (p / _SKILL_FILE).is_file() else None


def list_skill_resources(skill_dir: Path, *, limit: int = MAX_LISTED_FILES
                         ) -> Tuple[List[str], int]:
    """Bundled files under *skill_dir* as sorted root-relative POSIX paths.

    Returns ``(listed, omitted)``.  SKILL.md itself, hidden entries, caches
    and dependency trees are excluded.  Nothing is read.
    """
    found: List[str] = []
    root = Path(skill_dir)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        rel_dir = Path(dirpath).relative_to(root)
        depth = 0 if str(rel_dir) == "." else len(rel_dir.parts)
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith(".") and d not in _SKIP_DIRS and depth < _MAX_DEPTH
        )
        for name in filenames:
            if name.startswith(".") or name.endswith(_SKIP_SUFFIXES):
                continue
            rel = (rel_dir / name).as_posix() if depth else name
            if rel == _SKILL_FILE:
                continue
            found.append(rel)
    found.sort()
    return found[:limit], max(0, len(found) - limit)


def skill_resource_manifest(skill_path: Optional[str]) -> str:
    """The spec trailer for a file-backed skill, or "" when there is no
    skill directory (built-in and stored-JSON skills)."""
    d = _skill_dir(skill_path)
    if d is None:
        return ""
    lines = [
        f"Skill directory: {d}",
        "Relative paths in this skill are relative to the skill directory.",
    ]
    files, omitted = list_skill_resources(d)
    if files:
        lines.append("")
        lines.append("<skill_resources>")
        lines.extend(f"  <file>{escape(f)}</file>" for f in files)
        if omitted:
            lines.append(f"  <!-- {omitted} more files not listed -->")
        lines.append("</skill_resources>")
    return "\n".join(lines)


def skill_activation_body(skill: Any) -> str:
    """Skill prompt plus its resource manifest, as the model should see it.

    An empty prompt is returned unchanged: callers treat it as "body not
    loaded" and a bare manifest would read as a skill with no instructions.
    """
    prompt = getattr(skill, "prompt", "") or ""
    if not prompt:
        return prompt
    manifest = skill_resource_manifest(getattr(skill, "skillPath", None))
    return f"{prompt.rstrip()}\n\n{manifest}" if manifest else prompt


def readable_skill_dirs() -> List[str]:
    """Absolute prefixes the read tools should accept for user-level skills.

    The integration guide asks clients to allowlist skill directories so the
    model can read bundled resources without a permission failure.  Project
    skills already sit under the project root.  User-level roots
    (~/.ziya/skills, ~/.agents/skills, ...) do not, and installers commonly
    symlink each skill to a store elsewhere, so the resolved target of every
    symlinked skill is included too (the read tools check resolved paths).
    """
    try:
        from app.services.skill_discovery import iter_skill_dirs, user_skill_roots
        roots = [r for r, _ in user_skill_roots()]
    except Exception:  # noqa: BLE001 -- an allowlist lookup must never fail a read
        return []
    out: List[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        out.append(str(root))
        try:
            # Same bounded walk discovery uses, so a skill inside a grouping
            # folder is covered too.  resolve() follows a symlink at any
            # level of the path (the skill itself or a grouping folder), and
            # a target already under the root adds nothing new.
            for skill_dir in iter_skill_dirs(root):
                resolved = skill_dir.resolve()
                if resolved != skill_dir and str(resolved) not in out:
                    out.append(str(resolved))
        except OSError:
            continue
    return out
