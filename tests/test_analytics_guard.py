"""Statement allowlist for ``analyze`` (app/utils/analytics/guard.py).

Each refusal is paired with an accepted near-miss so a guard that rejects
everything cannot pass.
"""
import pytest

pytest.importorskip("sqlglot")

from app.utils.analytics.guard import SqlRejected, check_sql  # noqa: E402

DS = "ds_0123456789abcdef"
ALLOWED = {"t", "u", DS}
MACROS = {"intervals_union", "entity_distribution"}


def ok(sql):
    return check_sql(sql, ALLOWED, MACROS)


def bad(sql, match=None):
    with pytest.raises(SqlRejected, match=match):
        check_sql(sql, ALLOWED, MACROS)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t",
    "FROM t",
    f"WITH a AS (SELECT * FROM {DS}) SELECT count(*) FROM a",
    "SELECT * FROM intervals_union(t, 'ct', 'start', 'end')",
    "SELECT 1 UNION ALL SELECT 2",
    "(SELECT * FROM t)",
    "SELECT * FROM t JOIN (SELECT * FROM u) y USING (k)",
    "SELECT * FROM range(10)",
    "SELECT k, sum(v) FROM t GROUP BY k QUALIFY row_number() OVER (ORDER BY k) = 1",
    "SELECT 'not a url: http' AS s, 'path/like/a.csv' AS p FROM t",
])
def test_accepted(sql):
    ok(sql)


def test_accepts_macro_over_cte_and_reports_references():
    r = ok(f"WITH a AS (SELECT * FROM {DS}) SELECT * FROM intervals_union(a, 'e', 's', 'f')")
    assert DS in r.tables
    assert "intervals_union" in r.table_functions


@pytest.mark.parametrize("sql,match", [
    ("COPY t TO '/tmp/x.csv'", "Only SELECT"),
    ("ATTACH 'x.db'", "Only SELECT"),
    ("INSTALL httpfs", "Only SELECT"),
    ("LOAD httpfs", "Only SELECT"),
    ("PRAGMA version", "Only SELECT"),
    ("SET threads=1", "Only SELECT"),
    ("CREATE TABLE x AS SELECT 1", "Only SELECT"),
    ("DESCRIBE t", "Only SELECT"),
    ("SELECT 1; SELECT 2", "Exactly one"),
    ("SELECT * FROM read_csv('/etc/passwd')", "not allowed"),
    ("SELECT read_text('/etc/passwd')", "not allowed"),
    ("SELECT * FROM glob('*')", "not allowed"),
    ("SELECT * FROM '/etc/passwd'", "File paths"),
    ("SELECT * FROM 'https://example.com/x.csv'", "URLs"),
    ("SELECT * FROM t WHERE k = 's3://bucket/key'", "URLs"),
    ("SELECT * FROM main.t", "Qualified"),
    ("SELECT * FROM secret_table", "Unknown table"),
    ("SELECT * FROM some_macro_we_did_not_install(t)", "not allowed"),
    ("SELECT getenv('HOME')", "not allowed"),
    ("SELECT * FROM duckdb_settings()", "not allowed"),
    ("", "empty"),
])
def test_rejected(sql, match):
    bad(sql, match)


def test_unknown_table_message_lists_registered_inputs():
    with pytest.raises(SqlRejected) as e:
        check_sql("SELECT * FROM nope", {"alpha", "beta"})
    assert "alpha" in str(e.value) and "beta" in str(e.value)
