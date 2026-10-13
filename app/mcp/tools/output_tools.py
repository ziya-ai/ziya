"""
Builtin tool: validate_output (design/typed-findings.md, "Output contracts in
plain chat").

Output contracts were reachable only from a Task Card: a Task declares
``output_contract`` and its emits are checked.  An ordinary conversation that
produces a finding, a per-entity table or a distribution had no way to hold
it to the same schema.  This tool is that way: the same registry (shipped
``ziya:`` schemas, project ``.ziya/schemas/``, inline), the same field-level
errors, the same cross-field checks and the same ledger claim resolution.

With no ``data`` it describes the schema instead, so the model can ask what
a ``ziya:finding/v1`` needs before writing one.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.fileio import _get_project_root


class ValidateOutputInput(BaseModel):
    schema_ref: Union[str, Dict[str, Any]] = Field(
        ..., alias="schema",
        description=(
            "A schema reference: ziya:<name>/v<n> (shipped: finding, distribution, number, "
            "per-entity-table, text; no version means the newest), project:<name> "
            "(.ziya/schemas/<name>.json), or an inline JSON Schema object (up to 16 KB)."
        ),
    )
    data: Optional[Any] = Field(
        None,
        description="The value to check (a JSON object, list, string...). Omit to get the schema's fields instead.",
    )
    require_claims: Optional[bool] = Field(
        None,
        description=(
            "Reject literal numbers; every number must be an evidence-ledger claim "
            "{\"claim\": \"<id>\"}. Default: on when an evidence namespace is in scope."
        ),
    )
    namespace: Optional[str] = Field(
        None, description="Evidence namespace for relative claim ids (default: the one in scope).",
    )

    model_config = {"populate_by_name": True}


class ValidateOutputTool(BaseMCPTool):
    """Check a value against an output schema, in any conversation."""

    name: str = "validate_output"
    description: str = (
        "Check a structured result (a finding, a per-entity table, a distribution, or "
        "anything a project schema describes) against a schema before presenting or "
        "storing it: returns each problem as a JSON pointer with the field's meaning, "
        "runs the schema's cross-field checks, and resolves {\"claim\": id} numbers "
        "against the evidence ledger (unknown or refuted claims are rejected). Omit "
        "`data` to list the schema's fields. The same check Task Card output contracts "
        "apply, usable in an ordinary conversation."
    )
    InputSchema = ValidateOutputInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.evidence import service
        from app.utils.output_contracts import check_value
        project_root = service.ledger_root(_get_project_root(kwargs))
        schema = kwargs.get("schema", kwargs.get("schema_ref"))
        namespace = kwargs.get("namespace") or service.default_namespace()
        rc = kwargs.get("require_claims")
        has_data = "data" in kwargs and kwargs.get("data") is not None
        return await asyncio.to_thread(
            check_value, schema, kwargs.get("data"),
            project_root=project_root,
            require_claims=bool(namespace) if rc is None else bool(rc),
            namespace=namespace,
            describe_only=not has_data,
        )


class StoreOutputInput(ValidateOutputInput):
    data: Any = Field(..., description="The value to check and, when valid, store.")


class StoreOutputTool(BaseMCPTool):
    """Check a value against a schema and keep it where ``analyze`` can read it."""

    name: str = "store_output"
    description: str = (
        "Check a structured result against a schema (as validate_output does) and, when "
        "it passes, append it to the project's output store: .ziya/outputs/<family>.jsonl, "
        "one file per schema family (a finding under any project schema that extends "
        "ziya:finding lands in finding.jsonl). Findings emitted inside Task Card runs "
        "under a contract land in the same files, so one analyze query covers both: "
        "analyze(sql=\"SELECT key, data.title, data.impact.fleet_total.mid FROM f\", "
        "inputs={\"f\": \".ziya/outputs/finding.jsonl\"}). A value whose data.id "
        "matches an earlier record is stored as a new version of it. Returns the stored "
        "record id and the path to query; an invalid value is refused with the same "
        "field-level errors as validate_output and nothing is stored."
    )
    InputSchema = StoreOutputInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from app.utils.evidence import service
        from app.utils import output_store
        from app.utils.output_contracts import check_value
        project_root = service.ledger_root(_get_project_root(kwargs))
        schema = kwargs.get("schema", kwargs.get("schema_ref"))
        namespace = kwargs.get("namespace") or service.default_namespace()
        rc = kwargs.get("require_claims")
        data = kwargs.get("data")
        res = await asyncio.to_thread(
            check_value, schema, data, project_root=project_root,
            require_claims=bool(namespace) if rc is None else bool(rc), namespace=namespace,
        )
        if res.get("error") or not res.get("ok"):
            return {**res, "stored": False}
        rec = await asyncio.to_thread(
            output_store.append, project_root,
            schema_id=res["schema_id"], schema_sha256=res["schema_sha256"],
            schema_lineage=res["schema_lineage"], data=data,
            resolved_claims=res.get("resolved_claims"),
            unverified_numbers=res.get("unverified_numbers"),
            provenance=service.provenance_from_context(),
        )
        return {
            **res, "stored": True, "record_id": rec["id"], "family": rec["family"],
            "key": rec["key"], "version": rec["version"],
            "path": output_store.relative_path(rec["family"]),
            "query_hint": (
                f"analyze(sql=\"SELECT key, version, data.* FROM o\", "
                f"inputs={{\"o\": \"{output_store.relative_path(rec['family'])}\"}})"),
        }
