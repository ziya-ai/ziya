"""
Skill sources (design/capabilities-hub.md, slice 8a).

A source is a pool of installable skills: a local folder, a git repo, later
a registry.  ``SkillSourceProvider`` parallels ``mcp.registry.RegistryProvider``
so an enterprise skills registry plugs in the same way an MCP registry does
(``app.plugins.register_skill_source_provider``).  User-added sources persist
in ``~/.ziya/state/skill_sources.json``.

Install copies a skill's bundle into a root discovery already scans and writes
``.provenance.json`` beside SKILL.md: ``{source, remote_id, version, checksum,
installed_at}``.  ``current_version`` against that record is update detection.
Installed skills stay read-only in the hub (fork-to-project is backlog).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.skill_discovery import parse_skill_md
from app.storage.json_state import JsonStateStore
from app.utils.paths import cache_dir, get_ziya_home, user_state_file

logger = logging.getLogger(__name__)

PROVENANCE_FILE = ".provenance.json"
SOURCES_STATE = "skill_sources"
BUNDLE_DIRS = ("scripts", "references", "assets")


@dataclass
class SkillListing:
    source: str
    id: str                      # stable within the source (the skill dir name)
    name: str
    description: str
    version: str
    requires: List[str] = field(default_factory=list)   # e.g. ["mcp:kuiper-jira-mcp"]
    support_level: Optional[str] = None                  # a badge on a hit, never a gate (8b)
    matched: List[str] = field(default_factory=list)     # skills in a package that hit a query


@dataclass
class SkillBundle:
    listing: SkillListing
    files: Dict[str, bytes]      # relative path -> content; always has SKILL.md


@dataclass
class SearchResult:
    """One source's answer to a query: the top hits and how many there were in
    all (None when the source cannot say).  Browse renders a column of these
    per source; it never asks a source for everything (slice 8b)."""
    hits: List[SkillListing]
    total: Optional[int] = None


class SkillSourceProvider(ABC):
    kind: str = "unknown"
    # False for a registry too large to list.  Browse's bench-derived panels
    # skip it; only ``search`` reaches it.  ``list`` may still serve ``detail``.
    enumerable: bool = True

    @property
    @abstractmethod
    def identifier(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def location(self) -> str:
        return ""

    @abstractmethod
    def list(self) -> List[SkillListing]: ...

    def detail(self, skill_id: str) -> Optional[SkillListing]:
        return next((s for s in self.list() if s.id == skill_id), None)

    def search(self, q: str, limit: int = 8) -> SearchResult:
        """Substring match on name/description over ``list()``.  A registry
        provider overrides this with its own query so it is never listed."""
        needle = q.strip().lower()
        hits = [l for l in self.list() if needle in l.name.lower() or needle in l.description.lower()]
        return SearchResult(hits=hits[:limit], total=len(hits))

    @abstractmethod
    def fetch_bundle(self, skill_id: str) -> SkillBundle: ...

    @abstractmethod
    def current_version(self, skill_id: str) -> Optional[str]: ...


# -- frontmatter helpers ----------------------------------------------------

def parse_requires(value: Optional[str]) -> List[str]:
    """``requires: [mcp:a, mcp:b]`` / ``requires: mcp:a, mcp:b`` -> list."""
    if not value:
        return []
    return [p.strip().strip("'\"") for p in re.split(r"[,\s]+", value.strip().strip("[]")) if p.strip()]


def _bundle_files(skill_dir: Path) -> Dict[str, bytes]:
    files = {"SKILL.md": (skill_dir / "SKILL.md").read_bytes()}
    for sub in BUNDLE_DIRS:
        d = skill_dir / sub
        if d.is_dir():
            for p in sorted(x for x in d.rglob("*") if x.is_file()):
                files[str(p.relative_to(skill_dir))] = p.read_bytes()
    return files


def checksum(files: Dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode()); h.update(b"\0"); h.update(files[rel]); h.update(b"\0")
    return h.hexdigest()[:16]


# -- providers --------------------------------------------------------------

class LocalFolderSource(SkillSourceProvider):
    """A directory whose subdirectories each hold a SKILL.md."""
    kind = "folder"

    def __init__(self, identifier: str, path: str, name: Optional[str] = None):
        self._id, self._path, self._name = identifier, Path(path).expanduser(), name or identifier

    identifier = property(lambda self: self._id)
    name = property(lambda self: self._name)
    location = property(lambda self: str(self._path))

    def _dirs(self) -> List[Path]:
        if not self._path.is_dir():
            return []
        return sorted(d for d in self._path.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())

    def _listing(self, d: Path) -> Optional[SkillListing]:
        parsed = parse_skill_md(d / "SKILL.md")
        if not parsed:
            return None
        fm, _body = parsed
        return SkillListing(source=self.identifier, id=d.name, name=fm.get("name") or d.name,
                            description=fm.get("description", ""), version=self.current_version(d.name) or "",
                            requires=parse_requires(fm.get("requires")))

    def list(self) -> List[SkillListing]:
        return [l for l in (self._listing(d) for d in self._dirs()) if l]

    def fetch_bundle(self, skill_id: str) -> SkillBundle:
        d = self._path / skill_id
        listing = self._listing(d) if (d / "SKILL.md").is_file() else None
        if listing is None:
            raise KeyError(skill_id)
        return SkillBundle(listing=listing, files=_bundle_files(d))

    def current_version(self, skill_id: str) -> Optional[str]:
        d = self._path / skill_id
        return checksum(_bundle_files(d)) if (d / "SKILL.md").is_file() else None


class GitSource(LocalFolderSource):
    """A git repo of skill dirs, cloned under cache_dir('skill_sources').

    Version is the checkout's HEAD sha (whole repo, as the design note says);
    ``refresh`` fetches and fast-forwards.  Clone/fetch failures leave the last
    checkout in place and are logged, never raised into a GET.
    """
    kind = "git"

    def __init__(self, identifier: str, url: str, ref: str = "", name: Optional[str] = None,
                 subdir: str = ""):
        self._url, self._ref, self._subdir = url, ref, subdir
        self._checkout = cache_dir("skill_sources") / re.sub(r"[^A-Za-z0-9_.-]", "_", identifier)
        super().__init__(identifier, str(self._checkout / subdir if subdir else self._checkout), name)

    location = property(lambda self: self._url)

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(self._checkout), capture_output=True, text=True, timeout=60)

    def refresh(self) -> None:
        try:
            if not (self._checkout / ".git").is_dir():
                cmd = ["git", "clone", "--depth", "1"] + (["--branch", self._ref] if self._ref else []) + [self._url, str(self._checkout)]
                subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
            else:
                self._git("fetch", "--depth", "1", "origin", *([self._ref] if self._ref else []))
                self._git("reset", "--hard", "FETCH_HEAD")
        except Exception as e:  # noqa: BLE001
            logger.warning("skill source %s: git refresh failed: %s", self.identifier, e)

    def head(self) -> Optional[str]:
        if not (self._checkout / ".git").is_dir():
            return None
        r = self._git("rev-parse", "HEAD")
        return r.stdout.strip() or None if r.returncode == 0 else None

    def list(self) -> List[SkillListing]:
        self.refresh()
        return super().list()

    def current_version(self, skill_id: str) -> Optional[str]:
        return self.head() if (self._path / skill_id / "SKILL.md").is_file() else None


# -- source registry --------------------------------------------------------

def _store() -> JsonStateStore:
    return JsonStateStore(user_state_file(SOURCES_STATE))


def _from_record(r: Dict[str, Any]) -> Optional[SkillSourceProvider]:
    kind = r.get("kind")
    if kind == "folder" and r.get("path"):
        return LocalFolderSource(r["id"], r["path"], r.get("name"))
    if kind == "git" and r.get("url"):
        return GitSource(r["id"], r["url"], r.get("ref", ""), r.get("name"), r.get("subdir", ""))
    return None


def list_sources() -> List[SkillSourceProvider]:
    """User-added sources (state file) plus plugin-registered ones."""
    out: List[SkillSourceProvider] = []
    for r in _store().get("sources", []) or []:
        p = _from_record(r)
        if p:
            out.append(p)
    try:
        from app.plugins import get_skill_source_providers
        out.extend(get_skill_source_providers())
    except Exception as e:  # noqa: BLE001
        logger.debug("skill sources: plugin lookup failed: %s", e)
    return out


def get_source(identifier: str) -> Optional[SkillSourceProvider]:
    return next((s for s in list_sources() if s.identifier == identifier), None)


def add_source(record: Dict[str, Any]) -> SkillSourceProvider:
    """Persist a folder/git source; ValueError when the record is not one."""
    if not record.get("id") or _from_record(record) is None:
        raise ValueError("source needs id and kind=folder+path or kind=git+url")
    _store().update(lambda d: d.setdefault("sources", []).__setitem__(
        slice(None), [r for r in d.get("sources", []) if r.get("id") != record["id"]] + [record]))
    return _from_record(record)  # type: ignore[return-value]


def remove_source(identifier: str) -> None:
    _store().update(lambda d: d.__setitem__("sources", [r for r in d.get("sources", []) if r.get("id") != identifier]))


# -- install / provenance ---------------------------------------------------

def read_provenance(skill_path: Optional[str]) -> Optional[Dict[str, Any]]:
    """The ``.provenance.json`` beside a discovered skill, if any."""
    if not skill_path:
        return None
    p = Path(skill_path)
    d = p if p.is_dir() else p.parent
    f = d / PROVENANCE_FILE
    try:
        return json.loads(f.read_text()) if f.is_file() else None
    except (OSError, ValueError):
        return None


def install_root(project_root: Optional[str], layer: str) -> Path:
    if layer == "project":
        if not project_root:
            raise ValueError("project install needs a project root")
        return Path(project_root) / ".ziya" / "skills"
    return get_ziya_home() / "skills"


def _split_bundle(files: Dict[str, bytes]) -> Dict[str, Dict[str, bytes]]:
    """One skill (top-level SKILL.md) or a package of skills (``<dir>/SKILL.md``
    each).  Returns ``{skill_dir_name: files}`` with paths relative to the skill.
    A package is what an AIM capability or a git repo of skills delivers;
    discovery is one level under the root, so each skill must land there
    itself, never nested under the package name."""
    if "SKILL.md" in files:
        return {"": files}
    out: Dict[str, Dict[str, bytes]] = {}
    for rel, data in files.items():
        top, _, rest = rel.partition("/")
        if rest:
            out.setdefault(top, {})[rest] = data
    return {k: v for k, v in out.items() if "SKILL.md" in v}


def install_skill(source_id: str, skill_id: str, root: Path) -> Dict[str, Any]:
    """Copy the bundle under ``root/<skill>`` (one dir per skill for a package)
    and write provenance beside each SKILL.md.  Returns the record for the
    first skill plus ``path`` and ``installed`` (every skill dir written).
    KeyError when source or skill is unknown."""
    src = get_source(source_id)
    if src is None:
        raise KeyError(source_id)
    bundle = src.fetch_bundle(skill_id)
    parts = _split_bundle(bundle.files)
    if not parts:
        raise ValueError(f"{source_id}/{skill_id}: bundle has no SKILL.md")
    installed: List[str] = []
    first: Optional[Dict[str, Any]] = None
    for sub, files in parts.items():
        dest = root / (sub or bundle.listing.id)
        if dest.exists():
            shutil.rmtree(dest)
        for rel, data in files.items():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_bytes(data)
        fm = parse_skill_md(dest / "SKILL.md")
        name = (fm[0].get("name") if fm else None) or dest.name
        record = {"source": source_id, "remote_id": skill_id, "version": bundle.listing.version,
                  "checksum": checksum(files), "installed_at": int(time.time()),
                  "requires": bundle.listing.requires if not sub else parse_requires(fm[0].get("requires") if fm else "")}
        (dest / PROVENANCE_FILE).write_text(json.dumps(record, indent=2))
        installed.append(str(dest))
        first = first or {**record, "name": name, "path": str(dest)}
    return {**first, "installed": installed}  # type: ignore[arg-type]


def configured_mcp_servers() -> set:
    """Server names in the MCP config, enabled or not; empty when unavailable."""
    try:
        from app.mcp.manager import get_mcp_manager
        return set((get_mcp_manager().server_configs or {}).keys())
    except Exception:  # noqa: BLE001
        return set()


def missing_requirements(requires: List[str]) -> List[str]:
    """``mcp:<name>`` entries not present in the MCP config."""
    have = configured_mcp_servers()
    return [r for r in requires if r.startswith("mcp:") and r.partition(":")[2] not in have]


def update_available(provenance: Optional[Dict[str, Any]]) -> bool:
    """True when the source now reports a different version than was installed."""
    if not provenance:
        return False
    src = get_source(provenance.get("source", ""))
    if src is None:
        return False
    cur = src.current_version(provenance.get("remote_id", ""))
    return bool(cur) and cur != provenance.get("version")


def listing_dict(l: SkillListing) -> Dict[str, Any]:
    return asdict(l)
