"""
Task card storage — file-per-card under a project directory.

Follows the same pattern as SkillStorage: JSON files managed
through BaseStorage, which provides file locking and optional
ALE encryption.
"""

import re
import time
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseStorage, contained_path
from ..models.task_card import Block, TaskCard, TaskCardCreate, TaskCardUpdate

logger = logging.getLogger(__name__)

# A {{sibling("...")}} reference.  Must recognise exactly what
# task_card_validation._SIBLING_REF_RE does (tests assert it), so the
# validator never accepts a form the remint rewrite would skip.  Group 1 is
# the opening quote and group 2 the id, so a rewrite replaces only the id
# and keeps the author's quoting.
_SIBLING_REF_RE = re.compile(r"sibling\(\s*(['\"])([^'\"]+)['\"]\s*\)")


# ── Portable (export/import) format ────────────────────────────────

PORTABLE_FORMAT = "ziya-task-cards"
PORTABLE_FORMAT_VERSION = 1

# Fields that identify a card in THIS deck or record its history here.
# None of them means anything in another project: ``id`` is minted on
# create, ``version`` counts this deck's edits, ``run_count`` /
# ``last_run_at`` are this deck's runs, ``draft`` / ``source`` describe how
# the card got here.  Block ids inside ``root`` ARE exported: ``sibling()``
# references point at them, and import remints them (see ``import_cards``).
NON_PORTABLE_CARD_FIELDS = frozenset({
    "id", "draft", "source", "created_at", "updated_at",
    "last_run_at", "run_count", "version",
})

# Export-only hints written beside the definition and dropped on import.
# ``needs`` is derived from the tree (app.utils.task_card_portable); it is
# documentation for the reader, not part of what the card IS.
PORTABLE_HINT_FIELDS = frozenset({"needs"})


# Shipped inside every export so a reader with no Ziya knowledge — a person,
# or another tasking agent handed the file — can interpret the parts that
# are not self-evident from the tree.  Deliberately short: the authored
# ``instructions`` are the portable payload; this only covers what the
# control-flow vocabulary and ``scope`` leave implicit.
PORTABLE_ABOUT = [
    "Each card is a tree of blocks. 'body' holds a block's children, run "
    "top-to-bottom unless the parent's block_type is 'parallel'.",
    "block_type: task = one model turn following 'instructions'; group = "
    "run children once; repeat = loop (count / until substring / for_each "
    "over a JSON array, binding {{item}}); until = loop until a model judges "
    "'until_condition' true; parallel = run children concurrently; state = "
    "givens for later blocks ({{var.NAME}} and 'state_context' prose); "
    "ask = pause for a human answer; call = run another card by name; "
    "schedule = recurring trigger for its body.",
    "{{sibling(\"ID\")}} in instructions refers to another block's 'id' in "
    "this card and reads that block's result.",
    "'scope' is a REQUEST for privileges (shell commands, writable paths, "
    "tools, skills, model choice), additive from card to leaf. It grants "
    "nothing by itself: the importing installation must approve it.",
    "Each card's 'needs' lists what the tree depends on: tools, skills, MCP "
    "servers, output-contract schemas ('project:NAME', bundled under the "
    "top-level 'schemas' and restored on import), and absolute 'cwd' paths "
    "from the exporting machine. 'requires' is the author's declaration of "
    "the same; when the author wrote none it is filled from 'needs' "
    "(non-strict) so the importing installation can warn before a run.",
    "Fields at their default value are omitted; a missing 'on_failure' "
    "means continue past a failed child, a missing 'required' means true.",
    "Docs: https://github.com/ziya-ai/ziya/blob/main/Docs/FeatureInventory.md#2a-task-cards",
]


def card_to_portable(
    card: TaskCard, project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """The definition of ``card`` with every deck-local field removed.

    Defaults and nulls are dropped too.  ``Block`` carries every block
    type's fields, so a plain dump of a 4-block card was ~8 KB with ~190
    ``null`` entries, and a *task* block showed ``schedule_enabled: true``
    and ``repeat_propagate: "last"`` — fields that mean nothing on it and
    invite a reader to think otherwise.  Import is unaffected: pydantic
    refills every default.  ``name`` is kept even when empty because
    ``TaskCardCreate`` requires it.

    Adds the ``needs`` hint and, for a card without ``requires``, a derived
    non-strict one — see app.utils.task_card_portable.  Both are
    best-effort: a derivation fault yields a card without them.
    """
    portable = {
        k: v for k, v in card.model_dump(
            exclude_none=True, exclude_defaults=True,
        ).items()
        if k not in NON_PORTABLE_CARD_FIELDS
    }
    portable.setdefault("name", card.name)
    try:
        from ..utils.task_card_portable import card_needs, derived_requires
        needs = card_needs(card, project_root)
        if needs:
            portable["needs"] = needs
            req = derived_requires(card, needs)
            if req:
                portable["requires"] = req
    except Exception as e:  # noqa: BLE001 — hints never fail an export
        logger.warning(f"export hints skipped for card {card.id}: {e}")
    return portable


def build_export(
    cards: List[TaskCard], project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap ``cards`` in the portable envelope ``import_cards`` accepts.

    With a ``project_root`` the ``project:`` schemas the cards' contracts
    reference are bundled under ``schemas`` (transitively over ``$ref``).
    Without one they cannot be read and each card's ``needs`` lists them
    as unresolved instead.
    """
    env: Dict[str, Any] = {
        "format": PORTABLE_FORMAT,
        "format_version": PORTABLE_FORMAT_VERSION,
        "exported_at": int(time.time() * 1000),
        "about": list(PORTABLE_ABOUT),
        "cards": [card_to_portable(c, project_root) for c in cards],
    }
    try:
        from ..utils.task_card_portable import bundle_schemas, collect_schema_refs
        refs: List[str] = []
        for c in cards:
            for r in collect_schema_refs(c):
                if r not in refs:
                    refs.append(r)
        bundled, _unresolved = bundle_schemas(refs, project_root)
        if bundled:
            env["schemas"] = bundled
    except Exception as e:  # noqa: BLE001
        logger.warning(f"export schema bundle skipped: {e}")
    return env


def portable_cards_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """Accept the envelope, a bare card dict, or a list of card dicts.

    Lenient on the outer shape so a card pasted out of a deck file, or a
    hand-written one, imports as readily as an export.  Strict on the
    envelope's ``format``: a file claiming to be some other format is
    refused rather than mined for anything card-shaped.  Raises
    ``ValueError`` with an operator-readable reason.
    """
    if isinstance(payload, dict) and "cards" in payload:
        fmt = payload.get("format")
        if fmt is not None and fmt != PORTABLE_FORMAT:
            raise ValueError(
                f"not a task card export (format={fmt!r}, "
                f"expected {PORTABLE_FORMAT!r})")
        fv = payload.get("format_version", PORTABLE_FORMAT_VERSION)
        if not isinstance(fv, int) or fv > PORTABLE_FORMAT_VERSION:
            raise ValueError(
                f"task card export format_version {fv!r} is newer than this "
                f"version of Ziya understands ({PORTABLE_FORMAT_VERSION})")
        cards = payload["cards"]
    elif isinstance(payload, dict) and "root" in payload:
        cards = [payload]
    elif isinstance(payload, list):
        cards = payload
    else:
        raise ValueError(
            "expected a task card export ({\"format\": \"ziya-task-cards\", "
            "\"cards\": [...]}), a single card object, or a list of cards")
    if not isinstance(cards, list):
        raise ValueError("\"cards\" must be a list")
    if not cards:
        raise ValueError("the export contains no cards")
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(cards):
        if not isinstance(c, dict) or "root" not in c:
            raise ValueError(f"card #{i + 1} is not a card object (no \"root\")")
        out.append(c)
    return out


def _assign_block_ids(
    block_dict: dict, prefix: str = "b", force: bool = False,
    remap: Optional[Dict[str, str]] = None,
) -> None:
    """Walk a block tree (as a plain dict) and assign IDs where missing.

    ``force=True`` regenerates EVERY id, including ones already set.
    Needed when cloning a card: the source tree already has ids (none
    are missing), so the default fill-only behavior would leave a
    duplicate with byte-identical block ids to its original — a
    collision in ``TaskRunBlockState`` (both cards' runs write into the
    same keyed state) and, more seriously, in the scope-approval
    ledger, which keys an approval by block id alone: an approval
    signed for one card's block would silently authorize the other
    card's unchanged block too.

    ``remap``, when given, collects ``old id -> new id`` for every id a
    forced remint replaced, so ``_rewrite_sibling_refs`` can carry the
    by-id references with it.  Fill-only assignment records nothing:
    filling an empty id breaks no reference.
    """
    if not isinstance(block_dict, dict):
        return
    if force or not block_dict.get("id"):
        old = block_dict.get("id")
        new = f"{prefix}-{uuid.uuid4().hex[:8]}"
        block_dict["id"] = new
        if remap is not None and force and old:
            remap[old] = new
    for child in block_dict.get("body", []) or []:
        _assign_block_ids(child, prefix, force=force, remap=remap)


def _rewrite_sibling_refs(node: Any, remap: Dict[str, str]) -> Any:
    """Rewrite every ``sibling("old")`` in every string field of ``node``.

    ``sibling()`` resolves by block id, so a remint without this leaves
    each reference pointing at a block that exists only in the ORIGINAL
    card: it renders empty and a for_each over it runs zero iterations.
    Ids not in ``remap`` (a calling card's block) are left verbatim.
    Mutates dicts and lists in place; returns the (possibly new) value so
    a bare string can be rewritten too.
    """
    if isinstance(node, str):
        def _sub(m: "re.Match[str]") -> str:
            new = remap.get(m.group(2))
            if new is None:
                return m.group(0)
            s, a, b = m.start(), m.start(2), m.end(2)
            whole = m.group(0)
            return whole[:a - s] + new + whole[b - s:]
        return _SIBLING_REF_RE.sub(_sub, node)
    if isinstance(node, dict):
        for k, v in node.items():
            node[k] = _rewrite_sibling_refs(v, remap)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _rewrite_sibling_refs(v, remap)
    return node


class TaskCardStorage(BaseStorage[TaskCard]):
    """CRUD storage for task cards scoped to a project."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.cards_dir = project_dir / "task_cards"
        super().__init__(self.cards_dir)

    def _card_file(self, card_id: str) -> Path:
        return contained_path(self.cards_dir, f"{card_id}.json")

    def get(self, card_id: str) -> Optional[TaskCard]:
        data = self._read_json(self._card_file(card_id))
        if data:
            return TaskCard(**data)
        return None

    def list(self, templates_only: bool = False,
             include_drafts: bool = False) -> List[TaskCard]:
        """List cards, newest first.

        Drafts are EXCLUDED unless asked for.  A draft was persisted only so
        its blocks have ids a signed approval can key on (the proposal
        panel's Sign path); surfacing it here would defeat the point of
        signing without saving to the deck.
        """
        cards: List[TaskCard] = []
        if self.cards_dir.exists():
            for card_file in self.cards_dir.glob("*.json"):
                data = self._read_json(card_file)
                if data:
                    try:
                        card = TaskCard(**data)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Skipping corrupt task card {card_file}: {e}")
                        continue
                    if templates_only and not card.is_template:
                        continue
                    if not include_drafts and getattr(card, "draft", False):
                        # getattr, not attribute access: cards written before
                        # the field existed have no key, and are not drafts.
                        continue
                    cards.append(card)
        return sorted(cards, key=lambda c: c.updated_at, reverse=True)

    def create(
        self, data: TaskCardCreate, source: str = "custom",
        force_new_ids: bool = False,
    ) -> TaskCard:
        """Create a new card.

        ``force_new_ids=True`` regenerates every block id rather than
        only filling missing ones — see ``_assign_block_ids``.  Used by
        ``duplicate()``, whose incoming tree already carries the
        source card's ids.

        ``data.draft`` stores the card UNLISTED: it is persisted (so its
        blocks get ids an approval can key on) but hidden from every deck
        listing until an explicit save promotes it.
        """
        if data.draft:
            # Bounded, self-limiting GC.  Nothing in the UI deletes a draft
            # and none are visible in the deck, so the one thing that
            # creates them is what cleans them up.  Best-effort — a prune
            # failure must never fail the create.
            try:
                self.prune_stale_drafts()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Stale-draft prune failed: {e}")
        card_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        root_dict = data.root.model_dump()
        remap: Dict[str, str] = {}
        _assign_block_ids(root_dict, force=force_new_ids, remap=remap)
        if remap:
            _rewrite_sibling_refs(root_dict, remap)
        card = TaskCard(
            id=card_id,
            name=data.name,
            description=data.description,
            root=root_dict,
            # Was omitted, so a card-level permissions baseline was silently
            # dropped on save (and on every duplicate(), which forwards it).
            # scope-status then graded a scope the run would not request.
            scope=data.scope,
            requires=data.requires,
            tags=data.tags,
            is_template=data.is_template,
            draft=data.draft,
            source=source,
            created_at=now,
            updated_at=now,
        )
        self._write_json(self._card_file(card_id), card.model_dump())
        return card

    def prune_stale_drafts(
        self, max_age_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> int:
        """Delete never-run drafts older than ``max_age_ms``; count removed.

        A draft that HAS run is kept regardless of age: its run records
        reference the card by id, and deleting it would leave that history
        unresolvable.

        A draft that is BOUND to a conversation is kept too.  Cards staged
        by ``task_card_stage`` are conversation-only drafts whose tile may
        sit unlaunched for longer than the window; pruning one would leave
        the tile pointing at nothing.  Reachability, not age, is the test.
        """
        cutoff = int(time.time() * 1000) - max_age_ms
        bound = self._bound_card_ids()
        if bound is None:
            return 0
        removed = 0
        for card in self.list(include_drafts=True):
            if (getattr(card, "draft", False) and card.run_count == 0
                    and card.updated_at < cutoff and card.id not in bound):
                if self.delete(card.id):
                    removed += 1
        return removed

    def _bound_card_ids(self) -> Optional[set]:
        """Card ids referenced by any conversation's task bindings, or
        ``None`` when the scan failed.

        None (not an empty set) on failure: unknown reachability must make
        NOTHING prunable, since an empty set would make every draft
        eligible — the fail-open direction that deletes a live tile's card.
        """
        try:
            from .task_bindings import TaskBindingStorage
            return TaskBindingStorage(self.project_dir).all_bound_card_ids()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Task binding scan failed; skipping draft prune: {e}")
            return None

    def update(self, card_id: str, data: TaskCardUpdate) -> Optional[TaskCard]:
        card = self.get(card_id)
        if not card:
            return None
        # Capture the pre-edit structure + scope so a behaviour-changing
        # edit (block tree or permissions) is told apart from a
        # metadata-only one (name/description/tags/is_template).  Only the
        # former bumps ``version`` and thereby invalidates any prior
        # signing; a rename must not silently re-trigger a re-sign.
        def _dump(v):
            # ``update`` stores scope as a raw dict (it is NOT re-coerced
            # to a model the way ``root`` is below), so the compare must
            # tolerate a pydantic model or a plain dict on either side.
            return v.model_dump() if hasattr(v, "model_dump") else v
        old_root, old_scope = _dump(card.root), _dump(card.scope)
        update_dict = data.model_dump(exclude_unset=True)
        if "root" in update_dict and update_dict["root"]:
            _assign_block_ids(update_dict["root"])
            update_dict["root"] = Block(**update_dict["root"])
        for key, value in update_dict.items():
            # CWE-20 defense-in-depth: only ever set fields the update
            # request model (TaskCardUpdate) itself declares, regardless of
            # what extra-fields policy either model carries. Guards against
            # a future edit to either model (e.g. adding extra="allow")
            # silently becoming an arbitrary-attribute-write primitive here.
            if key in type(data).model_fields:
                setattr(card, key, value)
        # A monotonic bump on a real definition change; an identical
        # re-save (same tree, same scope) leaves the version — and any
        # signature keyed to it — untouched.
        if _dump(card.root) != old_root or _dump(card.scope) != old_scope:
            card.version = (card.version or 1) + 1
        card.updated_at = int(time.time() * 1000)
        self._write_json(self._card_file(card_id), card.model_dump())
        return card

    def delete(self, card_id: str) -> bool:
        card_file = self._card_file(card_id)
        if not card_file.exists():
            return False
        card_file.unlink()
        return True

    def duplicate(self, card_id: str, as_template: bool = False) -> Optional[TaskCard]:
        """Clone a card; optionally flip template flag.

        The clone gets a FRESH id for every block in the tree, not just
        the card itself — see ``_assign_block_ids`` for why reusing the
        source's block ids is unsafe (state-file collision, and a
        scope approval leaking across cards).
        """
        card = self.get(card_id)
        if not card:
            return None
        return self.create(TaskCardCreate(
            name=f"{card.name} (copy)",
            description=card.description,
            root=card.root,
            scope=card.scope,
            tags=card.tags,
            is_template=as_template,
        ), force_new_ids=True)

    def import_cards(
        self, payload: Any, project_root: Optional[str] = None,
    ) -> Tuple[List[TaskCard], List[str]]:
        """Create one card per entry of a portable payload.

        Returns ``(created, warnings)``.  Every entry is validated BEFORE
        the first write, so a bad fifth card leaves no partial import
        behind; a validation failure raises ``ValueError`` naming the
        entry.

        Every block id is reminted (``force_new_ids``), for the same
        reason ``duplicate()`` remints: approvals in the scope ledger key
        on block id alone, and a block state file keys on it too.  An
        imported card's escalation is therefore unsigned by construction,
        which is the right property — a signature made on another machine
        must not authorize anything here.  ``sibling()`` references are
        carried across the remint.

        Warnings, none of which block the import:
          - a name already in this deck (case-insensitive) or repeated
            within the payload: a ``call`` block addressing the card by
            name resolves the FIRST match, so a collision is ambiguous;
          - launch-validator findings on the created card (a ``call``
            whose target card lives only in the source deck, a stale
            ``sibling()`` id, ...), so the author learns before launch.
          - a bundled schema that already exists under ``project_root``
            with different content (kept, not overwritten).

        ``project_root`` is where the envelope's ``schemas`` are restored
        (``<root>/.ziya/schemas/``) and lets the validator resolve the
        cards' ``project:`` references.  Without it schemas are reported
        rather than written.
        """
        entries = portable_cards_from_payload(payload)
        creates: List[TaskCardCreate] = []
        for i, entry in enumerate(entries):
            clean = {k: v for k, v in entry.items()
                     if k not in NON_PORTABLE_CARD_FIELDS | PORTABLE_HINT_FIELDS}
            try:
                creates.append(TaskCardCreate(**clean))
            except (ValueError, TypeError) as e:
                label = entry.get("name") or f"#{i + 1}"
                raise ValueError(f"card {label!r} is not valid: {e}") from e

        warnings: List[str] = []
        existing = {c.name.lower() for c in self.list(include_drafts=True)}
        seen_in_payload: set = set()
        for req in creates:
            key = req.name.lower()
            if key in existing:
                warnings.append(
                    f"a card named {req.name!r} already exists in this deck; "
                    f"a call block addressing it by name will resolve the "
                    f"first match")
            elif key in seen_in_payload:
                warnings.append(
                    f"the export contains more than one card named {req.name!r}")
            seen_in_payload.add(key)

        # Schemas first: the validator below resolves ``project:`` refs
        # against the root, so a card whose schema arrived in this very
        # envelope must not be reported as referencing a missing one.
        bundled = payload.get("schemas") if isinstance(payload, dict) else None
        if bundled:
            try:
                from ..utils.task_card_portable import restore_schemas
                _restored, schema_warnings = restore_schemas(bundled, project_root)
                warnings.extend(schema_warnings)
            except Exception as e:  # noqa: BLE001 — advisory only
                warnings.append(f"bundled schemas were not restored: {e}")

        created: List[TaskCard] = []
        for req in creates:
            card = self.create(req, source="imported", force_new_ids=True)
            created.append(card)
            try:
                from ..utils.task_card_validation import validate_card_tree
                res = validate_card_tree(card.root, project_root=project_root)
                summary = res.summary()
                if summary:
                    warnings.append(f"{card.name!r}: {summary}")
            except Exception as e:  # noqa: BLE001 — advisory only
                logger.warning(f"post-import validation failed for {card.id}: {e}")
        return created, warnings

    def record_run(self, card_id: str) -> Optional[TaskCard]:
        """Bump run_count and last_run_at for a card."""
        card = self.get(card_id)
        if not card:
            return None
        card.last_run_at = int(time.time() * 1000)
        card.run_count += 1
        # Deliberately NOT stamped: ``updated_at`` means the card itself
        # changed, and the editor shows it as "edited N ago".  A launch
        # is recorded by ``last_run_at`` / ``run_count``; letting it move
        # ``updated_at`` made every run read as an edit.
        # (``version`` was never bumped here; this aligns the timestamp.)
        self._write_json(self._card_file(card_id), card.model_dump())
        return card
