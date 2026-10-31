"""Resource footprints for calls in one tool batch.

app/tool_batch.py runs the calls of one assistant message in groups: the
members of a group run concurrently, groups run one after another.  Until
this module, a group could only hold read-only calls, so a message of
"three reads and two ledger_record" ran as four serial steps.  A footprint
says which resources a call reads and writes; a call joins the current
group when it conflicts with no member, so independent writes overlap with
the reads around them and with each other.

A footprint is ``(reads, writes)``, two frozensets of resource keys:

  ``fs:<realpath>``         one file          ``fs:*``     any file
  ``ledger:claim:<leaf>``   claim versions    ``ledger:*`` the whole ledger
  ``ledger:obs:<call>``     one observation   ``outputs``  .ziya/outputs
  ``artifacts``             task-run parts    ``memory``   memories/proposals
  ``*``                     everything

Two calls conflict when one writes a key the other reads or writes (``*``
and ``<prefix>:*`` match by prefix).  Conflicting calls land in different
groups, in issue order, so a write and a later read of the same thing keep
their order.  ``None`` means barrier: the call runs alone.

Which writers may overlap, and why each is safe:

* ``file_write`` to a path the write policy already allows.  Its execute()
  has no await, so concurrent writes cannot interleave inside the event
  loop; writes to the same path conflict.  A write the policy does not
  allow would open an approval panel, so it stays a barrier.
* ``ledger_record``: one ``O_APPEND`` write under ``flock`` in a thread
  (app/utils/evidence/store.py).  Two records of one claim conflict, so
  version order follows issue order.  The claim key is reduced to its last
  segment, which over-matches across namespaces but never misses the same
  claim spelled with and without its namespace.  A claim with ``inputs``
  reads the whole ledger.
* ``emit_artifact``: appends to the run's shared part list.  Emits conflict
  with each other so part order follows issue order.
* ``store_output``: ``flock``-ed append; outputs conflict with each other
  (a repeated ``data.id`` is a new version) and read the ledger for claims.
* ``memory_propose``: proposals conflict with each other and with memory
  reads; the proposal store is not assumed safe for concurrent writers.

Any writer whose consent mode could be ``ask`` is a barrier, so a group
never holds two approval panels.  Read-only calls not listed below read
``*`` and so never overlap with a write: unknown tools, external MCP
tools, ``context_list_files`` (file_write records auto-context).

Known gap, accepted: filesystem readers (shell, ``file_list``, the AST and
PDF tools) read ``fs:*`` only.  A shell ``cat`` of a ledger or output file
in the same message as a write to that store may see either state.
"""
from __future__ import annotations

import logging
import os
from typing import Any, FrozenSet, Optional, Tuple

logger = logging.getLogger(__name__)

ANY = "*"
FS_ANY = "fs:*"
LEDGER_ANY = "ledger:*"

Footprint = Tuple[FrozenSet[str], FrozenSet[str]]  # (reads, writes)

_NONE: FrozenSet[str] = frozenset()

# Read-only tools that touch none of the resources the writers below change.
_UNRELATED_READS = frozenset({
    "render_diagram", "recall_image",
    "chat_search", "chat_read", "chat_list",
    "shadow_list", "shadow_read", "shadow_wait",
    "job_status", "job_read", "task_run_read",
    "task_card_list", "task_card_read", "task_card_validate",
    "bead_status", "handoff_read", "mcp_server_status", "get_skill_details",
    "list_architecture_shape_categories", "search_architecture_shapes",
    "get_architecture_diagram_template",
    "dataset_describe", "dataset_preview", "dataset_list",
    "nova_web_search",
})

# Read-only tools that may read any file but no Ziya store (see "Known gap").
_FS_READS = frozenset({
    "file_list", "ast_get_tree", "ast_search", "ast_references",
    "pdf_outline", "pdf_read_pages", "pdf_search", "view_image",
    "run_shell_command", "mcp_run_shell_command",
})

_STORE_READS = {
    "memory_search": frozenset({"memory"}),
    "memory_context": frozenset({"memory"}),
    "memory_expand": frozenset({"memory"}),
    "ledger_query": frozenset({LEDGER_ANY}),
    "validate_output": frozenset({LEDGER_ANY}),
    "list_run_artifacts": frozenset({"artifacts"}),
    "analyze": frozenset({FS_ANY, "outputs"}),
}


def _fs_key(path: Any, root: Optional[str]) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    raw = os.path.expanduser(path.strip())
    full = raw if os.path.isabs(raw) or not root else os.path.join(root, raw)
    # casefold: on a case-insensitive filesystem two spellings are one file;
    # folding can only add conflicts, never hide one.
    return "fs:" + os.path.realpath(full).casefold()


def _read_footprint(name: str, args: dict, root: Optional[str]) -> Footprint:
    if name == "file_read":
        key = _fs_key(args.get("path"), root)
        return (frozenset({key or FS_ANY}), _NONE)
    if name in _UNRELATED_READS:
        return (_NONE, _NONE)
    if name in _FS_READS:
        return (frozenset({FS_ANY}), _NONE)
    if name in _STORE_READS:
        return (_STORE_READS[name], _NONE)
    return (frozenset({ANY}), _NONE)


def _may_ask(name: str, args: dict, tool: Any, root: Optional[str]) -> bool:
    """True unless the consent gate is certain not to prompt for this call.

    Mirrors the mode resolution in app/utils/consent_gate.consent_gate,
    ignoring standing grants (so it errs toward True).  Any failure is True."""
    try:
        from app.utils import consent_gate as cg
        inst = cg._unwrap(tool) if tool is not None else None
        op = cg.op_for_call(inst, args, None)
        if op in cg.human_only_ops(inst):
            return True
        project_mode = None
        try:
            from app.utils.project_tool_modes import mode_for
            project_mode = mode_for(name, root)
        except Exception:  # noqa: BLE001
            project_mode = None
        if project_mode == "disabled":
            return True
        if project_mode == "autonomous":
            return False
        if project_mode == "gated":
            return cg.declared_mode(inst, op) != cg.MODE_OFF
        return cg.consent_mode(name, op, inst) == cg.MODE_ASK
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tool_footprint: consent mode unknown for {name}: {exc}")
        return True


def _write_allowed(path: str, root: str, exists: bool) -> bool:
    """The file_write preflight's test: no reason means no approval panel."""
    try:
        from app.utils import write_grants
        reason, _grantable = write_grants.classify(path, root, exists)
        return not reason
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tool_footprint: write policy unknown for {path}: {exc}")
        return False


def _write_footprint(name: str, args: dict, root: Optional[str],
                     call_id: str) -> Optional[Footprint]:
    if name == "file_write":
        path = args.get("path")
        key = _fs_key(path, root)
        if not key or not root:
            return None
        raw = os.path.expanduser(path.strip())
        exists = os.path.exists(raw if os.path.isabs(raw) else os.path.join(root, raw))
        if not _write_allowed(path, root, exists):
            return None
        return (_NONE, frozenset({key}))

    if name == "ledger_record":
        claim, obs = args.get("claim"), args.get("observation")
        if not isinstance(claim, (dict, type(None))) or not isinstance(obs, (dict, type(None))):
            return None
        if not claim and not obs:
            return None
        reads, writes = set(), set()
        if obs:
            writes.add(f"ledger:obs:{call_id}")
            key = _fs_key(obs.get("result_path"), root)
            if key:
                reads.add(key)
        if claim:
            ckey = str(claim.get("key") or "").strip()
            if not ckey:
                return None
            writes.add("ledger:claim:" + ckey.rsplit(".", 1)[-1])
            if (claim.get("derivation") or {}).get("inputs"):
                reads.add(LEDGER_ANY)
        return (frozenset(reads), frozenset(writes))

    if name == "emit_artifact":
        reads = set()
        if args.get("file_path"):
            reads.add(_fs_key(args.get("file_path"), root) or FS_ANY)
        return (frozenset(reads), frozenset({"artifacts"}))

    if name == "store_output":
        return (frozenset({LEDGER_ANY}), frozenset({"outputs"}))

    if name == "memory_propose":
        return (_NONE, frozenset({"memory"}))

    return None


def call_footprint(name: str, args: Optional[dict], tool: Any, root: Optional[str],
                   read_only: bool, call_id: str = "") -> Optional[Footprint]:
    """The call's ``(reads, writes)``, or None for a barrier.

    ``read_only`` is app/tool_batch.is_read_only_call's verdict, passed in so
    this module does not import the batch runner."""
    args = args if isinstance(args, dict) else {}
    if read_only:
        return _read_footprint(name, args, root)
    if _may_ask(name, args, tool, root):
        return None
    return _write_footprint(name, args, root, call_id)


def _match(a: str, b: str) -> bool:
    if a == b or a == ANY or b == ANY:
        return True
    if a.endswith(":*") and b.startswith(a[:-1]):
        return True
    if b.endswith(":*") and a.startswith(b[:-1]):
        return True
    return False


def conflicts(x: Footprint, y: Footprint) -> bool:
    """True if the two calls must not run concurrently."""
    (rx, wx), (ry, wy) = x, y
    return (any(_match(a, b) for a in wx for b in ry | wy)
            or any(_match(a, b) for a in rx for b in wy))
