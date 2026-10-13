"""Pre-launch structural validation of a task card block tree.

``Block`` sets ``extra="allow"`` and declares no validators, which is
the right call for forward compatibility — a card file written by a newer
version must still load — but it means an authoring mistake is accepted
silently and surfaces only when the executor reaches the block.  For a
long fan-out card that can be hours in, and for an orchestrator whose
Call blocks ARE the deck, a typo in the last target is discovered only
after every earlier phase has run and been paid for.

Two severities, deliberately:

* **error** — the run cannot do what the card says.  A Task with no
  instructions raises ``TaskExecutorError`` at dispatch; a ``for_each``
  with no source raises ``ForEachSourceError`` when the loop is reached;
  an unresolvable ``call_target`` yields a failed artifact.  Worth
  stopping for.
* **warning** — suspicious but runnable.  A typo'd field name is
  absorbed by ``extra="allow"`` and the authored intent silently
  dropped, which the author almost certainly wants to know about, but
  refusing to launch over it would be a behaviour change rather than a
  safety net.

What this module deliberately does NOT do:

* Re-implement cycle detection or the call-depth cap.  The executor
  already enforces both (``ExecutionContext.call_stack`` and
  ``MAX_CALL_DEPTH``) and is tested on them; duplicating that logic here
  would create two places for it to disagree.
* Judge a templated ``for_each`` source's ITEMS.  They resolve against
  runtime artifacts, so they cannot be checked statically, and flagging
  the canonical planner-then-fan-out shape would teach authors to ignore
  the validator.  The BLOCK a ``{{sibling("...")}}`` reference names is
  a different matter: that part of the template is fully static, and a
  reference to a block that is not in the tree — or that names a block
  by its ``name`` rather than its ``id`` — can never resolve, so it is
  refused here (see ``_check_sibling_refs``).
* Invent findings it cannot substantiate.  With no project context a
  Call target is unresolvable-in-principle rather than wrong, and a
  resolver that throws an unexpected exception means "cannot verify",
  not "invalid" — the same rule the launch credentials preflight
  follows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..agents.task_call import CallResolutionError, resolve_call_target
from ..models.task_card import Block
from .output_contracts import field_path_problem
from .roster_keys import roster_key_problems

# Block types the executor knows how to run.  A local set rather than a
# read off the Literal so a new block type must be consciously added
# here too — the alternative is a validator that silently accepts a type
# the executor will reject.
KNOWN_BLOCK_TYPES = frozenset({
    "task", "repeat", "parallel", "until", "schedule", "state", "group",
    "call", "ask",
})

# A template variable name has to be reachable as {{var.NAME}}.  Anything
# else binds the answer where nothing can read it, which is decidable here
# and therefore refused rather than discovered at run time.
_ASK_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Container types whose whole purpose is their body.
CONTAINER_TYPES = frozenset({
    "repeat", "parallel", "until", "schedule", "group",
})

# Above this many planned parallel iterations, note that the run will be
# bounded by the concurrency cap.  Mirrors
# block_executor.DEFAULT_REPEAT_CONCURRENCY; not imported, to keep this
# module importable from the API layer without pulling in the executor.
_WIDE_FANOUT_THRESHOLD = 8

# The block a {{sibling("...")}} reference names.  Deliberately looser
# than task_templating._PLACEHOLDER_RE (no closing braces, no field path)
# so a malformed-but-recognisable reference is still checked: the author
# clearly meant a sibling lookup, and the id inside it is what we are
# here to verify.
_SIBLING_REF_RE = re.compile(r"sibling\(\s*['\"]([^'\"]+)['\"]\s*\)")

# How many known block ids to list in an "unknown id" finding before
# truncating.  Enough to recognise the intended one; not the whole card.
_KNOWN_IDS_SHOWN = 12


@dataclass
class _TreeIndex:
    """Every block in the card, by id and by name.

    Built once before the walk so a reference can be resolved against the
    WHOLE tree, not just the ancestors seen so far — the executor's
    ``artifact_registry`` is run-scoped, so a block may legitimately
    reference an earlier sibling of one of its ancestors.
    """
    ids: Dict[str, Block] = field(default_factory=dict)
    names: Dict[str, List[Block]] = field(default_factory=dict)
    # Task id -> {part name: schema ref} after State defaults, and the
    # per-validation schema cache (output contracts, step 6).
    contracts: Dict[str, Dict[str, object]] = field(default_factory=dict)
    gathers: Dict[str, list] = field(default_factory=dict)
    schemas: Optional["_SchemaCache"] = None


def _index_tree(root: Block) -> _TreeIndex:
    idx = _TreeIndex()

    def _visit(b: Block) -> None:
        if b.id:
            idx.ids[b.id] = b
        if b.name:
            idx.names.setdefault(b.name, []).append(b)
        for c in b.body or []:
            _visit(c)

    _visit(root)
    return idx


def _descendant_ids(block: Block) -> Set[str]:
    out: Set[str] = set()
    for c in block.body or []:
        if c.id:
            out.add(c.id)
        out |= _descendant_ids(c)
    return out


def _slug_for(name: str) -> str:
    """A suggested explicit id for a block that has none yet."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:40] or "step"


@dataclass
class Finding:
    """One validation result, located well enough to act on."""

    message: str
    block_id: str
    #: Human-readable ancestry, e.g. "Root > Fan out > Audit step".  A
    #: bare block id is not locatable in a 40-block card.
    path: str
    severity: str = "error"


@dataclass
class ValidationResult:
    errors: List[Finding] = field(default_factory=list)
    warnings: List[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing blocks a launch.  Warnings do not."""
        return not self.errors

    def summary(self) -> str:
        """Operator-readable text, or "" when clean.

        Errors first: the reader is deciding what to fix, and a warning
        list ahead of the blocking reason buries it.
        """
        lines: List[str] = []
        for f in self.errors:
            lines.append(f"ERROR [{f.path}]: {f.message}")
        for f in self.warnings:
            lines.append(f"WARNING [{f.path}]: {f.message}")
        return "\n".join(lines)


def _label(block: Block) -> str:
    return block.name or block.id or block.block_type or "?"


def validate_card_tree(
    root: Optional[Block],
    *,
    project_id: Optional[str] = None,
    project_root: Optional[str] = None,
) -> ValidationResult:
    """Walk ``root`` and report structural defects.

    ``project_id`` / ``project_root`` enable Call-target resolution.
    Without them target checking is skipped rather than failed.
    """
    res = ValidationResult()
    if root is None:
        res.errors.append(Finding(
            message="card has no root block", block_id="", path="(root)",
        ))
        return res
    index = _index_tree(root)
    index.contracts, index.gathers = _effective_contracts(root)
    index.schemas = _SchemaCache(project_root)
    _walk(root, [], res, project_id, project_root, index=index)
    return res


# ── output contracts (design/typed-findings.md, step 6) ───────────────

_CONTRACT_FIELD_OWNERS = {
    "output_contract": ("task",),
    "output_contract_defaults": ("state",),
    "gather_contract": tuple(sorted(CONTAINER_TYPES - {"schedule"})),
    "gather_contract_defaults": ("state",),
}

# {{sibling("x").outputs.NAME.path}} / .outputs_all.NAME.path.  Looser
# than task_templating's placeholder regex for the same reason as
# _SIBLING_REF_RE: a recognisable reference is checked even if malformed.
_TYPED_REF_RE = re.compile(
    r"sibling\(\s*['\"]([^'\"]+)['\"]\s*\)\.(outputs|outputs_all)\.([A-Za-z0-9_.]+)"
)


def _entry_get(e, key):
    if isinstance(e, dict):
        return e.get(key)
    if key == "schema":
        return getattr(e, "schema_", None)
    return getattr(e, key, None)


def _effective_contracts(root: Block) -> Tuple[Dict[str, Dict[str, object]], Dict[str, list]]:
    """Task id -> {part name: schema ref}, State defaults applied.

    Mirrors the executor's rule (block_executor, contract_defaults): a
    State's ``output_contract_defaults`` reach its later siblings and
    their subtrees, a Task's own entry of the same name wins, and an
    explicit ``[]`` opts out.  Call callees are not in the tree.
    """
    from .output_contracts import effective_gather, merge_contract_defaults
    out: Dict[str, Dict[str, object]] = {}
    gathers: Dict[str, list] = {}

    def visit(block: Block, defaults: Dict[str, object], gdefaults: Dict[str, object]) -> None:
        if block.block_type == "task" and block.id:
            merged = merge_contract_defaults(block.output_contract, defaults) or []
            out[block.id] = {str(_entry_get(e, "name") or ""): _entry_get(e, "schema")
                             for e in merged}
        elif block.block_type in _GATHER_TYPES and block.id:
            # A container's gathered outputs are typed by its gather
            # contract (step 6e): {{sibling("lanes").outputs_all.finding...}}.
            entries = effective_gather(block, gdefaults)
            if entries:
                out[block.id] = {str(_entry_get(e, "name") or ""): _entry_get(e, "schema")
                                 for e in entries}
                gathers[block.id] = entries
        local, glocal = defaults, gdefaults
        for child in block.body or []:
            visit(child, local, glocal)
            if child.block_type == "state" and child.output_contract_defaults:
                local = {**local, **{e.name: e for e in child.output_contract_defaults}}
            if child.block_type == "state" and child.gather_contract_defaults:
                glocal = {**glocal, **{e.name: e for e in child.gather_contract_defaults}}

    visit(root, {}, {})
    return out, gathers


_GATHER_TYPES = frozenset({"parallel", "group", "repeat", "until"})


def _check_gather(block: Block, index: "_TreeIndex", err, warn) -> None:
    """Producer, placement and conflict rules for a gathered container (6e)."""
    from .output_contracts import _subtree_ids, gather_units, unit_producers
    entries = index.gathers.get(block.id or "")
    if not entries:
        return
    btype = block.block_type
    for g in entries:
        name = str(_entry_get(g, "name") or "")
        per = _entry_get(g, "per") or "child"
        mx = _entry_get(g, "max")
        if per == "child" and btype in ("repeat", "until"):
            err(f"gather_contract {name!r}: per 'child' applies to parallel and group; "
                f"a {btype} is judged per 'iteration' or 'total'")
            continue
        if per == "iteration" and btype in ("parallel", "group"):
            err(f"gather_contract {name!r}: per 'iteration' applies to repeat and until; "
                f"a {btype} is judged per 'child' or 'total'")
            continue
        if _entry_get(g, "rerun_missing"):
            if btype in ("group", "until") or per == "total":
                where = "per 'total'" if per == "total" else f"a {btype}"
                err(f"gather_contract {name!r}: rerun_missing is not allowed on {where} - "
                    f"only short children of a parallel or iterations of a repeat can be re-run")
            else:
                # The cost bound: one re-run pass of the short units, each
                # producer still able to take its correction turn.
                if btype == "parallel":
                    bound = f"up to {len(block.body or [])} children"
                elif block.repeat_mode == "for_each":
                    bound = "up to every roster member"
                else:
                    bound = f"up to {block.repeat_count or 1} iterations"
                warn(f"gather_contract {name!r}: rerun_missing can re-dispatch {bound} once "
                     f"(up to four model calls per unit with correction turns)")
                body_text = json.dumps([c.model_dump() for c in block.body or []])
                if btype == "repeat" and not block.repeat_parallel and \
                        re.search(r"\{\{\s*previous[\s.}]", body_text):
                    warn(f"gather_contract {name!r}: rerun_missing on a sequential repeat whose "
                         f"body uses {{{{previous}}}} - re-running iteration i does not re-run "
                         f"i+1, which already ran on the first attempt's result")
        wanted = list(_entry_get(g, "producer") or [])
        sub = _subtree_ids(block) - ({block.id} if block.id else set())
        for pid in wanted:
            if pid not in sub:
                err(f"gather_contract {name!r}: producer {pid!r} is not a block inside "
                    f"{block.name or block.id!r}")
            elif index.ids[pid].block_type not in ("task", "call"):
                err(f"gather_contract {name!r}: producer {pid!r} is a "
                    f"{index.ids[pid].block_type} block; a producer must be a task or a call")
        for label, unit in gather_units(block, g):
            producers, tasks = unit_producers(unit, g)
            if wanted:
                if mx == 1 and len(producers) > 1:
                    err(f"gather_contract {name!r}: unit {label!r} has {len(producers)} "
                        f"producers but max is 1 - each producer must emit, so the unit "
                        f"would always exceed it")
                if any(p.block_type == "call" for p in producers):
                    warn(f"gather_contract {name!r}: producer in {label!r} is a call - the "
                         f"callee gets no pushed-down contract and no correction turn")
                continue
            if len(tasks) > 1:
                names = ", ".join(t.name or t.id or "?" for t in tasks[:8])
                err(f"gather_contract {name!r}: {label!r} contains {len(tasks)} tasks "
                    f"({names}); set producer to the id of the task that emits {name!r}, "
                    f"and give that task an explicit id")
            elif not tasks:
                warn(f"gather_contract {name!r}: {label!r} has no task to push the contract "
                     f"to (a call, or nothing) - it is checked only at the container end, "
                     f"with no correction turn")
        _check_gather_conflicts(block, g, name, index, err)


def _check_gather_conflicts(block: Block, g, name: str, index: "_TreeIndex", err) -> None:
    """A descendant Task's own contract of the gathered name must be compatible.

    Compatible: the same schema, or a schema whose top-level ``allOf``
    references it.  Unresolvable schemas are reported elsewhere.
    """
    from .output_contracts import _refs_in, canonical_ref
    gref = _entry_get(g, "schema")
    gid = canonical_ref(gref) if isinstance(gref, str) else json.dumps(gref, sort_keys=True)

    def ok(tref) -> bool:
        tid = canonical_ref(tref) if isinstance(tref, str) else json.dumps(tref, sort_keys=True)
        if tid == gid:
            return True
        compiled, _ = index.schemas.get(tref)
        if compiled is None or not isinstance(compiled.schema, dict):
            return True                     # cannot verify: not refused
        return any(canonical_ref(r.split("#", 1)[0]) == gid
                   for sub in compiled.schema.get("allOf") or [] for r in _refs_in(sub))

    def visit(b: Block) -> None:
        if b.block_type == "call":
            return
        if b.block_type == "task":
            for e in b.output_contract or []:
                if _entry_get(e, "name") == name and not ok(_entry_get(e, "schema")):
                    err(f"gather_contract {name!r} on {block.name or block.id!r}: task "
                        f"{b.name or b.id!r} declares {name!r} with an unrelated schema "
                        f"{_entry_get(e, 'schema')!r} - use the gather schema, or a project "
                        f"schema whose allOf includes it")
        for c in b.body or []:
            visit(c)
    for c in block.body or []:
        visit(c)


class _SchemaCache:
    """Compile each schema reference once per validation.

    A ``project:`` reference with no project root cannot be checked and
    is skipped, not failed - the module's "cannot verify is not invalid"
    rule.  Returns ``(compiled or None, error or None)``.
    """

    def __init__(self, project_root: Optional[str]):
        self.project_root = project_root
        self.memo: Dict[str, Tuple[object, Optional[str]]] = {}

    def get(self, ref):
        key = json.dumps(ref, sort_keys=True) if isinstance(ref, dict) else str(ref)
        if key not in self.memo:
            from .output_contracts import ContractError, SchemaSet
            if isinstance(ref, str) and ref.strip().startswith("project:") and not self.project_root:
                self.memo[key] = (None, None)
            else:
                try:
                    s = SchemaSet.resolve([ref], self.project_root)
                    self.memo[key] = (s.compile(ref), None)
                except ContractError as e:
                    self.memo[key] = (None, str(e))
        return self.memo[key]


def _check_contract_fields(block: Block, index: "_TreeIndex", err, warn) -> None:
    """Every contract entry has a name, unique per field, and a schema that resolves."""
    for fld, owners in _CONTRACT_FIELD_OWNERS.items():
        entries = getattr(block, fld, None)
        if entries is None:
            continue
        if block.block_type not in owners:
            warn(f"{fld} is ignored on a {block.block_type} block "
                 f"(it applies to: {', '.join(owners)})")
            continue
        seen: Set[str] = set()
        for i, e in enumerate(entries):
            name = str(_entry_get(e, "name") or "").strip()
            if not name:
                err(f"{fld}[{i}] has no name - the part it constrains cannot be identified")
                continue
            if name in seen:
                err(f"{fld} declares {name!r} twice - only the last would apply")
            seen.add(name)
            _, problem = index.schemas.get(_entry_get(e, "schema"))
            if problem:
                err(f"{fld} {name!r}: {problem}")


def _check_typed_refs(block: Block, index: "_TreeIndex", err) -> None:
    """``sibling("x").outputs.NAME.path`` must exist in x's declared schema.

    Checked only where block ``x`` is a Task that declares (or inherits)
    a contract for NAME; references to anything else stay unchecked, as
    before.  A for_each source must also name a list.
    """
    sources = [("instructions", block.instructions or "", False)]
    if block.block_type == "repeat":
        sources.insert(0, ("repeat_for_each_source", block.repeat_for_each_source or "", True))
    for field_name, text, is_source in sources:
        if "sibling(" not in text:
            continue
        for ref_id, kind, rest in dict.fromkeys(_TYPED_REF_RE.findall(text)):
            rest = rest.strip(".")
            name, _, sub = rest.partition(".")
            schema_ref = (index.contracts.get(ref_id) or {}).get(name)
            if schema_ref is None:
                continue
            compiled, _ = index.schemas.get(schema_ref)
            if compiled is None:
                continue
            path = [t for t in sub.split(".") if t]
            # outputs_all is always a JSON array, so only the path is checked.
            problem = field_path_problem(
                compiled, path, want_array=is_source and kind == "outputs")
            if problem:
                err(f'{field_name}: sibling("{ref_id}").{kind}.{rest} - part '
                    f'{name!r} (schema {compiled.id}) {problem}')



def _has_effective_contract(block: Block, index: "_TreeIndex") -> bool:
    """Whether a Task has any output contract in scope (own or State default).

    Keyed by id when the block has one (the index already merged State
    defaults); an unsaved, id-less block falls back to its own entries,
    so a default supplied only by a State is not seen for it — the
    warning that uses this is advisory, and a saved card re-validates
    at launch with ids present.
    """
    if block.id:
        return bool(index.contracts.get(block.id))
    return bool(block.output_contract)


def _walk(
    block: Block,
    ancestry: List[str],
    res: ValidationResult,
    project_id: Optional[str],
    project_root: Optional[str],
    enclosing: Tuple[str, ...] = (),
    index: Optional[_TreeIndex] = None,
    lineage: Tuple[str, ...] = (),
    parent_stops: bool = False,
) -> None:
    path = " > ".join([*ancestry, _label(block)])
    bid = block.id or ""
    if index is None:
        index = _index_tree(block)
    if index.schemas is None:
        index.contracts, index.gathers = _effective_contracts(block)
        index.schemas = _SchemaCache(project_root)

    def err(msg: str) -> None:
        res.errors.append(Finding(message=msg, block_id=bid, path=path))

    def warn(msg: str) -> None:
        res.warnings.append(Finding(
            message=msg, block_id=bid, path=path, severity="warning",
        ))

    btype = block.block_type

    if btype not in KNOWN_BLOCK_TYPES:
        err(f"unknown block_type {btype!r} - the executor will refuse it")
        return  # Nothing below can be interpreted against an unknown type.

    # Fields the model absorbed but nothing reads.  Almost always a typo
    # for a real field, and the authored intent is silently lost.
    for name in sorted((block.model_extra or {})):
        warn(
            f"unknown field {name!r} is ignored - check the spelling "
            f"(nothing reads it, so any value set there has no effect)"
        )

    if btype == "task":
        if not (block.instructions or "").strip():
            err("task has no instructions - dispatch will fail")
        # A task's verdict without a contract is the model's own
        # self-assessment, and the executor fails the task on "partial"
        # alone.  Under on_failure=stop that single judgement halts every
        # later sibling (a nightly card lost its whole second half
        # to one "partial: lane 1B skipped").  A satisfied REQUIRED
        # output_contract overrides a partial verdict, and required:false
        # keeps the sequence going; either makes the halt a decision
        # rather than an accident, so say so before launch.
        if (parent_stops and block.required is not False
                and not _has_effective_contract(block, index)):
            warn(
                "required task in a sequence with on_failure=stop has no "
                "output_contract - a 'partial' self-assessment alone fails "
                "it and halts the remaining siblings; add an output_contract "
                "(a satisfied required contract overrides partial) or set "
                "required: false"
            )

    elif btype == "call":
        if not (block.call_target or "").strip():
            err("call block has no call_target")
        else:
            _check_call_target(block, err, warn, project_id, project_root)

    elif btype == "repeat":
        _check_repeat(block, err, warn)

    elif btype == "until":
        # until_condition is legitimately empty for the /goal shape,
        # which relies on Artifact.self_assessment instead (see
        # design/goal-exit-conditions.md), so its absence is not a
        # finding.  An absent cap is: _execute_until defaults to 5,
        # which may not be what an author writing an open-ended loop
        # expects.
        if not block.until_max:
            warn("until block has no until_max - defaults to 5 iterations")

    elif btype == "ask":
        _check_ask(block, err, enclosing)

    if btype in CONTAINER_TYPES and not (block.body or []):
        warn(f"{btype} block has an empty body - it will do nothing")

    _check_cwd(block, project_root, err, warn)
    _check_sibling_refs(block, index, lineage, err, warn)
    _check_contract_fields(block, index, err, warn)
    _check_typed_refs(block, index, err)
    _check_gather(block, index, err, warn)

    # What this block is, as an ENCLOSURE for the subtree beneath it.  A
    # parallel Repeat is both at once, which is why these accumulate as tags
    # rather than resolving to a single kind.
    child_enclosing = enclosing
    if btype == "parallel" or (btype == "repeat" and block.repeat_parallel):
        child_enclosing = (*child_enclosing, "parallel")
    if btype in ("repeat", "until"):
        child_enclosing = (*child_enclosing, "loop")

    child_lineage = (*lineage, bid) if bid else lineage
    # on_failure governs the implicit SEQUENCE of this block's body
    # (group / repeat / until / schedule); _execute_parallel never reads
    # it, so a Parallel's children are not "under stop" even when set.
    child_stops = (btype in ("group", "repeat", "until", "schedule")
                   and block.on_failure == "stop")
    for child in block.body or []:
        _walk(child, [*ancestry, _label(block)], res, project_id,
              project_root, child_enclosing, index, child_lineage,
              parent_stops=child_stops)


def _check_cwd(block: Block, project_root: Optional[str], err, warn) -> None:
    """``scope.cwd`` on ANY block must stay inside the project root.

    A cwd set on a container flows down to every task beneath it (the one
    non-additive scope field besides the model fields — merge_scopes takes
    the innermost non-null value), so the check runs on every block, not
    only on Tasks.  The executor (task_executor, ``effective_root``)
    confines cwd at run time by falling back to the project root with a
    warning in ``decisions`` — which means a bad cwd is discovered only
    after the task has run somewhere else.  Say so before launch:

      * escapes the root (``..`` up and out, or an absolute path outside
        it) -> error, the executor would ignore it;
      * inside the root but no such directory -> warning when the root is
        known (a card can be validated without one; the directory may be
        created by an earlier stage, so this is advisory).
    """
    from pathlib import Path, PurePosixPath

    cwd = getattr(block.scope, "cwd", None) if block.scope else None
    if not cwd or not str(cwd).strip():
        return
    raw = str(cwd).strip()
    p = PurePosixPath(raw)
    if not p.is_absolute():
        # Normalise lexically: "a/../.." escapes, "a/../b" does not.
        depth = 0
        for part in p.parts:
            if part == "..":
                depth -= 1
                if depth < 0:
                    err(f"scope.cwd {raw!r} escapes the project root - the "
                        f"executor ignores it and runs at the project root")
                    return
            elif part not in ("", "."):
                depth += 1
    if not project_root:
        return
    try:
        base = Path(project_root).resolve()
        cand = (base / raw).resolve()
        cand.relative_to(base)
    except (ValueError, OSError):
        err(f"scope.cwd {raw!r} escapes the project root - the executor "
            f"ignores it and runs at the project root")
        return
    if not cand.is_dir():
        warn(f"scope.cwd {raw!r} is not a directory under the project root "
             f"right now - unless an earlier stage creates it, the executor "
             f"falls back to the project root")


def _sibling_ref_problem(
    ref: str, block: Block, index: _TreeIndex, lineage: Tuple[str, ...],
) -> Optional[Tuple[str, str]]:
    """Why ``sibling(ref)`` cannot resolve, as ``(kind, message)``.

    ``None`` when the reference is fine.  ``kind`` is ``"unknown"`` for an
    id that is simply absent from this tree, and something else for every
    case that is a certain authoring error; the caller uses that split to
    choose severity.
    """
    if ref in index.ids:
        if ref == block.id:
            return ("self", (
                "refers to this block itself - a block has no artifact "
                "until it finishes, so this can never resolve"
            ))
        if ref in lineage:
            return ("ancestor", (
                "refers to an enclosing block, which cannot have completed "
                "while its body (this block) is still running"
            ))
        if ref in _descendant_ids(block):
            return ("descendant", (
                "refers to a block inside this one's body, which has not "
                "run yet when this template renders"
            ))
        return None
    named = index.names.get(ref) or []
    if len(named) == 1:
        target = named[0]
        if target.id:
            remedy = f'use sibling("{target.id}")'
        else:
            slug = _slug_for(ref)
            remedy = (
                f'give that block an explicit "id" (for example '
                f'"{slug}") and reference sibling("{slug}")'
            )
        return ("name", (
            f"is a block NAME, but sibling() takes the block ID - {remedy}. "
            f"Explicit ids are kept on save; only missing ones are "
            f"generated"
        ))
    if len(named) > 1:
        return ("name", (
            f"is a block NAME shared by {len(named)} blocks - sibling() "
            f"takes the block ID. Give the intended block an explicit "
            f"\"id\" and reference that"
        ))
    known = sorted(index.ids)
    shown = known[:_KNOWN_IDS_SHOWN]
    more = f" (+{len(known) - len(shown)} more)" if len(known) > len(shown) else ""
    return ("unknown", (
        f"names no block in this card. Block ids here: {shown!r}{more}. "
        f"If the block you mean has no id yet, set one explicitly and "
        f"reference it"
    ))


def _check_sibling_refs(
    block: Block, index: _TreeIndex, lineage: Tuple[str, ...], err, warn,
) -> None:
    """Refuse ``{{sibling("X")}}`` references that can never resolve.

    Measured on a real run: a for_each source of
    ``{{sibling("Build design-doc roster").outputs.roster.docs}}`` -
    the block's NAME, because the authoring skill's examples show names
    and ids are minted on save where the author never sees them.  The
    renderer's by-id lookup missed, rendered "", and the runtime error
    told the author to fix the upstream ``emit_artifact`` - the one part
    of the card that was probably already right.

    Severity: a reference in ``repeat_for_each_source`` is an error in
    every case, because the block fails with zero iterations.  A
    reference in ``instructions`` to an id that is merely absent is a
    warning: the executor's registry is shared with Call'd cards, so a
    callee may legitimately reference a block in its caller.  Naming a
    block by its name, or referencing self / an ancestor / a descendant,
    is an error wherever it appears - those cannot be right.
    """
    sources = []
    if block.block_type == "repeat":
        sources.append(("repeat_for_each_source",
                        block.repeat_for_each_source or "", True))
    sources.append(("instructions", block.instructions or "", False))
    for field_name, text, hard in sources:
        if "sibling(" not in text:
            continue
        # dict.fromkeys: de-duplicate while keeping first-seen order, so a
        # reference used three times yields one finding.
        for ref in dict.fromkeys(_SIBLING_REF_RE.findall(text)):
            problem = _sibling_ref_problem(ref, block, index, lineage)
            if problem is None:
                continue
            kind, message = problem
            full = f'{field_name}: sibling("{ref}") {message}'
            if kind == "unknown" and not hard:
                warn(full + " (harmless only if this card is Called from "
                            "a card that has that block)")
            else:
                err(full)


def _check_ask(block: Block, err, enclosing: Tuple[str, ...]) -> None:
    """Refusals for a human-in-the-loop Ask block.

    An Ask holds the run at a block boundary with status ``awaiting_input``
    until a human answers, then binds the answer into the run the way a
    State block binds its literals.

    The last two refusals are about the single-slot shape of the answer
    record rather than about the block itself, and both are errors rather
    than warnings because in each case the alternative is a run that carries
    on having acted on an answer nobody gave for that occasion.  Neither is
    a permanent limit -- they are the honest boundary of what the current
    record can express, stated where an author finds out before launching
    rather than after.
    """
    # ``getattr`` rather than attribute access, matching the repeat checks
    # above: extra="allow" means a card authored against a newer schema can
    # arrive carrying these as model extras, and a validator that raises
    # AttributeError reports nothing at all about the rest of the tree.
    if not (getattr(block, "ask_question", None) or "").strip():
        err(
            "ask block has no ask_question - it would hold the run "
            "indefinitely on a blank prompt, with nothing telling the "
            "operator what is being asked"
        )
    if block.body:
        err(
            f"ask block has a body of {len(block.body)} block(s) - ask is a "
            f"leaf, so nothing inside it would ever run"
        )
    name = (getattr(block, "ask_variable", None) or "").strip()
    if name and not _ASK_VARIABLE_RE.match(name):
        err(
            f"ask_variable {name!r} is not a usable template name - the "
            f"answer would be bound where no {{{{var.NAME}}}} can reach it"
        )
    choices = getattr(block, "ask_choices", None)
    if choices is not None:
        cleaned = [str(c).strip() for c in choices]
        if not cleaned:
            err(
                "ask_choices is present but empty - either offer choices or "
                "omit the field for a free-text answer"
            )
        if any(not c for c in cleaned):
            err("ask_choices contains a blank choice")
        dupes = sorted({c for c in cleaned if c and cleaned.count(c) > 1})
        if dupes:
            err(
                f"ask_choices repeats {dupes} - a duplicated choice cannot be "
                f"told apart in the recorded answer"
            )
    if "parallel" in enclosing:
        err(
            "ask block sits inside a parallel container - a run records ONE "
            "open question at a time, so concurrent asks would overwrite "
            "each other and the run would proceed on whichever answer landed "
            "last. Put the checkpoint above the fan-out"
        )
    if "loop" in enclosing:
        err(
            "ask block sits inside a loop body - answers are recorded per "
            "block, so the second iteration would silently reuse the first "
            "iteration's answer instead of asking again. Put the checkpoint "
            "outside the loop, or ask before the loop about the whole batch"
        )


def _literal_roster_size(raw: str) -> Optional[int]:
    """Item count when a for_each source is a literal JSON array.

    None when the source is templated or unparseable - the roster then
    only exists at run time, so nothing here can size it.
    """
    text = (raw or "").strip()
    if "{{" in text or not text.startswith("["):
        return None
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        return None
    return len(items) if isinstance(items, list) else None


def _check_for_each_cap(block: Block, raw: str, warn) -> None:
    """Flag a repeat_max that will clip a for_each roster.

    repeat_max is a legitimate cost ceiling, but on a for_each block it is
    also a SCOPE limit, and the two are easy to conflate: the loop
    completes every iteration it PLANNED and reports success, while the
    roster's tail was never dispatched.  Measured on a real run - a
    108-item roster under a cap of 60 dropped 48 items.

    It is especially easy to get wrong in this mode because the cap is not
    authorable here: RepeatBlockEditor renders the repeat_max input only
    for 'until', and switching modes does not clear it, so a number set as
    an until-bound goes on to act as a for_each clip the editor never
    shows.  The pre-existing wide-fanout warning does not cover this: it
    speaks about concurrency, and reading "60 parallel iterations will run
    8 at a time" as confirmation that all 60 run is the natural reading.

    A literal source is decidable now, so the warning names the real loss.
    A templated source is not, which is exactly why a fixed cap on one is
    worth stating: the author is choosing a number without being able to
    know the roster it will be measured against.
    """
    cap = block.repeat_max
    if not cap or cap <= 0:
        return  # None/0 mean uncapped - run the whole roster.
    roster = _literal_roster_size(raw)
    if roster is None:
        warn(
            f"repeat_max={cap} caps a templated for_each roster - if the "
            f"source resolves to more than {cap} items the remainder is "
            f"never run, and the block still reports success. Set "
            f"repeat_max to 0 to run the whole roster, and bound cost with "
            f"repeat_max_concurrency instead"
        )
    elif roster > cap:
        warn(
            f"repeat_max={cap} clips this for_each roster of {roster} "
            f"items - {roster - cap} would never run. Set repeat_max to 0 "
            f"to run all {roster}"
        )


def _check_require_complete(block: Block, raw: str, err) -> None:
    """Refusals for ``repeat_require_complete`` decidable before launch.

    Mirrors the executor's plan-time refusals in
    ``block_executor._plan_iterations`` - the two must agree on what the
    field means, which is why key derivation is shared via
    ``roster_keys`` rather than reimplemented here.

    A finite cap is refused for ANY source; roster-key hazards are only
    decidable for a literal source (a templated roster exists at run
    time, where the executor applies the same refusals).
    """
    if block.repeat_max and block.repeat_max > 0:
        err(
            f"repeat_require_complete with repeat_max={block.repeat_max} "
            f"is a contradiction - a completeness requirement and a cost "
            f"ceiling cannot both hold. Remove one; bound cost with "
            f"repeat_max_concurrency instead"
        )
    text = (raw or "").strip()
    if "{{" in text or not text.startswith("["):
        return  # Templated: the roster's keys exist only at run time.
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        return
    if not isinstance(items, list):
        return
    for problem in roster_key_problems(
        items, getattr(block, "repeat_item_key", None),
    ):
        err(problem)


def _check_repeat(block: Block, err, warn) -> None:
    mode = block.repeat_mode or "count"

    # The roster completeness assertion only means something where a
    # roster exists.  count runs N anonymous passes; until stops on a
    # condition - neither has a member set to be complete OVER.
    if getattr(block, "repeat_require_complete", False) and mode != "for_each":
        err(
            f"repeat_require_complete is set but repeat_mode is {mode!r} "
            f"- only for_each has a roster to assert completeness over"
        )

    if mode == "for_each":
        raw = block.repeat_for_each_source or ""
        if not raw.strip():
            err(
                "repeat_mode is 'for_each' but repeat_for_each_source is "
                "empty - the loop has no items to iterate over"
            )
        # A templated source's ITEMS resolve at runtime and are not
        # checkable here, but a cap that will clip them is.
        _check_for_each_cap(block, raw, warn)
        if getattr(block, "repeat_require_complete", False):
            _check_require_complete(block, raw, err)

    elif mode == "count":
        if block.repeat_count is not None and block.repeat_count < 1:
            warn(
                f"repeat_count is {block.repeat_count} - the body will "
                f"never run"
            )

    elif mode == "until":
        # An uncapped until-mode Repeat is inert rather than unbounded:
        # _plan_iterations reads ``int(block.repeat_max or 1)``, so the
        # body runs once and the until-condition is evaluated after the
        # only pass it will ever get.  "Until" that cannot loop is
        # indistinguishable from count=1, and nothing said so - the editor
        # displayed 3 for the same field, and the sibling Until BLOCK
        # defaults to 5, so all three descriptions of one concept
        # disagreed.  Stated here rather than raising the runtime default,
        # which would silently change spend on every existing card.
        if not block.repeat_max:
            warn(
                "repeat_mode is 'until' but repeat_max is unset - it "
                "defaults to 1, so the body runs once and the "
                "until-condition can never cause a second pass. Set "
                "repeat_max to the number of attempts the loop may make"
            )

    # Wide parallel fan-out: bounded by the concurrency cap, but the
    # author is choosing a shape whose wall-clock cost is set by that cap
    # rather than by the fan-out width, which is worth stating up front.
    #
    # ``planned`` was previously read off repeat_count/repeat_max alone,
    # so an UNCAPPED for_each - the shape most in need of the warning -
    # could never fire it.  For for_each the roster IS the width: size it
    # from a literal source when decidable, and for a templated source
    # say "roster-sized" rather than silently reading a cap that is not
    # there.
    if block.repeat_parallel:
        limit = block.repeat_max_concurrency
        planned: Optional[int]
        if mode == "for_each":
            roster = _literal_roster_size(block.repeat_for_each_source or "")
            cap = block.repeat_max if (block.repeat_max or 0) > 0 else None
            if roster is not None:
                planned = min(roster, cap) if cap else roster
            else:
                planned = cap  # templated: unknowable unless capped
        else:
            planned = block.repeat_count or block.repeat_max or 0
        if limit is None and planned is not None and planned > _WIDE_FANOUT_THRESHOLD:
            warn(
                f"{planned} parallel iterations with no "
                f"repeat_max_concurrency - they will run "
                f"{_WIDE_FANOUT_THRESHOLD} at a time (the default cap)"
            )
        elif limit is None and planned is None:
            warn(
                f"a roster-sized parallel fan-out with no "
                f"repeat_max_concurrency - if the templated roster "
                f"resolves to more than {_WIDE_FANOUT_THRESHOLD} items "
                f"they will run {_WIDE_FANOUT_THRESHOLD} at a time (the "
                f"default cap)"
            )


def _check_call_target(
    block: Block, err, warn,
    project_id: Optional[str],
    project_root: Optional[str],
) -> None:
    """Resolve a Call target now rather than when the block is reached.

    ``resolve_call_target`` is pure lookup with no side effects, which is
    what makes this safe to run at launch.
    """
    if not project_id:
        return  # Cannot verify; not a defect.
    try:
        resolve_call_target(
            block.call_target, block.call_target_kind,
            project_id=project_id, project_root=project_root,
        )
    except CallResolutionError as e:
        err(f"call target {block.call_target!r} does not resolve: {e}")
    except Exception as e:  # noqa: BLE001
        # A resolver fault is not a verdict on the card.
        warn(
            f"could not verify call target {block.call_target!r} "
            f"({type(e).__name__}: {e})"
        )
