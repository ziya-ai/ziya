"""
Agent Skills discovery — scans project directories for SKILL.md files
following the agentskills.io specification.

Discovery paths (checked in order):
  1. {project_root}/.agents/skills/
  2. {project_root}/.skills/
  3. {project_root}/SKILLS/

Each subdirectory containing a SKILL.md is treated as one skill.
"""
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..models.skill import Skill
from ..services.color_service import generate_color
from ..services.token_service import TokenService

logger = logging.getLogger(__name__)

# Directories to scan (relative to project root), in priority order
DISCOVERY_PATHS = [".agents/skills", ".skills", "SKILLS"]

# YAML frontmatter regex — captures everything between opening and closing ---
_FRONTMATTER_RE = re.compile(
    r"\A\s*---[ \t]*\n(.*?)\n---[ \t]*\n",
    re.DOTALL,
)

# Simple YAML key-value parser (avoids PyYAML dependency for this narrow use)
# Handles: key: value, key: "value", multi-line metadata blocks
_YAML_LINE_RE = re.compile(r"^(\w[\w-]*):\s*(.*?)\s*$")

# agentskills name validation
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _parse_simple_yaml(text: str) -> Dict[str, str]:
    """Parse flat YAML frontmatter into a dict.

    Handles simple key: value pairs and the metadata: block (one level deep).
    Does NOT handle full YAML — just enough for the agentskills spec.
    """
    result: Dict[str, str] = {}
    current_map_key: Optional[str] = None
    map_values: Dict[str, str] = {}

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect indented map entry (e.g. under metadata:)
        if current_map_key and line.startswith("  "):
            m = _YAML_LINE_RE.match(stripped)
            if m:
                map_values[m.group(1)] = m.group(2).strip('"').strip("'")
                continue

        # Flush any accumulated map
        if current_map_key and map_values:
            # Store as comma-separated k=v for simple transport
            result[current_map_key] = ",".join(f"{k}={v}" for k, v in map_values.items())
            map_values = {}
            current_map_key = None

        m = _YAML_LINE_RE.match(stripped)
        if m:
            key, value = m.group(1), m.group(2).strip('"').strip("'")
            if not value:
                # Bare key with no value — start of a map block
                current_map_key = key
            else:
                result[key] = value

    # Flush trailing map
    if current_map_key and map_values:
        result[current_map_key] = ",".join(f"{k}={v}" for k, v in map_values.items())

    return result


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
    visibility = (
        skill_metadata.get("ziya-visibility")
        or fm.get("visibility", "")
    ).strip().lower()
    if visibility not in ("model_discoverable", "user_selectable"):
        visibility = "user_selectable"

    return Skill(
        id=_stable_id(id_base, name, prefix=id_prefix),
        name=name,
        description=description,
        prompt=prompt,
        color=generate_color(name),
        tokenCount=token_service.count_tokens(prompt) if prompt else 0,
        isBuiltIn=False,
        source=source,
        visibility=visibility,
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

        for child in sorted(skills_root.iterdir()):
            if not child.is_dir():
                continue
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

    Cross-project sibling of discover_project_skills: same one-level
    directory model, same strict SKILL.md + name-matches-directory
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

    for child in sorted(skills_root.iterdir()):
        if not child.is_dir():
            continue
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
