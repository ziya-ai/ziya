"""Table macros installed on every ``analyze`` connection.

design/local-analytics.md, Primitives.  Each macro enforces one rule that was
got wrong by hand in the 2026-10-02 network scan: a silent bin is unknown,
not zero; a counter decrease is a reset; overlapping intervals count once; a
fleet total travels with its per-entity distribution.

Conventions, all pinned by tests/test_analytics_primitives.py:

* Column arguments are strings naming a column of the input.  They are
  matched EXACTLY with ``COLUMNS(c -> c = name)``; ``COLUMNS('ct')`` is a
  regex and would also pick ``ct2``.
* The table argument is a bare name: a registered input or a WITH table.
  The guard enforces that (TABLE_ARGS).
* Internal CTEs are prefixed ``__z_``: a macro's own CTE name shadows a
  caller's table of the same name inside the macro.
* Parameter names never equal an output column name, because DuckDB
  substitutes macro parameters for every matching identifier in the body.
* Durations and gaps are in seconds, via ``__z_to_s``, which is overloaded
  by type so it is right for numbers and for timestamps (naive timestamps
  are not shifted by the session time zone).
"""
from __future__ import annotations

from typing import Dict, Mapping, Sequence, Tuple


def _cols(*pairs: Tuple[str, str]) -> str:
    """`SELECT` list picking each (output alias, column parameter) exactly."""
    return ", ".join(f"COLUMNS(c -> c = {param}) AS {out}" for out, param in pairs)


_TO_S = (
    "CREATE MACRO __z_to_s(x DOUBLE) AS x, (x TIMESTAMP) AS epoch(x), "
    "(x TIMESTAMPTZ) AS epoch(x), (x DATE) AS epoch(x), (x INTERVAL) AS epoch(x)"
)

_INTERVALS_UNION = f"""
CREATE MACRO intervals_union(tbl, entity_col, start_col, end_col) AS TABLE
WITH __z_iu_s AS (
  SELECT {_cols(('entity', 'entity_col'), ('"start"', 'start_col'), ('"end"', 'end_col'))}
  FROM query_table(tbl::VARCHAR)
), __z_iu_v AS (
  SELECT * FROM __z_iu_s
  WHERE "start" IS NOT NULL AND "end" IS NOT NULL AND "end" > "start"
), __z_iu_m AS (
  SELECT *, max("end") OVER (PARTITION BY entity ORDER BY "start", "end"
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS __z_pm
  FROM __z_iu_v
), __z_iu_g AS (
  SELECT *, sum(CASE WHEN __z_pm IS NULL OR "start" > __z_pm THEN 1 ELSE 0 END)
              OVER (PARTITION BY entity ORDER BY "start", "end"
                    ROWS UNBOUNDED PRECEDING) AS __z_grp
  FROM __z_iu_m
)
SELECT entity, min("start") AS "start", max("end") AS "end"
FROM __z_iu_g GROUP BY entity, __z_grp
"""

_INTERVAL_DURATION = """
CREATE MACRO interval_duration(tbl, entity_col, start_col, end_col) AS TABLE
SELECT entity,
       sum(__z_to_s("end") - __z_to_s("start")) AS total_s,
       count(*) AS n_intervals,
       max(__z_to_s("end") - __z_to_s("start")) AS longest_s
FROM intervals_union(tbl, entity_col, start_col, end_col)
GROUP BY entity
"""

_INTERVALS_INTERSECT = """
CREATE MACRO intervals_intersect(left_tbl, right_tbl, entity_col := 'entity',
                                 start_col := 'start', end_col := 'end') AS TABLE
SELECT x.entity,
       greatest(x."start", y."start") AS "start",
       least(x."end", y."end") AS "end"
FROM intervals_union(left_tbl, entity_col, start_col, end_col) x
JOIN intervals_union(right_tbl, entity_col, start_col, end_col) y
  ON x.entity = y.entity AND y."start" < x."end" AND y."end" > x."start"
"""

_INTERVALS_SUBTRACT = """
CREATE MACRO intervals_subtract(left_tbl, right_tbl, entity_col := 'entity',
                                start_col := 'start', end_col := 'end') AS TABLE
WITH __z_is_a AS (
  SELECT * FROM intervals_union(left_tbl, entity_col, start_col, end_col)
), __z_is_b AS (
  SELECT * FROM intervals_union(right_tbl, entity_col, start_col, end_col)
), __z_is_o AS (
  SELECT x.entity, x."start" AS a_start, x."end" AS a_end,
         greatest(y."start", x."start") AS b_start,
         least(y."end", x."end") AS b_end
  FROM __z_is_a x JOIN __z_is_b y
    ON x.entity = y.entity AND y."start" < x."end" AND y."end" > x."start"
), __z_is_l AS (
  SELECT *, coalesce(lag(b_end) OVER (PARTITION BY entity, a_start ORDER BY b_start),
                     a_start) AS prev_end
  FROM __z_is_o
)
SELECT entity, prev_end AS "start", b_start AS "end"
FROM __z_is_l WHERE b_start > prev_end
UNION ALL
SELECT entity, max(b_end) AS "start", a_end AS "end"
FROM __z_is_o GROUP BY entity, a_start, a_end HAVING a_end > max(b_end)
UNION ALL
SELECT x.entity, x."start", x."end" FROM __z_is_a x
WHERE NOT EXISTS (SELECT 1 FROM __z_is_o o
                  WHERE o.entity = x.entity AND o.a_start = x."start")
"""

_RUNS = f"""
CREATE MACRO runs(tbl, entity_col, ts_col, flag_col) AS TABLE
WITH __z_r_s AS (
  SELECT {_cols(('entity', 'entity_col'), ('ts', 'ts_col'), ('flag', 'flag_col'))}
  FROM query_table(tbl::VARCHAR)
), __z_r_g AS (
  SELECT *, row_number() OVER (PARTITION BY entity ORDER BY ts)
          - row_number() OVER (PARTITION BY entity, flag ORDER BY ts) AS __z_grp
  FROM __z_r_s WHERE ts IS NOT NULL
)
SELECT entity, flag, min(ts) AS "start", max(ts) AS "end", count(*) AS n_samples
FROM __z_r_g GROUP BY entity, flag, __z_grp
"""

_COUNTER_DELTAS = f"""
CREATE MACRO counter_deltas(tbl, entity_col, ts_col, value_col, max_gap_s := NULL) AS TABLE
WITH __z_cd_s AS (
  SELECT {_cols(('entity', 'entity_col'), ('ts', 'ts_col'), ('value', 'value_col'))}
  FROM query_table(tbl::VARCHAR)
), __z_cd_l AS (
  SELECT *, lag(value) OVER __z_w AS __z_pv, lag(ts) OVER __z_w AS __z_pt
  FROM __z_cd_s WHERE ts IS NOT NULL AND value IS NOT NULL
  WINDOW __z_w AS (PARTITION BY entity ORDER BY ts)
), __z_cd_g AS (
  SELECT *, coalesce(__z_to_s(ts) - __z_to_s(__z_pt) > max_gap_s, false) AS gap
  FROM __z_cd_l WHERE __z_pv IS NOT NULL
)
SELECT entity, ts, value,
       CASE WHEN gap THEN NULL WHEN value < __z_pv THEN value ELSE value - __z_pv END AS delta,
       (NOT gap AND value < __z_pv) AS reset,
       gap
FROM __z_cd_g
"""

_BIN_WITH_COVERAGE = f"""
CREATE MACRO bin_with_coverage(tbl, entity_col, ts_col, bin_width, expected_every,
                               threshold := 0.8) AS TABLE
WITH __z_bc_s AS (
  SELECT {_cols(('entity', 'entity_col'), ('ts', 'ts_col'))}
  FROM query_table(tbl::VARCHAR)
), __z_bc_b AS (
  SELECT entity, time_bucket(bin_width::INTERVAL, ts) AS bin_start, count(*) AS n_obs
  FROM __z_bc_s WHERE ts IS NOT NULL GROUP BY ALL
), __z_bc_r AS (
  SELECT min(bin_start) AS lo, max(bin_start) AS hi FROM __z_bc_b
), __z_bc_g AS (
  SELECT unnest(generate_series(lo, hi, bin_width::INTERVAL)) AS bin_start FROM __z_bc_r
), __z_bc_e AS (
  SELECT DISTINCT entity FROM __z_bc_b
)
SELECT e.entity, g.bin_start, coalesce(b.n_obs, 0) AS n_obs,
       least(1.0, coalesce(b.n_obs, 0)
                  / (epoch(bin_width::INTERVAL) / epoch(expected_every::INTERVAL))) AS coverage,
       coverage >= threshold AS known
FROM __z_bc_e e CROSS JOIN __z_bc_g g
LEFT JOIN __z_bc_b b ON b.entity = e.entity AND b.bin_start = g.bin_start
"""

_ENTITY_DISTRIBUTION = f"""
CREATE MACRO entity_distribution(tbl, entity_col, metric_col) AS TABLE
WITH __z_ed_s AS (
  SELECT {_cols(('entity', 'entity_col'), ('__z_m', 'metric_col'))}
  FROM query_table(tbl::VARCHAR)
), __z_ed_p AS (
  SELECT entity, sum(__z_m) AS v FROM __z_ed_s GROUP BY entity
), __z_ed_o AS (
  SELECT v,
         row_number() OVER (ORDER BY v) AS i,
         row_number() OVER (ORDER BY v DESC) AS rd,
         count(*) OVER () AS n
  FROM __z_ed_p WHERE v IS NOT NULL
)
SELECT count(*) AS n_entities,
       sum(v) AS total,
       avg(v) AS mean,
       median(v) AS median,
       quantile_cont(v, 0.1) AS p10,
       quantile_cont(v, 0.9) AS p90,
       stddev_samp(v) AS sd,
       max(v) AS max,
       sum(CASE WHEN rd <= ceil(n * 0.1) THEN v ELSE 0 END) / nullif(sum(v), 0) AS top10_share,
       sum((2 * i - n - 1) * v) / nullif(max(n) * sum(v), 0) AS gini
FROM __z_ed_o
"""

# Installed in this order: later macros call earlier ones.
MACROS: Tuple[str, ...] = (
    _TO_S,
    _INTERVALS_UNION,
    _INTERVAL_DURATION,
    _INTERVALS_INTERSECT,
    _INTERVALS_SUBTRACT,
    _RUNS,
    _COUNTER_DELTAS,
    _BIN_WITH_COVERAGE,
    _ENTITY_DISTRIBUTION,
)

# Table macros callable from user SQL, and which arguments are tables.
TABLE_ARGS: Dict[str, Tuple[int, ...]] = {
    "bin_with_coverage": (0,),
    "counter_deltas": (0,),
    "intervals_union": (0,),
    "intervals_intersect": (0, 1),
    "intervals_subtract": (0, 1),
    "interval_duration": (0,),
    "runs": (0,),
    "entity_distribution": (0,),
}

# One line each, for the tool description.
DESCRIPTIONS: Dict[str, str] = {
    "bin_with_coverage": (
        "bin_with_coverage(t, 'entity', 'ts', '1 hour', '1 minute', threshold := 0.8) "
        "-> entity, bin_start, n_obs, coverage, known. Every entity gets every bin; "
        "a silent bin has coverage 0 and known = false (unknown, not zero)."
    ),
    "counter_deltas": (
        "counter_deltas(t, 'entity', 'ts', 'value', max_gap_s := NULL) -> entity, ts, "
        "value, delta, reset, gap. A decrease is a reset (delta = new value); a gap "
        "over max_gap_s seconds gives delta NULL."
    ),
    "intervals_union": (
        "intervals_union(t, 'entity', 'start', 'end') -> entity, start, end. "
        "Overlapping or touching intervals merge, so each instant counts once."
    ),
    "intervals_intersect": (
        "intervals_intersect(a, b, entity_col := 'entity', start_col := 'start', "
        "end_col := 'end') -> entity, start, end: time in both."
    ),
    "intervals_subtract": (
        "intervals_subtract(a, b, ...) -> entity, start, end: time in a and not in b."
    ),
    "interval_duration": (
        "interval_duration(t, 'entity', 'start', 'end') -> entity, total_s, "
        "n_intervals, longest_s, after merging overlaps."
    ),
    "runs": (
        "runs(t, 'entity', 'ts', 'flag') -> entity, flag, start, end, n_samples: "
        "consecutive samples with the same flag (start/end are sample times)."
    ),
    "entity_distribution": (
        "entity_distribution(t, 'entity', 'metric') -> n_entities, total, mean, median, "
        "p10, p90, sd, max, top10_share (top 10% of entities), gini. Report it with "
        "any fleet total; total equals the sum of the metric."
    ),
}


def run(sql: str, inputs: Mapping, **kwargs):
    """``engine.run_query`` with the primitives installed and guarded."""
    from .engine import run_query
    return run_query(
        sql, inputs,
        macros=MACROS,
        macro_names=TABLE_ARGS.keys(),
        macro_table_args=TABLE_ARGS,
        **kwargs,
    )
