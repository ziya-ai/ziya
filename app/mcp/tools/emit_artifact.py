"""
Builtin tool: emit_artifact

Lets a Task Card agent declare durable outputs mid-run.  Parts are
collected per-task (app/utils/task_artifacts.py), drained into
``Artifact.outputs`` by the task executor, and rendered by the run
tile's artifact viewer.

Grouping vocabulary is deliberately tiny and semantically neutral —
``group`` ("these belong together"), ``label`` (display name within the
group), ``seq`` (ordering).  The viewer selects a layout from group
SHAPE; no label string is magic.

When ``diagram`` is supplied, the spec is rendered through the headless
renderer AT EMIT TIME and the resulting PNG is frozen under the run's
artifacts directory — preserving the exact pixels (and console
warnings) as they were during the run, not a later re-render.  A
failed render is preserved too: the error evidence becomes the
artifact (status="error"), because for a broken spec the failure IS
the output worth keeping.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool

logger = logging.getLogger(__name__)


def _text(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}]}


class EmitArtifactInput(BaseModel):
    """Input schema for emit_artifact."""

    name: str = Field(
        ...,
        description="Short identifier for this artifact (e.g. 'fix_rationale', 'baseline_chart').",
    )
    part_type: str = Field(
        default="text",
        description="Artifact kind: 'text' (prose/conclusions), 'file' (existing file by path), 'data' (JSON object). Ignored when 'diagram' is provided.",
    )
    text: Optional[str] = Field(
        default=None, description="Content for part_type='text'.",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Path for part_type='file' (project-relative or granted absolute path).",
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None, description="JSON object for part_type='data'.",
    )
    diagram: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Render-and-freeze: {type, definition, theme?}. The diagram is "
            "rendered now and the PNG preserved with the run; a failed "
            "render preserves the error evidence instead."
        ),
    )
    group: Optional[str] = Field(
        default=None,
        description=(
            "The SUBJECT these parts are about; parts sharing a group "
            "are shown together. Reuse the same id across iterations and "
            "later tasks to accumulate one subject's evidence. May be a "
            "path, 'section/subject' (e.g. 'd2/D-020'), to gather "
            "subjects under a heading."
        ),
    )
    label: Optional[str] = Field(
        default=None,
        description=(
            "What this part IS within its subject ('before', 'after', "
            "'baseline', 'us-east'). Two differently-labeled parts in a "
            "group read as a comparison and are shown side-by-side. Put "
            "any extra axis in the group id, not the label."
        ),
    )
    seq: Optional[int] = Field(
        default=None,
        description=(
            "Position when the group is a progression rather than a "
            "comparison (0-based)."
        ),
    )
    from_run: Optional[str] = Field(
        default=None,
        description=(
            "Include a blob captured elsewhere, with 'file_path' set to "
            "its BARE FILENAME. Accepts 'self' for evidence emitted "
            "earlier in THIS run (including by a card this run called), a "
            "card name or card id for that card's most recent finished "
            "run, or an explicit run id. Foreign blobs are copied in so "
            "this run's report stays self-contained."
        ),
    )


class EmitArtifactTool(BaseMCPTool):
    """Declare a durable output artifact for the current Task Card run."""

    name: str = "emit_artifact"
    description: str = (
        "[DIRECT] Declare a durable output of this task — collected into the "
        "run's artifact record and shown in the run tile's artifact viewer. "
        "Kinds: text (conclusions/rationale), file (an existing file by "
        "path), data (a JSON object), or diagram={type, definition} which is "
        "rendered NOW and frozen as a PNG (a failed render preserves the "
        "error evidence instead). Structure with `group` (the subject; "
        "reusable across iterations and tasks, and may be a "
        "'section/subject' path), `label` (what this part is within the "
        "subject — two labels read as a comparison), and `seq` (ordering "
        "for a progression). `from_run` includes a blob captured "
        "elsewhere: 'self' for this run's own earlier output (including "
        "cards it called), or a card name for an earlier launch — use "
        "`list_run_artifacts` to enumerate what exists. Only available "
        "inside Task Card runs."
    )

    InputSchema = EmitArtifactInput

    async def execute(self, **kwargs) -> Any:
        kwargs.pop("_workspace_path", None)
        from app.utils.task_artifacts import (
            build_part, collection_active, emit_part,
        )

        if not collection_active():
            return _text(
                "emit_artifact is only available inside a Task Card run — "
                "there is no active artifact collection in this context."
            )

        name = kwargs.get("name") or ""
        group = kwargs.get("group")
        label = kwargs.get("label")
        seq = kwargs.get("seq")
        from_run = kwargs.get("from_run")
        diagram = kwargs.get("diagram")

        if diagram is not None:
            # 'diagram' renders NEW content at emit time; 'from_run'
            # names content captured ELSEWHERE.  One part cannot be
            # both, and the render path takes no from_run — so honouring
            # the combination silently dropped it, which is the worst
            # available failure for the case that motivates passing both:
            # a before/after that renders, reports success, and is
            # missing the half that made it a comparison.  Refuse, and
            # name the shape that does work rather than guessing.
            if from_run:
                return _text(
                    "Error: 'diagram' and 'from_run' cannot be combined on "
                    "one part — 'diagram' renders new content now, while "
                    "'from_run' includes a blob captured elsewhere. For a "
                    "before/after, emit TWO parts sharing one 'group' with "
                    "different 'label's: the prior evidence with 'from_run', "
                    "the new render with 'diagram'."
                )
            return await self._emit_diagram(name, diagram, group, label, seq)

        part, err = build_part(
            name=name,
            part_type=kwargs.get("part_type", "text"),
            text=kwargs.get("text"),
            file_path=kwargs.get("file_path"),
            data=kwargs.get("data"),
            group=group, label=label, seq=seq,
            from_run=from_run,
        )
        if err:
            return _text(f"Error: {err}")
        ok, msg = emit_part(part)
        return _text(msg if ok else f"Error: {msg}")

    async def _emit_diagram(self, name, diagram, group, label, seq) -> dict:
        """Render-and-freeze path: capture the post-rendered image now."""
        from app.utils.task_artifacts import (
            MAX_DIAGRAM_DEF_CHARS, build_part, emit_part, save_artifact_blob,
        )

        dtype = (diagram or {}).get("type", "")
        definition = (diagram or {}).get("definition", "")
        theme = (diagram or {}).get("theme", "light")
        if not dtype or not definition:
            return _text("Error: 'diagram' requires both 'type' and 'definition'.")
        # Structured specs (vega-lite, plotly, packet, ...) are natively JSON
        # objects and arrive as such.  Slicing the object raised
        # ``KeyError: slice(None, 50000, None)`` here -- above the try below,
        # so the evidence-preserving handler never fired and the emit
        # produced nothing at all rather than a recoverable error artifact.
        if isinstance(definition, (dict, list)):
            try:
                definition = json.dumps(definition)
            except (TypeError, ValueError) as exc:
                return _text(
                    f"Error: 'diagram.definition' is not JSON-serializable: {exc}"
                )
        elif not isinstance(definition, str):
            definition = str(definition)
        def_record = definition[:MAX_DIAGRAM_DEF_CHARS]

        try:
            from app.services.diagram_renderer import get_diagram_renderer
            from app.config.env_registry import ziya_env
            renderer = await get_diagram_renderer(server_port=ziya_env("ZIYA_PORT"))
            image_bytes, diagnostics = await renderer.render_diagram_with_diagnostics(
                {"type": dtype, "definition": definition, "theme": theme},
                format="png",
            )
            file_uri, save_err = save_artifact_blob(f"{name}.png", image_bytes)
            if save_err:
                raise RuntimeError(f"rendered OK but blob persistence failed: {save_err}")
            warnings = list(diagnostics.get("console_warnings") or []) + \
                list(diagnostics.get("console_errors") or [])
            part, err = build_part(
                name=name, part_type="file",
                file_uri=file_uri, media_type="image/png",
                group=group, label=label, seq=seq,
                extra={
                    "rendered": True,
                    "diagram_type": dtype,
                    "diagram_definition": def_record,
                    "render_warnings": warnings,
                },
            )
        except Exception as exc:  # noqa: BLE001 — failure evidence IS the artifact
            logger.info("emit_artifact: diagram render failed, preserving evidence: %s", exc)
            part, err = build_part(
                name=name, part_type="text",
                text=(
                    "Diagram render FAILED at emit time — preserved as evidence.\n"
                    f"Error: {exc}"
                ),
                group=group, label=label, seq=seq, status="error",
                extra={"diagram_type": dtype, "diagram_definition": def_record},
            )
        if err:
            return _text(f"Error: {err}")
        ok, msg = emit_part(part)
        return _text(msg if ok else f"Error: {msg}")
