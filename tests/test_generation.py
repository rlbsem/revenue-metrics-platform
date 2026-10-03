"""Independent source properties complement (never replace) the exact fixture oracle."""

import json

import duckdb
import pytest

from revenue_platform.generator import configuration, generate
from revenue_platform.runtime import add_correction, load_local, run_dbt


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("generated")
    config = {**configuration("enterprise"), "accounts": 500}
    manifest = generate(root / "sources", config=config)
    db = root / "warehouse.duckdb"
    load_local(db, source_dir=root / "sources")
    return root, db, manifest


def test_same_seed_is_byte_identical_and_other_seed_changes_sources(generated):
    root, _, manifest = generated
    assert generate(root / "repeat", config=manifest["config"]) == manifest
    other = generate(root / "other-seed", config={**manifest["config"], "seed": 71})
    assert other["source_fingerprint"] != manifest["source_fingerprint"]
    with pytest.raises(FileExistsError):
        generate(root / "repeat", config=manifest["config"])


def test_profile_configuration_and_manifest(generated):
    root, _, manifest = generated
    assert json.loads((root / "sources/dataset-manifest.json").read_text()) == manifest
    assert 50000 <= configuration("enterprise")["accounts"] <= 80000
    assert configuration("stress")["accounts"] == 3 * configuration("enterprise")["accounts"]
    assert manifest["row_counts"]["late_opportunity_events"] > 0


def test_source_economics_and_descriptive_references(generated):
    _, db, _ = generated
    with duckdb.connect(str(db)) as con:
        from revenue_platform.scenario_checks import validate_scenario

        assert not any(validate_scenario(con).values())
        assert (
            con.execute(
                "select count(distinct extract(year from acquired_on)) from raw.contract_attributes"
            ).fetchone()[0]
            >= 6
        )
        assert (
            con.execute(
                "select count(*) from raw.subscription_contracts where end_date>'2026-03-31' and start_date<'2026-01-01'"
            ).fetchone()[0]
            > 250
        )
        assert (
            con.execute("select count(distinct segment) from raw.account_attributes").fetchone()[0]
            == 3
        )
        assert (
            con.execute("select count(distinct product) from raw.contract_attributes").fetchone()[0]
            == 3
        )
        assert (
            con.execute(
                "select count(*) from raw.account_history where valid_to='2026-02-15'"
            ).fetchone()[0]
            > 0
        )


def test_generated_sources_execute_dbt_and_correction_is_not_a_noop(generated):
    root, db, _ = generated
    run_dbt(db, root / "base")
    with duckdb.connect(str(db)) as con:
        before = con.execute("select count(*) from analytics.fct_opportunity_daily").fetchone()[0]
    add_correction(db, root / "sources")
    run_dbt(db, root / "corrected")
    with duckdb.connect(str(db)) as con:
        after = con.execute("select count(*) from analytics.fct_opportunity_daily").fetchone()[0]
    assert after > before


@pytest.mark.parametrize(
    "mutation,selector",
    [
        (
            "update analytics.fct_invoice_lines set contract_id='UNKNOWN' where line_id='L1'",
            "assert_contract_references",
        ),
        (
            "update analytics.fct_payment_allocations set amount_cents=99999999 where allocation_id='P1'",
            "assert_invoice_settlement",
        ),
    ],
)
def test_financial_relationship_failures_rejected(database, tmp_path, mutation, selector):
    with duckdb.connect(str(database)) as con:
        con.execute(mutation)
    with pytest.raises(RuntimeError, match="dbt failed"):
        run_dbt(database, tmp_path, test_only=selector)


def test_bookings_cannot_use_lost_opportunities(generated):
    _, db, _ = generated
    from revenue_platform.scenario_checks import validate_scenario

    with duckdb.connect(str(db)) as con:
        con.execute("begin transaction")
        con.execute(
            "update raw.commercial_events set opportunity_id=(select opportunity_id from raw.opportunity_events where stage='lost' limit 1) where event_type='renewal'"
        )
        try:
            with pytest.raises(AssertionError, match="won_commercial_authority"):
                validate_scenario(con)
        finally:
            con.execute("rollback")
