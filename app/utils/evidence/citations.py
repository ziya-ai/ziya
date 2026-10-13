"""``{{claim:…}}`` citations in exported documents (design/evidence-ledger.md,
Citations).

``resolve_claim_citations`` runs on a document body before
``preprocess_document_body``.  It substitutes each citation's current value,
appends an **Evidence** appendix, records a Citation per substituted claim in
``.ziya/ledger/citations.jsonl``, and applies the document's policy:

    status           strict                       warn (default)
    unknown id       CitationError (lists ids)    ⟦missing: id⟧
    drifted/refuted  CitationError                value with ⚠, listed
    recorded         allowed, "unverified"        same
    superseded pin   allowed (it is pinned)       latest value, noted

A document without an ``evidence:`` front-matter block is untouched.  With
``policy: off`` values are substituted but nothing is appended or reported.

Syntax: ``{{claim:KEY|fmt=.2%|unit}}``.  ``KEY`` is a claim id or claim.field,
relative to the document's namespace, optionally pinned ``@vN``.  ``fmt`` is a
Python format spec (default: the claim's own ``format``); ``unit`` appends the
claim's unit, ``unit=X`` appends ``X``.  Fenced code is never touched.

The colon in ``claim:`` keeps these out of task templating's
``_PLACEHOLDER_RE``, whose names admit only identifier characters and dots.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .refs import format_value, resolve
from .store import Ledger, LedgerError, ledger_for

POLICIES = ("strict", "warn", "off")
DEFAULT_POLICY = "warn"
MAX_LINT_SAMPLES = 20

CITATION_RE = re.compile(r"\{\{\s*claim:\s*([^|}\s]+)\s*((?:\|[^|}]*)*)\s*\}\}")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
# A number in prose: not part of a word or identifier, not a list marker.
_NUMBER_RE = re.compile(r"(?<![\w.\-/#@])-?\d[\d,]*(?:\.\d+)?%?(?![\w\-/@])")
# List markers and (numbered) headings: "1. ", "## 2. Results", "### 3.1 ".
_LIST_MARKER_RE = re.compile(r"^\s*(?:#+\s+)?(?:\d+(?:\.\d+)*[.)]?\s)?")
_FLAGGED = ("drifted", "refuted")


class CitationError(ValueError):
    """A ``strict`` document cites claims that may not be published.

    The message names every offending id, so one export round-trip reports
    them all.  A ValueError so the export route answers 400.
    """

    def __init__(self, problems: List[Tuple[str, str]]):
        self.problems = problems
        ids = ", ".join(f"{cid} ({why})" for cid, why in problems)
        super().__init__(f"Evidence policy is strict; cannot export: {ids}")


@dataclass
class CitationReport:
    policy: str
    namespace: Optional[str]
    cited: List[Dict[str, Any]] = field(default_factory=list)   # one per distinct claim version
    missing: List[str] = field(default_factory=list)
    flagged: List[str] = field(default_factory=list)              # drifted / refuted ids
    unverified: List[str] = field(default_factory=list)
    superseded_pins: List[str] = field(default_factory=list)
    uncited_numbers: int = 0
    uncited_samples: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {
            "policy": self.policy, "namespace": self.namespace,
            "citations": len(self.cited), "missing": self.missing,
            "flagged": self.flagged, "unverified": self.unverified,
            "superseded_pins": self.superseded_pins,
            "uncited_numbers": self.uncited_numbers,
            "uncited_samples": self.uncited_samples,
        }


def evidence_settings(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``{namespace, policy}`` from normalized front-matter, or None."""
    ev = meta.get("evidence") if isinstance(meta, dict) else None
    if not isinstance(ev, dict):
        return None
    policy = str(ev.get("policy") or DEFAULT_POLICY).strip().lower()
    # YAML reads a bare `off` / `on` as a boolean before we see it.
    policy = {"false": "off", "no": "off", "true": DEFAULT_POLICY, "yes": DEFAULT_POLICY}.get(policy, policy)
    if policy not in POLICIES:
        policy = DEFAULT_POLICY
    ns = ev.get("namespace")
    return {"namespace": str(ns).strip() if ns else None, "policy": policy}


def _segments(body: str) -> List[Tuple[bool, str]]:
    """Split ``body`` into (is_code, text) runs; fenced blocks are code."""
    out: List[Tuple[bool, str]] = []
    lines = body.split("\n")
    buf: List[str] = []
    fence: Optional[str] = None
    for line in lines:
        m = _FENCE_RE.match(line)
        if fence is None and m:
            if buf:
                out.append((False, "\n".join(buf)))
            buf, fence = [line], m.group(1)[0] * 3
        elif fence is not None and m and m.group(1).startswith(fence):
            buf.append(line)
            out.append((True, "\n".join(buf)))
            buf, fence = [], None
        else:
            buf.append(line)
    if buf:
        out.append((fence is not None, "\n".join(buf)))
    return out


def _parse_options(raw: str) -> Dict[str, Optional[str]]:
    opts: Dict[str, Optional[str]] = {}
    for part in raw.split("|"):
        part = part.strip()
        if not part:
            continue
        k, sep, v = part.partition("=")
        opts[k.strip()] = v.strip() if sep else None
    return opts


def _source_summary(led: Ledger, claim: Dict[str, Any]) -> str:
    der = claim.get("derivation") or {}
    parts: List[str] = []
    for oid in der.get("obs_ids") or []:
        obs = led.observation(oid)
        if obs is None:
            parts.append(f"{oid} (missing)")
            continue
        res = obs.get("result") or {}
        ref = res.get("ref") or {}
        where = ref.get("path") or ref.get("id") or obs.get("connector") or obs.get("tool") or ""
        where = where.rsplit("/", 1)[-1] if where else ""
        sha = (res.get("sha256") or "")[:12]
        att = "" if obs.get("attested") else ", unattested"
        parts.append(f"{obs.get('source', '?')} {where} {sha}{att}".strip())
    for pin in der.get("inputs") or []:
        parts.append(f"from {pin}")
    return "; ".join(parts) or "—"


def _as_of(led: Ledger, claim: Dict[str, Any]) -> str:
    import datetime as dt
    stamps = []
    for oid in (claim.get("derivation") or {}).get("obs_ids") or []:
        obs = led.observation(oid)
        if obs:
            stamps.append(obs.get("as_of") or dt.datetime.fromtimestamp(
                obs["at"], dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ"))
    if not stamps:
        stamps.append(dt.datetime.fromtimestamp(claim["at"], dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ"))
    return min(stamps)


def _population(claim: Dict[str, Any]) -> str:
    pop = claim.get("population") or {}
    desc = str(pop.get("description") or "").strip()
    n = pop.get("n")
    if n is not None:
        desc = f"{desc} (n={n:,})" if isinstance(n, (int, float)) and not isinstance(n, bool) else f"{desc} (n={n})"
    return desc.strip() or "—"


def _appendix(report: CitationReport) -> str:
    rows = ["", "", "## Evidence", "",
            "| Claim | Value | Status | Population | Source | As of |",
            "|---|---|---|---|---|---|"]
    for c in report.cited:
        status = c["status"]
        if status == "recorded":
            status = "unverified"
        if c["id"] in report.flagged:
            status = f"⚠ {status}"
        if c.get("note"):
            status = f"{status} ({c['note']})"
        rows.append(f"| `{c['ref']}` | {c['rendered']} | {status} | {c['population']} "
                    f"| {c['source']} | {c['as_of']} |")
    for cid in report.missing:
        rows.append(f"| `{cid}` | ⟦missing⟧ | no such claim | — | — | — |")
    if report.unverified:
        rows += ["", f"Unverified: {len(report.unverified)} claim(s) recorded but never re-checked."]
    return "\n".join(rows) + "\n"


def _lint(prose_segments: List[str]) -> Tuple[int, List[Dict[str, Any]]]:
    count, samples = 0, []
    for seg in prose_segments:
        for line in seg.split("\n"):
            line = _LIST_MARKER_RE.sub("", line, count=1)
            for m in _NUMBER_RE.finditer(line):
                count += 1
                if len(samples) < MAX_LINT_SAMPLES:
                    samples.append({"text": m.group(0), "line": line.strip()[:120]})
    return count, samples


def resolve_claim_citations(
    body: str,
    meta: Dict[str, Any],
    *,
    project_root: str,
    document: str = "inline",
    ledger: Optional[Ledger] = None,
) -> Tuple[str, Optional[CitationReport]]:
    """Substitute citations in ``body`` under the document's evidence policy.

    Returns ``(new_body, report)``; ``report`` is None when the document has
    no ``evidence:`` block (body returned unchanged).  Raises CitationError
    under ``strict`` for unknown, drifted or refuted claims.
    """
    settings = evidence_settings(meta)
    if settings is None:
        return body, None
    policy, ns = settings["policy"], settings["namespace"]
    led = ledger or ledger_for(project_root)
    report = CitationReport(policy=policy, namespace=ns)
    problems: List[Tuple[str, str]] = []
    seen: Dict[str, Dict[str, Any]] = {}

    def sub(m: re.Match) -> str:
        ref, opts = m.group(1), _parse_options(m.group(2) or "")
        try:
            r = resolve(led, ref, ns)
        except LedgerError:
            if ref not in report.missing:
                report.missing.append(ref)
            problems.append((ref, "unknown claim"))
            return f"⟦missing: {ref}⟧"
        claim = r["claim"]
        value, note = r["value"], None
        status = claim["status"]
        if status == "superseded":
            # Only a pinned ref can resolve to a superseded version.  Its own
            # latest verdict still counts: a refuted v1 is refuted when pinned.
            latest = led.claim(claim["id"])
            own = (claim.get("latest_verdict") or {}).get("outcome")
            if policy == "strict":
                note = f"pinned v{claim['version']}, latest v{latest['version']}"
                if own in _FLAGGED:
                    status = own
            else:
                pinned_v = claim["version"]
                r = resolve(led, f"{claim['id']}.{r['field']}" if r["field"] != "value" else claim["id"], None)
                claim, value = r["claim"], r["value"]
                note = f"pin v{pinned_v} superseded; shows v{claim['version']}"
                status = claim["status"]
            if claim["id"] not in report.superseded_pins:
                report.superseded_pins.append(claim["id"])
        fmt = opts.get("fmt") or claim.get("format")
        unit = None
        if "unit" in opts:
            unit = opts["unit"] or claim.get("unit")
        rendered = format_value(value, fmt, unit)
        if status in _FLAGGED:
            if claim["id"] not in report.flagged:
                report.flagged.append(claim["id"])
            problems.append((claim["id"], status))
            rendered = f"⚠ {rendered}"
        elif status == "recorded" and claim["id"] not in report.unverified:
            report.unverified.append(claim["id"])
        pin = f"{claim['id']}@v{claim['version']}"
        key = f"{pin}.{r['field']}"
        if key not in seen:
            entry = {
                "ref": pin if r["field"] == "value" else f"{pin}.{r['field']}",
                "id": claim["id"], "pin": pin, "field": r["field"],
                "rendered": rendered.lstrip("⚠ "), "status": status, "note": note,
                "population": _population(claim), "source": _source_summary(led, claim),
                "as_of": _as_of(led, claim),
            }
            seen[key] = entry
            report.cited.append(entry)
        return rendered

    out_parts: List[str] = []
    prose_for_lint: List[str] = []
    for is_code, text in _segments(body):
        if is_code:
            out_parts.append(text)
            continue
        out_parts.append(CITATION_RE.sub(sub, text))
        prose_for_lint.append(CITATION_RE.sub(" ", text))
    new_body = "\n".join(out_parts)

    if policy == "strict" and problems:
        # Deduplicate while keeping order.
        uniq: List[Tuple[str, str]] = []
        for p in problems:
            if p not in uniq:
                uniq.append(p)
        raise CitationError(uniq)

    if policy == "off":
        return new_body, report

    report.uncited_numbers, report.uncited_samples = _lint(prose_for_lint)
    for entry in report.cited:
        try:
            led.record_citation(document, entry["pin"], entry["field"], entry["rendered"])
        except Exception:  # noqa: BLE001 -- recording the citation must not block export
            pass
    if report.cited or report.missing:
        new_body = new_body.rstrip("\n") + "\n" + _appendix(report)
    return new_body, report
