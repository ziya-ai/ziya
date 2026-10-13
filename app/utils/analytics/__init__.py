"""Local analytics: read-only DuckDB SQL over registered datasets.

See design/local-analytics.md.  ``guard`` decides whether a statement may
run, ``engine`` runs it on a locked, isolated connection, and ``primitives``
holds the table macros installed on every connection.

Nothing here imports duckdb or sqlglot at module load: both are optional
(``ziya[data]``), and ``available()`` is how callers check.
"""
from __future__ import annotations


def available() -> bool:
    """True when the optional engine dependencies are importable."""
    try:
        import duckdb  # noqa: F401
        import sqlglot  # noqa: F401
    except ImportError:
        return False
    return True


INSTALL_HINT = (
    "Local analytics needs DuckDB and sqlglot, which are not installed. "
    "Install them with: ziya-install-extras --data"
)
