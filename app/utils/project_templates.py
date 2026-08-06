"""
Project templates — seed a new project's settings from a named preset.

Why this exists
---------------
``ProjectSettings.defaultSkillIds`` already flows into every new chat (see
``app/api/chats.py`` -> ``ChatStorage.create``), and ``writePolicy`` already
gates tool writes.  Nothing downstream needed inventing.  What was missing
was something to *populate* those fields at creation time so a code project
starts with the skills a code project wants.

A template is therefore a **seed function for ProjectSettings**, not a new
inheritance layer.  Semantics are deliberately APPLY-ONCE: the template's
values are stamped into the project record at creation and the project owns
them from then on.  ``ProjectSettings.templateId`` records which template
was used purely as provenance ("why is this skill on?"), and is NOT consulted
when reading settings.  Live inheritance can be added later on top of an
apply-once world; it cannot be removed from an inheritance-based one without
breaking every project that came to depend on it.

Model lives here rather than in ``app/models/`` on purpose: detection, the
built-in catalogue, and the merge rules are one cohesive unit, and keeping
them together means the whole thing is importable by the prompt/CLI layers
without pulling in storage.

Scaffolding is explicitly out of scope.  A template never creates files in
the user's directory — it only ever writes to the project's own record in
``~/.ziya``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

# Fields of ProjectSettings a template is permitted to seed.  An allowlist
# rather than "whatever keys are present" so a hand-authored template file
# in ~/.ziya/templates cannot reach fields that were never meant to be
# template-controlled (and so a future ProjectSettings field is opt-in to
# templating rather than silently exposed).
TEMPLATABLE_SETTINGS_KEYS = frozenset({
    "defaultSkillIds",
    "defaultContextIds",
    "writePolicy",
    "contextManagement",
    "taskScope",
    "modelPreference",
})

# Settings keys whose values are lists that should be UNIONED rather than
# replaced when a template is applied over existing values.  At create time
# the project's settings are empty so this is academic, but an explicit
# "re-apply template" action would otherwise silently discard user additions.
_LIST_UNION_KEYS = frozenset({"defaultSkillIds", "defaultContextIds"})

# The id every project falls back to.  Seeds nothing.
GENERAL_TEMPLATE_ID = "general"
SOFTWARE_TEMPLATE_ID = "software_development"

# Built-in skill ids, as persisted.
#
# This is the trap worth spelling out: ``SkillStorage._ensure_built_in_skills``
# derives the on-disk id from the skill NAME
# (``f"builtin-{name.lower().replace(' ', '-')}"``) and ignores the ``'id'``
# field in ``BUILT_IN_SKILLS`` — which is what ``get_skill_by_id`` and the
# model-facing catalogue use.  Two namespaces for the same skill.
# ``defaultSkillIds`` is matched against the *persisted* id, so seeding
# ``'continuous_documentation'`` here would silently no-op.
SKILL_CONTINUOUS_DOCUMENTATION = "builtin-continuous-documentation"
SKILL_TESTS_FOR_EVERYTHING = "builtin-tests-for-everything"


class ProjectTemplate(BaseModel):
    """A named preset that seeds ProjectSettings for a new project."""

    model_config = {"extra": "allow"}

    id: str
    name: str
    description: str = ""
    # Whether this template ships with Ziya (not user-deletable).
    isBuiltIn: bool = False
    # Filenames/dirnames whose presence in a directory identifies a project
    # of this type.  Checked at the top level only — a marker buried three
    # directories down says nothing about what the project as a whole is.
    detectMarkers: List[str] = []
    # Partial ProjectSettings.  Only keys present are applied; keys absent
    # leave the project's own value alone.  Filtered through
    # TEMPLATABLE_SETTINGS_KEYS on apply.
    settings: Dict[str, Any] = {}


BUILT_IN_TEMPLATES: List[ProjectTemplate] = [
    ProjectTemplate(
        id=GENERAL_TEMPLATE_ID,
        name="General",
        description=(
            "No presets. Skills, context, and write policy start empty and "
            "you configure whatever the project turns out to need."
        ),
        isBuiltIn=True,
        detectMarkers=[],
        settings={},
    ),
    ProjectTemplate(
        id=SOFTWARE_TEMPLATE_ID,
        name="Software Development",
        description=(
            "Turns on the documentation-upkeep and test-coverage skills for "
            "every new conversation in the project."
        ),
        isBuiltIn=True,
        # Deliberately manifest/build files only.  ``.git`` was considered
        # and rejected: a notes or docs repo is version-controlled too, and
        # matching it would switch on test-coverage instructions for a
        # project that has no tests to cover.
        detectMarkers=[
            "pyproject.toml",
            "setup.py",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "Gemfile",
            "composer.json",
            "CMakeLists.txt",
            "Makefile",
            "build.sbt",
            "mix.exs",
            "pubspec.yaml",
        ],
        # Skills only — no writePolicy.
        #
        # Both skills are arguably inert without a matching write grant
        # ("keep Docs/ current" is unactionable if Docs/ is not writable),
        # which is the real argument for bundling settings into a template
        # at all.  It is still left out of the BUILT-IN preset because
        # detection is silent: widening write permission as a side effect of
        # a directory happening to contain a package.json is not a default
        # anyone opted into.  The MECHANISM is general — a user-authored
        # template snapshotted from a project they configured themselves
        # carries writePolicy happily, because that is an explicit act.
        settings={
            "defaultSkillIds": [
                SKILL_CONTINUOUS_DOCUMENTATION,
                SKILL_TESTS_FOR_EVERYTHING,
            ],
        },
    ),
]


def builtin_template_ids() -> List[str]:
    """Ids of the templates that ship with Ziya."""
    return [t.id for t in BUILT_IN_TEMPLATES]


def get_builtin_template(template_id: str) -> Optional[ProjectTemplate]:
    """Look up a built-in template by id.  None when not built in."""
    return next((t for t in BUILT_IN_TEMPLATES if t.id == template_id), None)


class TemplateDetection(BaseModel):
    """Result of sniffing a directory for a matching template."""

    template_id: str
    # The marker file that produced the match, for honest UI copy
    # ("detected from pyproject.toml").  None when the match came from a
    # fallback rather than evidence.
    marker: Optional[str] = None
    # True when a real marker was found on disk, as opposed to falling
    # back.  Callers use this to decide whether to say "detected".
    detected: bool = False


def detect_template(
    project_path: Optional[str],
    templates: Optional[List[ProjectTemplate]] = None,
) -> TemplateDetection:
    """Sniff *project_path* for template markers.

    Returns the first template whose marker set matches, preferring
    templates with more specific (longer) marker lists so a user-authored
    template can win over the broad built-in.  A directory that is missing,
    unreadable, or matches nothing yields the general template with
    ``detected=False`` — never an exception, because failing to classify a
    directory must not be able to fail project creation.
    """
    fallback = TemplateDetection(
        template_id=GENERAL_TEMPLATE_ID, marker=None, detected=False,
    )
    if not project_path:
        return fallback

    try:
        root = Path(project_path).expanduser()
        if not root.is_dir():
            return fallback
        present = {entry.name for entry in root.iterdir()}
    except OSError:
        # Unreadable directory (permissions, race, stale mount).  Classify
        # as unknown rather than propagating — see docstring.
        return fallback

    candidates = list(templates if templates is not None else BUILT_IN_TEMPLATES)
    # More markers == a more deliberately-specified template; let it win.
    candidates.sort(key=lambda t: len(t.detectMarkers or []), reverse=True)

    for tpl in candidates:
        for marker in tpl.detectMarkers or []:
            if marker in present:
                return TemplateDetection(
                    template_id=tpl.id, marker=marker, detected=True,
                )
    return fallback


def resolve_template_id(
    requested: Optional[str],
    detected: Optional[TemplateDetection],
    global_default: Optional[str],
) -> str:
    """Decide which template a new project should use.

    Precedence, strongest first:
      1. ``requested`` — the user picked one in the create dialog.
      2. ``detected`` — evidence on disk (only when it actually matched).
      3. ``global_default`` — the user's "template for new projects" pref.
      4. ``general``.

    Detection outranks the global default on purpose: a user who set a
    default was expressing what to do in the ABSENCE of evidence, and
    silently overriding a positive on-disk match with a blanket preference
    is how you end up with a Rust project configured as a notes folder.
    """
    if requested:
        return requested
    if detected is not None and detected.detected:
        return detected.template_id
    if global_default:
        return global_default
    return GENERAL_TEMPLATE_ID


def apply_template(
    settings: Dict[str, Any],
    template: Optional[ProjectTemplate],
) -> Dict[str, Any]:
    """Stamp *template*'s settings onto a ProjectSettings dict.

    Returns a NEW dict; *settings* is not mutated.  Only keys in
    ``TEMPLATABLE_SETTINGS_KEYS`` are honoured, list-valued keys in
    ``_LIST_UNION_KEYS`` are unioned (order-preserving, de-duplicated) with
    whatever is already there, and everything else replaces.

    ``templateId`` is recorded for provenance even when the template seeds
    nothing, so "this project was created as General" is distinguishable
    from "this project predates templates".
    """
    result = dict(settings or {})
    if template is None:
        return result

    for key, value in (template.settings or {}).items():
        if key not in TEMPLATABLE_SETTINGS_KEYS:
            # Silently ignored rather than raising: a hand-authored template
            # file with a stray key should not break project creation.
            continue
        if key in _LIST_UNION_KEYS and isinstance(value, list):
            existing = result.get(key) or []
            if not isinstance(existing, list):
                existing = []
            merged = list(existing)
            for item in value:
                if item not in merged:
                    merged.append(item)
            result[key] = merged
        else:
            result[key] = value

    result["templateId"] = template.id
    return result
