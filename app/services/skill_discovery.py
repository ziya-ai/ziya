"""
Agent Skills discovery — scans project directories for SKILL.md files
following the agentskills.io specification.

Discovery paths (checked in order):
  1. {project_root}/.agents/skills/
  2. {project_root}/.skills/
  3. {project_root}/SKILLS/

Each directory containing a SKILL.md is treated as one skill.  Directories
without one are grouping folders and are searched further, up to
MAX_SKILL_DEPTH levels below the root (see iter_skill_dirs).
"""
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from ..models.skill import Skill
from ..models.recipe import KIND_KEY, SIGNATURE_FILE
from ..recipes.layout import (
    RECIPE_PROJECT_PATHS, load_provenance, load_signature, user_recipe_roots,
)
from ..services.color_service import generate_color
from ..services.token_service import TokenService
from ..utils.skill_resources import skill_resource_manifest

logger = logging.getLogger(__name__)

# Remembers shadowing situations already reported, so the per-call
# discovery in list()/catalog paths doesn't re-log the same clash every
# request.  Keyed on (name, winner_path, tuple(shadowed_paths)).
# Shadowing is a normal, resolved condition (two installers shipping the
# same skill), so it is summarised once at INFO with per-skill detail at DEBUG.
_WARNED_SHADOWS: set = set()

# Project-relative skill roots, in priority order.  ``.ziya/skills`` is
# first so a project's Ziya-native skills win within the project tier; the
# rest cover other common agent harnesses (agentskills, Claude, Kiro).
DISCOVERY_PATHS = [
    ".ziya/skills",
    ".agents/skills",
    ".skills",
    "SKILLS",
    ".claude/skills",
    ".kiro/skills",
]

# Home-relative skill roots scanned for user-global skills.  The ``.ziya``
# user root is resolved via get_ziya_home() (honors $ZIYA_HOME); these are
# the non-Ziya harness equivalents under $HOME.  ``.agents/skills`` is the
# canonical global root ``npx skills add -g`` writes for every universal
# agent (Cursor, Codex, Copilot, Cline, Zed, ...), so one global install
# is visible to Ziya without a second copy.
USER_HARNESS_PATHS = [".agents/skills", ".claude/skills", ".kiro/skills"]

# How far below a skills root a skill directory may sit.  1 is the flat
# layout (root/<skill>/SKILL.md); 3 allows two levels of grouping folders.
MAX_SKILL_DEPTH = 3


def iter_skill_dirs(root: Path, max_depth: int = MAX_SKILL_DEPTH) -> Iterator[Path]:
    """Every skill directory under ``root``, depth-first in name order.

    A child holding a SKILL.md is a skill: it is yielded and never searched
    inside, because its references/ and scripts/ are resources, not skills.
    A child without one is a grouping folder and is searched further, until
    a skill would sit more than ``max_depth`` levels below ``root``.
    Hidden directories are skipped, and a missing or unreadable directory
    yields nothing.  Symlinks are followed; the depth bound stops a cycle.
    The leaf-name check stays in _skill_from_dir: ``name`` must match the
    skill directory, never a grouping folder.
    """
    def walk(d: Path, depth: int) -> Iterator[Path]:
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or not child.is_dir():
                continue
            if (child / "SKILL.md").is_file():
                yield child
            elif depth < max_depth:
                yield from walk(child, depth + 1)

    if root.is_dir():
        yield from walk(root, 1)


def user_skill_roots() -> List[Tuple[Path, bool]]:
    """User-global skill roots as ``(path, is_ziya)``, in precedence order.

    Shared by discovery and by the read-tool allowlist
    (app/utils/skill_resources.readable_skill_dirs) so the two cannot
    drift.  The roots need not exist.
    """
    roots: List[Tuple[Path, bool]] = []
    try:
        from ..utils.paths import get_ziya_home
        roots.append((get_ziya_home() / "skills", True))
    except Exception as e:
        logger.debug("Could not resolve Ziya home for user skills: %s", e)
    try:
        home = Path.home()
        for rel in USER_HARNESS_PATHS:
            roots.append((home / rel, rel.startswith(".ziya")))
    except Exception as e:
        logger.debug("Could not resolve $HOME for user skills: %s", e)
    # Plugin-contributed roots (e.g. an enterprise harness such as Amazon AIM
    # that installs skills under its own home).  Appended last so built-in
    # roots win a name clash; each entry is a Path/str (non-ziya) or an
    # (path, is_ziya) pair.  A failing provider must never break discovery or
    # the read-tool allowlist.
    try:
        from app.plugins import get_user_skill_root_providers
        for provider in get_user_skill_root_providers():
            try:
                for entry in provider.roots() or []:
                    if isinstance(entry, (tuple, list)):
                        path = entry[0]
                        is_ziya = bool(entry[1]) if len(entry) > 1 else False
                    else:
                        path, is_ziya = entry, False
                    roots.append((Path(path), is_ziya))
            except Exception as e:  # noqa: BLE001
                logger.debug("User skill root provider failed: %s", e)
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not consult user skill root providers: %s", e)
    return roots


# YAML frontmatter regex — captures everything between opening and closing ---
_FRONTMATTER_RE = re.compile(
    r"\A\s*---[ \t]*\n(.*?)\n---[ \t]*\n",
    re.DOTALL,
)

# Frontmatter parsing lives in app/utils/skill_frontmatter.py (lenient:
# multi-line plain scalars, | and > block scalars).  The old name is kept
# for callers and tests.
from ..utils.skill_frontmatter import parse_frontmatter as _parse_simple_yaml  # noqa: E402

# agentskills name validation
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _parse_metadata_string(raw: str) -> Dict[str, str]:
    """Convert 'author=org,version=1.0' back to a dict."""
    if not raw:
        return {}
    result = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, _, v = pair.partition("=")
            result[k.strip()] = v.strip()
    return result


def parse_skill_md(path: Path) -> Optional[Tuple[Dict[str, str], str]]:
    """Parse a SKILL.md file into (frontmatter_dict, body_markdown).

    Returns None if the file is missing, unreadable, or lacks valid frontmatter.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Cannot read %s: %s", path, e)
        return None

    m = _FRONTMATTER_RE.match(text)
    if not m:
        logger.warning("No valid YAML frontmatter in %s", path)
        return None

    frontmatter = _parse_simple_yaml(m.group(1))
    body = text[m.end():].strip()
    return frontmatter, body


def _lead_paragraph(markdown_body: str, *, max_chars: int = 1024) -> str:
    """Extract the first non-empty paragraph from a markdown body,
    skipping headers, blockquotes, and code fences.  Used as a
    fallback description for skills whose frontmatter doesn't
    supply one — the H1 + first paragraph in well-written skill
    pages already convey what the skill does, so falling back to
    a generic ``(prompt loaded on activation)`` placeholder there
    just hides useful information from the browser.

    Returns the joined paragraph text trimmed to ``max_chars``.
    Returns an empty string if no suitable paragraph is found.
    """
    if not markdown_body:
        return ""
    in_fence = False
    paragraph: list[str] = []
    for line in markdown_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if paragraph:
                break  # first paragraph found, stop
            continue
        if stripped.startswith("#") or stripped.startswith(">"):
            continue  # skip headers/blockquotes
        paragraph.append(stripped)
    return " ".join(paragraph).strip()[:max_chars]


def _stable_id(base_path: str, skill_name: str, prefix: str = "project") -> str:
    """Generate a deterministic ID for a file-backed skill.

    ``prefix`` distinguishes project ('project-…') from user-global
    ('user-…') skills; ``base_path`` is the discovery root so the same
    skill name under different roots yields distinct, stable IDs.
    """
    digest = hashlib.sha256(f"{base_path}:{skill_name}".encode()).hexdigest()[:12]
    return f"{prefix}-{skill_name}-{digest}"


def _skill_from_dir(
    child: Path,
    token_service: TokenService,
    *,
    load_body: bool,
    source: str,
    id_base: str,
    id_prefix: str,
    default_kind: str = "skill",
    include_archived: bool = False,
) -> Optional[Skill]:
    """Parse one skill directory's SKILL.md into a Skill.

    Shared by discover_project_skills and discover_user_skills so the two
    discovery roots cannot drift in parsing, name validation, or field
    population.  Returns None when the directory lacks a valid SKILL.md or
    fails agentskills.io name validation.
    """
    skill_md = child / "SKILL.md"
    if not skill_md.exists():
        return None

    parsed = parse_skill_md(skill_md)
    if parsed is None:
        return None

    fm, body = parsed
    name = fm.get("name", "")

    # Validate name per agentskills spec
    if not name or not _NAME_RE.match(name) or len(name) > 64:
        logger.warning(
            "Skipping %s: invalid name '%s' (must be lowercase-hyphenated, 1-64 chars)",
            skill_md, name,
        )
        return None

    # Spec requires: name must match parent directory name
    if name != child.name:
        logger.warning(
            "Skipping %s: name '%s' does not match directory '%s'",
            skill_md, name, child.name,
        )
        return None

    # ``kind: recipe`` marks a skill directory that also carries a contract
    # (design/recipe-store.md): signature.json must exist and parse, and an
    # archived recipe is hidden unless the caller asks for it.  The marker
    # selects the stricter rule wherever the directory sits; a directory
    # under a recipes root defaults to the recipe kind.
    kind = (fm.get(KIND_KEY) or default_kind).strip().lower()
    if kind not in ("skill", "recipe"):
        logger.warning("%s: unknown kind '%s'; treating as a skill", skill_md, kind)
        kind = "skill"
    if kind == "recipe":
        if not (child / SIGNATURE_FILE).is_file():
            logger.warning(
                "Skipping recipe '%s': no %s (a recipe needs its contract)",
                name, SIGNATURE_FILE,
            )
            return None
        if load_signature(child) is None:
            logger.warning(
                "Skipping recipe '%s': %s does not parse", name, SIGNATURE_FILE,
            )
            return None
        if not include_archived and load_provenance(child).status == "archived":
            logger.debug("Hiding archived recipe '%s'", name)
            return None

    # Frontmatter ``description:`` is the preferred source.  When absent,
    # fall back to the first markdown paragraph so under-specified SKILL.md
    # files still surface useful browse-time text.
    description = (fm.get("description", "") or _lead_paragraph(body))[:1024]
    prompt = body if load_body else ""
    now = int(time.time() * 1000)

    skill_metadata = _parse_metadata_string(fm.get("metadata", ""))
    # visibility: 'model_discoverable' (auto-loadable via the model's skill
    # catalog) or 'user_selectable' (toggled by the user in the UI).
    # 'visibility' is NOT an agentskills.io frontmatter field, so the
    # spec-conformant location is the free-form metadata map under the
    # ``ziya-visibility`` key.  A top-level ``visibility:`` is still honored
    # as a back-compat fallback for files authored before this convention.
    # Defaults to user_selectable per agentskills.io conservatism.
    # A recipe defaults to model_discoverable: the bench places it
    # ondemand and the model may load it, but it is never on by default.
    visibility = (
        skill_metadata.get("ziya-visibility")
        or fm.get("visibility", "")
    ).strip().lower()
    if visibility not in ("model_discoverable", "user_selectable"):
        visibility = "model_discoverable" if kind == "recipe" else "user_selectable"

    return Skill(
        id=_stable_id(id_base, name, prefix=id_prefix),
        name=name,
        description=description,
        prompt=prompt,
        color=generate_color(name),
        # The body is already read (for the frontmatter), so its weight is
        # known even when the prompt is left unloaded; a bench list must show
        # every skill's tokens, not only the loaded ones'.
        tokenCount=token_service.count_tokens(body) if body else 0,
        isBuiltIn=False,
        source=source,
        visibility=visibility,
        kind=kind,
        createdAt=int(skill_md.stat().st_mtime * 1000),
        lastUsedAt=now,
        # agentskills metadata
        keywords=fm.get("keywords", "").split() if fm.get("keywords") else None,
        license=fm.get("license"),
        compatibility=fm.get("compatibility"),
        skillMetadata=skill_metadata,
        allowedTools=fm.get("allowed-tools", "").split() if fm.get("allowed-tools") else None,
        skillPath=str(child),
        hasScripts=(child / "scripts").is_dir(),
        hasReferences=(child / "references").is_dir(),
        hasAssets=(child / "assets").is_dir(),
        # Rendered only alongside the body: the cheap catalog (load_body=False)
        # must not walk every skill directory.
        resourceManifest=(skill_resource_manifest(str(child)) or None)
        if load_body else None,
    )


def discover_project_skills(
    workspace_path: str,
    token_service: TokenService,
    *,
    load_body: bool = True,
) -> List[Skill]:
    """Scan the project workspace for agentskills-format skill directories.

    Args:
        workspace_path: Absolute path to the project root.
        token_service: For counting prompt tokens.
        load_body: If False, only loads frontmatter (progressive disclosure).

    Returns:
        List of Skill objects with source='project'.
    """
    root = Path(workspace_path)
    if not root.is_dir():
        return []

    skills: List[Skill] = []
    seen_names: set = set()

    for rel_dir in DISCOVERY_PATHS:
        skills_root = root / rel_dir
        if not skills_root.is_dir():
            continue

        for child in iter_skill_dirs(skills_root):
            skill = _skill_from_dir(
                child, token_service, load_body=load_body,
                source="project", id_base=workspace_path, id_prefix="project",
            )
            if skill is None:
                continue
            if skill.name in seen_names:
                continue  # First discovery path wins
            seen_names.add(skill.name)
            skills.append(skill)

    logger.info("Discovered %d project skills from %s", len(skills), workspace_path)
    return skills


def discover_user_skills(
    token_service: TokenService,
    *,
    load_body: bool = True,
) -> List[Skill]:
    """Scan ~/.ziya/skills/<name>/SKILL.md for user-global skills.

    Cross-project sibling of discover_project_skills: same grouping-folder
    walk (iter_skill_dirs), same strict SKILL.md + name-matches-directory
    validation, same agentskills.io frontmatter.  These skills live under
    the Ziya home directory so they are available in every project.

    Returns:
        List of Skill objects with source='user'.
    """
    from ..utils.paths import get_ziya_home
    try:
        skills_root = get_ziya_home() / "skills"
    except Exception as e:
        logger.debug("Could not resolve Ziya home for user skills: %s", e)
        return []

    if not skills_root.is_dir():
        return []

    id_base = str(skills_root)
    skills: List[Skill] = []
    seen_names: set = set()

    for child in iter_skill_dirs(skills_root):
        skill = _skill_from_dir(
            child, token_service, load_body=load_body,
            source="user", id_base=id_base, id_prefix="user",
        )
        if skill is None:
            continue
        if skill.name in seen_names:
            continue
        seen_names.add(skill.name)
        skills.append(skill)

    logger.info("Discovered %d user skills from %s", len(skills), skills_root)
    return skills


def _scan_skill_root(
    skills_root: Path,
    token_service: TokenService,
    *,
    load_body: bool,
    source: str,
    id_base: str,
    id_prefix: str,
    default_kind: str = "skill",
) -> List[Skill]:
    """Parse every valid skill directory under ``skills_root`` (see iter_skill_dirs).

    Unlike discover_project_skills/discover_user_skills this performs NO
    name-level dedup — callers (notably discover_all_skills) need the raw
    per-root candidate list so cross-root precedence can be applied.
    Returns an empty list when the root does not exist.
    """
    if not skills_root.is_dir():
        return []
    out: List[Skill] = []
    for child in iter_skill_dirs(skills_root):
        skill = _skill_from_dir(
            child, token_service, load_body=load_body,
            source=source, id_base=id_base, id_prefix=id_prefix,
            default_kind=default_kind,
        )
        if skill is not None:
            out.append(skill)
    return out


def discover_all_skills(
    workspace_path: Optional[str],
    token_service: TokenService,
    *,
    load_body: bool = True,
) -> List[Skill]:
    """Discover skills across every well-known root and resolve name clashes.

    Roots scanned (each existing root only):
      * Project (relative to ``workspace_path``): the DISCOVERY_PATHS list,
        i.e. .ziya/skills, .agents/skills, .skills, SKILLS, .claude/skills,
        .kiro/skills.
      * User-global: ~/.ziya/skills (via get_ziya_home, honors $ZIYA_HOME)
        plus the USER_HARNESS_PATHS equivalents under $HOME.
      * Recipes (design/recipe-store.md): <project>/.ziya/recipes and
        ~/.ziya/recipes, parsed with ``kind: recipe`` as the default.

    When the same ``(kind, name)`` appears in more than one root, a single
    winner is chosen by this precedence (lower rank wins) -- a skill and a
    recipe of the same name are distinct and both survive:
      1. a ``.ziya`` root beats any non-ziya root (absolute preference);
      2. otherwise a project root beats a user-global root;
      3. otherwise the newest SKILL.md (by mtime) wins.
    New clashes are summarised in one INFO line; each winning/shadowed
    path pair is logged at DEBUG.

    Returns a name-deduped list.  IDs/sources match the legacy
    discover_project_skills / discover_user_skills outputs so stored-skill
    cross-references stay stable.
    """
    # (kind, name) -> list of (skill, is_ziya, tier_rank)  (0=project, 1=user)
    candidates: Dict[Tuple[str, str], List[Tuple[Skill, bool, int]]] = {}

    def _add(skill: Skill, is_ziya: bool, tier_rank: int) -> None:
        candidates.setdefault((skill.kind or "skill", skill.name), []).append(
            (skill, is_ziya, tier_rank))

    # --- project roots ---
    if workspace_path:
        root = Path(workspace_path)
        if root.is_dir():
            for rel in DISCOVERY_PATHS:
                is_ziya = rel.startswith(".ziya")
                for skill in _scan_skill_root(
                    root / rel, token_service, load_body=load_body,
                    source="project", id_base=workspace_path, id_prefix="project",
                ):
                    _add(skill, is_ziya, 0)
            for rel in RECIPE_PROJECT_PATHS:
                for skill in _scan_skill_root(
                    root / rel, token_service, load_body=load_body,
                    source="project", id_base=str(root / rel), id_prefix="project",
                    default_kind="recipe",
                ):
                    _add(skill, True, 0)

    # --- user-global roots ---
    for skills_root, is_ziya in user_skill_roots():
        for skill in _scan_skill_root(
            skills_root, token_service, load_body=load_body,
            source="user", id_base=str(skills_root), id_prefix="user",
        ):
            _add(skill, is_ziya, 1)
    for recipes_root, is_ziya in user_recipe_roots():
        for skill in _scan_skill_root(
            recipes_root, token_service, load_body=load_body,
            source="user", id_base=str(recipes_root), id_prefix="user",
            default_kind="recipe",
        ):
            _add(skill, is_ziya, 1)

    # --- resolve precedence per (kind, name) ---
    result: List[Skill] = []
    new_shadows = 0
    for (kind, name), cands in candidates.items():
        # Sort ascending so the first element is the winner:
        #   ziya first, then project-over-user, then newest mtime.
        ranked = sorted(
            cands,
            key=lambda c: (0 if c[1] else 1, c[2], -c[0].createdAt),
        )
        winner = ranked[0][0]
        if len(ranked) > 1:
            shadowed = [c[0].skillPath for c in ranked[1:]]
            sig = (name, winner.skillPath, tuple(shadowed))
            if sig not in _WARNED_SHADOWS:
                _WARNED_SHADOWS.add(sig)
                new_shadows += 1
                logger.debug(
                    "%s '%s' found in multiple roots; using %s "
                    "(preferred), shadowing: %s",
                    kind.capitalize(), name, winner.skillPath,
                    ", ".join(p for p in shadowed if p),
                )
        result.append(winner)

    if new_shadows:
        logger.info(
            "%d skill name(s) found in multiple roots; using the preferred "
            "copy of each (ZIYA_LOG_LEVEL=DEBUG lists the shadowed paths)",
            new_shadows,
        )
    logger.info(
        "Discovered %d skills across all roots (workspace=%s)",
        len(result), workspace_path,
    )
    return result
