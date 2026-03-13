"""
AST Query builtin MCP tools.

Exposes the background-indexed AST as on-demand tools so the model can
search symbols, inspect file structure, and trace references without
dumping the entire tree into the prompt context.
"""

import os
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_enhancer():
    """Return the AST enhancer for the current project, or None if not yet indexed."""
    from app.utils.ast_parser.integration import get_current_enhancer
    return get_current_enhancer()


def _get_query_engine(file_path: Optional[str] = None):
    """Return the query engine for a file or the whole project."""
    enhancer = _get_enhancer()
    if enhancer is None:
        return None
    key = file_path if file_path and file_path in enhancer.query_engines else "project"
    return enhancer.query_engines.get(key)


def _not_ready_message() -> Dict[str, Any]:
    """Standard response when the AST index is not yet available."""
    from app.utils.context_enhancer import get_ast_indexing_status
    status = get_ast_indexing_status()
    if status.get("is_indexing"):
        pct = status.get("completion_percentage", 0)
        return {
            "error": True,
            "message": (
                f"AST indexing is in progress ({pct}% complete). "
                "Try again in a moment."
            ),
        }
    return {
        "error": True,
        "message": (
            "AST index is not available. The codebase may not have been "
            "indexed yet. Check /api/ast/status for details."
        ),
    }


def _rel(path: str) -> str:
    """Best-effort relative path for display."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


# ============================================================================
# TOOL 1 — ast_get_tree
# ============================================================================

class ASTGetTreeInput(BaseModel):
    """Input schema for ast_get_tree."""
    path: Optional[str] = Field(
        default=None,
        description=(
            "File or directory path to inspect. "
            "Omit for a project-wide overview."
        ),
    )
    max_symbols: int = Field(
        default=30,
        description="Maximum symbols to return per file.",
    )


class ASTGetTreeTool(BaseMCPTool):
    """Return the AST structure for the project or a specific file."""

    name: str = "ast_get_tree"
    description: str = (
        "Get the AST structure of the indexed codebase.\n\n"
        "- Omit `path` for a project-wide overview (file list, type breakdown, stats).\n"
        "- Provide a file `path` for detailed symbols, imports, and structure of that file.\n"
        "- Provide a directory `path` to list all indexed files under it.\n\n"
        "The AST is indexed in the background at startup. Use this instead of "
        "shell commands to understand code structure."
    )

    InputSchema = ASTGetTreeInput

    @property
    def is_internal(self) -> bool:
        return True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("_workspace_path", None)
        inp = self.InputSchema.model_validate(kwargs)
        enhancer = _get_enhancer()
        if enhancer is None or not enhancer.ast_cache:
            return _not_ready_message()

        lines: List[str] = []

        if inp.path is None:
            # --- Project overview ---
            file_types: Dict[str, int] = {}
            for fp in enhancer.ast_cache:
                ext = os.path.splitext(fp)[1]
                file_types[ext] = file_types.get(ext, 0) + 1

            lines.append("# AST Project Overview")
            lines.append(f"**Indexed files**: {len(enhancer.ast_cache)}")
            lines.append(f"**Total nodes**: {len(enhancer.project_ast.nodes)}")
            lines.append(f"**Total edges**: {len(enhancer.project_ast.edges)}")
            lines.append(f"\n## Files by type: {dict(sorted(file_types.items()))}")
            lines.append("\n## Indexed files")
            for fp in sorted(enhancer.ast_cache):
                ast = enhancer.ast_cache[fp]
                lines.append(f"- `{_rel(fp)}` ({len(ast.nodes)} nodes)")
            return {"content": "\n".join(lines)}

        # --- Specific path ---
        # Resolve to absolute
        codebase = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        target = os.path.normpath(os.path.join(codebase, inp.path))

        # Directory mode
        if os.path.isdir(target):
            matching = sorted(
                fp for fp in enhancer.ast_cache if fp.startswith(target)
            )
            lines.append(f"# AST: files under `{_rel(target)}`")
            lines.append(f"**Matched**: {len(matching)} indexed files")
            for fp in matching:
                ast = enhancer.ast_cache[fp]
                syms = enhancer._extract_key_symbols(ast)[:5]
                sym_str = ", ".join(syms) if syms else "(none)"
                lines.append(f"- `{_rel(fp)}`: {sym_str}")
            return {"content": "\n".join(lines)}

        # File mode — try exact or fuzzy match
        matched_path = None
        for fp in enhancer.ast_cache:
            if fp == target or fp.endswith(inp.path):
                matched_path = fp
                break
        if matched_path is None:
            return {"error": True, "message": f"File not in AST index: {inp.path}"}

        ast = enhancer.ast_cache[matched_path]
        qe = enhancer.query_engines.get(matched_path)

        lines.append(f"# AST: `{_rel(matched_path)}`")
        lines.append(f"**Nodes**: {len(ast.nodes)}  |  **Edges**: {len(ast.edges)}")

        # Symbols
        symbols = enhancer._extract_key_symbols(ast)[:inp.max_symbols]
        if symbols:
            lines.append("\n## Defined symbols")
            for s in symbols:
                lines.append(f"- {s}")

        # Dependencies
        deps = enhancer._extract_dependencies(ast)
        if deps:
            lines.append("\n## Dependencies")
            for d in deps:
                lines.append(f"- {d}")

        # Node-type breakdown
        type_counts: Dict[str, int] = {}
        for node in ast.nodes.values():
            type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1
        lines.append(f"\n## Node types: {dict(sorted(type_counts.items()))}")

        return {"content": "\n".join(lines)}


# ============================================================================
# TOOL 2 — ast_search
# ============================================================================

class ASTSearchInput(BaseModel):
    """Input schema for ast_search."""
    query: str = Field(
        description="Symbol name or substring to search for.",
    )
    node_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter by node type: function, class, method, variable, import. "
            "Omit to search all types."
        ),
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Limit search to a specific file path (substring match).",
    )
    max_results: int = Field(
        default=30,
        description="Maximum results to return.",
    )


class ASTSearchTool(BaseMCPTool):
    """Search symbols across the AST-indexed codebase."""

    name: str = "ast_search"
    description: str = (
        "Search the AST index for symbols (functions, classes, methods, "
        "variables, imports) across the codebase.\n\n"
        "Faster and more precise than grep — finds semantic code elements "
        "with their exact locations, types, and attributes.\n\n"
        "Examples:\n"
        '- `query="sendPayload"` → find where sendPayload is defined\n'
        '- `query="render", node_type="function"` → all render functions\n'
        '- `query="Router", file_path="routes"` → Router symbols in routes/\n'
    )

    InputSchema = ASTSearchInput

    @property
    def is_internal(self) -> bool:
        return True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("_workspace_path", None)
        inp = self.InputSchema.model_validate(kwargs)
        enhancer = _get_enhancer()
        if enhancer is None or not enhancer.project_ast:
            return _not_ready_message()

        query_lower = inp.query.lower()
        results: List[Dict[str, Any]] = []

        for node in enhancer.project_ast.nodes.values():
            # Name match (substring, case-insensitive)
            if query_lower not in node.name.lower():
                continue
            # Type filter
            if inp.node_type and node.node_type != inp.node_type:
                continue
            # File filter
            if inp.file_path and inp.file_path not in node.source_location.file_path:
                continue

            results.append({
                "name": node.name,
                "type": node.node_type,
                "file": _rel(node.source_location.file_path),
                "line": node.source_location.start_line,
                "end_line": node.source_location.end_line,
                "attributes": node.attributes or {},
            })

            if len(results) >= inp.max_results:
                break

        lines = [f"# AST Search: `{inp.query}`"]
        if inp.node_type:
            lines[0] += f" (type={inp.node_type})"
        lines.append(f"**Results**: {len(results)}")

        for r in results:
            attrs = ""
            if r["attributes"]:
                attr_parts = [f"{k}={v}" for k, v in list(r["attributes"].items())[:3]]
                attrs = f"  [{', '.join(attr_parts)}]" if attr_parts else ""
            lines.append(
                f"- **{r['type']}** `{r['name']}` "
                f"in `{r['file']}` L{r['line']}-{r['end_line']}{attrs}"
            )

        return {"content": "\n".join(lines)}


# ============================================================================
# TOOL 3 — ast_references
# ============================================================================

class ASTReferencesInput(BaseModel):
    """Input schema for ast_references."""
    name: str = Field(
        description="Symbol name to look up.",
    )
    action: Literal["definitions", "dependencies", "callers", "summary"] = Field(
        default="definitions",
        description=(
            "What to retrieve:\n"
            "- definitions: where this symbol is defined\n"
            "- dependencies: what files a given file depends on\n"
            "- callers: what calls this function\n"
            "- summary: full file summary (pass a file path as `name`)"
        ),
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Scope the lookup to a specific file.",
    )


class ASTReferencesTool(BaseMCPTool):
    """Find definitions, dependencies, and callers for a symbol."""

    name: str = "ast_references"
    description: str = (
        "Trace references for a symbol in the AST index.\n\n"
        "Actions:\n"
        "- **definitions**: find where a symbol is defined\n"
        "- **dependencies**: list files that a given file imports from\n"
        "- **callers**: find call sites for a function\n"
        "- **summary**: get a structured summary of a file "
        "(pass the file path as `name`)\n"
    )

    InputSchema = ASTReferencesInput

    @property
    def is_internal(self) -> bool:
        return True

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("_workspace_path", None)
        inp = self.InputSchema.model_validate(kwargs)
        enhancer = _get_enhancer()
        if enhancer is None or not enhancer.project_ast:
            return _not_ready_message()

        qe = enhancer.query_engines.get("project")
        if qe is None:
            return _not_ready_message()

        lines: List[str] = []

        if inp.action == "definitions":
            nodes = qe.find_definitions(inp.name)
            lines.append(f"# Definitions of `{inp.name}`")
            lines.append(f"**Found**: {len(nodes)}")
            for n in nodes:
                lines.append(
                    f"- **{n.node_type}** `{n.name}` in "
                    f"`{_rel(n.source_location.file_path)}` "
                    f"L{n.source_location.start_line}-{n.source_location.end_line}"
                )

        elif inp.action == "dependencies":
            # Resolve file path
            resolved = self._resolve_file(enhancer, inp.name)
            if resolved is None:
                return {"error": True, "message": f"File not in index: {inp.name}"}
            deps = qe.get_dependencies(resolved)
            lines.append(f"# Dependencies of `{_rel(resolved)}`")
            lines.append(f"**Count**: {len(deps)}")
            for d in deps:
                lines.append(f"- `{_rel(d)}`")

        elif inp.action == "callers":
            calls = qe.get_function_calls(inp.name)
            lines.append(f"# Callers of `{inp.name}`")
            lines.append(f"**Found**: {len(calls)}")
            for c in calls:
                lines.append(
                    f"- `{_rel(c.source_location.file_path)}` "
                    f"L{c.source_location.start_line}"
                )

        elif inp.action == "summary":
            resolved = self._resolve_file(enhancer, inp.name)
            if resolved is None:
                return {"error": True, "message": f"File not in index: {inp.name}"}
            summary = qe.generate_summary(resolved)
            lines.append(f"# Summary: `{_rel(resolved)}`")
            lines.append(f"**Types**: {summary.get('type_counts', {})}")
            for defn in summary.get("top_level_definitions", []):
                lines.append(f"- {defn['type']} `{defn['name']}`")
            if summary.get("dependencies"):
                lines.append("\n**Dependencies**:")
                for d in summary["dependencies"]:
                    lines.append(f"- `{_rel(d)}`")

        return {"content": "\n".join(lines)}

    @staticmethod
    def _resolve_file(enhancer, name: str) -> Optional[str]:
        """Try to match a user-supplied name to an indexed file path."""
        for fp in enhancer.ast_cache:
            if fp.endswith(name) or name in fp:
                return fp
        return None
