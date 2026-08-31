"""
Task card API endpoints.

CRUD endpoints plus a launch endpoint.  Launch creates a TaskRun,
schedules execution in a background task, and returns immediately
with the run_id.  Clients poll GET /task-runs/{run_id} for status.
"""

import asyncio
import os
import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List

from ..models.task_card import (
    # ``Artifact`` is needed to coerce mid-loop resume iteration records,
    # which arrive as plain dicts when read back off disk.
    Artifact, Block, TaskCard, TaskCardCreate, TaskCardUpdate, TaskCardRun,
)
from ..models.task_run import TaskRun, TaskRunCreate, TaskRunBlockState
from ..storage.projects import ProjectStorage
from ..storage.task_cards import TaskCardStorage
from ..storage.task_runs import TaskRunStorage
from ..agents.task_executor import TaskExecutorError
from ..agents.block_executor import (
    execute_block, ExecutionContext, BlockExecutionCancelled,
)
from ..agents import task_run_stream_relay as _relay
from ..utils.paths import get_ziya_home, get_project_dir
from ..utils.logging_utils import logger

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/task-cards",
    tags=["task-cards"],
)


def _get_storage(project_id: str) -> TaskCardStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return TaskCardStorage(get_project_dir(project_id))


@router.get("", response_model=List[TaskCard])
async def list_task_cards(
    project_id: str,
    templates_only: bool = Query(False),
):
    """List all task cards in a project, optionally templates only."""
    return _get_storage(project_id).list(templates_only=templates_only)


@router.get("/lessons-summary")
async def get_lessons_summary(project_id: str):
    """Per-card self-improvement aggregates for the whole deck in one
    request: {card_id: {count, edits_applied, last_ts}}.

    Drives the deck list's 🌱 badge without an N-request burst (the
    same shape the deck already uses for run status).  One ledger
    read regardless of card count.

    MUST be declared before ``get_task_card`` below: FastAPI matches
    routes in declaration order, so with ``/{card_id}`` first this
    literal path would be captured as a card id and 404.  The test
    ``test_summary_route_not_shadowed_by_card_id`` exists to fail if
    this route is ever moved below it.
    """
    # Validates the project exists (404 otherwise) — same gate every
    # other endpoint gets via _get_storage.
    _get_storage(project_id)
    from ..utils.self_improve import LessonLedger
    return {
        "cards": LessonLedger(get_project_dir(project_id)).summary_by_card(),
    }


@router.get("/{card_id}", response_model=TaskCard)
async def get_task_card(project_id: str, card_id: str):
    card = _get_storage(project_id).get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


def _walk_blocks(block, ancestors=()):
    """Depth-first walk of a card's block tree (root + nested bodies).

    Yields ``(block, ancestor_scopes)`` where ``ancestor_scopes`` is the
    root→parent tuple of scopes above ``block`` (its own scope is NOT
    included — callers merge it in themselves)."""
    yield block, ancestors
    for child in (getattr(block, "body", None) or []):
        yield from _walk_blocks(child, ancestors + (getattr(block, "scope", None),))


def _denial_reason_message(reason: str) -> str:
    """Human-readable explanation for a scope_approvals denial code.

    Mirrors the reason codes returned by
    ``scope_approvals.is_scope_authorized_with_reason`` — kept here (rather
    than in scope_approvals) since it's presentation, not authorization logic.
    """
    if reason == "no_record":
        return "Not yet signed."
    if reason == "scope_hash_mismatch":
        return "Signed for an earlier version of this scope — re-sign after editing."
    if reason == "signature_invalid":
        return "Signature failed verification — re-sign with the current root key."
    if reason.startswith("unbounded_approval_requires_expiry:"):
        max_s = reason.split(":", 1)[1]
        days = int(max_s) // 86400 if max_s.isdigit() else "?"
        return (f"Signed without an expiry, but policy requires approvals to "
                f"expire within {days} day(s) — re-sign to pick up an expiry.")
    if reason.startswith("approval_lifetime_exceeds_policy:"):
        return "Signed lifetime exceeds the policy's maximum — re-sign with a shorter --ttl-days."
    if reason == "malformed_expiry":
        return "Approval record has an unreadable expiry — re-sign."
    return "Escalation not authorized."


def _escalation_rows(
    card, deck_scope, *, project_id: str = "", card_id: str = "",
    check_approvals: bool = True,
):
    """Per-leaf-task escalation rows for a card — saved OR unsaved.

    Shared by the saved-card status endpoint and the unsaved-proposal
    preview endpoint so both derive escalation through the SAME floor
    subtraction (``scope_canonical.task_escalation_block``).  Deriving it
    client-side instead would either cry wolf on a card writing only
    inside a floor safe-write path like ``.ziya/`` or — the direction
    that actually matters — miss a real grant whose command happens to
    look familiar.

    ``check_approvals=False`` is the proposal case: the spec's blocks
    carry no persisted ids yet, so no approval could exist for them and
    no ``signCommand`` is mintable.  Every escalating row comes back
    ``authorized=False`` / ``needsSignature=True``, and nothing is
    staged for the out-of-process signer.

    Returns ``(rows, staged_scopes)``; ``staged_scopes`` is always empty
    when ``check_approvals`` is False.
    """
    from app.config import scope_canonical as sc
    from app.utils import scope_approvals as sa
    from app.models.task_card import merge_scopes

    rows = []
    staged_scopes = {}  # "project:card:block" -> {name, scope} for the signer
    for position, (block, ancestor_scopes) in enumerate(_walk_blocks(card.root)):
        # Only LEAF task blocks are reportable.  The runtime gate lives in
        # execute_task_block (authorize_scope keyed on block.id), and
        # block_executor calls that for block_type == "task" only — a
        # container's own scope is never hashed under the container's id.
        #
        # Reporting a container therefore emitted a signCommand for an id
        # nothing ever checks: signing it wrote a record the gate never
        # reads, so the operator saw "✓ Signed" and the card still ran at
        # the floor.  Worse, because an ancestor's scope is merged into
        # each descendant's effective scope, the container and its leaf
        # produce the SAME hash — so the editor showed one escalation
        # twice, and only one of the two sign commands had any effect.
        #
        # Containers still contribute their privileges: they arrive here
        # via ``ancestor_scopes`` in the merge below, which is what makes
        # the leaf's hash cover them.  Skipping the container drops the
        # duplicate row, not the grant.
        if getattr(block, "block_type", None) != "task":
            continue
        own_scope = getattr(block, "scope", None)
        scope = merge_scopes(deck_scope, card.scope, *ancestor_scopes, own_scope)
        escalation = sc.task_escalation_block(scope)
        if not escalation:
            continue  # no privilege-bearing escalation -> nothing to approve
        # An unsaved spec's blocks have id "" (ids are assigned by
        # TaskCardStorage.create), and several of them would collide on
        # that empty string as a display key.  Fall back to a positional
        # key, which keeps the rows distinct without implying a
        # persisted identity the caller could sign against.
        block_id = getattr(block, "id", "") or ""
        row_key = block_id or f"#{position}"
        authorized = False
        denial_reason = None
        if check_approvals:
            try:
                authorized, denial_reason = sa.is_scope_authorized_with_reason(
                    block_id, scope)
            except Exception as e:  # noqa: BLE001 — must never 500 the editor
                logger.warning(f"scope-status check failed for {block_id}: {e}")
                authorized = False
                denial_reason = "check_failed"
        sign_command = ""
        if check_approvals and not authorized:
            sign_command = (
                f"sudo ziya-approve --task {card_id} "
                f"--block {block_id} --project {project_id}"
            )
            # Stage the DECRYPTED scope so the out-of-process signer (which
            # runs under sudo with no plugin system / KEK and therefore
            # cannot decrypt the card itself) can recompute the identical
            # scope hash.  Stage the full scope shape (shell_commands +
            # paths) that task_escalation_block reads, NOT the reduced
            # escalation block, so the signer's hash matches what the
            # runtime gate re-derives.  This cannot widen authority: the
            # gate independently re-hashes the real card, so a stale
            # staging just fails the match and clamps to floor.
            staged_scopes[f"{project_id}:{card_id}:{block_id}"] = {
                "name": getattr(block, "name", "") or "",
                "scope": {
                    "shell_commands": list(getattr(scope, "shell_commands", []) or []),
                    "paths": [
                        {"path": getattr(e, "path", None),
                         "write": bool(getattr(e, "write", False))}
                        for e in (getattr(scope, "paths", []) or [])
                    ],
                },
            }
        rows.append({
            "blockId": row_key,
            "name": getattr(block, "name", "") or "",
            "hasEscalation": True,
            "authorized": bool(authorized),
            # True whenever this block's escalation is not currently
            # active.  The single field every UI surface reads to decide
            # whether to say "needs signing" — so the proposal block, the
            # live preview and the deck badge cannot drift apart.
            "needsSignature": not authorized,
            "escalation": {k: list(v) for k, v in escalation.items()},
            "signCommand": sign_command,
            "denialReason": denial_reason,
            "denialMessage": (_denial_reason_message(denial_reason)
                              if denial_reason else None),
        })
    return rows, staged_scopes


@router.get("/{card_id}/scope-status")
async def get_card_scope_status(project_id: str, card_id: str):
    """Per-block escalation-approval status for a card (ASR F-001).

    For every LEAF TASK block whose EFFECTIVE scope (deck-level project
    scope + the card's own scope + every ancestor block's scope + its
    own, merged additively — see app.models.task_card.merge_scopes)
    grants a privilege escalation (shell_commands or writable paths),
    report whether a signed approval record matches the CURRENT
    effective-scope hash. Drives the "needs approval" banner in
    TaskCardEditor.

    Container blocks are not reported: only leaf tasks are gated at
    runtime (see the walk below). Their scopes still count, arriving via
    each descendant's ancestor chain.
    Blocks with no escalation (or restriction-only scopes) are omitted — they
    run at the floor and need no approval. The signCommand is the exact
    ``ziya-approve`` invocation that mints the missing record.
    """
    from app.utils import scope_approvals as sa

    # Refresh the approval-TTL breadcrumb the out-of-process signer reads to
    # auto-stamp a policy-compliant expires_at. It is otherwise written only
    # once at server startup (main.py), which is racy and permanent: if the
    # policy bound was unresolved at that moment, the build predates that
    # writer, or the policy was tightened after startup, the file is missing
    # or stale and the signer mints an UNBOUNDED approval that the fail-closed
    # TTL gate then rejects ("Signed without an expiry" — re-sign loop that
    # never converges). This endpoint runs immediately before the operator
    # copies signCommand and signs, has the live in-process policy, and
    # already writes under get_ziya_home(), so it is the natural refresh
    # point. Best-effort: never fail the editor's status check over UX state.
    try:
        sa.write_approval_policy_breadcrumb()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not refresh approval-TTL breadcrumb: {e}")

    card = _get_storage(project_id).get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")

    project = ProjectStorage(get_ziya_home()).get(project_id)
    deck_scope = getattr(project.settings, "taskScope", None) if project else None

    blocks, staged_scopes = _escalation_rows(
        card, deck_scope, project_id=project_id, card_id=card_id,
        check_approvals=True,
    )

    # Merge-write the staging file: replace this card's entries (drop stale ones
    # for blocks now approved/changed), preserve other cards' staged scopes.
    try:
        import json as _json
        staging_path = get_ziya_home() / "pending_task_approvals.json"
        try:
            existing = _json.loads(staging_path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}
        prefix = f"{project_id}:{card_id}:"
        existing = {k: v for k, v in existing.items() if not k.startswith(prefix)}
        existing.update(staged_scopes)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.write_text(_json.dumps(existing, indent=2))
    except Exception as e:  # noqa: BLE001 — staging is best-effort; never 500 the editor
        logger.warning(f"Could not stage task scopes for signing: {e}")

    # One invocation that signs every unapproved block in this card. Offered
    # only when it actually saves work (>1 unsigned block): a card with three
    # escalating blocks otherwise makes the UI present three near-identical
    # sudo lines, which is the wall of text users skip rather than read.
    # Minted here rather than assembled in the frontend so the CLI's flag
    # vocabulary lives in one place — a client-built string rots silently.
    _unsigned = [b for b in blocks if not b["authorized"]]
    sign_all_command = (
        f"sudo ziya-approve --task {card_id} --project {project_id} --all"
        if len(_unsigned) > 1 else ""
    )

    return {
        "cardId": card_id,
        "preview": False,
        "anyUnapproved": any(not b["authorized"] for b in blocks),
        "anyNeedsSignature": any(b["needsSignature"] for b in blocks),
        "signAllCommand": sign_all_command,
        "blocks": blocks,
    }


@router.post("/scope-preview")
async def preview_card_scope(project_id: str, body: TaskCardCreate):
    """Escalation preview for an UNSAVED card spec.

    Exists because the by-id status endpoint cannot answer for a spec
    that was never persisted.  The AI-authored proposal block and its
    preview modal used a synthetic ``'draft'`` id, so they requested
    ``/task-cards/draft/scope-status``, took the 404, and silently
    rendered no warning — making the one moment the user decides whether
    to run a model-authored card the one moment its privilege escalation
    was invisible.

    Reports the same rows as ``scope-status`` but with
    ``authorized=False`` / ``needsSignature=True`` throughout: the spec's
    blocks have no persisted ids, so no approval could exist for them and
    no ``signCommand`` is mintable until the card is saved.  Read-only —
    persists nothing and stages nothing for the signer, so previewing a
    proposal the user never accepts leaves no trace.
    """
    project = ProjectStorage(get_ziya_home()).get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    deck_scope = getattr(project.settings, "taskScope", None)

    # The deck scope is included for the same reason the saved path
    # includes it: it is a real layer of the effective scope, so omitting
    # it here would under-report escalation that the run will actually
    # request.  A TaskCardCreate exposes ``root`` and ``scope`` exactly as
    # a TaskCard does, so the walk needs no adapter.
    blocks, _staged = _escalation_rows(body, deck_scope, check_approvals=False)
    return {
        "cardId": "",
        "preview": True,
        "anyUnapproved": any(not b["authorized"] for b in blocks),
        "anyNeedsSignature": any(b["needsSignature"] for b in blocks),
        # No persisted block ids exist, so no approval is mintable — same
        # reason each row's signCommand is "".
        "signAllCommand": "",
        "blocks": blocks,
    }


@router.post("", response_model=TaskCard, status_code=201)
async def create_task_card(project_id: str, body: TaskCardCreate):
    return _get_storage(project_id).create(body)


@router.put("/{card_id}", response_model=TaskCard)
async def update_task_card(project_id: str, card_id: str, body: TaskCardUpdate):
    card = _get_storage(project_id).update(card_id, body)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


@router.delete("/{card_id}", status_code=204)
async def delete_task_card(project_id: str, card_id: str):
    if not _get_storage(project_id).delete(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")


@router.post("/{card_id}/duplicate", response_model=TaskCard, status_code=201)
async def duplicate_task_card(
    project_id: str, card_id: str,
    as_template: bool = Query(False),
):
    card = _get_storage(project_id).duplicate(card_id, as_template=as_template)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


@router.get("/{card_id}/lessons")
async def get_card_lessons(
    project_id: str, card_id: str,
    limit: int = Query(200, ge=1, le=2000),
):
    """The card's self-improvement history: every judge verdict and
    every applied text revision, newest first.

    Read from the project's lesson ledger (app.utils.self_improve.
    LessonLedger) — the durable record; ``improve_revision`` WS events
    are transient and expire with the relay's replay buffer.  The card
    must exist (404 otherwise) so a deleted card's residual ledger
    entries don't render as a ghost history.
    """
    if not _get_storage(project_id).get(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")
    from ..utils.self_improve import LessonLedger
    records = LessonLedger(get_project_dir(project_id)).for_card(
        card_id, limit=limit)
    return {
        "card_id": card_id,
        "count": len(records),
        "edits_applied": sum(1 for r in records if r.get("applied")),
        # Newest first for display; the ledger stores oldest-first.
        "lessons": list(reversed(records)),
    }


class LessonRevertRequest(BaseModel):
    """Identifies the applied revision to revert by content hash.

    ``patch_hash`` (not a ledger index) because the ledger is capped
    and oldest-dropped — an index is not stable across appends, and a
    hash also cannot revert a DIFFERENT record than the one the user
    was shown.  ``block_id`` disambiguates the same text applied at
    two blocks.
    """
    patch_hash: str
    block_id: str


@router.post("/{card_id}/lessons/revert")
async def revert_card_lesson(
    project_id: str, card_id: str, body: LessonRevertRequest,
):
    """Write an applied revision's pre-image back onto the live card.

    The pre-image is a text patch like any other, so it flows through
    the same guarded path the improvement itself used
    (persist_patch_to_card): field whitelist, existing-id keying,
    structure fingerprint.  Privilege and signed approvals are
    untouched by construction.
    """
    if not _get_storage(project_id).get(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")
    from ..utils.self_improve import LessonLedger, revert_lesson_patch
    records = LessonLedger(get_project_dir(project_id)).for_card(
        card_id, limit=2000)
    # Newest matching record wins — the same patch_hash can only recur
    # across blocks (the oscillation guard blocks a same-block repeat).
    match = next(
        (r for r in reversed(records)
         if r.get("patch_hash") == body.patch_hash
         and r.get("block_id") == body.block_id and r.get("applied")),
        None,
    )
    if not match:
        raise HTTPException(
            status_code=404, detail="No applied revision with that hash")
    if not match.get("pre_image"):
        raise HTTPException(
            status_code=409, detail=(
                "Revision predates pre-image capture and cannot be "
                "auto-reverted; edit the card text directly."))
    reverted = revert_lesson_patch(project_id, card_id, match)
    return {"success": reverted, "card_id": card_id,
            "block_id": body.block_id}


def _seed_block_states(run_storage: TaskRunStorage, run_id: str, block: Block) -> None:
    """Pre-populate TaskRun.block_states so append_iteration_summary
    has a place to write.  Walks the tree depth-first."""
    if block.id:
        run_storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type, status="queued",
        ))
    for child in block.body or []:
        _seed_block_states(run_storage, run_id, child)

async def _launch_run_for_card(
    project_id: str,
    card_id: str,
    source_conversation_id=None,
    parameter_overrides=None,
    resume_root: Block = None,
    resume_from_block_id: str = None,
    resume_artifacts: dict = None,
    parent_run_id: str = None,
    root_run_id: str = None,
    attempt: int = 1,
    resume_kind: str = None,
    resumed_from_block_id: str = None,
    resume_from_iteration: int = None,
    resume_iteration_artifacts: dict = None,
    resume_iteration_summaries: list = None,
    resume_call_chain: list = None,
) -> TaskRun:
    """Shared helper: validates the card, creates a TaskRun, seeds
    block_states, and schedules the background executor task.
    Returns the run immediately.

    Used by the plain /launch endpoint and by the binding-creation
    endpoint, which needs the run_id before recording the binding.

    Resume mode (the three ``resume_*`` args, set together by the
    resume-from-block endpoint) creates a NEW run rather than reviving
    the old one, keeping the source run as an immutable record.  It
    differs from a normal launch in three ways:

    * ``resume_root`` is the source run's ``card_snapshot`` tree, not
      the live card — the snapshot carries the same block ids the source
      run's ``block_states`` are keyed by, and it is immune to card
      edits made since that run.
    * ``resume_artifacts`` maps block id → the source run's recorded
      Artifact.  Blocks ahead of the target replay these instead of
      executing, so the resumed blocks still see prior deck state via
      {{sibling("id")}} / {{previous_sibling}}.
    * ``resume_from_block_id`` is where real execution begins; see the
      gate in block_executor.execute_block.

    The five lineage args are recorded on the run so the GUI can state
    the relationship between attempts rather than showing an unexplained
    second tile.  ``resumed_from_block_id`` is the block the USER
    pointed at, which for a continue is deliberately NOT
    ``resume_from_block_id`` (that is its successor) — see
    app.utils.resume_targets.
    """
    storage = _get_storage(project_id)
    card = storage.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")

    # The tree this run actually executes.  Resume uses the source
    # run's snapshot so block ids line up with the artifacts being
    # replayed; a normal launch uses the live card.
    root_block = resume_root if resume_root is not None else card.root

    # ── Structural validation ───────────────────────────────────
    # Block sets extra="allow" and declares no validators, so an
    # authoring defect is accepted silently and surfaces only when the
    # executor reaches the block.  For a long fan-out that is hours in;
    # for an orchestrator whose Call blocks ARE the deck, a typo in the
    # last target costs every earlier phase before it is discovered.
    #
    # Errors block the launch, warnings do not — refusing to run on a
    # warning would be a behaviour change rather than a safety net.
    #
    # Deliberately raises rather than minting a held run: this executes
    # synchronously in the request (unlike the credentials preflight
    # below, which runs inside the background coroutine where no HTTP
    # response is left to fail), so a 422 reaches the caller directly.
    # A run record would also be actively wrong — record_run() bumps
    # run_count, so a launch that never executed would corrupt the
    # deck's "never run" vs "has history" distinction, and a held run
    # advertises resume affordances for progress that does not exist.
    # The refusal is persisted to its own append-only log instead.
    #
    # Resume paths are validated too: they execute a snapshot tree, and
    # a snapshot of a broken card is still broken.
    _skip_validation = os.environ.get(
        "ZIYA_SKIP_CARD_VALIDATION", "",
    ).strip().lower() in ("1", "true", "yes")
    if not _skip_validation:
        try:
            from ..context import get_project_root_or_none as _get_root
            from ..utils.task_card_validation import validate_card_tree
            _vres = validate_card_tree(
                root_block, project_id=project_id, project_root=_get_root(),
            )
        except Exception as e:  # noqa: BLE001
            # A validator fault must never be the reason a launch fails.
            logger.warning(f"📋 TASK_LAUNCH: validation skipped: {e}")
            _vres = None
        if _vres is not None:
            for _w in _vres.warnings:
                logger.info(f"📋 TASK_LAUNCH: warning [{_w.path}]: {_w.message}")
            if not _vres.ok:
                logger.warning(
                    f"📋 TASK_LAUNCH: refused - {len(_vres.errors)} "
                    f"structural error(s) in card {card_id[:8]}"
                )
                # Record the refusal.  These are the only trace of the
                # defect class that never becomes a run, so they are the
                # one class nobody pays for and therefore nobody
                # remembers.  Best-effort by construction: the sink must
                # not turn an actionable 422 into a 500.
                try:
                    from ..storage.task_card_refusals import (
                        RefusalLog, build_refusal_record,
                    )
                    RefusalLog(get_project_dir(project_id)).record(
                        build_refusal_record(
                            card_id=card_id,
                            card_name=card.name or "",
                            result=_vres,
                            is_resume=resume_root is not None,
                            project_id=project_id,
                        )
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"📋 TASK_LAUNCH: refusal not logged: {e}")
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": (
                            "Task card has structural errors and was not "
                            "launched."
                        ),
                        "errors": [
                            {"message": f.message, "block_id": f.block_id,
                             "path": f.path}
                            for f in _vres.errors
                        ],
                        "warnings": [
                            {"message": f.message, "block_id": f.block_id,
                             "path": f.path}
                            for f in _vres.warnings
                        ],
                    },
                )

    # Deck-level (project-wide) permissions baseline — the outermost
    # scope layer, merged additively with the card's own scope and
    # every ancestor block's scope (see app.models.task_card.merge_scopes
    # and app.agents.block_executor.ExecutionContext.effective_scope).
    project = ProjectStorage(get_ziya_home()).get(project_id)
    deck_scope = getattr(project.settings, "taskScope", None) if project else None

    run_storage = TaskRunStorage(get_project_dir(project_id))
    run = run_storage.create(TaskRunCreate(
        card_id=card_id,
        source_conversation_id=source_conversation_id,
        # Recorded on the run alongside card_snapshot so the run is
        # reproducible from its own record.  ExecutionContext.overrides
        # (seeded below) is in-memory only and outranks State blocks, so
        # a resume that could not read these back would silently fall
        # back to the card's authored baselines.
        parameter_overrides=dict(parameter_overrides or {}),
        parent_run_id=parent_run_id,
        root_run_id=root_run_id,
        attempt=attempt,
        resume_kind=resume_kind,
        resumed_from_block_id=resumed_from_block_id,
        # Mid-loop resume position, recorded on the run so the attempt is
        # reproducible from its own record and the UI can say which
        # iteration was resumed rather than only which block.
        resume_from_iteration=resume_from_iteration,
        resume_iteration_artifacts={
            int(k): v for k, v in (resume_iteration_artifacts or {}).items()
        },
    ))
    storage.record_run(card_id)
    _seed_block_states(run_storage, run.id, root_block)

    # Mid-loop resume: install the replayed prefix as this run's own
    # record.  Must come AFTER _seed_block_states (which replaces the
    # whole state object, wiping iteration_summaries) and BEFORE the
    # executor coroutine is scheduled below (both write the full run
    # file, so an interleaving would lose records).
    #
    # Without this the resumed run recorded only the iterations it
    # executed, so a resume at 3 of 5 rendered a two-dot strip counting
    # from 1 — indistinguishable from a fresh two-iteration run, and
    # reading as though the three banked iterations had been thrown away.
    # They had not; nothing said so.
    #
    # Also fires for a PARALLEL fan-out resume, which banks an index SET
    # rather than a prefix and therefore leaves ``resume_from_iteration``
    # unset — gating on that alone dropped the display record for exactly
    # the shape where the most work is preserved, so a 19-of-20 resume
    # rendered a one-dot strip.
    if resumed_from_block_id and (
        resume_from_iteration or resume_iteration_artifacts
    ):
        try:
            from ..models.task_run import IterationSummary
            prefix = [
                IterationSummary(**s) if isinstance(s, dict) else s
                for s in (resume_iteration_summaries or [])
            ]
            run_storage.seed_replayed_iterations(
                run.id, resumed_from_block_id, prefix,
                # A resume point inside a called card has no seeded state
                # yet: _seed_block_states above walks only ``root_block``,
                # and a Call names its callee rather than inlining it.
                # Safe specifically here — the resume endpoint located this
                # id in the card snapshot or a call snapshot before banking
                # a single artifact against it, so it is a block this run
                # will genuinely execute, not a stale or misspelled id.
                block_type="repeat",
                create_if_missing=True,
            )
            # Copy the carried artifacts into THIS run's iteration files.
            # The dot strip's open action fetches
            # /task-runs/{this_run}/iterations/{block}/{index}, so without
            # the copy every replayed dot is a visible circle that 404s on
            # click — worse than an absent dot, since it looks openable.
            # Also makes the run self-contained: the source may be deleted
            # while this one is still going.
            for idx, val in (resume_iteration_artifacts or {}).items():
                if val is None:
                    continue
                art = val if isinstance(val, Artifact) else Artifact(**val)
                run_storage.write_iteration_artifact(
                    run.id, resumed_from_block_id, int(idx), art,
                )
        except Exception as e:
            # Non-fatal: the prefix is a display and audit convenience.
            # Failing the launch over it would trade a cosmetic gap for
            # the lost work the whole resume path exists to recover.
            logger.warning(
                f"📋 TASK_LAUNCH: replayed-prefix seeding failed: {e}"
            )

    # Snapshot the card definition at launch so later edits to the card
    # cannot retroactively rewrite what this run is displayed to have
    # executed.  Uses the same block ids the run's block_states
    # reference, keeping the run map consistent after edits too.
    try:
        run_storage.set_card_snapshot(run.id, {
            "name": card.name,
            "description": card.description,
            # ``root_block``, not ``card.root``: a resumed run must
            # snapshot the tree it actually executes (the source run's
            # snapshot), or its own block_states would be keyed by ids
            # from a tree its snapshot does not describe — and a later
            # resume-of-a-resume would read a mismatched tree.
            "root": root_block.model_dump(),
        })
    except Exception as e:
        logger.warning(f"📋 TASK_LAUNCH: card_snapshot capture failed: {e}")

    from ..context import get_project_root_or_none, set_project_root
    project_root = get_project_root_or_none()

    # Capture an audit-trail snapshot of effective permissions before
    # the run starts.  Done once at launch so later edits to the card
    # don't rewrite history; this is what lets us reconstruct *what
    # the agent was actually allowed to do* after the fact.
    try:
        from ..utils.permissions_snapshot import build_permissions_snapshot
        snapshot = build_permissions_snapshot(
            root_block=root_block, project_root=project_root,
            deck_scope=deck_scope, card_scope=card.scope,
        )
        run_storage.set_permissions_snapshot(run.id, snapshot)
    except Exception as e:
        # Non-fatal — missing audit trail shouldn't block task execution.
        logger.warning(f"📋 TASK_LAUNCH: permissions_snapshot capture failed: {e}")

    async def _run(run_id: str, block, project_root):
        logger.info(f"🚀 TASK_RUN: _run coroutine entered for {run_id[:8]}")
        # ── Launch preflight ────────────────────────────────────────
        # Two of ten runs of one long campaign card died at 0.0 minutes
        # because they were relaunched straight into a dead endpoint,
        # producing a run record whose only content was the error.  A
        # cheap check first turns that into an explicit held run.
        #
        # Deliberately mints a HELD run rather than refusing the launch:
        # a refusal at the HTTP layer would leave no record the user
        # could see, resume, or reason about, whereas 'held' is exactly
        # "the work never started because the infrastructure was down".
        #
        # Bedrock-only, opt-out-able, and best-effort.  The STS
        # round-trip is NOT free — measured at ~10s cold — so it is
        # skipped for every non-Bedrock endpoint, and
        # ZIYA_SKIP_LAUNCH_PREFLIGHT=1 disables it for callers that know
        # no model call will occur: a stubbed executor under test, a card
        # doing only shell/file work, or an offline environment.  Without
        # that escape hatch the check holds runs that would have
        # succeeded — the one outcome worse than not checking — while
        # billing every launch ~10s for an answer it did not need.
        #
        # Any failure of the check ITSELF proceeds to launch: "cannot
        # verify" is not "invalid".
        try:
            _skip = os.environ.get(
                "ZIYA_SKIP_LAUNCH_PREFLIGHT", "",
            ).strip().lower() in ("1", "true", "yes")
            if _skip:
                _endpoint = ""   # sentinel: no endpoint check performed
                logger.debug(
                    "Launch preflight skipped (ZIYA_SKIP_LAUNCH_PREFLIGHT)"
                )
            else:
                from ..agents.models import ModelManager
                _endpoint = (ModelManager.get_state() or {}).get("endpoint", "bedrock")
            if _endpoint == "bedrock":
                from ..utils.aws_utils import check_aws_credentials
                _ok, _msg = check_aws_credentials(is_server_startup=False)
                if not _ok:
                    run_storage.mark_held(
                        run_id, reason="authentication_error",
                        block_id=getattr(block, "id", "") or "",
                        error=(
                            f"Launch preflight failed — the run did not start. "
                            f"{_msg or 'AWS credentials are not valid.'}"
                        ),
                    )
                    await _relay.safe_push(run_id, {
                        "type": "run_completed", "run_id": run_id,
                        "status": "held", "at": time.time(),
                        "error": _msg or "credentials invalid",
                    })
                    logger.warning(
                        f"⏸️ Task run held before start (preflight): {run_id[:8]}"
                    )
                    return
        except Exception as e:  # noqa: BLE001 — never block a launch on the check
            logger.debug("Launch preflight skipped: %s", e)
        # Defense in depth: re-set the request-scoped ContextVar inside
        # the spawned task.  asyncio.create_task copies the current
        # Context, so this is normally redundant — but if project_root
        # was passed via a path other than the X-Project-Root header
        # (or if the var was cleared), tool calls fired from inside
        # the task would otherwise fall through to ``os.getcwd()``,
        # which is wherever the server happened to be launched from.
        if project_root:
            set_project_root(project_root)
        # Lifecycle event emitter — kept local so the run_id/project_id
        # are captured in closure and callers don't have to thread them.
        async def _emit_run(status: str, **extra):
            await _relay.safe_push(run_id, {
                "type": "run_completed" if status != "started" else "run_started",
                "run_id": run_id,
                "status": status,
                "at": time.time(),
                **extra,
            })
        # Mark this run as actively executing in this process so the
        # cancel endpoint can distinguish "live executor — soft-cancel"
        # from "zombie from a prior server lifetime — force-cancel".
        # The startup reconciler handles zombies left behind by a hard
        # crash that bypassed the finally block below.
        run_storage.mark_active(run_id)
        try:
            logger.info(f"🚀 TASK_RUN: {run_id[:8]} → marking running")
            run_storage.update_status(run_id, "running")
            await _emit_run("started")
            ctx = ExecutionContext(
                run_id=run_id,
                project_root=project_root,
                project_id=project_id,
                storage=run_storage,
                overrides=dict(parameter_overrides or {}),
                deck_scope=deck_scope,
                card_scope=card.scope,
                # Outermost hop of the hold breadcrumb.  Without it, a hold
                # inside a called card reports a path that starts at the
                # CALLEE and omits the card the user actually launched.
                root_card_label=(card.name or card.id),
                # Resume state.  ``resume_skipping`` starts True only
                # when a target was given, so a normal launch is
                # untouched: the gate in execute_block is inert unless
                # resume_skipping is set.
                resume_from_block_id=resume_from_block_id,
                resume_skipping=bool(resume_from_block_id),
                resume_artifacts=dict(resume_artifacts or {}),
                # Mid-loop resume.  Coerced to Artifact here rather than
                # at the endpoint so a record read straight off disk (a
                # plain dict) and one passed in-process behave the same.
                resume_from_iteration=resume_from_iteration,
                resume_iteration_artifacts={
                    int(k): (v if isinstance(v, Artifact) else Artifact(**v))
                    for k, v in (resume_iteration_artifacts or {}).items()
                    if v is not None
                },
                # Descend-through-Call hint.  Without it the gate replays a
                # Call whole (empty body -> _subtree_contains False), so a
                # target inside a called card is unreachable and the run
                # reports success having executed nothing — which is worse
                # than the 404 that behaviour replaced.
                resume_call_chain=list(resume_call_chain or []),
            )
            logger.info(f"🚀 TASK_RUN: {run_id[:8]} → execute_block start (type={block.block_type})")

            def _terminal(base: str) -> str:
                """Reclassify a terminal status against what completed.

                Reads block_states back from disk rather than tracking
                progress here: the executor already persists every
                block's outcome, and re-deriving keeps this to one
                lookup instead of a parallel accounting scheme that
                could drift from the durable record.
                """
                from ..utils.run_outcome import classify_terminal_status
                fresh = run_storage.get(run_id)
                return classify_terminal_status(
                    base, fresh.block_states if fresh else None,
                )

            artifact = await execute_block(block, ctx)
            logger.info(
                f"🚀 TASK_RUN: {run_id[:8]} → execute_block returned "
                f"(summary_len={len(artifact.summary)}, failed={artifact.failed})"
            )
            run_storage.set_artifact(run_id, artifact)
            final_status = _terminal("failed" if artifact.failed else "done")
            run_storage.update_status(run_id, final_status)
            await _emit_run(final_status)
            logger.info(f"✅ Task run complete: {run_id[:8]}")
        except BlockExecutionCancelled:
            # A user-stopped run that got partway carries the same
            # workspace hazard as a crash-partway.
            _st = _terminal("cancelled")
            run_storage.update_status(run_id, _st)
            await _emit_run(_st)
            logger.info(f"🛑 Task run cancelled: {run_id[:8]}")
        except TaskExecutorError as e:
            # An infrastructure fault is not a verdict on the work: the
            # card never reached a decision, so recording it as
            # "failed" both misdescribes it and throws away the
            # position the run had reached.  Detected by attribute
            # rather than by importing TaskInfraError, keeping this
            # module's import surface unchanged.
            _kind = getattr(e, "infra_kind", "")
            if _kind:
                _blk = getattr(e, "block_id", "") or ""
                # The aggregate, not just the exception that happened to
                # survive gather().  ctx accumulated every fault in
                # memory; this is the single write of that record.
                _faults = None
                _gate = None
                try:
                    _faults = ctx.infra_summary()
                    _gate = ctx.infra_gated_reason
                except Exception:  # noqa: BLE001 — surfacing must not mask the hold
                    pass
                run_storage.mark_held(
                    run_id, reason=_kind, block_id=_blk, error=str(e),
                    faults=_faults, gate_reason=_gate,
                )
                await _emit_run("held", error=str(e))
                _n = (_faults or {}).get("fault_count", 1)
                logger.warning(
                    f"⏸️ Task run held ({_kind}): {run_id[:8]} at block "
                    f"{_blk[:14] or '?'} — {_n} fault(s)"
                    + (f", gate: {_gate}" if _gate else "") + f" — {e}"
                )
            else:
                _st = _terminal("failed")
                run_storage.update_status(run_id, _st, error=str(e))
                await _emit_run(_st, error=str(e))
                logger.warning(f"❌ Task run failed: {run_id[:8]}: {e}")
        except Exception as e:  # Broad: background task must not bubble
            _st = _terminal("failed")
            run_storage.update_status(run_id, _st, error=str(e))
            await _emit_run(_st, error=str(e))
            logger.error(f"❌ Task run crashed: {run_id[:8]}: {e}", exc_info=True)
        finally:
            # Always drop from the active-runs set, even on error.
            run_storage.mark_inactive(run_id)

    asyncio.create_task(_run(run.id, root_block, project_root))
    logger.info(f"🚀 Task card launched: {card.name} → run {run.id[:8]} (task scheduled)")
    return run


@router.post("/{card_id}/launch")
async def launch_task_card(
    project_id: str, card_id: str, body: TaskCardRun,
) -> TaskRun:
    """Launch a task card — create a TaskRun and start executing in
    the background.  Returns the run immediately; clients poll the
    task-runs endpoints for status and the final artifact.
    """
    return await _launch_run_for_card(
        project_id=project_id, card_id=card_id,
        source_conversation_id=body.source_conversation_id,
        parameter_overrides=body.parameter_overrides,
    )


@router.get("/{card_id}/schedule-state")
async def get_schedule_state(project_id: str, card_id: str) -> dict:
    """Return the scheduler's per-card fire-history record.

    Empty dict if the card has no schedule block, or has one but has
    never fired yet.  When populated:

        {
          "block_id":      "<schedule block id>",
          "next_fire_at":  <epoch ms or null>,
          "last_fire_at":  <epoch ms or null>,
          "fires_so_far":  <int>,
          "run_ids":       ["<run_id>", ...]   # most-recent first, capped
        }

    Drives the "next fire in 2h 14m / fired 47 times so far" surface
    in the schedule editor.  Read-only; the scheduler owns writes via
    its internal `_write_state` path.
    """
    storage = _get_storage(project_id)
    if not storage.get(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")
    # Lazy-import the scheduler so this endpoint stays cheap when the
    # caller is just listing cards (and to avoid pulling croniter at
    # module load).
    from ..agents.task_scheduler import _read_state
    state = _read_state(project_id)
    return state.get(card_id) or {}
