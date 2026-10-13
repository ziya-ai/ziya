"""
The user's language locale, for the system prompt.

Without it the model picks a regional variety on its own and often
picks British English for a US user ("colour", "organise").  There is
no settings dialog to ask in, but the browser already states the
user's preference on every request in the ``Accept-Language`` header,
so the web path needs no configuration at all.

Resolution order:

1. the ``Accept-Language`` header of the current request, captured by
   ``ProjectContextMiddleware`` into a request-scoped ContextVar;
2. the process locale (``LC_ALL``, ``LC_MESSAGES``, ``LANG``), which
   covers the CLI and headless task runs that have no browser behind
   them.

The header is client-supplied text that ends up in the system prompt,
so only well-formed language tags are accepted; anything else is
dropped rather than sanitised.
"""

from __future__ import annotations

import contextvars
import os
import re
from typing import List, Optional, Tuple

# BCP 47-ish: a 2-3 letter language, then subtags of 1-8 alphanumerics.
# Length-capped so a crafted header cannot place much text in the prompt.
_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,3}$")
_MAX_TAG_LEN = 35

_request_locale: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_locale", default=None
)


def normalize_tag(raw: str) -> Optional[str]:
    """Canonical case for a language tag (``en-us`` -> ``en-US``), or None.

    Accepts the POSIX ``en_US`` spelling too.  Language is lower case,
    a 2-letter or 3-digit region upper case, a 4-letter script title
    case; other subtags are kept lower case.
    """
    tag = (raw or "").strip().replace("_", "-")
    if not tag or len(tag) > _MAX_TAG_LEN or not _TAG_RE.match(tag):
        return None
    parts = tag.split("-")
    out = [parts[0].lower()]
    for p in parts[1:]:
        if len(p) == 2 and p.isalpha() or len(p) == 3 and p.isdigit():
            out.append(p.upper())
        elif len(p) == 4 and p.isalpha():
            out.append(p.title())
        else:
            out.append(p.lower())
    return "-".join(out)


def _has_region(tag: str) -> bool:
    return any(
        (len(p) == 2 and p.isalpha()) or (len(p) == 3 and p.isdigit())
        for p in tag.split("-")[1:]
    )


def parse_accept_language(header: Optional[str]) -> Optional[str]:
    """The preferred tag in an ``Accept-Language`` header, or None.

    Highest ``q`` wins, ties going to the earlier entry.  When the
    winner has no region (``en``) but a later entry for the same
    language does (``en-US``), the regional one is returned: the region
    is the whole point here, and browsers do send ``en,en-US;q=0.9``.
    """
    if not header:
        return None
    ranked: List[Tuple[float, int, str]] = []
    for pos, item in enumerate(header.split(",")[:20]):
        fields = [f.strip() for f in item.split(";")]
        tag = normalize_tag(fields[0]) if fields[0] != "*" else None
        if not tag:
            continue
        q = 1.0
        for f in fields[1:]:
            if f.lower().startswith("q="):
                try:
                    q = float(f[2:])
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        ranked.append((q, pos, tag))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (-r[0], r[1]))
    best = ranked[0][2]
    if not _has_region(best):
        lang = best.split("-")[0]
        for _q, _pos, tag in ranked[1:]:
            if tag.split("-")[0] == lang and _has_region(tag):
                return tag
    return best


def locale_from_env(environ: Optional[dict] = None) -> Optional[str]:
    """The process locale as a language tag (``en_US.UTF-8`` -> ``en-US``).

    ``C`` and ``POSIX`` say nothing about the user and are ignored.
    """
    env = os.environ if environ is None else environ
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = (env.get(var) or "").strip()
        if not val:
            continue
        base = val.split(".", 1)[0].split("@", 1)[0]
        if base.upper() in ("C", "POSIX"):
            continue
        tag = normalize_tag(base)
        if tag:
            return tag
    return None


def set_request_locale_from_header(header: Optional[str]) -> Optional[str]:
    """Record this request's browser locale.  Returns the tag stored."""
    tag = parse_accept_language(header)
    if tag:
        _request_locale.set(tag)
    return tag


def effective_locale() -> Tuple[Optional[str], str]:
    """``(tag, source)``; source is ``browser``, ``environment`` or ``""``."""
    tag = _request_locale.get()
    if tag:
        return tag, "browser"
    tag = locale_from_env()
    if tag:
        return tag, "environment"
    return None, ""


def locale_prompt_lines() -> List[str]:
    """The Session Context lines stating the locale; empty when unknown.

    Stable for a given user, so it is safe in the cached system prefix.
    """
    tag, source = effective_locale()
    if not tag:
        return []
    return [
        f'<UserLocale value="{tag}" source="{source}" />',
        f"Default language locale: {tag}. When writing in this locale's "
        f"language, use its regional variety (spelling, vocabulary, date "
        f"and number formats). Quoted text, code identifiers and existing "
        f"file content keep their own spelling. An explicit instruction "
        f"from the user overrides this.",
    ]
