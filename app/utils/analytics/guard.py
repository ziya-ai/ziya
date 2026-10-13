"""Statement allowlist for ``analyze`` (design/local-analytics.md, Engine §1).

The first of three layers.  It rejects anything that is not a single
read-only query over names the server registered, so a refusal comes back as
a clear message rather than a DuckDB permission error.  It is not the only
defence: the engine also runs with external access disabled and its
configuration locked, so a statement that slips past this check still cannot
read a file the server did not allowlist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Mapping, Optional, Sequence, Set


class SqlRejected(ValueError):
    """The statement is not allowed.  The message is safe to show the model."""


# Table functions that read no files and are useful in analysis.
SAFE_TABLE_FUNCTIONS: FrozenSet[str] = frozenset({
    "range", "generate_series", "unnest",
})

# Any function whose (lower-cased) name matches is refused, scalar or table.
_DENY_FUNC_RE = re.compile(
    r"^(read_|parquet_|sniff_|duckdb_|json_execute)|_scan$|"
    r"^(glob|query|query_table|getenv|current_setting|load_extension)$"
)

# A string literal that names a remote or file resource.
_URL_RE = re.compile(r"^\s*[a-zA-Z][a-zA-Z0-9+.\-]*://")


@dataclass
class GuardResult:
    """What the statement refers to, for error enrichment by the engine."""
    tables: Set[str] = field(default_factory=set)
    table_functions: Set[str] = field(default_factory=set)


def _func_name(node) -> str:
    from sqlglot import exp
    if isinstance(node, exp.Anonymous):
        return str(node.name or "").lower()
    try:
        return str(node.sql_name() or "").lower()
    except Exception:  # noqa: BLE001
        return type(node).__name__.lower()


def check_sql(
    sql: str,
    allowed_tables: Iterable[str],
    allowed_table_functions: Iterable[str] = (),
    table_args: Optional[Mapping[str, Sequence[int]]] = None,
) -> GuardResult:
    """Raise ``SqlRejected`` unless ``sql`` is one read-only query.

    ``allowed_tables`` are the names the server registered as views (input
    aliases and dataset ids).  ``allowed_table_functions`` are the table
    macros the server installs (see primitives).  CTE names are allowed
    automatically.  ``table_args`` maps a macro to the positions of its
    table arguments, which must be bare registered or WITH names: a macro
    reads them through ``query_table``, which would otherwise accept a path.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError

    if not isinstance(sql, str) or not sql.strip():
        raise SqlRejected("The query is empty.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as e:
        raise SqlRejected(f"The query could not be parsed: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise SqlRejected(
            f"Exactly one statement is allowed; got {len(statements)}."
        )
    root = statements[0]
    while isinstance(root, exp.Subquery):
        root = root.this
    if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SqlRejected(
            f"Only SELECT queries are allowed (optionally with WITH); got "
            f"{type(statements[0]).__name__.upper()}."
        )

    forbidden_nodes = tuple(
        getattr(exp, n) for n in (
            "Create", "Insert", "Update", "Delete", "Drop", "Copy", "Attach",
            "Detach", "Install", "Command", "Pragma", "Set", "Use", "Alter",
            "Merge", "Describe", "Summarize", "Export",
        ) if hasattr(exp, n)
    )
    allowed = {t.lower() for t in allowed_tables}
    table_funcs = {f.lower() for f in allowed_table_functions} | SAFE_TABLE_FUNCTIONS
    ctes = {str(c.alias_or_name).lower() for c in root.find_all(exp.CTE)}
    result = GuardResult()

    for node in root.walk():
        if forbidden_nodes and isinstance(node, forbidden_nodes):
            raise SqlRejected(f"{type(node).__name__.upper()} is not allowed in a query.")
        if isinstance(node, exp.Literal) and node.is_string and _URL_RE.match(node.this or ""):
            raise SqlRejected("URLs are not allowed; register the data as a dataset first.")

    for table in root.find_all(exp.Table):
        target = table.this
        if isinstance(target, exp.Func):
            name = _func_name(target)
            if name not in table_funcs or _DENY_FUNC_RE.search(name):
                raise SqlRejected(
                    f"Table function {name!r} is not allowed. Use a registered "
                    f"input or one of: {', '.join(sorted(table_funcs))}."
                )
            result.table_functions.add(name)
            args = list(getattr(target, "expressions", None) or [])
            for pos in (table_args or {}).get(name, ()):
                arg = args[pos] if pos < len(args) else None
                ok = (isinstance(arg, exp.Column) and not arg.table
                      and str(arg.name).lower() in (allowed | ctes))
                if not ok:
                    shown = arg.sql("duckdb") if arg is not None else "(missing)"
                    raise SqlRejected(
                        f"Argument {pos + 1} of {name} must be a registered input "
                        f"or a WITH table name, unquoted; got {shown}."
                    )
                if str(arg.name).lower() in allowed:
                    result.tables.add(str(arg.name).lower())
            continue
        if table.args.get("db") or table.args.get("catalog"):
            raise SqlRejected(
                f"Qualified table names are not allowed: {table.sql('duckdb')!r}."
            )
        name = str(table.name or "").lower()
        # A quoted FROM target ('https://…') parses as an identifier, not a
        # string literal, so the literal check above does not see it.
        if _URL_RE.match(name):
            raise SqlRejected("URLs are not allowed; register the data as a dataset first.")
        if name in ctes:
            continue
        if name not in allowed:
            hint = (" File paths cannot be queried directly; pass the file in "
                    "inputs instead." if ("/" in name or "." in name) else "")
            raise SqlRejected(
                f"Unknown table {table.name!r}. Registered inputs: "
                f"{', '.join(sorted(allowed)) or '(none)'}.{hint}"
            )
        result.tables.add(name)

    for func in root.find_all(exp.Func):
        name = _func_name(func)
        if _DENY_FUNC_RE.search(name):
            raise SqlRejected(f"Function {name!r} is not allowed.")
    return result
