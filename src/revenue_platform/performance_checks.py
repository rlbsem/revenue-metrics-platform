"""Independent complete-multiset comparisons using sorted, lossless typed exports."""

from pathlib import Path
import re
import tempfile
import time

import duckdb

from revenue_platform.runtime import MODELS
from revenue_platform.selective import connect


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
    # Every non-null value is quoted. The null token remains unquoted, so NULL,
    # empty text and a literal __NULL__ string cannot alias. DuckDB writes
    # round-trip numeric text. Restrict the scalar types instead of assuming
    # arbitrary nested values have a lossless textual serialization.
    con.execute(
        f"copy (select * from analytics.{name} order by all) to '{escaped}' "
        "(format csv, header false, force_quote *, null '__NULL__')"
    )


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
                a, b = folder / "left.csv", folder / "right.csv"
                exact_export(con, name, a)
                exact_export(independent, name, b)
                if not equal_files(a, b):
                    raise AssertionError("Complete multiset differs: " + name)
                rows.append(
                    {
                        "model": name,
                        "rows": counts[0],
                        "exact_multiset_equal": True,
                        "differences": 0,
                        "compared_export_bytes": a.stat().st_size,
                    }
                )
                a.unlink()
                b.unlink()
    return {
        "method": "Complete sorted typed CSV exports; every byte compared; forced non-null quoting; not hash signatures",
        "seconds": time.perf_counter() - started,
        "models": rows,
    }
