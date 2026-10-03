from decimal import Decimal
import json

import duckdb
import pytest

from revenue_platform.runtime import (
    ROOT,
    MODELS,
    add_correction,
    assert_expected,
    load_local,
    local_snapshot,
    records,
    run_dbt,
)


def query(db, sql):
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_independent_quarter_and_account_bridge(built):
    assert_expected(local_snapshot(built))


def test_fanout_is_detected_by_dbt(database, tmp_path):
    # A real common defect: each invoice line is repeated for every payment on its invoice.
    with duckdb.connect(str(database)) as con:
        wrong = con.execute(
            "select sum(i.amount_cents) from analytics.fct_invoice_lines i join analytics.fct_payment_allocations p using(invoice_id)"
        ).fetchone()[0]
        assert wrong == 2300000
        assert wrong != 1600000
        con.execute("update analytics.mart_flows set invoiced_cents=invoiced_cents*2")
    with pytest.raises(RuntimeError, match="dbt failed"):
        run_dbt(database, tmp_path, test_only="assert_flow_conservation")


def test_historical_owner_boundary_and_current_owner_difference(built):
    rows = query(
        built,
        "select region,bookings_cents from analytics.mart_flows where account_id='A1' and bookings_cents<>0 order by event_date",
    )
    assert rows == [("North", 1200000), ("East", -200000)]
    assert query(
        built,
        "select region from analytics.mart_pipeline where account_id='A1' and date_day='2026-02-14'",
    ) == [("North",)]
    assert query(
        built,
        "select region from analytics.mart_pipeline where account_id='A1' and date_day='2026-02-15'",
    ) == [("East",)]


def test_booking_amendment_and_partial_credit_remain_separate(built):
    assert query(
        built, "select sum(amount_cents) from analytics.fct_booking_events where account_id='A1'"
    ) == [(1000000,)]
    assert query(
        built, "select sum(amount_cents) from analytics.fct_invoice_lines where account_id='A1'"
    ) == [(1100000,)]
    assert query(
        built, "select amount_cents from analytics.fct_invoice_lines where line_type='credit'"
    ) == [(-100000,)]


def test_ratio_uses_counts_not_average_rates(built):
    assert query(
        built,
        "select value,numerator,denominator,excluded from analytics.mart_metric_values where metric_id='cohort_win_rate' and region='ALL'",
    ) == [(Decimal("0.20000000"), Decimal("1"), Decimal("5"), 1)]
    average = query(
        built, "select avg(win_rate) from analytics.mart_cohorts where mature_opportunities>0"
    )[0][0]
    assert abs(float(average) - 0.2) > 0.01


def test_immature_cohort_is_not_a_loss(built):
    assert query(
        built,
        "select opportunities,mature_opportunities,wins,win_rate from analytics.mart_cohorts where cohort_month='2026-03-01'",
    ) == [(1, 0, 0, None)]


def test_pipeline_is_not_additive_over_time(built):
    across_days = query(built, "select sum(pipeline_cents)/100.0 from analytics.mart_pipeline")[0][
        0
    ]
    assert across_days != 4000
    assert query(
        built,
        "select value from analytics.mart_metric_values where metric_id='open_pipeline' and region='ALL'",
    ) == [(Decimal("4000"),)]


def test_arr_eligibility_and_end_exclusive(built):
    rows = query(
        built,
        "select contract_id,arr_cents from analytics.fct_subscription_period where date_day='2026-03-31' order by contract_id",
    )
    assert rows == [("S1", 1200000), ("S2", 600000), ("S3", 0), ("S4", 0), ("S5", 0), ("S6", 0)]
    assert query(
        built,
        "select arr_cents from analytics.fct_subscription_period where contract_id='S6' and date_day='2026-02-28'",
    ) == [(1200000,)]


@pytest.mark.parametrize(
    "mutation,selector",
    [
        ("delete from raw.coverage where source_name='invoice_lines'", "assert_source_coverage"),
        (
            "update raw.coverage set complete_through='2026-03-15' where source_name='opportunity_events'",
            "assert_source_coverage",
        ),
        (
            "update analytics.fct_booking_events set account_id='UNKNOWN' where booking_event_id='B1'",
            "assert_account_references",
        ),
        (
            "update analytics.dim_account set valid_to='2026-03-01' where account_version_id='A1_1'",
            "assert_scd_intervals",
        ),
        (
            "update analytics.stg_subscriptions set billing_months=0 where contract_id='S1'",
            "assert_contract_validity",
        ),
        (
            "update analytics.fct_payment_allocations set invoice_id='UNKNOWN' where allocation_id='P1'",
            "assert_payment_invoice_reference",
        ),
    ],
)
def test_invalid_inputs_are_rejected(database, tmp_path, mutation, selector):
    if "update analytics.stg_subscriptions" in mutation:
        mutation = mutation.replace("analytics.stg_subscriptions", "raw.subscription_contracts")
    with duckdb.connect(str(database)) as con:
        con.execute(mutation)
    with pytest.raises(RuntimeError, match="dbt failed"):
        run_dbt(database, tmp_path, test_only=selector)


def test_backdated_correction_incremental_full_equivalence(tmp_path):
    inc = tmp_path / "inc.duckdb"
    load_local(inc)
    run_dbt(inc, tmp_path / "base")
    assert query(
        inc, "select sum(pipeline_cents) from analytics.mart_pipeline where date_day='2026-02-04'"
    ) == [(1200000,)]
    add_correction(inc)
    run_dbt(inc, tmp_path / "incremental")
    assert query(
        inc, "select sum(pipeline_cents) from analytics.mart_pipeline where date_day='2026-02-04'"
    ) == [(2100000,)]
    clean = tmp_path / "clean.duckdb"
    load_local(clean, late=True)
    run_dbt(clean, tmp_path / "clean", full_refresh=True)
    for model in MODELS:
        with (
            duckdb.connect(str(inc), read_only=True) as a,
            duckdb.connect(str(clean), read_only=True) as b,
        ):
            assert records(a.execute(f"select * from analytics.{model} order by all")) == records(
                b.execute(f"select * from analytics.{model} order by all")
            ), model


def test_forward_date_correction_removes_obsolete_snapshot_keys(database, tmp_path):
    with duckdb.connect(str(database)) as con:
        con.execute(
            "insert into raw.opportunity_events select event_id,opportunity_id,account_id,cast('2026-03-20' as date),cast('2026-03-20' as date),stage,amount_cents,origin,3 from raw.opportunity_events where event_id='E5'"
        )
    run_dbt(database, tmp_path)
    assert query(
        database,
        "select count(*) from analytics.fct_opportunity_daily where opportunity_id='O3' and date_day<'2026-03-20'",
    ) == [(0,)]


def test_metric_catalog_contains_full_contracts():
    required = {
        "business_definition",
        "owner",
        "grain",
        "time_basis",
        "filters",
        "permitted_dimensions",
        "null_behavior",
        "additivity",
        "version",
        "caveats",
    } - {"business_definition"} | {"definition"}
    catalog = json.loads((ROOT / "metrics/catalog.json").read_text())
    assert len(catalog) == 7
    assert all(required <= d.keys() for d in catalog)
