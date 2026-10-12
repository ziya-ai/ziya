"""One rule for "does this requested skill name refer to that skill".

A skill is referred to by three different strings in three places, and
until 2026-10-10 each consumer matched a different subset:

  * the system-prompt catalog and ``get_skill_details`` use the built-in
    ``id`` (``code_review``) or a discovered skill's directory name
    (``log-sweep``);
  * ``SkillStorage`` keys a built-in by ``builtin-code-review`` and a
    discovered skill by a path-hashed id a card author cannot know;
  * ``Skill.name`` is the display form (``Code Review``).

The task executor resolved ``scope.skills`` by storage id or exact
``name``, so a card that wrote ``skills: ["code_review"]`` -- the only
form the model is ever shown -- ran without the skill, with a warning
in the artifact's decisions and nothing else.  The card preflight had
its own comparison and disagreed with the executor about what matched.

``skill_key`` folds all three spellings to one token: lowercase, with
whitespace, ``_`` and ``-`` runs collapsed to ``-``, and a leading
``builtin-`` dropped.  ``Code Review``, ``code_review``, ``code-review``
and ``builtin-code-review`` are the same key.  An exact id match is still
tried first by every consumer, so a path-hashed discovered id keeps
working and two skills whose names collide under the key are told apart
when the request is precise.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Set

_SEP_RE = re.compile(r"[\s_\-]+")
_BUILTIN_PREFIX = "builtin-"


def skill_key(text: Optional[str]) -> str:
    """Canonical comparison key for a skill id or name.  ``""`` for empty."""
    s = _SEP_RE.sub("-", (text or "").strip().lower()).strip("-")
    if s.startswith(_BUILTIN_PREFIX):
        s = s[len(_BUILTIN_PREFIX):]
    return s


def skill_keys(skill: Any) -> Set[str]:
    """Every key under which ``skill`` (a Skill model, or a dict with
    ``id``/``name``) may be requested."""
    if isinstance(skill, dict):
        sid, name = skill.get("id"), skill.get("name")
    else:
        sid, name = getattr(skill, "id", None), getattr(skill, "name", None)
    return {k for k in (skill_key(sid), skill_key(name)) if k}


def skill_matches(requested: Optional[str], skill: Any) -> bool:
    """``requested`` names ``skill`` by exact id or by key."""
    if not requested:
        return False
    sid = skill.get("id") if isinstance(skill, dict) else getattr(skill, "id", None)
    if sid and requested.strip() == sid:
        return True
    return skill_key(requested) in skill_keys(skill)


def find_skill(requested: Optional[str], skills: Iterable[Any]) -> Optional[Any]:
    """The skill ``requested`` refers to, or None.

    Exact id first, so a precise reference is never shadowed by a
    display-name collision; then the first key match in iteration order
    (callers list stored skills before discovered ones, so a stored skill
    outranks a same-named SKILL.md as it does everywhere else).
    """
    if not requested:
        return None
    items = list(skills)
    want = requested.strip()
    for s in items:
        sid = s.get("id") if isinstance(s, dict) else getattr(s, "id", None)
        if sid and sid == want:
            return s
    key = skill_key(want)
    if not key:
        return None
    for s in items:
        if key in skill_keys(s):
            return s
    return None


def any_skill_matches(requested: Optional[str], available: Iterable[str]) -> bool:
    """For the preflight: ``requested`` matches some id or name in
    ``available`` (a flat list of ids and names)."""
    if not requested:
        return False
    want = requested.strip()
    key = skill_key(want)
    for a in available:
        if not a:
            continue
        if a == want or (key and skill_key(a) == key):
            return True
    return False
