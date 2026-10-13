"""
Builtin evidence-ledger tools (design/evidence-ledger.md).

Thin wrappers over app/utils/evidence/service.py: record where a number came
from, look it up, and re-check it.  Provenance (conversation, task ids) is
taken from the server context, never from the model's arguments.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.fileio import (
    _resolve_and_validate, _get_project_root, _get_all_readable_prefixes,
)
from app.utils.logging_utils import logger


def _err(message: str) -> Dict[str, Any]:
    return {"error": True, "message": message}


class LedgerRecordInput(BaseModel):
    observation: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "One act of looking at data: {source: sql|file|command|tool|computed, "
            "query_text?, path?, argv?, connector?, and exactly one result: "
            "result_path (a file the server hashes; attested), dataset_id (attested), "
            "or inline (a value you supply; not attested), volatile?, as_of?}."
        ),
    )
    claim: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "A named value: {key: 'namespace.key', value, namespace?, unit?, format?, "
            "population?: {description, n, filter}, distribution?: {n, mean, median, p10, "
            "p90, sd, min, max}, derivation: {obs_ids?, inputs?: [claim] or {name: claim}, "
            "column?, expression?, code_ref?}, tolerance?: {abs?, rel?}}. Given with an "
            "observation, the claim cites it automatically."
        ),
    )


class LedgerRecordTool(BaseMCPTool):
    name: str = "ledger_record"
    description: str = (
        "Record where a reported number came from: an observation (the query, file or "
        "command and its result's hash) and/or a claim (the value, its population, and "
        "the column and expression it was derived from). Recording an existing claim id "
        "adds a new version that supersedes the old one. Record every number you will "
        "report, so documents can cite it as {{claim:key}} and it can be re-checked."
    )
    InputSchema = LedgerRecordInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.evidence import service
        project_root = _get_project_root(kwargs)

        def resolve(p: str):
            return _resolve_and_validate(
                p.strip().strip("'\""), project_root,
                allowed_absolute_prefixes=_get_all_readable_prefixes(),
            )

        try:
            # Paths resolve against the task's workspace; the ledger is the
            # card's project ledger (see service.ledger_root).
            return await asyncio.to_thread(
                service.record, project_root=service.ledger_root(project_root),
                resolve_path=resolve,
                observation=kwargs.get("observation"), claim=kwargs.get("claim"),
                namespace=service.default_namespace(),
                provenance=service.provenance_from_context(),
            )
        except Exception as e:  # noqa: BLE001 -- the model gets the error, not a crash
            logger.warning(f"ledger_record failed: {e}")
            return _err(f"ledger_record failed: {e}")


class LedgerQueryInput(BaseModel):
    ref: Optional[str] = Field(
        None, description="A claim, or claim.field (e.g. s1.per_unit.distribution.median); @vN pins a version.",
    )
    prefix: Optional[str] = Field(None, description="List the latest claims under this namespace or prefix.")
    namespace: Optional[str] = Field(None, description="Namespace for a relative ref.")
    history: bool = Field(False, description="With ref: include every version.")


class LedgerQueryTool(BaseMCPTool):
    name: str = "ledger_query"
    description: str = (
        "Look up recorded claims: one ref (current value, status, derivation, latest "
        "verdict) or every claim under a prefix."
    )
    InputSchema = LedgerQueryInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.evidence import service
        project_root = _get_project_root(kwargs)
        return await asyncio.to_thread(
            service.query, project_root=service.ledger_root(project_root), ref=kwargs.get("ref"),
            prefix=kwargs.get("prefix"),
            namespace=kwargs.get("namespace") or service.default_namespace(),
            history=bool(kwargs.get("history")),
        )


class LedgerVerifyInput(BaseModel):
    refs: Optional[List[str]] = Field(None, description="Claim ids to re-check.")
    prefix: Optional[str] = Field(None, description="Re-check every claim under this namespace or prefix.")
    method: str = Field(
        "rehash",
        description=(
            "rehash: re-hash the files and datasets the claim came from. computed: "
            "re-evaluate derivation.expression from the current input claims."
        ),
    )


class LedgerVerifyTool(BaseMCPTool):
    name: str = "ledger_verify"
    description: str = (
        "Re-check claims and record a verdict for each: agrees, drifted, refuted or "
        "not_testable. A claim's status follows its latest verdict."
    )
    InputSchema = LedgerVerifyInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.evidence import service
        project_root = _get_project_root(kwargs)
        try:
            return await asyncio.to_thread(
                service.verify, project_root=service.ledger_root(project_root),
                method=kwargs.get("method") or "rehash",
                refs=kwargs.get("refs"), prefix=kwargs.get("prefix"),
                provenance=service.provenance_from_context(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ledger_verify failed: {e}")
            return _err(f"ledger_verify failed: {e}")


__all__ = ["LedgerRecordTool", "LedgerQueryTool", "LedgerVerifyTool"]
