"""Analysis table macros (app/utils/analytics/primitives.py).

Fixtures are the hand-checked cases from design/local-analytics.md, Tests.
Everything runs through primitives.run, i.e. the guarded, locked engine, so
these also prove the macros work after the configuration lock.
"""
import pytest

pytest.importorskip("duckdb")
pytest.importorskip("sqlglot")

from app.utils.analytics.engine import AnalyticsError  # noqa: E402
from app.utils.analytics.primitives import DESCRIPTIONS, TABLE_ARGS, run  # noqa: E402


@pytest.fixture
def csv(tmp_path):
    def make(name, text):
        p = tmp_path / f"{name}.csv"
        p.write_text(text.strip() + "\n")
        return p
    return make


def rows(sql, **inputs):
    return run(sql, inputs).rows


def test_every_table_macro_is_described():
    assert set(DESCRIPTIONS) == set(TABLE_ARGS)


# -- intervals ---------------------------------------------------------------

def test_union_merges_overlaps_and_counts_each_instant_once(csv):
    t = csv("t", "ct,s,e\na,0,10\na,5,15\na,20,25\nb,0,1")
    assert rows("SELECT * FROM intervals_union(t, 'ct', 's', 'e') ORDER BY 1, 2", t=t) == [
        ("a", 0, 15), ("a", 20, 25), ("b", 0, 1)]
    assert rows("SELECT entity, total_s, n_intervals, longest_s FROM "
                "interval_duration(t, 'ct', 's', 'e') ORDER BY 1", t=t) == [
        ("a", 20.0, 2, 15.0), ("b", 1.0, 1, 1.0)]


def test_union_merges_touching_intervals(csv):
    t = csv("t", "ct,s,e\na,0,5\na,5,8")
    assert rows("SELECT * FROM intervals_union(t, 'ct', 's', 'e')", t=t) == [("a", 0, 8)]


def test_duration_of_timestamps_is_wall_clock_seconds(csv):
    # 2026-03-08 is a US DST change; naive timestamps must not lose the hour.
    t = csv("t", "ct,s,e\na,2026-03-08 01:00:00,2026-03-08 03:00:00\n"
                 "a,2026-03-08 02:30:00,2026-03-08 03:30:00")
    assert rows("SELECT total_s FROM interval_duration(t, 'ct', 's', 'e')", t=t) == [(9000.0,)]


def test_intersect_and_subtract(csv):
    a = csv("a", "entity,start,end\nx,0,10\nx,20,30\nx,40,50\ny,0,5")
    b = csv("b", "entity,start,end\nx,5,25\nx,26,27")
    assert rows("SELECT * FROM intervals_intersect(a, b) ORDER BY 1, 2", a=a, b=b) == [
        ("x", 5, 10), ("x", 20, 25), ("x", 26, 27)]
    assert rows("SELECT * FROM intervals_subtract(a, b) ORDER BY 1, 2", a=a, b=b) == [
        ("x", 0, 5), ("x", 25, 26), ("x", 27, 30), ("x", 40, 50), ("y", 0, 5)]


# -- runs and counters -------------------------------------------------------

def test_runs_turn_samples_into_incidents(csv):
    t = csv("t", "ct,ts,bad\na,1,false\na,2,true\na,3,true\na,4,false\na,5,true")
    got = rows("SELECT flag, start, \"end\", n_samples FROM runs(t, 'ct', 'ts', 'bad') "
               "ORDER BY start", t=t)
    assert got == [(False, 1, 1, 1), (True, 2, 3, 2), (False, 4, 4, 1), (True, 5, 5, 1)]


def test_counter_decrease_is_a_reset_not_a_negative_delta(csv):
    t = csv("t", "ct,ts,v\na,1,10\na,2,20\na,3,3\na,4,8")
    got = rows("SELECT delta, reset, gap FROM counter_deltas(t, 'ct', 'ts', 'v') ORDER BY ts", t=t)
    assert got == [(10, False, False), (3, True, False), (5, False, False)]


def test_counter_gap_over_limit_gives_null_delta(csv):
    t = csv("t", "ct,ts,v\na,2026-01-01 00:00:00,1\na,2026-01-01 00:01:00,2\n"
                 "a,2026-01-01 00:06:00,9")
    got = rows("SELECT delta, gap FROM counter_deltas(t, 'ct', 'ts', 'v', max_gap_s := 90) "
               "ORDER BY ts", t=t)
    assert got == [(1, False), (None, True)]
    # Without a limit the same row is an ordinary delta (positive control).
    assert rows("SELECT delta FROM counter_deltas(t, 'ct', 'ts', 'v') ORDER BY ts", t=t) == [
        (1,), (7,)]


# -- coverage ----------------------------------------------------------------

def test_silent_bin_is_unknown_not_zero(csv):
    lines = ["ct,ts"]
    lines += [f"a,2026-01-01 00:{m:02d}:00" for m in range(60)]   # hour 0: full
    lines += [f"a,2026-01-01 02:{m:02d}:00" for m in range(30)]   # hour 2: half
    lines += ["b,2026-01-01 00:00:00"]
    t = csv("t", "\n".join(lines))
    got = rows("SELECT entity, hour(bin_start), n_obs, coverage, known FROM "
               "bin_with_coverage(t, 'ct', 'ts', '1 hour', '1 minute') ORDER BY 1, 2", t=t)
    assert got[:3] == [("a", 0, 60, 1.0, True), ("a", 1, 0, 0.0, False), ("a", 2, 30, 0.5, False)]
    # b reported once, but it still gets every bin of the range.
    assert [r[1:4] for r in got[3:]] == [(0, 1, pytest.approx(1 / 60)), (1, 0, 0.0), (2, 0, 0.0)]


def test_coverage_threshold_is_overridable(csv):
    t = csv("t", "ct,ts\n" + "\n".join(f"a,2026-01-01 00:{m:02d}:00" for m in range(30)))
    q = "SELECT known FROM bin_with_coverage(t, 'ct', 'ts', '1 hour', '1 minute'{})"
    assert rows(q.format(""), t=t) == [(False,)]
    assert rows(q.format(", threshold := 0.5"), t=t) == [(True,)]


# -- distribution ------------------------------------------------------------

def test_distribution_totals_reconcile_and_equal_values_have_no_skew(csv):
    t = csv("t", "ct,v\na,1\na,2\nb,3\nc,3")
    (r,) = rows("SELECT n_entities, total, mean, median, max, gini FROM "
                "entity_distribution(t, 'ct', 'v')", t=t)
    assert r == (3, 9, 3.0, 3.0, 3, 0.0)
    assert rows("SELECT sum(v) FROM t", t=t) == [(9,)]


def test_one_dominant_entity_raises_gini_and_top10_share(csv):
    t = csv("t", "ct,v\n" + "\n".join(f"e{i},0" for i in range(9)) + "\nbig,100")
    (gini, top) = rows("SELECT gini, top10_share FROM entity_distribution(t, 'ct', 'v')", t=t)[0]
    assert gini == pytest.approx(0.9)
    assert top == 1.0
    even = csv("even", "ct,v\n" + "\n".join(f"e{i},5" for i in range(10)))
    assert rows("SELECT gini, top10_share FROM entity_distribution(even, 'ct', 'v')",
                even=even) == [(0.0, 0.1)]


# -- conventions -------------------------------------------------------------

def test_column_names_match_exactly_not_as_regex(csv):
    # COLUMNS('ct') is a regex: it also matches xct, and takes it first.
    t = csv("t", "xct,ct,s,e\nWRONG,a,0,1")
    assert rows("SELECT * FROM intervals_union(t, 'ct', 's', 'e')", t=t) == [("a", 0, 1)]


def test_missing_column_is_a_binder_error(csv):
    t = csv("t", "ct,s,e\na,0,1")
    with pytest.raises(AnalyticsError) as e:
        run("SELECT * FROM intervals_union(t, 'nope', 's', 'e')", {"t": t})
    assert e.value.kind == "binder"
    assert e.value.columns == {"t": ["ct", "s", "e"]}


def test_inputs_named_like_common_words_are_not_shadowed(csv):
    # A macro's own CTE names shadow the caller's tables inside the macro: if
    # intervals_subtract named its first CTE "a", a right-hand input called
    # "a" would resolve to the left side and the result would be empty.
    x = csv("x", "entity,start,end\nk,0,10")
    a = csv("a", "entity,start,end\nk,2,3")
    assert rows("SELECT * FROM intervals_subtract(x, a) ORDER BY 2", x=x, a=a) == [
        ("k", 0, 2), ("k", 3, 10)]


def test_every_internal_cte_is_prefixed():
    import re
    from app.utils.analytics.primitives import MACROS
    names = set()
    for m in MACROS:
        names |= set(re.findall(r"(?:WITH|\),)\s+(\w+)\s+AS\s*\(", m))
    assert names, "no CTEs found; the pattern no longer matches the macro text"
    assert {n for n in names if not n.startswith("__z_")} == set()


def test_with_table_can_feed_a_macro(csv):
    t = csv("t", "ct,s,e,keep\na,0,10,true\na,5,15,false")
    assert rows("WITH k AS (SELECT * FROM t WHERE keep) "
                "SELECT * FROM intervals_union(k, 'ct', 's', 'e')", t=t) == [("a", 0, 10)]


@pytest.mark.parametrize("arg", ["'t'", "'/etc/passwd'", "nope", "main.t"])
def test_table_argument_must_be_a_registered_name(csv, arg):
    t = csv("t", "ct,s,e\na,0,1")
    with pytest.raises(AnalyticsError) as e:
        run(f"SELECT * FROM intervals_union({arg}, 'ct', 's', 'e')", {"t": t})
    assert e.value.kind == "rejected"
    assert "Argument 1 of intervals_union" in str(e.value)


def test_second_table_argument_is_checked_too(csv):
    a = csv("a", "entity,start,end\nx,0,1")
    with pytest.raises(AnalyticsError, match="Argument 2"):
        run("SELECT * FROM intervals_subtract(a, '/etc/passwd')", {"a": a})
