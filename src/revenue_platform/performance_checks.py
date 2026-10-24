"""Independent complete-multiset comparisons; no consumer success flags or hash signatures."""

from pathlib import Path
import time

import duckdb

from revenue_platform.runtime import MODELS
from revenue_platform.selective import connect


def compare(left, right, *, buckets=16):
    started = time.perf_counter()
    rows = []
    with connect(left) as con, duckdb.connect(str(right), read_only=True) as independent:
        independent.execute("set threads=1; set memory_limit='2GB'")
        escaped = str(Path(right).resolve()).replace("'", "''")
        con.execute(f"attach '{escaped}' as reference (read_only)")
        for name in MODELS:
            a, b = f"analytics.{name}", f"reference.analytics.{name}"
            description = con.execute(f"describe select * from {a}").fetchall()
            if description != independent.execute(f"describe select * from {a}").fetchall():
                raise AssertionError("Column contract differs: " + name)
            cols = [r[0] for r in description]
            counts = [
                c.execute(f"select count(*) from {a}").fetchone()[0] for c in (con, independent)
            ]
            if counts[0] != counts[1]:
                raise AssertionError("Row count differs: " + name)
            is_view = (
                con.execute(
                    "select table_type from information_schema.tables where table_schema='analytics' and table_name=? and table_catalog=current_database()",
                    [name],
                ).fetchone()[0]
                == "VIEW"
            )
            if is_view:
                # Each view must resolve its own database's catalog-qualified raw
                # references. An attached alias could accidentally read the other DB.
                left_rows = con.execute(f"select * from {a} order by all")
                right_rows = independent.execute(f"select * from {a} order by all")
                while True:
                    batch = left_rows.fetchmany(2000)
                    if batch != right_rows.fetchmany(2000):
                        raise AssertionError("Complete multiset differs: " + name)
                    if not batch:
                        break
                rows.append(
                    {
                        "model": name,
                        "rows": counts[0],
                        "partitions": 1,
                        "method": "Exact sorted streams on separate source-bound connections",
                        "exact_multiset_equal": True,
                        "differences": 0,
                    }
                )
                continue
            key = next((k for k in ("opportunity_id", "account_id") if k in cols), None)
            partitions = buckets if key and counts[0] > 500000 else 1
            differences = 0
            for bucket in range(partitions):
                # Hash chooses a bucket only. Every row, including duplicates/nulls,
                # then participates in exact SQL EXCEPT ALL in both directions.
                where = (
                    f" where coalesce(hash({key}),0)%{partitions}={bucket}"
                    if partitions > 1
                    else ""
                )
                differences += con.execute(
                    f"select count(*) from ((select * from {a}{where} except all select * from {b}{where}) "
                    f"union all (select * from {b}{where} except all select * from {a}{where}))"
                ).fetchone()[0]
            if differences:
                raise AssertionError(f"Complete multiset differs: {name}, {differences}")
            rows.append(
                {
                    "model": name,
                    "rows": counts[0],
                    "partitions": partitions,
                    "exact_multiset_equal": True,
                    "differences": differences,
                }
            )
    return {
        "method": "Complete bidirectional EXCEPT ALL; deterministic buckets only bound memory; not signatures",
        "seconds": time.perf_counter() - started,
        "models": rows,
    }
