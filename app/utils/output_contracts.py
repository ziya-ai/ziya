"""
Output contracts for Task Card blocks (design/typed-findings.md).

A Task block may declare the outputs it must produce:

    "output_contract": [
        {"name": "finding", "schema": "project:audit-finding", "required": true},
        {"name": "notes", "schema": "ziya:text", "required": false}
    ]

This module owns everything about a contract that does not depend on the
executor: the schema registry, compilation, validating one emitted part,
the fixed set of cross-field checks, resolving ledger claim references,
and the generated text (prompt section, correction message) that tells the
model what the contract requires.  The executor and the artifact collector
call into it; nothing here imports them.

Schema references
-----------------
* ``ziya:<name>/v<n>`` -- shipped with Ziya, ``output_schemas/<name>_v<n>.json``
  next to this file.  ``ziya:<name>`` without a version means the highest
  shipped version; the version actually used is recorded at launch.
* ``project:<name>`` -- ``<project>/.ziya/schemas/<name>.json``.
* an inline JSON Schema object, at most 16 KB.

``$ref`` inside a schema may only name another ``ziya:`` / ``project:``
schema or a fragment of the same document.  Nothing is ever fetched.

Pinning
-------
``SchemaSet.resolve`` loads every schema a card references, transitively,
and ``to_snapshot`` records each with its SHA-256.  A run validates against
that snapshot, so editing ``.ziya/schemas/`` mid-run does not change what a
running card is checked against.

Numbers and claims
------------------
A number in a contracted output is ``{"claim": "ns.key", "field"?: "..."}``
(a ledger claim reference) or ``{"value": n, "unit"?, "source"?}`` (a
literal).  When ``require_claims`` is on, literals are rejected.  Claim
references are resolved through the evidence ledger: an unknown id or a
refuted claim rejects the part; otherwise the resolved value, status and
version are copied onto the part (``resolved_claims``).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

import jsonschema
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError, best_match
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

BUILTIN_DIR = Path(__file__).with_name("output_schemas")
PROJECT_SCHEMA_DIR = Path(".ziya") / "schemas"
INLINE_SCHEMA_MAX_BYTES = 16 * 1024
MAX_REPORTED_ERRORS = 20
SUMMARY_MAX_CHARS = 6000        # ~1,500 tokens
SUMMARY_MAX_DEPTH = 4
CHECKS_KEYWORD = "x-ziya-checks"
CHECK_KINDS = ("ordered", "range", "reconciles", "subset")

_BUILTIN_RE = re.compile(r"^ziya:([a-z0-9][a-z0-9_-]*)(?:/v(\d+))?$")
_PROJECT_RE = re.compile(r"^project:([A-Za-z0-9][A-Za-z0-9_.-]*)$")


class ContractError(ValueError):
    """A contract or schema that cannot be used (bad ref, bad schema, too big)."""


_ENSURE_LOCK = threading.Lock()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj)).hexdigest()


# ── loading ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SchemaDoc:
    """One schema document, pinned by its content hash."""
    id: str
    schema: Any
    sha256: str


def builtin_versions(name: str) -> List[int]:
    out = []
    for p in BUILTIN_DIR.glob(f"{name}_v*.json"):
        m = re.fullmatch(re.escape(name) + r"_v(\d+)\.json", p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def builtin_names() -> List[str]:
    """Every shipped schema id, e.g. ``ziya:finding/v1``."""
    out = []
    for p in sorted(BUILTIN_DIR.glob("*_v*.json")):
        m = re.fullmatch(r"(.+)_v(\d+)\.json", p.name)
        if m:
            out.append(f"ziya:{m.group(1)}/v{m.group(2)}")
    return out


def _read_json_file(path: Path) -> Any:
    raw = path.read_bytes()
    try:
        from app.utils.encryption import get_encryptor, is_encrypted
        if is_encrypted(raw):
            raw = get_encryptor().decrypt(raw)
    except ImportError:
        pass
    return json.loads(raw)


def canonical_ref(ref: Any) -> Optional[str]:
    """The registry id a string reference names, or None if it is not one."""
    if not isinstance(ref, str):
        return None
    ref = ref.strip()
    m = _BUILTIN_RE.match(ref)
    if m:
        name, ver = m.group(1), m.group(2)
        if ver is None:
            versions = builtin_versions(name)
            if not versions:
                return None
            ver = str(versions[-1])
        return f"ziya:{name}/v{ver}"
    if _PROJECT_RE.match(ref):
        return ref
    return None


def load_schema(ref: Any, project_root: Optional[str] = None) -> SchemaDoc:
    """Load one schema document by reference (or wrap an inline object)."""
    if isinstance(ref, dict):
        size = len(_canonical(ref))
        if size > INLINE_SCHEMA_MAX_BYTES:
            raise ContractError(
                f"inline schema is {size:,} bytes; the limit is {INLINE_SCHEMA_MAX_BYTES:,}. "
                "Move it to .ziya/schemas/<name>.json and reference it as project:<name>.")
        sha = _sha(ref)
        return SchemaDoc(id=f"inline:{sha[:16]}", schema=ref, sha256=sha)
    if not isinstance(ref, str) or not ref.strip():
        raise ContractError("schema must be a 'ziya:'/'project:' reference or a JSON Schema object")
    cid = canonical_ref(ref)
    if cid is None:
        raise ContractError(
            f"unknown schema reference {ref!r}: use ziya:<name>/v<n> "
            f"(shipped: {', '.join(builtin_names()) or 'none'}), project:<name>, or an inline schema")
    if cid.startswith("ziya:"):
        m = _BUILTIN_RE.match(cid)
        path = BUILTIN_DIR / f"{m.group(1)}_v{m.group(2)}.json"
        if not path.is_file():
            raise ContractError(
                f"no shipped schema {cid} (shipped: {', '.join(builtin_names()) or 'none'})")
        schema = _read_json_file(path)
    else:
        name = cid[len("project:"):]
        if not project_root:
            raise ContractError(f"{cid} needs a project root to resolve")
        path = Path(project_root) / PROJECT_SCHEMA_DIR / f"{name}.json"
        if not path.is_file():
            raise ContractError(f"{cid}: {PROJECT_SCHEMA_DIR / (name + '.json')} does not exist")
        try:
            schema = _read_json_file(path)
        except (OSError, ValueError) as e:
            raise ContractError(f"{cid}: cannot read {path.name}: {e}") from None
    return SchemaDoc(id=cid, schema=schema, sha256=_sha(schema))


def _refs_in(schema: Any) -> Iterator[str]:
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _refs_in(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from _refs_in(v)


def _ref_target(ref: str) -> Optional[str]:
    """Registry id a ``$ref`` points at; None for a same-document fragment."""
    if ref.startswith("#"):
        return None
    base = ref.split("#", 1)[0]
    cid = canonical_ref(base)
    if cid is None:
        raise ContractError(
            f"$ref {ref!r} is not allowed: a schema may only reference ziya:/project: schemas "
            "or its own fragments (nothing is fetched)")
    return cid


# ── the pinned schema set ──────────────────────────────────────────────────

class SchemaSet:
    """Every schema a card needs, pinned by hash, plus its offline registry."""

    def __init__(self, docs: Mapping[str, SchemaDoc], aliases: Optional[Mapping[str, str]] = None):
        self.docs: Dict[str, SchemaDoc] = dict(docs)
        self.aliases: Dict[str, str] = dict(aliases or {})
        self._registry: Optional[Registry] = None
        self._compiled: Dict[str, "CompiledSchema"] = {}

    @classmethod
    def resolve(cls, refs: Iterable[Any], project_root: Optional[str] = None) -> "SchemaSet":
        """Load ``refs`` and everything they ``$ref``, transitively.  Raises ContractError."""
        docs: Dict[str, SchemaDoc] = {}
        aliases: Dict[str, str] = {}
        pending: List[Any] = list(refs)
        while pending:
            ref = pending.pop()
            doc = load_schema(ref, project_root)
            if isinstance(ref, str):
                aliases[ref.strip()] = doc.id
            if doc.id in docs:
                continue
            docs[doc.id] = doc
            for r in _refs_in(doc.schema):
                tgt = _ref_target(r)
                if tgt and tgt not in docs:
                    pending.append(tgt)
                    aliases.setdefault(r.split("#", 1)[0], tgt)
        out = cls(docs, aliases)
        for sid in docs:
            out.compile(sid)       # surface schema errors at resolve time
        return out

    def ensure(self, refs: Iterable[Any], project_root: Optional[str] = None) -> "SchemaSet":
        """Pin ``refs`` that are not pinned yet (a Call's callee), in place.

        A run pins everything its own card references at launch; a callee
        card is named, not inlined, so its schemas are first seen when it
        runs and are pinned then, for the rest of the run.
        """
        todo = []
        for r in refs:
            try:
                self.id_for(r)
            except ContractError:
                todo.append(r)
        if not todo:
            return self
        extra = SchemaSet.resolve(todo, project_root)
        with _ENSURE_LOCK:
            for sid, d in extra.docs.items():
                self.docs.setdefault(sid, d)
            for a, t in extra.aliases.items():
                self.aliases.setdefault(a, t)
            self._registry = None
            self._compiled.clear()
        return self

    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "schemas": {sid: {"sha256": d.sha256, "schema": d.schema} for sid, d in self.docs.items()},
            "aliases": dict(self.aliases),
        }

    @classmethod
    def from_snapshot(cls, snap: Optional[Mapping[str, Any]]) -> "SchemaSet":
        snap = snap or {}
        docs = {sid: SchemaDoc(id=sid, schema=e["schema"], sha256=e["sha256"])
                for sid, e in (snap.get("schemas") or {}).items()}
        return cls(docs, snap.get("aliases") or {})

    def id_for(self, ref: Any) -> str:
        if isinstance(ref, dict):
            return f"inline:{_sha(ref)[:16]}"
        key = ref.strip() if isinstance(ref, str) else ref
        if key in self.docs:
            return key
        if key in self.aliases:
            return self.aliases[key]
        cid = canonical_ref(key)
        if cid in self.docs:
            return cid
        raise ContractError(f"schema {ref!r} was not resolved at launch")

    @property
    def registry(self) -> Registry:
        if self._registry is None:
            resources = []
            for sid, d in self.docs.items():
                res = DRAFT202012.create_resource(d.schema)
                resources.append((sid, res))
                for alias, target in self.aliases.items():
                    if target == sid and alias != sid:
                        resources.append((alias, res))
            self._registry = Registry().with_resources(resources)
        return self._registry

    def compile(self, ref: Any) -> "CompiledSchema":
        sid = self.id_for(ref)
        if sid not in self._compiled:
            doc = self.docs[sid]
            try:
                _Validator.check_schema(doc.schema)
            except SchemaError as e:
                raise ContractError(f"{sid} is not a valid JSON Schema: {e.message}") from None
            _check_checks(doc.schema, sid)
            validator = _Validator(doc.schema, registry=self.registry)
            self._compiled[sid] = CompiledSchema(id=sid, sha256=doc.sha256, schema=doc.schema,
                                                 validator=validator, schemas=self)
        return self._compiled[sid]


@dataclass
class CompiledSchema:
    id: str
    sha256: str
    schema: Any
    validator: Any
    schemas: SchemaSet

    def lookup(self, ref: str) -> Any:
        try:
            return self.schemas.registry.resolver().lookup(ref).contents
        except Unresolvable:
            return {}


# ── numbers and claim references ───────────────────────────────────────────

def is_claim_ref(obj: Any) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("claim"), str) and bool(obj["claim"].strip())


def is_literal_number(obj: Any) -> bool:
    return (isinstance(obj, dict) and "claim" not in obj and "value" in obj
            and isinstance(obj["value"], (int, float)) and not isinstance(obj["value"], bool)
            and set(obj) <= {"value", "unit", "source"})


def _escape(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def walk(obj: Any, pointer: str = "") -> Iterator[Tuple[str, Any]]:
    """(json-pointer, value) for every node, depth-first."""
    yield pointer, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{pointer}/{_escape(k)}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{pointer}/{i}")


ClaimLookup = Callable[[str], Dict[str, Any]]
"""``ref -> {"claim_id", "version", "status", "value", "unit"}``; raises LookupError."""


def ledger_claim_lookup(project_root: Optional[str], namespace: Optional[str] = None) -> Optional[ClaimLookup]:
    """A lookup reading the project's evidence ledger, or None with no project."""
    if not project_root:
        return None

    def lookup(ref: str) -> Dict[str, Any]:
        from app.utils.evidence.refs import resolve
        from app.utils.evidence.store import LedgerError, ledger_for
        try:
            r = resolve(ledger_for(project_root), ref, namespace)
        except LedgerError as e:
            raise LookupError(str(e)) from None
        c = r["claim"]
        return {"claim_id": c["id"], "version": c.get("version"), "status": c.get("status"),
                "value": r["value"], "unit": c.get("unit")}
    return lookup


class _ClaimCache:
    def __init__(self, lookup: Optional[ClaimLookup]):
        self.lookup = lookup
        self.memo: Dict[str, Any] = {}

    def get(self, ref_obj: Mapping[str, Any]) -> Dict[str, Any]:
        ref = ref_obj["claim"].strip()
        fld = ref_obj.get("field")
        key = f"{ref}.{fld}" if isinstance(fld, str) and fld.strip() else ref
        if key not in self.memo:
            if self.lookup is None:
                self.memo[key] = LookupError("no evidence ledger is in scope to resolve claims")
            else:
                try:
                    self.memo[key] = self.lookup(key)
                except LookupError as e:
                    self.memo[key] = e
        hit = self.memo[key]
        if isinstance(hit, Exception):
            raise hit
        return hit


# ── checks (x-ziya-checks) ─────────────────────────────────────────────────

_ACTIVE_CLAIMS: contextvars.ContextVar[Optional[_ClaimCache]] = contextvars.ContextVar(
    "ziya_output_contract_claims", default=None)
_MISSING = object()


def _deref(obj: Any) -> Any:
    if is_claim_ref(obj):
        cache = _ACTIVE_CLAIMS.get()
        if cache is None:
            return _MISSING
        try:
            return cache.get(obj)["value"]
        except LookupError:
            return _MISSING
    return obj


def _at(instance: Any, path: Any) -> Any:
    if not isinstance(path, str):
        return _MISSING
    cur = _deref(instance)
    for tok in [t for t in path.strip("/").split("/") if t]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        cur = _deref(cur)
        if isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        elif isinstance(cur, list) and tok.isdigit() and int(tok) < len(cur):
            cur = cur[int(tok)]
        else:
            return _MISSING
    return cur


def _num(v: Any) -> Optional[float]:
    v = _deref(v)
    if is_literal_number(v):
        v = v["value"]
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def _value(instance: Any, spec: Any) -> Optional[float]:
    """A check operand: a JSON number, or a path to one."""
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return float(spec)
    found = _at(instance, spec)
    return None if found is _MISSING else _num(found)


def _check_checks(schema: Any, sid: str) -> None:
    for ptr, node in walk(schema):
        if not isinstance(node, dict) or CHECKS_KEYWORD not in node:
            continue
        checks = node[CHECKS_KEYWORD]
        if not isinstance(checks, list):
            raise ContractError(f"{sid}{ptr}: {CHECKS_KEYWORD} must be a list")
        for c in checks:
            if not isinstance(c, dict) or len(c) != 1 or next(iter(c)) not in CHECK_KINDS:
                raise ContractError(
                    f"{sid}{ptr}: each {CHECKS_KEYWORD} entry is one of "
                    f"{', '.join(CHECK_KINDS)}, got {c!r}")


def _run_checks(validator, checks, instance, schema):
    if not isinstance(instance, (dict, list)):
        return
    for check in checks or []:
        kind, arg = next(iter(check.items()))
        if kind == "ordered":
            present = [(p, _value(instance, p)) for p in arg]
            present = [(p, v) for p, v in present if v is not None]
            for (pa, a), (pb, b) in zip(present, present[1:]):
                if a > b:
                    yield ValidationError(f"{pa} ({a:g}) must not exceed {pb} ({b:g})")
        elif kind == "range":
            v = _value(instance, arg.get("path"))
            if v is None:
                continue
            lo, hi = arg.get("min"), arg.get("max")
            if lo is not None and v < lo:
                yield ValidationError(f"{arg['path']} is {v:g}; the minimum is {lo:g}")
            if hi is not None and v > hi:
                yield ValidationError(f"{arg['path']} is {v:g}; the maximum is {hi:g}")
        elif kind == "subset":
            part, of = _value(instance, arg.get("part")), _value(instance, arg.get("of"))
            if part is not None and of is not None and part > of:
                yield ValidationError(f"{arg['part']} ({part:g}) exceeds {arg['of']} ({of:g})")
        elif kind == "reconciles":
            a, b = _value(instance, arg.get("a")), _value(instance, arg.get("b"))
            tol = _value(instance, arg.get("tol_rel"))
            if a is None or b is None or tol is None:
                continue
            rel = abs(a - b) / abs(b) if b else (0.0 if a == b else math.inf)
            if rel > tol:
                yield ValidationError(
                    f"{arg['a']} ({a:g}) does not reconcile with {arg['b']} ({b:g}): "
                    f"relative difference {rel:.3g} exceeds tolerance {tol:g}")


_Validator = jsonschema.validators.extend(Draft202012Validator, {CHECKS_KEYWORD: _run_checks})


# ── validating a part ──────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    resolved_claims: List[Dict[str, Any]] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)


def _describe(compiled: CompiledSchema, schema: Any, prop: Optional[str] = None) -> str:
    node = schema
    if prop is not None and isinstance(schema, dict):
        node = (schema.get("properties") or {}).get(prop) or {}
    if isinstance(node, dict):
        if node.get("description"):
            return str(node["description"])
        if isinstance(node.get("$ref"), str):
            tgt = compiled.lookup(node["$ref"]) if not node["$ref"].startswith("#") else {}
            if isinstance(tgt, dict) and tgt.get("description"):
                return str(tgt["description"])
    return ""


def _describe_at(compiled: CompiledSchema, path: List[Any]) -> str:
    """Description of the field at an instance path, following allOf and $ref.

    Used when the failing subschema carries no description itself, e.g. a
    project schema that only adds ``required`` to a shipped field.
    """
    node: Any = compiled.schema
    desc = ""
    for tok in path:
        if isinstance(tok, int):
            node = node.get("items") if isinstance(node, dict) else None
            continue
        props, _ = _flatten(compiled, node)
        node = props.get(tok)
        if not isinstance(node, dict):
            return ""
        desc = _describe(compiled, {"properties": {tok: node}}, tok)
    return desc


def _branch_keys(compiled: CompiledSchema, branch: Any) -> set:
    props, req = _flatten(compiled, branch)
    return set(props) | set(req)


def _expand(compiled: CompiledSchema, err: ValidationError) -> List[ValidationError]:
    """Replace a oneOf/anyOf failure with the errors of the branch the value meant.

    The branch is the one whose declared keys overlap the instance's keys
    most (ties: fewest errors).  A non-object instance keeps the original
    error, whose schema description says what the alternatives are.
    """
    if err.validator not in ("oneOf", "anyOf") or not err.context or not isinstance(err.instance, dict):
        return [err]
    branches: Dict[int, List[ValidationError]] = {}
    for sub in err.context:
        if sub.relative_schema_path:
            branches.setdefault(sub.relative_schema_path[0], []).append(sub)
    options = err.schema.get(err.validator) if isinstance(err.schema, dict) else None
    if not branches or not isinstance(options, list):
        return [best_match(err.context) or err]
    keys = set(err.instance)

    def score(idx: int) -> Tuple[int, int]:
        branch = options[idx] if isinstance(idx, int) and idx < len(options) else {}
        return (-len(keys & _branch_keys(compiled, branch)), len(branches[idx]))

    chosen = min(branches, key=score)
    out: List[ValidationError] = []
    for sub in branches[chosen]:
        out.extend(_expand(compiled, sub))
    return out


def _format_error(compiled: CompiledSchema, err: ValidationError) -> Tuple[str, str]:
    ptr = "".join(f"/{_escape(p)}" for p in err.absolute_path) or "/"
    if err.validator == CHECKS_KEYWORD:
        # The check message names its own (relative) paths.
        return ptr, (err.message if ptr == "/" else f"{ptr}: {err.message}")
    if err.validator == "required":
        m = re.match(r"'([^']+)' is a required property", err.message)
        prop = m.group(1) if m else None
        where = (ptr.rstrip("/") + "/" + _escape(prop)) if prop else ptr
        desc = _describe(compiled, err.schema, prop) or (
            _describe_at(compiled, list(err.absolute_path) + [prop]) if prop else "")
        return where, f"{where}: required" + (f" ({desc})" if desc else "")
    desc = _describe(compiled, err.schema) or _describe_at(compiled, list(err.absolute_path))
    return ptr, f"{ptr}: {err.message}" + (f" ({desc})" if desc else "")


def validate_instance(
    compiled: CompiledSchema,
    instance: Any,
    *,
    require_claims: bool = False,
    claims: Optional[ClaimLookup] = None,
) -> ValidationResult:
    """Validate one value against a compiled schema, resolving claim references."""
    errors: List[Tuple[str, str]] = []
    resolved: List[Dict[str, Any]] = []
    unverified: List[str] = []
    cache = _ClaimCache(claims)

    for ptr, node in walk(instance):
        if is_claim_ref(node):
            try:
                hit = cache.get(node)
            except LookupError as e:
                errors.append((ptr, f"{ptr or '/'}: claim {node['claim']!r} cannot be used: {e}"))
                continue
            if hit.get("status") == "refuted":
                errors.append((ptr, f"{ptr or '/'}: claim {hit['claim_id']} is refuted and cannot be cited"))
                continue
            resolved.append({"pointer": ptr or "/", "ref": node["claim"], "field": node.get("field"),
                             "claim_id": hit["claim_id"], "version": hit.get("version"),
                             "status": hit.get("status"), "value": hit.get("value"),
                             "unit": hit.get("unit")})
        elif is_literal_number(node):
            if require_claims:
                errors.append((ptr, (
                    f"{ptr or '/'}: a literal number is not allowed here; record it with "
                    f"ledger_record and cite it as {{\"claim\": \"<claim id>\"}}")))
            else:
                unverified.append(ptr or "/")

    token = _ACTIVE_CLAIMS.set(cache)
    try:
        raw = [e for top in compiled.validator.iter_errors(instance) for e in _expand(compiled, top)]
        for err in sorted(raw, key=lambda e: [str(p) for p in e.absolute_path]):
            errors.append(_format_error(compiled, err))
    finally:
        _ACTIVE_CLAIMS.reset(token)

    seen, msgs = set(), []
    for _, msg in errors:
        if msg not in seen:
            seen.add(msg)
            msgs.append(msg)
    return ValidationResult(ok=not msgs, errors=msgs, resolved_claims=resolved, unverified=unverified)


# ── contracts on a task ────────────────────────────────────────────────────

def _get(c: Any, key: str, default: Any = None) -> Any:
    if isinstance(c, Mapping):
        return c.get(key, default)
    if key == "schema":
        return getattr(c, "schema_", getattr(c, "schema", default))
    return getattr(c, key, default)


@dataclass
class ActiveContract:
    """One contract entry, bound to its pinned compiled schema."""
    name: str
    schema: CompiledSchema
    required: bool = True
    max_count: int = 1
    require_claims: bool = False


def bind_contracts(
    entries: Optional[Iterable[Any]],
    schemas: SchemaSet,
    *,
    evidence_namespace: Optional[str] = None,
) -> Dict[str, ActiveContract]:
    """Bind contract entries (dicts or models) to the run's pinned schemas.

    ``require_claims`` left unset defaults to on exactly when an evidence
    namespace is in scope (Decision 3).
    """
    out: Dict[str, ActiveContract] = {}
    for e in entries or []:
        name = str(_get(e, "name") or "").strip()
        if not name:
            raise ContractError("every output_contract entry needs a 'name'")
        rc = _get(e, "require_claims")
        out[name] = ActiveContract(
            name=name,
            schema=schemas.compile(_get(e, "schema")),
            required=bool(_get(e, "required", True)),
            max_count=max(1, int(_get(e, "max_count", 1) or 1)),
            require_claims=bool(evidence_namespace) if rc is None else bool(rc),
        )
    return out


def part_instance(part: Mapping[str, Any]) -> Any:
    """The value a contract validates for one emitted part."""
    pt = part.get("part_type")
    if pt == "data":
        return part.get("data")
    if pt == "text":
        return part.get("text")
    return {k: part.get(k) for k in ("file_uri", "media_type") if part.get(k) is not None}


def counted_parts(parts: Iterable[Mapping[str, Any]], contract: ActiveContract) -> List[Mapping[str, Any]]:
    """Recorded parts that satisfied ``contract`` (by name and pinned schema hash)."""
    return [p for p in parts
            if p.get("name") == contract.name and p.get("schema_sha256") == contract.schema.sha256]


def check_part(
    contracts: Mapping[str, ActiveContract],
    part: Dict[str, Any],
    recorded: Iterable[Mapping[str, Any]],
    claims: Optional[ClaimLookup] = None,
) -> Tuple[bool, str]:
    """Validate a part before it is recorded.  Stamps metadata on success.

    Returns ``(ok, model-facing message)``; the message is empty on success.
    A part whose name no contract declares passes untouched.
    """
    c = contracts.get(part.get("name") or "")
    if c is None:
        return True, ""
    already = len(counted_parts(recorded, c))
    if already >= c.max_count:
        return False, (
            f"Artifact '{c.name}' not recorded: its output contract allows {c.max_count} "
            f"and {already} {'is' if already == 1 else 'are'} already recorded.")
    res = validate_instance(c.schema, part_instance(part), require_claims=c.require_claims, claims=claims)
    if not res.ok:
        shown = res.errors[:MAX_REPORTED_ERRORS]
        more = len(res.errors) - len(shown)
        lines = "\n".join(f"- {m}" for m in shown)
        return False, (
            f"Artifact '{c.name}' rejected by its output contract ({c.schema.id}); not recorded. "
            f"Fix these and emit it again:\n{lines}"
            + (f"\n- ...and {more} more" if more > 0 else ""))
    part["contract"] = c.name
    part["schema_id"] = c.schema.id
    part["schema_sha256"] = c.schema.sha256
    # What the schema IS, for presentation: a project schema extending
    # ziya:finding/v1 renders as a finding card (design/typed-findings.md,
    # step 8).  The id alone cannot say so.
    part["schema_lineage"] = schema_lineage(c.schema)
    if res.resolved_claims:
        part["resolved_claims"] = res.resolved_claims
    if res.unverified:
        part["unverified_numbers"] = res.unverified
    return True, ""


def schema_lineage(compiled: CompiledSchema) -> List[str]:
    """The schema's id followed by every registry schema it extends.

    "Extends" means a ``$ref`` reachable through the top-level ``allOf``
    (recursively), which is how a project schema builds on a shipped one.
    Order: the schema itself, then ancestors depth-first, no repeats.
    """
    out: List[str] = [compiled.id]

    def visit(node: Any, depth: int) -> None:
        if not isinstance(node, dict) or depth > 8:
            return
        for sub in node.get("allOf") or []:
            ref = sub.get("$ref") if isinstance(sub, dict) else None
            if isinstance(ref, str) and not ref.startswith("#"):
                cid = canonical_ref(ref.split("#", 1)[0]) or ref
                if cid not in out:
                    out.append(cid)
                    visit(compiled.lookup(ref), depth + 1)
            else:
                visit(sub, depth + 1)

    visit(compiled.schema, 0)
    return out


def check_value(
    schema_ref: Any,
    value: Any = None,
    *,
    project_root: Optional[str] = None,
    require_claims: bool = False,
    namespace: Optional[str] = None,
    describe_only: bool = False,
) -> Dict[str, Any]:
    """Check one value against a schema, outside any Task Card run.

    The plain-chat face of output contracts (``validate_output`` tool):
    the same registry, validation, checks and claim resolution a contracted
    emit uses, returning a JSON-safe dict.  ``describe_only`` returns the
    schema's field outline instead.  Never raises for a bad schema or
    value; a refusal is ``{"error": True, "message": ...}``.
    """
    try:
        schemas = SchemaSet.resolve([schema_ref], project_root)
        compiled = schemas.compile(schema_ref)
    except ContractError as e:
        return {"error": True, "message": str(e)}
    head = {"schema_id": compiled.id, "schema_sha256": compiled.sha256,
            "schema_lineage": schema_lineage(compiled)}
    if describe_only:
        return {**head, "fields": schema_summary(compiled)}
    res = validate_instance(compiled, value, require_claims=require_claims,
                            claims=ledger_claim_lookup(project_root, namespace))
    shown = res.errors[:MAX_REPORTED_ERRORS]
    out: Dict[str, Any] = {**head, "ok": res.ok}
    if not res.ok:
        out["errors"] = shown
        if len(res.errors) > len(shown):
            out["more_errors"] = len(res.errors) - len(shown)
        out["fields"] = schema_summary(compiled, max_chars=SUMMARY_MAX_CHARS // 2)
    if res.resolved_claims:
        out["resolved_claims"] = res.resolved_claims
    if res.unverified:
        out["unverified_numbers"] = res.unverified
    return out


def missing_required(contracts: Mapping[str, ActiveContract], parts: Iterable[Mapping[str, Any]]) -> List[ActiveContract]:
    parts = list(parts)
    return [c for c in contracts.values() if c.required and not counted_parts(parts, c)]


# ── generated text ─────────────────────────────────────────────────────────

def _type_label(compiled: CompiledSchema, node: Any) -> str:
    if not isinstance(node, dict):
        return "any"
    if isinstance(node.get("$ref"), str):
        tgt = compiled.lookup(node["$ref"]) if not node["$ref"].startswith("#") else {}
        return str((tgt or {}).get("title") or node["$ref"])
    if "enum" in node:
        return "one of " + ", ".join(json.dumps(v) for v in node["enum"])
    t = node.get("type")
    if t == "array":
        return f"list of {_type_label(compiled, node.get('items'))}"
    if isinstance(t, list):
        return " or ".join(map(str, t))
    if t:
        return str(t)
    for k in ("allOf", "oneOf", "anyOf"):
        if node.get(k):
            return " / ".join(_type_label(compiled, s) for s in node[k][:3])
    return "any"


def _flatten(compiled: CompiledSchema, node: Any, depth: int = 0) -> Tuple[Dict[str, Any], List[str]]:
    """Properties and required names of ``node``, merging allOf and $ref."""
    props: Dict[str, Any] = {}
    req: List[str] = []
    if not isinstance(node, dict) or depth > 6:
        return props, req
    if isinstance(node.get("$ref"), str) and not node["$ref"].startswith("#"):
        p, r = _flatten(compiled, compiled.lookup(node["$ref"]), depth + 1)
        props.update(p)
        req += r
    for sub in node.get("allOf") or []:
        p, r = _flatten(compiled, sub, depth + 1)
        for k, v in p.items():
            props[k] = {**props.get(k, {}), **v} if isinstance(v, dict) else v
        req += r
    for k, v in (node.get("properties") or {}).items():
        props[k] = {**props.get(k, {}), **v} if isinstance(v, dict) and isinstance(props.get(k), dict) else v
    req += [r for r in node.get("required") or [] if isinstance(r, str)]
    return props, list(dict.fromkeys(req))


def schema_summary(compiled: CompiledSchema, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Compact, model-facing outline of a schema's fields."""
    lines: List[str] = []

    def emit(node: Any, indent: int, depth: int) -> None:
        props, req = _flatten(compiled, node)
        for name, sub in props.items():
            if sum(len(l) + 1 for l in lines) > max_chars:
                return
            desc = _describe(compiled, {"properties": props}, name)
            flag = ", required" if name in req else ""
            lines.append(f"{'  ' * indent}- {name}: {_type_label(compiled, sub)}{flag}"
                         + (f" -- {desc}" if desc else ""))
            if depth < SUMMARY_MAX_DEPTH and isinstance(sub, dict):
                inner = sub.get("items") if sub.get("type") == "array" else sub
                if isinstance(inner, dict) and (inner.get("properties") or inner.get("allOf")):
                    emit(inner, indent + 1, depth + 1)

    emit(compiled.schema, 0, 0)
    if not lines and isinstance(compiled.schema, dict):
        lines.append(f"- {_type_label(compiled, compiled.schema)}"
                     + (f" -- {compiled.schema['description']}" if compiled.schema.get("description") else ""))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n- ...(truncated)"
    return text


def _numbers_note(contracts: Iterable[ActiveContract]) -> str:
    if any(c.require_claims for c in contracts):
        return ("Every number must be a ledger claim reference {\"claim\": \"<id>\"} "
                "(record it first with ledger_record); literal numbers are rejected.")
    return ("Numbers are {\"claim\": \"<id>\"} ledger references or "
            "{\"value\": n, \"unit\": \"...\", \"source\": \"...\"} literals.")


def required_outputs_section(contracts: Mapping[str, ActiveContract]) -> str:
    """The prompt section listing a task's contracted outputs."""
    if not contracts:
        return ""
    out = ["REQUIRED OUTPUTS: emit each of these with emit_artifact(name=<exactly the name "
           "below>, part_type=\"data\", data={...}) (part_type \"text\" for text schemas). The "
           "name must match exactly: a part under any other name (e.g. 'alpha_finding' for "
           "'finding') does not count. Each is checked when you emit it; a rejected part is not "
           "recorded, so fix the listed fields and emit again."]
    for c in contracts.values():
        need = "required" if c.required else "optional"
        count = f", up to {c.max_count}" if c.max_count > 1 else ""
        out.append(f"\n`{c.name}` ({need}{count}; schema {c.schema.id}):\n{schema_summary(c.schema)}")
    out.append("\n" + _numbers_note(contracts.values()))
    return "\n".join(out)


def correction_message(missing: Iterable[ActiveContract]) -> str:
    """The user turn sent once when a task ended without its required outputs."""
    missing = list(missing)
    names = ", ".join(f"`{c.name}`" for c in missing)
    body = [f"You finished without emitting the required output(s): {names}. "
            "Emit them now with emit_artifact; nothing else is needed."]
    budget = SUMMARY_MAX_CHARS
    for c in missing:
        s = schema_summary(c.schema, max_chars=max(500, budget // max(1, len(missing))))
        body.append(f"\n`{c.name}` (schema {c.schema.id}):\n{s}")
    body.append("\n" + _numbers_note(missing))
    return "\n".join(body)


# ── card-level helpers ─────────────────────────────────────────────────────

CONTRACT_FIELDS = ("output_contract", "output_contract_defaults",
                   "gather_contract", "gather_contract_defaults")


def card_schema_refs(root: Any) -> List[Any]:
    """Every schema reference in a block tree (dicts or Block models)."""
    refs: List[Any] = []

    def visit(block: Any) -> None:
        for fld in CONTRACT_FIELDS:
            for entry in _get(block, fld) or []:
                ref = _get(entry, "schema")
                if ref is not None and ref not in refs:
                    refs.append(ref)
        for child in _get(block, "body") or []:
            visit(child)

    visit(root)
    return refs


def snapshot_for_card(root: Any, project_root: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve and pin every schema a card references; None when it has none."""
    refs = card_schema_refs(root)
    if not refs:
        return None
    return SchemaSet.resolve(refs, project_root).to_snapshot()


def launch_snapshot_entry(root: Any, project_root: Optional[str]) -> Optional[Dict[str, Any]]:
    """What a run record stores at launch: the pinned schemas, or the error.

    Stored under ``permissions_snapshot["output_schemas"]``.  A schema that
    cannot be resolved is recorded as ``{"error": ...}`` rather than raised,
    because the permissions snapshot is best-effort; the run root raises it.
    """
    try:
        return snapshot_for_card(root, project_root)
    except ContractError as e:
        return {"error": str(e)}


# ── the run's pinned schemas ───────────────────────────────────────────────

_RUN_SCHEMAS: contextvars.ContextVar[Optional[SchemaSet]] = contextvars.ContextVar(
    "ziya_run_output_schemas", default=None)


def run_schemas() -> Optional[SchemaSet]:
    """The schemas pinned for the active Task Card run, or None outside one."""
    return _RUN_SCHEMAS.get()


def _stored_snapshot(storage: Any, run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if storage is None or not run_id:
        return None
    try:
        run = storage.get(run_id)
    except Exception:  # noqa: BLE001 -- a missing record falls back to resolving now
        return None
    snap = getattr(run, "permissions_snapshot", None) or {}
    entry = snap.get("output_schemas") if isinstance(snap, dict) else None
    return entry if isinstance(entry, dict) else None


@contextmanager
def pinned_for_run(root: Any, *, project_root: Optional[str] = None,
                   run_id: Optional[str] = None, storage: Any = None):
    """Pin a run's schemas for the duration of its outermost block.

    Uses the snapshot recorded on the run at launch when there is one, so
    the run is checked against what existed when it was launched; otherwise
    (scheduler, CLI, tests) resolves the card now, which is the same moment.
    Nested calls see a set already pinned and leave it alone.  Raises
    ContractError for a card whose schemas cannot be resolved.
    """
    if _RUN_SCHEMAS.get() is not None:
        yield _RUN_SCHEMAS.get()
        return
    snap = _stored_snapshot(storage, run_id)
    if snap is None:
        snap = snapshot_for_card(root, project_root)
    if snap and snap.get("error"):
        raise ContractError(f"output contract schemas could not be resolved: {snap['error']}")
    schemas = SchemaSet.from_snapshot(snap) if snap else SchemaSet({})
    token = _RUN_SCHEMAS.set(schemas)
    try:
        yield schemas
    finally:
        _RUN_SCHEMAS.reset(token)


def merge_contract_defaults(own: Optional[List[Any]], defaults: Optional[Mapping[str, Any]]):
    """A Task's effective ``output_contract`` under State-block defaults.

    ``own`` None (no contract declared): the defaults, in declaration
    order.  ``own == []``: an explicit opt-out, kept as ``[]``.  Otherwise
    ``own`` plus every default whose name ``own`` does not declare (merge
    by name, Decision 2).  Returns ``own`` itself when nothing changes, so
    a caller can test identity to skip copying the block.
    """
    if not defaults or (own is not None and len(own) == 0):
        return own
    names = {str(_get(e, "name") or "").strip() for e in (own or [])}
    extra = [e for n, e in defaults.items() if n not in names]
    if not extra:
        return own
    return list(own or []) + extra


# ── container (gather) contracts, steps 6a-6e ──────────────────────────────

def gather_as_output_entry(g: Any, required: bool) -> Dict[str, Any]:
    """A gather entry pushed down to one Task as a Task contract entry.

    ``max_count`` is per task: the gather ``max`` when set, otherwise the
    inline-part budget, since ``min``/``max`` are judged per unit at the
    container end, not per task.
    """
    mx = _get(g, "max")
    return {"name": str(_get(g, "name") or "").strip(), "schema": _get(g, "schema"),
            "required": bool(required), "max_count": int(mx) if mx else 50,
            "require_claims": _get(g, "require_claims")}


def effective_gather(block: Any, defaults: Optional[Mapping[str, Any]]) -> List[Any]:
    """A container's gather entries: its own plus State defaults that match.

    A default applies when the container declares no entry of that name
    and the default's ``container`` is ``any`` or this block's type.  An
    explicit ``gather_contract: []`` opts out, as for Task contracts.
    """
    own = _get(block, "gather_contract")
    if own is not None and len(own) == 0:
        return []
    own = list(own or [])
    names = {str(_get(e, "name") or "").strip() for e in own}
    btype = _get(block, "block_type")
    for n, e in (defaults or {}).items():
        if n not in names and (_get(e, "container") or "any") in ("any", btype):
            own.append(e)
    return own


def _unit_tasks(block: Any) -> List[Any]:
    """Task blocks inside ``block`` (itself included), not looking behind a Call."""
    if _get(block, "block_type") == "task":
        return [block]
    if _get(block, "block_type") == "call":
        return []
    out: List[Any] = []
    for c in _get(block, "body") or []:
        out += _unit_tasks(c)
    return out


def _subtree_ids(block: Any) -> set:
    out = {_get(block, "id")} if _get(block, "id") else set()
    for c in _get(block, "body") or []:
        out |= _subtree_ids(c)
    return out


def gather_units(container: Any, g: Any) -> List[Tuple[str, Any]]:
    """The units a gather entry is judged over: ``[(label, unit block)]``.

    ``per: child`` -> each direct body block.  ``per: iteration`` and
    ``per: total`` -> the body as one unit (every iteration runs the same
    body, so its producer is decided once); represented by the container.
    """
    if (_get(g, "per") or "child") == "child":
        return [(str(_get(c, "name") or _get(c, "id") or f"child {i}"), c)
                for i, c in enumerate(_get(container, "body") or [])]
    return [(str(_get(container, "name") or "body"), container)]


def unit_producers(unit: Any, g: Any) -> Tuple[List[Any], List[Any]]:
    """``(producers, tasks)`` for one unit; producers may be empty.

    Explicit ``producer`` ids win (restricted to blocks in the unit);
    otherwise a unit with exactly one Task has that Task as producer.
    More than one Task and no ``producer`` is a validation error, so at
    run time such a unit simply has no required producer.
    """
    tasks = _unit_tasks(unit)
    wanted = _get(g, "producer")
    if wanted:
        ids = _subtree_ids(unit)
        found = []

        def visit(b: Any) -> None:
            if _get(b, "id") in wanted and _get(b, "id") in ids:
                found.append(b)
            if _get(b, "block_type") != "call":
                for c in _get(b, "body") or []:
                    visit(c)
        visit(unit)
        return found, tasks
    return (tasks if len(tasks) == 1 else []), tasks


def gather_pushdown(container: Any, entries: Iterable[Any]) -> Dict[str, Any]:
    """What a gathered container pushes to the Tasks beneath it.

    ``{"required": {task_id: [entry]}, "optional": [entry]}``: producer
    Tasks get each gather entry as a required Task contract; every other
    Task gets it as optional (validated if emitted, never required).
    Tasks behind a Call get nothing (the boundary is not crossed).
    """
    required: Dict[str, List[Dict[str, Any]]] = {}
    optional: List[Dict[str, Any]] = []
    for g in entries:
        optional.append(gather_as_output_entry(g, required=False))
        for _, unit in gather_units(container, g):
            producers, _ = unit_producers(unit, g)
            for p in producers:
                if _get(p, "block_type") == "task" and _get(p, "id"):
                    required.setdefault(_get(p, "id"), []).append(
                        gather_as_output_entry(g, required=True))
    return {"required": required, "optional": optional}


def effective_task_contract(own: Optional[List[Any]], task_id: Optional[str],
                            pushdown: Optional[Mapping[str, Any]],
                            defaults: Optional[Mapping[str, Any]]):
    """A Task's contract after gather push-down and State defaults.

    Precedence by name: the Task's own entry, then a gather entry
    (required for a producer, optional otherwise), then a State default.
    ``own == []`` opts out of State defaults only: a container that names
    this Task its producer still requires the part.  Returns ``own``
    itself when nothing changes.
    """
    pd = pushdown or {}
    gathered: Dict[str, Any] = {}
    for e in (pd.get("required") or {}).get(task_id or "", []):
        gathered.setdefault(e["name"], e)
    for e in pd.get("optional") or []:
        gathered.setdefault(e["name"], e)
    if own is not None and len(own) == 0:
        return list(gathered.values()) if gathered else own
    merged = merge_contract_defaults(own, gathered)
    return merge_contract_defaults(merged, defaults)


def _part_dict(p: Any) -> Dict[str, Any]:
    if isinstance(p, Mapping):
        return dict(p)
    if hasattr(p, "model_dump"):
        return p.model_dump()
    return dict(getattr(p, "__dict__", {}) or {})


def count_valid_parts(contract: ActiveContract, parts: Iterable[Any],
                      claims: Optional[ClaimLookup] = None) -> int:
    """Parts named like ``contract`` that satisfy its schema.

    A part already validated against the same pinned schema (same
    ``schema_sha256``) counts directly; any other part of that name -- one
    a Task validated against its own compatible schema, or one a Call'd
    card returned unvalidated -- is validated against the gather schema now.
    """
    n = 0
    for p in parts:
        d = _part_dict(p)
        if d.get("name") != contract.name:
            continue
        if d.get("schema_sha256") == contract.schema.sha256:
            n += 1
        elif validate_instance(contract.schema, part_instance(d),
                               require_claims=contract.require_claims, claims=claims).ok:
            n += 1
    return n


def part_in_iteration(part: Any, owner_id: str, index: int) -> bool:
    """True when ``part`` was emitted inside iteration ``index`` of ``owner_id``."""
    for pair in _part_dict(part).get("iteration_path") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 \
                and pair[0] == owner_id and pair[1] == index:
            return True
    return False


# ── static path checks (card validation, step 6) ───────────────────────────

def _resolve_fragment(root: Any, ref: str) -> Any:
    node = root
    for tok in [t for t in ref.lstrip("#").split("/") if t]:
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and tok in node:
            node = node[tok]
        elif isinstance(node, list) and tok.isdigit() and int(tok) < len(node):
            node = node[int(tok)]
        else:
            return {}
    return node


def _branches(compiled: CompiledSchema, node: Any, depth: int = 0) -> List[Dict[str, Any]]:
    """``node`` and every branch it can take: $ref, allOf, oneOf, anyOf."""
    if not isinstance(node, dict) or depth > 8:
        return []
    out = [node]
    ref = node.get("$ref")
    if isinstance(ref, str):
        tgt = _resolve_fragment(compiled.schema, ref) if ref.startswith("#") else compiled.lookup(ref)
        out += _branches(compiled, tgt, depth + 1)
    for k in ("allOf", "oneOf", "anyOf"):
        for sub in node.get(k) or []:
            out += _branches(compiled, sub, depth + 1)
    return out


_SCALAR_TYPES = {"string", "number", "integer", "boolean", "null"}


def _types(nodes: List[Dict[str, Any]]) -> set:
    out = set()
    for n in nodes:
        t = n.get("type")
        out |= set(t) if isinstance(t, list) else ({t} if t else set())
    return out


def field_path_problem(compiled: CompiledSchema, path: List[str],
                       want_array: bool = False) -> Optional[str]:
    """Why ``path`` cannot name a value in instances of ``compiled``, or None.

    A field counts as present if any branch (allOf/oneOf/anyOf/$ref)
    declares it, and as absent only when the branches at that point
    declare their fields and none has it.  Where nothing declares a shape
    (``{}``, a description only, patternProperties) nothing can be proven
    and the check stops.  ``want_array`` additionally requires the final
    value to be array-typed.
    """
    nodes: List[Any] = [compiled.schema]
    walked: List[str] = []
    for tok in path:
        cands = [n for node in nodes for n in _branches(compiled, node)]
        where = "/" + "/".join(walked) if walked else "the top level"
        if any("patternProperties" in n for n in cands):
            return None
        structural = [n for n in cands if n.get("properties") or isinstance(n.get("items"), dict)
                      or isinstance(n.get("additionalProperties"), dict)]
        if not structural:
            types = _types(cands)
            if types and types <= _SCALAR_TYPES:
                return f"has no field {tok!r}: {where} is a {'/'.join(sorted(types))}"
            return None
        nxt: List[Any] = []
        fields: List[str] = []
        for n in structural:
            if tok.isdigit() and isinstance(n.get("items"), dict):
                nxt.append(n["items"])
            props = n.get("properties") or {}
            fields += [f for f in props if f not in fields]
            if tok in props:
                nxt.append(props[tok])
            elif isinstance(n.get("additionalProperties"), dict):
                nxt.append(n["additionalProperties"])
        if not nxt:
            shown = ", ".join(fields[:20]) or "none"
            return f"has no field {tok!r} at {where} (fields there: {shown})"
        nodes = nxt
        walked.append(tok)
    if want_array:
        cands = [n for node in nodes for n in _branches(compiled, node)]
        types = _types(cands)
        if types and "array" not in types and not any("items" in n for n in cands):
            return f"names a {'/'.join(sorted(types))}, not a list, so it cannot be a for_each source"
    return None


def contracts_for_block(block: Any, *, project_root: Optional[str] = None,
                        entries: Optional[Iterable[Any]] = None) -> Dict[str, ActiveContract]:
    """Bind a Task's contract entries (default: ``block.output_contract``).

    Schemas come from the run's pinned set; a reference not in it (a Call's
    callee) is pinned on first use.  Outside a run they are resolved now.
    ``require_claims`` follows the evidence namespace in scope.
    """
    entries = list(_get(block, "output_contract") or []) if entries is None else list(entries)
    if not entries:
        return {}
    refs = [_get(e, "schema") for e in entries]
    schemas = run_schemas()
    if schemas is None:
        schemas = SchemaSet.resolve(refs, project_root)
    else:
        schemas.ensure(refs, project_root)
    try:
        from app.context import get_evidence_namespace
        namespace = get_evidence_namespace()
    except ImportError:
        namespace = None
    return bind_contracts(entries, schemas, evidence_namespace=namespace)


# ── rerun note (step 6g) ───────────────────────────────────────────────────

_RERUN_NOTE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "ziya_gather_rerun_note", default=None)


def current_rerun_note() -> Optional[str]:
    """Why the unit being run now is a re-run, or None on a first attempt."""
    return _RERUN_NOTE.get()


@contextmanager
def rerun_note(text: Optional[str]):
    """Tell every task in this (asyncio task's) context why it is re-running.

    A contextvar, not a run-scoped note: re-runs of several units run
    concurrently, and each must see only its own reason.
    """
    token = _RERUN_NOTE.set(text)
    try:
        yield
    finally:
        _RERUN_NOTE.reset(token)
