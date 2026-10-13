"""
Environment hints for a task card's portable (export) form.

A card exports *faithfully* but is rarely *portable* on its own: a real
card is a thin orchestration layer over a project-local toolkit — MCP
tools, ``.ziya/schemas/`` output-contract schemas, an absolute ``cwd``.
The tree carries the references; this module derives what they point at
so the export can SAY what the card needs, and the import can restore the
one kind of dependency that is pure data (schemas) and check the rest
against the live environment.

Three products, all best-effort (a derivation failure degrades the hint,
never the export):

* ``card_needs(card, project_root)`` — ``{tools, skills, mcp_servers,
  schemas, cwd, unresolved_schemas}``.  Written to the portable card under
  ``needs`` and stripped on import; a reader (person or another tasking
  agent) sees the dependencies without walking 29 blocks.
* ``derived_requires(card, needs)`` — a non-strict ``requires`` block for a
  card that declares none, so the importing installation's launch
  preflight (app.utils.task_card_preflight) warns about missing tools and
  servers without the author having written anything.
* ``bundle_schemas`` / ``restore_schemas`` — the ``project:`` schemas a
  card's contracts reference, transitively closed over ``$ref``, carried
  in the envelope and written into the target project's
  ``.ziya/schemas/`` when absent.  A local schema of the same name with
  different content is never overwritten: the card validates against the
  local definition and the import says so.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_SCHEMA_PREFIX = "project:"

# Contract-bearing fields and the block types they are meaningful on.  Kept
# local rather than imported from task_card_validation so this module does
# not drag the validator's imports into the storage layer.
_CONTRACT_FIELDS = (
    "output_contract", "gather_contract",
    "output_contract_defaults", "gather_contract_defaults",
)


def _walk(block: Any) -> Iterator[Any]:
    yield block
    for child in (getattr(block, "body", None) or []):
        yield from _walk(child)


def _entry_schema(entry: Any) -> Any:
    """The ``schema`` of a contract entry — a model (``schema_``) or a dict."""
    if isinstance(entry, dict):
        return entry.get("schema")
    return getattr(entry, "schema_", None)


def collect_schema_refs(card: Any) -> List[str]:
    """Every ``project:`` schema reference in any contract of ``card``.

    Order-stable, de-duplicated.  Inline schema objects and shipped
    ``ziya:`` schemas are not dependencies: the first travel inside the
    tree, the second ship with every Ziya.
    """
    out: List[str] = []
    for block in _walk(card.root):
        for field in _CONTRACT_FIELDS:
            for entry in (getattr(block, field, None) or []):
                ref = _entry_schema(entry)
                if isinstance(ref, str):
                    ref = ref.strip()
                    if ref.startswith(PROJECT_SCHEMA_PREFIX) and ref not in out:
                        out.append(ref)
    return out


def tool_server_map() -> Dict[str, str]:
    """``{tool_name: mcp_server_name}`` for every loaded MCP tool.

    Empty when the tool layer is unavailable (tests, CLI without servers).
    Builtin tools carry no server and are absent from the map.
    """
    try:
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        out: Dict[str, str] = {}
        for t in create_secure_mcp_tools():
            name = getattr(t, "name", "") or ""
            server = (getattr(t, "metadata", None) or {}).get("server_name")
            if name and server:
                out[name] = server
        return out
    except Exception as e:  # noqa: BLE001 — a hint, never a failure
        logger.debug(f"portable: tool->server map unavailable: {e}")
        return {}


def card_needs(
    card: Any, project_root: Optional[str] = None,
    *, servers_by_tool: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Derive what ``card`` depends on from its tree.

    Only non-empty keys are returned, so a self-contained card produces
    ``{}`` and no ``needs`` entry at all.  ``servers_by_tool`` is injectable
    for tests; by default the live tool layer is consulted.
    """
    from app.utils.task_card_preflight import collect_requirements

    wanted = collect_requirements(card)
    tools = sorted(wanted["tools"])
    skills = sorted(wanted["skills"])
    servers = set(wanted["mcp_servers"])
    if tools:
        by_tool = servers_by_tool if servers_by_tool is not None else tool_server_map()
        for t in tools:
            s = by_tool.get(t)
            if s:
                servers.add(s)

    cwds: List[str] = []
    for block in _walk(card.root):
        sc = getattr(block, "scope", None)
        cwd = getattr(sc, "cwd", None) if sc is not None else None
        if cwd and os.path.isabs(cwd) and cwd not in cwds:
            cwds.append(cwd)
    card_scope = getattr(card, "scope", None)
    card_cwd = getattr(card_scope, "cwd", None) if card_scope is not None else None
    if card_cwd and os.path.isabs(card_cwd) and card_cwd not in cwds:
        cwds.insert(0, card_cwd)

    refs = collect_schema_refs(card)
    unresolved: List[str] = []
    if refs:
        _, unresolved = bundle_schemas(refs, project_root)

    needs: Dict[str, Any] = {}
    if tools:
        needs["tools"] = tools
    if skills:
        needs["skills"] = skills
    if servers:
        needs["mcp_servers"] = sorted(servers)
    if refs:
        needs["schemas"] = refs
    if unresolved:
        needs["unresolved_schemas"] = unresolved
    if cwds:
        needs["cwd"] = cwds
    return needs


def derived_requires(card: Any, needs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A ``requires`` block for a card that has none, from ``needs``.

    Non-strict by construction: the author never asked for a refusal, so
    the import side warns rather than refuses.  ``None`` when the card
    already declares ``requires`` (the author's word stands) or when there
    is nothing to require.  Keys with nothing in them are omitted, matching
    the default-stripped shape of the rest of the portable card.
    """
    if getattr(card, "requires", None) is not None:
        return None
    req: Dict[str, Any] = {}
    for key in ("skills", "tools", "mcp_servers"):
        if needs.get(key):
            req[key] = list(needs[key])
    return req or None


def bundle_schemas(
    refs: Iterable[str], project_root: Optional[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """``({"project:<name>": <schema>, ...}, unresolved_refs)``.

    Transitively closed over ``$ref`` via ``SchemaSet.resolve``; only
    ``project:`` documents are bundled (``ziya:`` ones ship with Ziya).
    A reference that does not resolve — no project root, or the file is
    missing — goes to ``unresolved`` rather than failing the export: the
    card is still worth having, and the hint says what is missing.
    """
    bundled: Dict[str, Any] = {}
    unresolved: List[str] = []
    if not project_root:
        return bundled, [r for r in refs]
    from app.utils.output_contracts import ContractError, SchemaSet
    for ref in refs:
        try:
            resolved = SchemaSet.resolve([ref], project_root)
        except ContractError as e:
            logger.debug(f"portable: {ref} not bundled: {e}")
            unresolved.append(ref)
            continue
        except Exception as e:  # noqa: BLE001
            logger.warning(f"portable: {ref} not bundled: {e}")
            unresolved.append(ref)
            continue
        for cid, doc in resolved.docs.items():
            if cid.startswith(PROJECT_SCHEMA_PREFIX):
                bundled.setdefault(cid, doc.schema)
    return bundled, unresolved


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def restore_schemas(
    schemas: Optional[Dict[str, Any]], project_root: Optional[str],
) -> Tuple[List[str], List[str]]:
    """Write bundled schemas into ``<project_root>/.ziya/schemas/``.

    Returns ``(restored_ids, warnings)``.  A schema already present with
    identical content is silently kept; one with DIFFERENT content is kept
    too, and reported — the imported card will validate against the local
    definition, which may not be what its author tested against.  Without
    a project root nothing is written and every schema is reported.
    """
    if not schemas:
        return [], []
    from app.utils.output_contracts import PROJECT_SCHEMA_DIR
    warnings: List[str] = []
    restored: List[str] = []
    if not project_root:
        warnings.append(
            "the export bundles output-contract schemas but this project has "
            "no root directory, so none were written: " + ", ".join(sorted(schemas)))
        return restored, warnings
    target_dir = Path(project_root) / PROJECT_SCHEMA_DIR
    for cid, schema in schemas.items():
        if not isinstance(cid, str) or not cid.startswith(PROJECT_SCHEMA_PREFIX):
            warnings.append(f"ignored bundled schema with unexpected id {cid!r}")
            continue
        name = cid[len(PROJECT_SCHEMA_PREFIX):]
        # Names come from a file; refuse anything that is not a plain
        # single-segment name so a bundle cannot write outside the dir.
        if not name or "/" in name or "\\" in name or name.startswith("."):
            warnings.append(f"ignored bundled schema with unsafe name {cid!r}")
            continue
        path = target_dir / f"{name}.json"
        try:
            if path.is_file():
                try:
                    local = json.loads(path.read_text())
                except (OSError, ValueError):
                    local = None
                if local is not None and _canonical(local) == _canonical(schema):
                    continue
                warnings.append(
                    f"schema {cid} already exists in this project with different "
                    f"content; the local {PROJECT_SCHEMA_DIR / (name + '.json')} "
                    f"was kept and imported cards will validate against it")
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
            restored.append(cid)
        except OSError as e:
            warnings.append(f"could not write schema {cid}: {e}")
    return restored, warnings
