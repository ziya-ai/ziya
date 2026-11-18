"""
Agent SOPs (https://github.com/strands-agents/agent-sop): ``<name>.sop.md``
files holding a procedure as an Overview, Parameters and Steps with RFC 2119
constraints.

The public project defines the file format and three ways to hand a SOP to
a model (as a system prompt, as an MCP prompt, converted to an Agent
Skill).  It defines no on-disk location: its tools take ``--sop-paths``, a
colon-separated list of directories in which only ``*.sop.md`` files count.
Ziya reads the files directly, with no conversion step:

  * ``ZIYA_SOP_PATHS``     same form as ``--sop-paths``; listed first wins
  * ``<project>/.ziya/sops``
  * ``~/.ziya/sops``       (``$ZIYA_HOME/sops``)
  * roots a plugin contributes (app.plugins.register_sop_root_provider)

in that order; the first file found for a name is the one used.  The name
is the file name without ``.sop.md``.  The body is used verbatim.

Two consumers:
  * skill discovery lists each SOP as a skill of kind ``sop``
    (app/services/skill_discovery.discover_all_skills), so it can be placed
    on the Bench and loaded on demand in any conversation;
  * the agent overlay loads the SOPs an agent definition names
    (app/services/agent_overlay.overlay_for).

How an agent comes to name a SOP is not part of the public format; that
mapping belongs to whoever supplies the definition.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.utils.logging_utils import logger

SOP_SUFFIX = ".sop.md"
SOP_PATHS_ENV = "ZIYA_SOP_PATHS"
PROJECT_SOP_PATHS = [".ziya/sops"]
# How far below a root a SOP file may sit (1 = directly in the root).
MAX_SOP_DEPTH = 3
DESCRIPTION_MAX_CHARS = 1024

# The spec asks for kebab-case; other harnesses' files are accepted as long
# as the name is a plain file stem.
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


@dataclass(frozen=True)
class SopFile:
    name: str
    path: Path
    root: Path
    source: str          # 'project' or 'user'


def sop_name(path: Path) -> Optional[str]:
    """The SOP's name, or None when the file is not a usable ``*.sop.md``."""
    fname = path.name
    if not fname.endswith(SOP_SUFFIX):
        return None
    name = fname[:-len(SOP_SUFFIX)]
    return name if _NAME_RE.match(name) else None


def iter_sop_files(root: Path, max_depth: int = MAX_SOP_DEPTH) -> Iterator[Path]:
    """``*.sop.md`` files under ``root`` in name order, files before
    subdirectories.  Hidden entries are skipped; a missing or unreadable
    directory yields nothing.  Symlinks are followed; the depth bound stops
    a cycle."""
    def walk(d: Path, depth: int) -> Iterator[Path]:
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        dirs = []
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                dirs.append(child)
            elif child.name.endswith(SOP_SUFFIX) and child.is_file():
                yield child
        if depth < max_depth:
            for sub in dirs:
                yield from walk(sub, depth + 1)

    if root.is_dir():
        yield from walk(root, 1)


def _env_roots(project_root: Optional[str]) -> List[Path]:
    raw = os.environ.get(SOP_PATHS_ENV, "")
    out: List[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        p = Path(os.path.expanduser(part))
        if not p.is_absolute():
            p = Path(project_root or os.getcwd()) / p
        out.append(p)
    return out


def _plugin_roots() -> List[Path]:
    out: List[Path] = []
    try:
        from app.plugins import get_sop_root_providers
        providers = get_sop_root_providers()
    except Exception as e:  # noqa: BLE001 -- discovery must not fail on a seam
        logger.debug("SOP root providers unavailable: %s", e)
        return out
    for provider in providers:
        try:
            for entry in provider.roots() or []:
                out.append(Path(entry[0] if isinstance(entry, (tuple, list)) else entry))
        except Exception as e:  # noqa: BLE001 -- one provider must not hide the rest
            logger.debug("SOP root provider failed: %s", e)
    return out


def sop_roots(project_root: Optional[str] = None) -> List[Tuple[Path, str]]:
    """Every SOP root as ``(path, source)``, in precedence order.  The roots
    need not exist."""
    roots: List[Tuple[Path, str]] = [(p, "user") for p in _env_roots(project_root)]
    if project_root:
        roots += [(Path(project_root) / rel, "project") for rel in PROJECT_SOP_PATHS]
    try:
        from app.utils.paths import get_ziya_home
        roots.append((get_ziya_home() / "sops", "user"))
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not resolve Ziya home for SOPs: %s", e)
    roots += [(p, "user") for p in _plugin_roots()]
    return roots


def sop_candidates(project_root: Optional[str] = None) -> List[SopFile]:
    """Every SOP file in every root, in precedence order (same name may
    appear more than once)."""
    out: List[SopFile] = []
    seen: set = set()
    for root, source in sop_roots(project_root):
        for path in iter_sop_files(root):
            name = sop_name(path)
            if name is None:
                logger.debug("Skipping %s: not a usable SOP file name", path)
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(SopFile(name=name, path=path, root=root, source=source))
    return out


def find_sops(project_root: Optional[str] = None) -> Dict[str, SopFile]:
    """``name -> file``; the first file found for a name is the one used."""
    out: Dict[str, SopFile] = {}
    for sop in sop_candidates(project_root):
        out.setdefault(sop.name, sop)
    return out


def _under(path: Path, parent: str) -> bool:
    for a, b in ((path, Path(parent)), (path.resolve(), Path(parent).resolve())):
        try:
            a.relative_to(b)
            return True
        except (ValueError, OSError):
            continue
    return False


def read_sop(name: str, *, project_root: Optional[str] = None,
             prefer_under: Optional[str] = None) -> Optional[Tuple[Path, str]]:
    """``(path, text)`` of the SOP called ``name``, or None.

    ``prefer_under``: when several roots hold a SOP of this name, one inside
    this directory is used.  A packaged agent names its own package's
    procedure; another package shipping the same name must not replace it.
    """
    matches = [s for s in sop_candidates(project_root) if s.name == name]
    if prefer_under:
        matches.sort(key=lambda s: 0 if _under(s.path, prefer_under) else 1)   # stable
    for sop in matches:
        try:
            return sop.path, sop.path.read_text(encoding="utf-8")
        except OSError as e:
            logger.debug("SOP %s unreadable: %s", sop.path, e)
    return None


def describe(text: str) -> str:
    """The first paragraph of ``## Overview``; without that section, the
    first paragraph of the file.  Headings are never part of it."""
    lines = (text or "").splitlines()
    start = next((i + 1 for i, line in enumerate(lines)
                  if line.strip().lower() == "## overview"), None)
    paragraph: List[str] = []
    for line in lines[start or 0:]:
        s = line.strip()
        if not s:
            if paragraph:
                break
            continue
        if s.startswith("#"):
            if paragraph or start is not None:
                break               # the Overview ended
            continue
        paragraph.append(s)
    return " ".join(paragraph)[:DESCRIPTION_MAX_CHARS]


def sop_skills(workspace_path: Optional[str], token_service: Any, *,
               load_body: bool = True) -> List[Any]:
    """Each SOP as a Skill of kind ``sop``.

    ``user_selectable``: a SOP is listed on the Bench and can be placed
    there; it is not put in the model's catalog by default, since a package
    can ship many and each Overview is a paragraph.
    """
    from app.models.skill import Skill
    from app.services.color_service import generate_color
    from app.services.skill_discovery import _stable_id
    out: List[Any] = []
    now = int(time.time() * 1000)
    for sop in find_sops(workspace_path).values():
        try:
            text = sop.path.read_text(encoding="utf-8")
            mtime = int(sop.path.stat().st_mtime * 1000)
        except OSError as e:
            logger.debug("SOP %s unreadable: %s", sop.path, e)
            continue
        out.append(Skill(
            id=_stable_id(str(sop.root), sop.name, prefix=f"{sop.source}-sop"),
            name=sop.name,
            description=describe(text) or sop.name,
            prompt=text if load_body else "",
            color=generate_color(sop.name),
            tokenCount=token_service.count_tokens(text) if text else 0,
            isBuiltIn=False,
            source=sop.source,
            visibility="user_selectable",
            kind="sop",
            createdAt=mtime,
            lastUsedAt=now,
            skillPath=str(sop.path),
        ))
    return out
