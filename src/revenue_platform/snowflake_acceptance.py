"""Duplicate-aware equality checks for native Snowflake dbt model results.

An ordinary EXCEPT on model rows loses duplicates. Grouping complete rows with
their frequencies first makes a symmetrical EXCEPT sensitive to multiplicity.
The check uses actual values, not hashes, and retains no source rows in evidence.
"""

import re


def _identifier(value):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError("Invalid Snowflake identifier")
    return value.upper()


def multiset_difference_sql(left_schema, right_schema, model):
    left = f"{_identifier(left_schema)}.{_identifier(model)}"
    right = f"{_identifier(right_schema)}.{_identifier(model)}"
    return (
        "SELECT COUNT(*) FROM ("
        f"SELECT *, COUNT(*) AS _RMP_MULTIPLICITY FROM {left} GROUP BY ALL "
        "EXCEPT "
        f"SELECT *, COUNT(*) AS _RMP_MULTIPLICITY FROM {right} GROUP BY ALL"
        ") AS ROW_DIFFERENCE"
    )


def compare_model_multisets(cursor, incremental_schema, full_schema, models, query_ids=None):
    """Require exact equality of every complete-row multiset in both directions."""
    report = []
    for model in models:
        incremental = f"{_identifier(incremental_schema)}.{_identifier(model)}"
        full = f"{_identifier(full_schema)}.{_identifier(model)}"

        cursor.execute(f"SELECT COUNT(*) FROM {incremental}")
        incremental_rows = cursor.fetchone()[0]
        if query_ids is not None:
            query_ids.append(cursor.sfqid)
        cursor.execute(f"SELECT COUNT(*) FROM {full}")
        full_rows = cursor.fetchone()[0]
        if query_ids is not None:
            query_ids.append(cursor.sfqid)

        cursor.execute(multiset_difference_sql(incremental_schema, full_schema, model))
        incremental_only_groups = cursor.fetchone()[0]
        if query_ids is not None:
            query_ids.append(cursor.sfqid)
        cursor.execute(multiset_difference_sql(full_schema, incremental_schema, model))
        full_only_groups = cursor.fetchone()[0]
        if query_ids is not None:
            query_ids.append(cursor.sfqid)

        if incremental_rows != full_rows or incremental_only_groups or full_only_groups:
            raise AssertionError(
                f"Snowflake incremental/full disagreement for {model}: "
                f"rows {incremental_rows}/{full_rows}; "
                f"different groups {incremental_only_groups}/{full_only_groups}"
            )
        report.append(
            {
                "model": model,
                "incremental_rows": incremental_rows,
                "full_rows": full_rows,
                "incremental_only_groups": incremental_only_groups,
                "full_only_groups": full_only_groups,
                "exact_multiset_equal": True,
            }
        )
    return report
