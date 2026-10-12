"""
Scope-preservation guard for the agent-facing ``task_card_write`` MCP tool.

The tool's charter is TEXT and STRUCTURE — instructions, loop counts,
conditions, block arrangement.  Scope (permissions) is deliberately not
in it: the signed-approval ledger keys an escalation by ``(block_id,
scope_hash)``, so an agent-submitted root that edits a scope, or that
drops the ``id`` of a scope-bearing block (``_assign_block_ids`` then
mints a fresh one), silently orphans a signed approval — the block
falls to the permission floor on the next run with nothing saying so.
That is not hypothetical malice; it is the default outcome of a model
rewriting a tree from memory.

This module makes the rule mechanical: a submitted root must carry
exactly the same ``{block_id: scope}`` map as the stored card.  Any
difference — added, removed, or edited scope, or a scope-bearing block
whose id changed/vanished — is a finding, and the tool refuses the
write, naming each offense and the remedy (the Task Card editor, where
scope edits surface their signing requirements to a human).

Parity note: the self-improvement patch path enforces the same "text,
never privilege" invariant structurally (field whitelist + existing-id
keying + structure fingerprint — app/utils/self_improve.py).  This
guard closes the one remaining card-write path that had no equivalent.
"""

import json
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger


def _normalize_scope(scope: Optional[Dict[str, Any]]) -> Optional[str]:
    """Canonical JSON for a block's scope, or None when the scope is
    absent or grants nothing.

    Normalized through the TaskScope model so a sparse agent-authored
    dict ({"tools": ["x"]}) and a fully-populated model_dump of the
    same scope compare equal.  A scope whose every field is empty is
    treated as absent — an agent echoing ``scope: {}`` where the
    stored card has ``scope: null`` (or vice versa) is not a
    permissions change.
    """
    if not scope:
        return None
    try:
        from app.models.task_card import TaskScope
        normalized = TaskScope(**scope).model_dump()
    except Exception:  # noqa: BLE001 — an unparseable scope IS a finding;
        # surface it as a distinct value that can never equal a parsed one.
        return json.dumps({"__unparseable__": str(scope)}, sort_keys=True)
    # ``cwd`` is a working directory, not a grant: it is absent from the
    # signing hash (scope_canonical.task_escalation_block keys only
    # shell_commands and out-of-floor writable paths) and the executor
    # confines it to the project root regardless.  It is also the one
    # scope field the harness expects a card author -- including the
    # model -- to set per block, so comparing it here refused the exact
    # edit the executor permits.  Dropped before comparison.
    normalized.pop("cwd", None)
    meaningful = any(
        normalized.get(f) for f in (
            "paths", "tools", "skills", "shell_commands",
            "shell_timeout_secs", "model_tier", "model_name",
            "model_id_override", "model_endpoint",
        )
    )
    if not meaningful:
        return None
    return json.dumps(normalized, sort_keys=True)


def _collect_scopes(root: Dict[str, Any]) -> Dict[str, str]:
    """{block_id: canonical scope} for every block in the tree whose
    scope grants anything.  A scope-bearing block with NO id keys under
    "" — which can never match a stored id, so the mismatch is caught
    rather than silently resolved by fresh-id minting at save time."""
    out: Dict[str, str] = {}

    def _walk(b: Dict[str, Any]) -> None:
        if not isinstance(b, dict):
            return
        canon = _normalize_scope(b.get("scope"))
        if canon is not None:
            out[str(b.get("id") or "")] = canon
        for child in b.get("body") or []:
            _walk(child)

    _walk(root)
    return out


REMEDY = (
    "A replacement root may not change any block's scope — resend the "
    "tree with every block's id and scope exactly as task_card_read "
    "returned them.  To change a scope field, use the 'edits' parameter "
    "instead ({block_id, set: {'scope.<field>': value}}): it may set any "
    "TaskScope field and may narrow a grant, but may not widen one.  "
    "Grants (shell_commands, writable paths outside the safe-write floor) "
    "are authorized by a root-signed approval, not by the card file, so "
    "editing the deck file by other means gains nothing — ask the user to "
    "add the grant in the Task Card editor and sign it."
)


def find_scope_violations(
    stored_root: Dict[str, Any], submitted_root: Dict[str, Any],
) -> List[str]:
    """Findings (empty = OK) comparing the stored card's scope map to a
    submitted replacement root.  Any difference in the set of
    scope-bearing blocks, their ids, or their scope content is a
    violation.
    """
    stored = _collect_scopes(stored_root)
    submitted = _collect_scopes(submitted_root)
    findings: List[str] = []

    for bid, canon in stored.items():
        got = submitted.get(bid)
        if got is None:
            findings.append(
                f"block '{bid}' carries permissions in the saved card but "
                f"is missing (or lost its id) in the submitted root — "
                f"saving would orphan its signed approval and drop it to "
                f"the permission floor"
            )
        elif got != canon:
            findings.append(
                f"block '{bid}': submitted scope differs from the saved "
                f"card's — permissions edits are not allowed here"
            )
    for bid in submitted:
        if bid not in stored:
            label = f"block '{bid}'" if bid else "a block with no id"
            findings.append(
                f"{label} introduces a scope the saved card does not have "
                f"— permissions cannot be granted through this tool"
            )

    if findings:
        logger.info(
            f"task_card_write guard: {len(findings)} scope violation(s) "
            f"refused"
        )
    return findings
