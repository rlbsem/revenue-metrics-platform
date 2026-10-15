"""Execute actual Snowflake ingestion/dbt/consumer/RBAC, or report pending without credentials."""

import argparse
import csv
import hashlib
import json
from contextlib import closing
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from revenue_platform.runtime import (
    ROOT,
    MODELS,
    TABLES,
    assert_expected,
    assert_enterprise,
    cursor_fingerprint,
    dataset_summary,
    canonical,
    make_payload,
    publish,
    records,
    run_dbt,
)
from revenue_platform.snowflake_acceptance import compare_model_multisets
from revenue_platform.snowflake_controls import (
    REQUIRED,
    connect,
    distinct_credentials,
    exclusive_run,
    expect_denied,
    load_csv,
    metric_projection,
    promote,
    publication_state,
    warehouse_preflight,
)


def save_node_results(workspace, label, results):
    """Allowlist executed dbt outcomes; raw manifests/logs remain private."""
    outcomes = [
        {
            "node": item["unique_id"],
            "status": item["status"],
            "seconds": item["execution_time"],
            "query_id": item.get("adapter_response", {}).get("query_id"),
        }
        for item in results["results"]
    ]
    (workspace / (label + "-results.json")).write_text(canonical(outcomes), encoding="utf-8")
    return outcomes


def _execute(workspace, require_cloud=False, status_only=False, profile="fixture", source_dir=None):
    workspace.mkdir(parents=True, exist_ok=True)
    missing = [key for key in REQUIRED if not os.environ.get(key)]
    if missing or status_only:
        result = {
            "status": "pending_credentials" if missing else "configured_not_executed",
            "executed_cloud": False,
            "missing_variable_names": missing,
            "note": "DuckDB tests do not establish Snowflake execution, grants or warehouse performance.",
        }
        (workspace / "verification.json").write_text(canonical(result), encoding="utf-8")
        print(canonical(result))
        return 2 if require_cloud else 0
    from revenue_platform.generator import ATTRIBUTES, generate

    manifest = None
    if profile == "fixture":
        source_dir = ROOT / "data"
    elif source_dir is None:
        source_dir = workspace / "sources"
        manifest = generate(source_dir, profile)
    else:
        manifest = json.loads((source_dir / "dataset-manifest.json").read_text())
    tables = {**TABLES, **(ATTRIBUTES if manifest else {})}
    schema = "BUILD_" + uuid4().hex.upper()
    rebuilt_schema = "FULL_" + uuid4().hex.upper()
    os.environ["REVENUE_BUILD_SCHEMA"] = schema
    query_ids = []
    timings = {}
    correction_file = source_dir / "late_opportunity_events.csv"
    with correction_file.open(newline="", encoding="utf-8") as stream:
        late_reader = csv.DictReader(stream)
        sample_opportunity = next(late_reader, {}).get("opportunity_id")
    if not sample_opportunity:
        raise AssertionError("No actual late correction was provided")

    def timed_build(label, folder, *, full_refresh):
        started = time.perf_counter()
        result = run_dbt(
            workspace / "unused.duckdb",
            workspace / folder,
            full_refresh=full_refresh,
            target="snowflake",
        )
        timings[label] = round(time.perf_counter() - started, 3)
        results = json.loads((result / "run_results.json").read_text())
        outcomes = save_node_results(workspace, label, results)
        if (
            any(row["status"] not in ("success", "pass") for row in outcomes)
            or sum(row["node"].startswith("model.") for row in outcomes) != len(MODELS)
            or sum(row["node"].startswith("test.") for row in outcomes) < 30
        ):
            raise AssertionError("Incomplete dbt build/test evidence")
        query_ids.extend(row["query_id"] for row in outcomes if row["query_id"])
        return result

    def sample_daily_fact():
        with closing(connect("RMP_BUILD")) as build_conn:
            sample_cursor = build_conn.cursor()
            rows = records(
                sample_cursor.execute(
                    f"select opportunity_day_key,opportunity_id,date_day,stage,amount_cents "
                    f"from {schema}.fct_opportunity_daily "
                    "where opportunity_id=%s order by opportunity_day_key",
                    (sample_opportunity,),
                )
            )
            query_ids.append(sample_cursor.sfqid)
            return rows

    with closing(connect("RMP_LOADER")) as conn:
        cursor = conn.cursor()
        for table, ddl in tables.items():
            # This command replaces synthetic source tables in the fixed demo database only.
            cursor.execute(f"create or replace table RAW.{table} ({ddl})")
            load_csv(cursor, source_dir / f"{table}.csv", table, ddl, query_ids)
            query_ids.append(cursor.sfqid)
        if manifest:
            from revenue_platform.scenario_checks import validate_scenario

            validate_scenario(cursor)

    # Prove an actual incremental repair: initial build must NOT contain the
    # correction. A full refresh with the correction preloaded proves neither
    # the incremental pre-hook nor stale-row replacement.
    timed_build("initial_full_build", "baseline", full_refresh=True)
    baseline_sample = sample_daily_fact()

    with closing(connect("RMP_LOADER")) as conn:
        cursor = conn.cursor()
        total_late_rows = load_csv(
            cursor, correction_file, "opportunity_events", TABLES["opportunity_events"], query_ids
        )
        if not total_late_rows:
            raise AssertionError("Correction batch was empty")
        if manifest:
            from revenue_platform.scenario_checks import validate_scenario

            validate_scenario(cursor)

    timed_build("corrected_incremental_build", "incremental", full_refresh=False)
    corrected_sample = sample_daily_fact()
    if baseline_sample == corrected_sample:
        raise AssertionError("Late correction did not change the sampled daily fact")

    # A genuinely independent rebuild has its own schema, not --full-refresh
    # over the incremental tables. Require duplicate-aware complete-row equality
    # for every model, including staging and marts, in both directions.
    os.environ["REVENUE_BUILD_SCHEMA"] = rebuilt_schema
    timed_build("independent_corrected_full_build", "full", full_refresh=True)
    os.environ["REVENUE_BUILD_SCHEMA"] = schema
    with closing(connect("RMP_BUILD")) as conn:
        comparisons = compare_model_multisets(
            conn.cursor(), schema, rebuilt_schema, MODELS, query_ids
        )
        (workspace / "model-comparisons.json").write_text(canonical(comparisons), encoding="utf-8")

    with closing(connect("RMP_BUILD")) as conn:
        cursor = conn.cursor()
        snapshot = {}
        for key, table, sort in [
            ("metrics", "mart_metric_values", "metric_id,region"),
            ("bridge", "mart_discrepancy_bridge", "account_id"),
            ("cohorts", "mart_cohorts", "cohort_month,region"),
        ]:
            snapshot[key] = records(
                cursor.execute(
                    f"select * from {schema}.{table} order by {sort}"
                    + (" limit 20" if key == "bridge" and manifest else "")
                )
            )
            query_ids.append(cursor.sfqid)
        snapshot["coverage"] = records(
            cursor.execute("select * from raw.coverage order by source_name")
        )
        if manifest:
            snapshot["dataset"] = dataset_summary(cursor, schema, manifest)
            snapshot["source_hashes"] = {}
            for table, ddl in tables.items():
                columns = ",".join(field.split()[0] for field in ddl.split(","))
                snapshot["source_hashes"][table] = cursor_fingerprint(
                    cursor.execute(f"select * from raw.{table} order by {columns}")
                )
            assert_enterprise(snapshot)
        else:
            snapshot["source_rows"] = {}
            for table, ddl in tables.items():
                columns = ",".join(field.split()[0] for field in ddl.split(","))
                snapshot["source_rows"][table] = records(
                    cursor.execute(f"select * from raw.{table} order by {columns}")
                )
            assert_expected(snapshot)
        payload = make_payload(snapshot, "1.0", "snowflake")
        from revenue_platform.runtime import digest

        release_id = digest(payload)
        cursor.execute(
            "create table if not exists CONSUMER.RELEASE_HISTORY (release_id varchar, payload variant)"
        )
        cursor.execute(
            "create table if not exists CONSUMER.CURRENT_RELEASE (release_id varchar, payload variant)"
        )
        # Stable reader view is initialized BEFORE any release transaction.
        cursor.execute("""create view if not exists CONSUMER.CURRENT_METRICS as
            select r.release_id, m.value:metric_id::varchar as metric_id,m.value:region::varchar as region,
            m.value:value::varchar as value,m.value:definition_version::varchar as definition_version
            from CONSUMER.CURRENT_RELEASE r,lateral flatten(input=>r.payload:metrics) m""")
        cursor.execute("grant select on view CONSUMER.CURRENT_METRICS to role RMP_CONSUMER")
        cursor.execute(
            f"CREATE VIEW {schema}.CANDIDATE_METRICS AS "
            f"SELECT metric_id,region,value FROM {schema}.MART_METRIC_VALUES"
        )
        cursor.execute(f"GRANT USAGE ON SCHEMA {schema} TO ROLE RMP_CONSUMER")
        cursor.execute(f"GRANT SELECT ON VIEW {schema}.CANDIDATE_METRICS TO ROLE RMP_CONSUMER")
        previous = publication_state(cursor)
        # A known target must exist before concealed-object authorization probes.
        cursor.execute("SELECT COUNT(*) FROM RAW.ACCOUNT_HISTORY").fetchone()
    denied = []
    with closing(connect("RMP_CONSUMER")) as conn:
        cursor = conn.cursor()
        visible = records(
            cursor.execute(
                f"select metric_id,region,value from {schema}.CANDIDATE_METRICS order by metric_id,region"
            )
        )
        assert metric_projection(visible) == metric_projection(snapshot["metrics"])
        query_ids.append(cursor.sfqid)
        for name, sql in [
            ("raw_read", "select * from RAW.ACCOUNT_HISTORY"),
            ("release_write", "update CONSUMER.CURRENT_RELEASE set release_id='invalid'"),
            ("build_role_escalation", "USE ROLE RMP_BUILD"),
            ("loader_role_escalation", "USE ROLE RMP_LOADER"),
        ]:
            denied.append(expect_denied(cursor, name, sql, query_ids))
    with closing(connect("RMP_BUILD")) as conn:
        cursor = conn.cursor()
        if not previous:
            # First-ever run: establish a real validated release before testing
            # preservation, rather than claiming an empty table is prior evidence.
            promote(cursor, release_id, payload)
            previous = publication_state(cursor)
        fault_release = "injected_failure_" + uuid4().hex
        # Actual DML rollback after deleting the current pointer must preserve it.
        try:
            promote(cursor, fault_release, payload, fail_before_commit=True)
        except RuntimeError as error:
            if str(error) != "deliberate_publication_failure":
                raise
        else:
            raise AssertionError("Publication fault did not execute")
        assert publication_state(cursor) == previous
        assert (
            cursor.execute(
                "SELECT COUNT(*) FROM CONSUMER.RELEASE_HISTORY WHERE release_id=%s",
                (fault_release,),
            ).fetchone()[0]
            == 0
        )
        query_ids.append(cursor.sfqid)
        # A real dbt quality failure must not modify the publication.
        coverage = cursor.execute(
            "SELECT complete_through FROM RAW.COVERAGE WHERE source_name='booking_events'"
        ).fetchone()[0]
    with closing(connect("RMP_LOADER")) as conn:
        conn.cursor().execute(
            "UPDATE RAW.COVERAGE SET complete_through='2025-12-31' "
            "WHERE source_name='booking_events'"
        )
    try:
        try:
            run_dbt(
                workspace / "unused.duckdb",
                workspace / "rejected-coverage",
                target="snowflake",
                test_only="assert_source_coverage",
            )
        except RuntimeError:
            failed = json.loads(
                (workspace / "rejected-coverage/dbt-target/run_results.json").read_text()
            )
            rejected = save_node_results(workspace, "rejected-coverage", failed)
            query_ids.extend(row["query_id"] for row in rejected if row["query_id"])
            if not any(
                r["status"] == "fail" and "assert_source_coverage" in r["unique_id"]
                for r in failed["results"]
            ):
                raise AssertionError("Expected data-quality failure was not observed")
        else:
            raise AssertionError("Incomplete coverage unexpectedly passed")
        with closing(connect("RMP_BUILD")) as conn:
            assert publication_state(conn.cursor()) == previous
    finally:
        with closing(connect("RMP_LOADER")) as conn:
            conn.cursor().execute(
                "UPDATE RAW.COVERAGE SET complete_through=%s WHERE source_name='booking_events'",
                (coverage,),
            )
    with closing(connect("RMP_BUILD")) as conn:
        cursor = conn.cursor()
        # Re-run the quality gate after restoring the synthetic source.
        run_dbt(
            workspace / "unused.duckdb",
            workspace / "restored-coverage",
            target="snowflake",
            test_only="assert_source_coverage",
        )
        restored = save_node_results(
            workspace,
            "restored-coverage",
            json.loads((workspace / "restored-coverage/dbt-target/run_results.json").read_text()),
        )
        if not restored or any(row["status"] != "pass" for row in restored):
            raise AssertionError("Restored coverage quality gate did not pass")
        query_ids.extend(row["query_id"] for row in restored if row["query_id"])
        promote(cursor, release_id, payload)
        assert publication_state(cursor) == [(release_id, payload)]
        promote(cursor, release_id, payload)  # repeated promotion must not duplicate history
        assert (
            cursor.execute(
                "SELECT COUNT(*) FROM CONSUMER.RELEASE_HISTORY WHERE release_id=%s", (release_id,)
            ).fetchone()[0]
            == 1
        )
        query_ids.append(cursor.sfqid)
    with closing(connect("RMP_CONSUMER")) as conn:
        visible = records(
            conn.cursor().execute(
                "SELECT metric_id,region,value FROM CONSUMER.CURRENT_METRICS ORDER BY metric_id,region"
            )
        )
        assert metric_projection(visible) == metric_projection(snapshot["metrics"])
    with closing(connect("RMP_BUILD")) as conn:
        history = records(
            conn.cursor().execute(
                "SELECT query_id,query_type,execution_status,total_elapsed_time,bytes_scanned,"
                "rows_produced,execution_time,compilation_time,queued_provisioning_time,"
                "queued_overload_time,credits_used_cloud_services "
                "FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(RESULT_LIMIT=>10000)) "
                "WHERE query_tag=%s ORDER BY start_time",
                (os.environ["RMP_QUERY_TAG"],),
            )
        )
        if not history or len(history) >= 10000:
            raise AssertionError("Missing or potentially truncated query-history evidence")
        query_ids.extend(row["query_id"] for row in history)
        (workspace / "query-history.json").write_text(canonical(history), encoding="utf-8")
    publish(payload, workspace / "published")
    result = {
        "status": "passed",
        "executed_cloud": True,
        "profile": profile,
        "implementation_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/verify_snowflake.py",
                "src/revenue_platform/snowflake_controls.py",
                "src/revenue_platform/snowflake_acceptance.py",
                "dbt/profiles.yml",
                "snowflake/bootstrap.sql",
                "requirements.lock",
            )
        },
        "query_tag": os.environ["RMP_QUERY_TAG"],
        "publication_safety": {
            "rollback_preserved_previous": True,
            "failed_dbt_test_preserved_previous": True,
            "repeated_promotion_history_rows": 1,
        },
        "credential_boundary": "Separate consumer user and key; stronger role switching denied",
        "release_id": release_id,
        "query_ids": sorted(set(query_ids)),
        "query_history_limits": {
            "spill_statistics": "Not exposed by INFORMATION_SCHEMA.QUERY_HISTORY; not measured",
            "cloud_services_credits": "Reported query attribution before billing adjustments; "
            "not warehouse compute credits or final billed consumption",
        },
        "denied_operations": denied,
        "timings_seconds": timings,
        "late_correction": {
            "rows_loaded": total_late_rows,
            "sampled_opportunity_changed": True,
            "initial_daily_fact_rows": len(baseline_sample),
            "corrected_daily_fact_rows": len(corrected_sample),
        },
        "model_comparison": comparisons,
        "comparison_method": "Symmetric EXCEPT on complete rows grouped with duplicate counts",
        "warehouse_size": "Verify live warehouse configuration independently; bootstrap requests XSMALL",
        "note": "No throughput, cost saving or production-scale claim. Synthetic dedicated demo database.",
    }
    (workspace / "verification.json").write_text(canonical(result), encoding="utf-8")
    print(canonical(result))
    return 0


def verify(
    workspace,
    require_cloud=False,
    status_only=False,
    profile="fixture",
    source_dir=None,
    owner_confirmed_fixture_monitor=False,
):
    """No cloud query or provisioning occurs until independent credentials exist."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing or status_only:
        return _execute(workspace, require_cloud, status_only, profile, source_dir)
    previous_environment = {
        name: os.environ.get(name) for name in ("REVENUE_BUILD_SCHEMA", "RMP_QUERY_TAG")
    }
    started = datetime.now(timezone.utc)
    tag = "rmp_acceptance_" + uuid4().hex
    os.environ["RMP_QUERY_TAG"] = tag
    record = {
        "status": "checks_in_progress",
        "executed_cloud": False,
        "profile": profile,
        "started_at": started.isoformat(),
        "query_tag": tag,
    }
    (workspace / "verification.json").write_text(canonical(record), encoding="utf-8")
    try:
        if owner_confirmed_fixture_monitor and profile != "fixture":
            raise ValueError("Owner fixture monitor confirmation cannot authorize another profile")
        approval = {"enterprise": "RMP_ENTERPRISE_APPROVED", "stress": "RMP_STRESS_APPROVED"}
        if profile in approval and os.environ.get(approval[profile]) != "yes":
            raise ValueError("Owner approval is required before this non-fixture cloud execution")
        distinct_credentials()
        with closing(connect("RMP_BUILD")) as gate:
            record["executed_cloud"] = True
            warehouse = warehouse_preflight(
                gate.cursor(), owner_confirmed_fixture_monitor=owner_confirmed_fixture_monitor
            )
            record["warehouse_observed"] = warehouse
            with exclusive_run(gate):
                try:
                    result = _execute(workspace, require_cloud, False, profile, source_dir)
                finally:
                    # A second connection keeps warehouse DDL out of the gate transaction.
                    with closing(connect("RMP_BUILD")) as operator:
                        operation = operator.cursor()
                        operation.execute("SHOW WAREHOUSES LIKE 'RMP_WH'")
                        names = [c[0].lower() for c in operation.description]
                        observed = dict(zip(names, operation.fetchone(), strict=True))
                        if observed["state"] != "SUSPENDED":
                            operation.execute("ALTER WAREHOUSE RMP_WH SUSPEND")
                        operation.execute("SHOW WAREHOUSES LIKE 'RMP_WH'")
                        final_warehouse = dict(zip(names, operation.fetchone(), strict=True))
                        if final_warehouse["state"] != "SUSPENDED":
                            raise AssertionError("Dedicated warehouse did not suspend")
                        suspension_query_id = operation.sfqid
                        record["warehouse_final_state"] = final_warehouse["state"]
                        record["warehouse_final_state_query_id"] = suspension_query_id
            record = json.loads((workspace / "verification.json").read_text())
            record["warehouse_observed"] = warehouse
            record["warehouse_final_state"] = final_warehouse["state"]
            record["warehouse_final_state_query_id"] = suspension_query_id
            record["acceptance_passed"] = True
            record["started_at"] = started.isoformat()
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            record["run_isolation"] = (
                "Dedicated gate transaction; native contention acceptance pending"
            )
            record["consumption"] = {
                "status": "pending_owner_metering_export",
                "note": "Elapsed time is not billed credits. Use snowflake/consumption.sql after reporting latency.",
            }
            (workspace / "verification.json").write_text(canonical(record), encoding="utf-8")
        return result
    except Exception as error:
        record.update(
            status="failed",
            acceptance_passed=False,
            error_class=type(error).__name__,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        for field in ("errno", "sqlstate", "sfqid"):
            if getattr(error, field, None) is not None:
                record[field] = getattr(error, field)
        (workspace / "verification.json").write_text(canonical(record), encoding="utf-8")
        raise
    finally:
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT / ".local/snowflake")
    parser.add_argument("--require-cloud", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument(
        "--owner-confirmed-fixture-monitor",
        action="store_true",
        help="Fixture only: owner has executed the administrator postflight successfully; "
        "record this authority when the restricted role cannot see monitor metadata",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        help="Private JSON settings outside repository; never include it in evidence",
    )
    parser.add_argument("--profile", choices=["enterprise", "fixture", "stress"], default="fixture")
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.config_file:
            config_path = Path(os.path.abspath(args.config_file)).resolve()
            if config_path == ROOT or ROOT in config_path.parents:
                raise ValueError("Private config must be outside repository")
            settings = json.loads(config_path.read_text(encoding="utf-8"))
            allowed = set(REQUIRED) | {
                "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
                "SNOWFLAKE_CONSUMER_PRIVATE_KEY_PASSPHRASE",
            }
            if set(settings) - allowed or any(not isinstance(v, str) for v in settings.values()):
                raise ValueError("Invalid private configuration fields")
            if settings.get("SNOWFLAKE_ACCOUNT", "").startswith("REPLACE_"):
                raise ValueError("Set the account identifier in the private configuration")
            os.environ.update(settings)
        return verify(
            args.workspace,
            args.require_cloud,
            args.status_only,
            args.profile,
            args.source_dir,
            args.owner_confirmed_fixture_monitor,
        )
    except Exception as error:
        # Avoid serializing connection details or key material from SDK exceptions.
        print(
            f"Snowflake verification failed ({type(error).__name__}); inspect the local dbt logs if created."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
