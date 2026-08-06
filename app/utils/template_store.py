"""
Template store — user-authored project templates in ``~/.ziya``.

Storage shape
-------------
One file, ``~/.ziya/templates.json``, following the existing user-config
precedent (``mcp_config.json``, ``models.json``):

    {
      "defaultTemplateId": "software_development",
      "templates": [
        {"id": "...", "name": "...", "detectMarkers": [...],
         "settings": {...}}
      ]
    }

Why ``~/.ziya`` and not per-project: a template's whole purpose is to be
applied to a project that does not exist yet, so it cannot live inside one.
Per-project storage would also mean templates vanish on a fresh clone —
exactly when a new project is most likely to be created.

Both keys are optional and the file itself is optional.  A missing,
malformed, or unreadable file degrades to "no user templates, no default"
rather than raising: project creation must never be blocked by a
configuration file the user hand-edited.

Read-mostly and deliberately uncached.  The file is touched at project
creation and when the template list is displayed — neither is hot, and a
cache would mean a hand-edit needs a restart to take effect.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from app.utils.logging_utils import logger
from app.utils.paths import get_ziya_home
from app.utils.project_templates import (
    BUILT_IN_TEMPLATES,
    ProjectTemplate,
    get_builtin_template,
)

TEMPLATES_FILENAME = "templates.json"


def templates_file() -> Path:
    """Absolute path to the user's template file (may not exist)."""
    return get_ziya_home() / TEMPLATES_FILENAME


def _read_raw() -> dict:
    """Parse the template file, or {} when absent/unusable."""
    path = templates_file()
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(
            "Ignoring %s: could not read or parse it (%s). Falling back to "
            "built-in templates only.", path, e,
        )
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring %s: expected a JSON object.", path)
        return {}
    return data


def _write_raw(data: dict) -> None:
    """Atomically replace the template file.

    Written via temp-file-and-rename so a crash mid-write cannot leave a
    truncated file that would silently drop every user template on next
    read.  Plaintext by design — this is authored configuration a user is
    expected to be able to edit and diff, the same as ``models.json``.
    """
    path = templates_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_user_templates() -> List[ProjectTemplate]:
    """User-authored templates.  Invalid entries are skipped, not fatal."""
    raw = _read_raw().get("templates")
    if not isinstance(raw, list):
        return []
    out: List[ProjectTemplate] = []
    builtin_ids = {t.id for t in BUILT_IN_TEMPLATES}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            tpl = ProjectTemplate(**{**entry, "isBuiltIn": False})
        except Exception as e:  # noqa: BLE001 — one bad entry must not
            # take out the rest of the user's templates.
            logger.warning("Skipping malformed project template: %s", e)
            continue
        if not tpl.id or not tpl.name:
            continue
        if tpl.id in builtin_ids:
            # Shadowing a built-in id would make "which template is this?"
            # unanswerable and let a user file silently redefine the
            # shipped Software Development preset.
            logger.warning(
                "Skipping user template %r: id collides with a built-in.",
                tpl.id,
            )
            continue
        out.append(tpl)
    return out


def all_templates() -> List[ProjectTemplate]:
    """Built-ins followed by user templates, de-duplicated by id."""
    seen = set()
    out: List[ProjectTemplate] = []
    for tpl in [*BUILT_IN_TEMPLATES, *load_user_templates()]:
        if tpl.id in seen:
            continue
        seen.add(tpl.id)
        out.append(tpl)
    return out


def get_template(template_id: Optional[str]) -> Optional[ProjectTemplate]:
    """Resolve a template id across built-ins and user templates."""
    if not template_id:
        return None
    builtin = get_builtin_template(template_id)
    if builtin is not None:
        return builtin
    return next((t for t in load_user_templates() if t.id == template_id), None)


def get_default_template_id() -> Optional[str]:
    """The user's "template for new projects" preference, if set.

    A preference naming a template that no longer exists is treated as
    unset — reporting it would produce a create dialog offering a template
    that cannot be applied.
    """
    value = _read_raw().get("defaultTemplateId")
    if not isinstance(value, str) or not value:
        return None
    if get_template(value) is None:
        logger.warning(
            "Ignoring defaultTemplateId %r: no such template.", value,
        )
        return None
    return value


def set_default_template_id(template_id: Optional[str]) -> None:
    """Set (or clear, with None) the default-template preference."""
    data = _read_raw()
    if template_id:
        data["defaultTemplateId"] = template_id
    else:
        data.pop("defaultTemplateId", None)
    _write_raw(data)


def save_user_template(template: ProjectTemplate) -> ProjectTemplate:
    """Create or replace a user template.

    This is the "snapshot the project I already configured" path — the
    authoring UX is a snapshot rather than a template editor, so this
    receives an already-assembled template rather than building one.
    """
    if not template.id or not template.name:
        raise ValueError("A template needs both an id and a name.")
    if get_builtin_template(template.id) is not None:
        raise ValueError(
            f"{template.id!r} is a built-in template and cannot be replaced."
        )
    data = _read_raw()
    existing = data.get("templates")
    entries = [e for e in existing if isinstance(e, dict)] \
        if isinstance(existing, list) else []
    entries = [e for e in entries if e.get("id") != template.id]
    stored = template.model_dump()
    stored["isBuiltIn"] = False
    entries.append(stored)
    data["templates"] = entries
    _write_raw(data)
    return template


def delete_user_template(template_id: str) -> bool:
    """Remove a user template.  False when it did not exist.

    Deleting the template a project was created from is harmless: apply-once
    means the project already owns its settings, and ``templateId`` is only
    provenance.
    """
    if get_builtin_template(template_id) is not None:
        raise ValueError("Built-in templates cannot be deleted.")
    data = _read_raw()
    existing = data.get("templates")
    if not isinstance(existing, list):
        return False
    remaining = [
        e for e in existing
        if not (isinstance(e, dict) and e.get("id") == template_id)
    ]
    if len(remaining) == len(existing):
        return False
    data["templates"] = remaining
    # A default pointing at a now-deleted template would read as unset on
    # every subsequent load; clear it here so the file stays truthful.
    if data.get("defaultTemplateId") == template_id:
        data.pop("defaultTemplateId", None)
    _write_raw(data)
    return True
