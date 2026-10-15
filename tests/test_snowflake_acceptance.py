"""Credential-free acceptance helper tests; real Snowflake execution is separate."""

import pytest

from revenue_platform.snowflake_acceptance import (
    compare_model_multisets,
    multiset_difference_sql,
)


class FakeCursor:
    def __init__(self, values):
        self.values = iter(values)
        self.statements = []
        self.sfqid = None

    def execute(self, sql):
        self.statements.append(sql)
        self.sfqid = f"Q{len(self.statements)}"
        return self

    def fetchone(self):
        return (next(self.values),)


def test_multiset_difference_preserves_duplicate_frequency_and_null_rows():
    sql = multiset_difference_sql("build_a", "full_b", "fct_opportunity_daily")
    assert "COUNT(*) AS _RMP_MULTIPLICITY" in sql
    assert sql.count("GROUP BY ALL") == 2
    assert "EXCEPT" in sql
    assert "BUILD_A.FCT_OPPORTUNITY_DAILY" in sql
    assert "FULL_B.FCT_OPPORTUNITY_DAILY" in sql
    assert "TO_JSON" not in sql
    assert "HASH" not in sql


def test_exact_multiset_comparison_records_both_directions_and_query_ids():
    cursor = FakeCursor([12, 12, 0, 0, 5, 5, 0, 0])
    query_ids = []
    report = compare_model_multisets(
        cursor, "BUILD_X", "FULL_X", ["dim_account", "mart_metric_values"], query_ids
    )
    assert len(report) == 2
    assert all(item["exact_multiset_equal"] for item in report)
    assert len(cursor.statements) == 8
    assert query_ids == [f"Q{i}" for i in range(1, 9)]


@pytest.mark.parametrize("values", [[12, 12, 1, 0], [12, 12, 0, 1], [12, 13, 0, 0]])
def test_differences_abort_before_any_publication(values):
    cursor = FakeCursor(values)
    with pytest.raises(AssertionError, match="disagreement"):
        compare_model_multisets(cursor, "BUILD_X", "FULL_X", ["dim_account"])


@pytest.mark.parametrize("unsafe", ["x;drop table raw", "x.y", "", "x--comment"])
def test_untrusted_sql_identifiers_rejected(unsafe):
    with pytest.raises(ValueError, match="identifier"):
        multiset_difference_sql(unsafe, "FULL_X", "dim_account")
