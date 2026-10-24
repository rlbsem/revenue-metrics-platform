"""Independent complete-multiset comparisons using sorted, lossless typed exports."""

from pathlib import Path
import re
import tempfile
import time

import duckdb

from revenue_platform.runtime import MODELS
from revenue_platform.selective import connect

GRAINS = {
    "dim_account": "account_version_id",
    "dim_date": "date_day",
    "fct_booking_events": "booking_event_id",
    "fct_invoice_lines": "line_id",
    "fct_payment_allocations": "allocation_id",
    "fct_opportunity_daily": "opportunity_day_key",
    "fct_subscription_period": "contract_period_key",
    "stg_opportunities": "event_id",
    "stg_subscriptions": "contract_id",
    "mart_pipeline": "account_id,date_day,region,owner",
    "mart_arr": "account_id,date_day,region,owner",
    "mart_flows": "account_id,event_date,region,owner",
    "mart_cohorts": "cohort_month,region",
    "mart_metric_values": "metric_id,region,period_start,period_end",
    "mart_discrepancy_bridge": "account_id",
}


def equal_files(left, right):
    # Compare every byte, not digests. Duplicate rows remain duplicated.
    with left.open("rb") as a, right.open("rb") as b:
        while True:
            data = a.read(1024 * 1024)
            if data != b.read(1024 * 1024):
                return False
            if not data:
                return True


def exact_export(con, name, path):
    escaped = str(path.resolve()).replace("'", "''")
    grain = GRAINS.get(name)
    if (
        grain
        and con.execute(
            f"select exists(select {grain} from analytics.{name} group by {grain} having count(*)>1)"
        ).fetchone()[0]
    ):
        raise AssertionError("Declared grain not unique: " + name)
    columns = con.execute(f"describe select * from analytics.{name}").fetchall()
    # Preserve HUGEINT exactly: Parquet has no native signed 128-bit integer.
    # Other accepted types retain their native typed representation.
    selected = ",".join(
        (
            f'cast("{c[0]}" as varchar) as "{c[0]}"'
            if c[1] in ("HUGEINT", "UHUGEINT")
            else f'"{c[0]}"'
        )
        for c in columns
    )
    con.execute(
        f"copy (select {selected} from analytics.{name} order by {grain or 'all'}) to '{escaped}' (format parquet, compression snappy, row_group_size 10000)"
    )


def equal_exports(a, b):
    if equal_files(a, b):
        return True
    # Different valid encodings need not mean different data. Compare every typed
    # row through equal-sized Arrow batches; no floating tolerance or signatures.
    import pyarrow.parquet as pq

    left, right = pq.ParquetFile(a), pq.ParquetFile(b)
    if left.schema_arrow != right.schema_arrow:
        return False
    first, second = left.iter_batches(batch_size=10000), right.iter_batches(batch_size=10000)
    while True:
        x, y = next(first, None), next(second, None)
        if x is None or y is None:
            return x is None and y is None
        if not x.equals(y):
            return False


def compare(left, right):
    started = time.perf_counter()
    rows = []
    parent = Path(left).parent / "comparison-temp"
    parent.mkdir(exist_ok=True)
    with connect(left) as con, duckdb.connect(str(right), read_only=True) as independent:
        independent.execute("set threads=1; set memory_limit='2GB'")
        # Separate connections preserve each view's own catalog-qualified source
        # bindings. An attached alias can silently bind a view to the wrong raw DB.
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            folder = Path(directory)
            for name in MODELS:
                table = f"analytics.{name}"
                description = con.execute(f"describe select * from {table}").fetchall()
                if description != independent.execute(f"describe select * from {table}").fetchall():
                    raise AssertionError("Column contract differs: " + name)
                if any(
                    not re.fullmatch(
                        r"(?:VARCHAR|DATE|BOOLEAN|(?:U?TINY|U?SMALL|U?BIG|U?HUGE)?INT(?:EGER)?|DOUBLE|FLOAT|DECIMAL\(\d+,\d+\))",
                        r[1],
                    )
                    for r in description
                ):
                    raise ValueError("Unsupported exact export type: " + name)
                counts = [
                    c.execute(f"select count(*) from {table}").fetchone()[0]
                    for c in (con, independent)
                ]
                if counts[0] != counts[1]:
                    raise AssertionError("Row count differs: " + name)
                a, b = folder / "left.parquet", folder / "right.parquet"
                exact_export(con, name, a)
                exact_export(independent, name, b)
                if not equal_exports(a, b):
                    raise AssertionError("Complete multiset differs: " + name)
                rows.append(
                    {
                        "model": name,
                        "rows": counts[0],
                        "exact_multiset_equal": True,
                        "differences": 0,
                        "compared_export_bytes": a.stat().st_size,
                        "verified_sort_grain": GRAINS.get(name, "all columns"),
                    }
                )
                a.unlink()
                b.unlink()
    return {
        "method": "Complete grain-ordered typed Parquet exports; exact bytes or every Arrow value; HUGEINT text preserves precision; not signatures",
        "seconds": time.perf_counter() - started,
        "models": rows,
    }
