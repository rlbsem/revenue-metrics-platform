"""Execute actual Snowflake ingestion/dbt/consumer/RBAC, or report pending without credentials."""

import argparse
import csv
import json
from itertools import islice
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from revenue_platform.runtime import (
    ROOT,
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

REQUIRED = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH")


def connect(role):
    from cryptography.hazmat.primitives import serialization
    import snowflake.connector

    secret = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    key = serialization.load_pem_private_key(
        Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]).read_bytes(),
        password=secret.encode() if secret else None,
    )
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        role=role,
        database="REVENUE_METRICS_DEMO",
        warehouse="RMP_WH",
        session_parameters={"QUERY_TAG": "revenue_metrics_platform"},
    )
    conn.cursor().execute("USE SECONDARY ROLES NONE")
    assert conn.cursor().execute("select current_role()").fetchone()[0] == role
    return conn


def verify(
    workspace, require_cloud=False, status_only=False, profile="enterprise", source_dir=None
):
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
    os.environ["REVENUE_BUILD_SCHEMA"] = schema
    query_ids = []
    with connect("RMP_LOADER") as conn:
        cursor = conn.cursor()
        for table, ddl in tables.items():
            # This command replaces synthetic source tables in the fixed demo database only.
            cursor.execute(f"create or replace table RAW.{table} ({ddl})")
            with (source_dir / f"{table}.csv").open(newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                while rows := list(islice(reader, 10000)):
                    cursor.executemany(
                        f"insert into RAW.{table} values ({','.join('%s' for _ in header)})", rows
                    )
            query_ids.append(cursor.sfqid)
        with (source_dir / "late_opportunity_events.csv").open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)
            while rows := list(islice(reader, 10000)):
                cursor.executemany(
                    "insert into RAW.opportunity_events values (%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows
                )
        if manifest:
            from revenue_platform.scenario_checks import validate_scenario

            validate_scenario(cursor)
    run_dbt(workspace / "unused.duckdb", workspace, full_refresh=True, target="snowflake")
    with connect("RMP_BUILD") as conn:
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
        # A single current payload makes all metric/bridge/definition fields change together.
        cursor.execute("begin")
        try:
            cursor.execute(
                "insert into CONSUMER.RELEASE_HISTORY select %s,parse_json(%s)",
                (release_id, json.dumps(payload)),
            )
            cursor.execute("delete from CONSUMER.CURRENT_RELEASE")
            cursor.execute(
                "insert into CONSUMER.CURRENT_RELEASE select %s,parse_json(%s)",
                (release_id, json.dumps(payload)),
            )
            cursor.execute("commit")
        except Exception:
            cursor.execute("rollback")
            raise
        cursor.execute("""create or replace view CONSUMER.CURRENT_METRICS as
            select r.release_id, m.value:metric_id::varchar as metric_id,m.value:region::varchar as region,
            m.value:value::varchar as value,m.value:definition_version::varchar as definition_version
            from CONSUMER.CURRENT_RELEASE r,lateral flatten(input=>r.payload:metrics) m""")
        cursor.execute("grant select on view CONSUMER.CURRENT_METRICS to role RMP_CONSUMER")
    from snowflake.connector.errors import ProgrammingError

    denied = []
    with connect("RMP_CONSUMER") as conn:
        cursor = conn.cursor()
        visible = records(
            cursor.execute(
                "select metric_id,region,value from CONSUMER.CURRENT_METRICS order by metric_id,region"
            )
        )
        wanted = [{k: r[k] for k in ("metric_id", "region", "value")} for r in snapshot["metrics"]]
        assert visible == wanted, "Actual warehouse consumer differs from dbt output"
        query_ids.append(cursor.sfqid)
        for name, sql in [
            ("raw_read", "select * from RAW.ACCOUNT_HISTORY"),
            ("release_write", "update CONSUMER.CURRENT_RELEASE set release_id='invalid'"),
        ]:
            try:
                cursor.execute(sql)
            except ProgrammingError as error:
                if not re.search("not authorized|insufficient privileges", str(error), re.I):
                    raise
                denied.append(name)
            else:
                raise AssertionError(f"Unexpected permission: {name}")
    publish(payload, workspace / "published")
    result = {
        "status": "passed",
        "executed_cloud": True,
        "release_id": release_id,
        "query_ids": query_ids,
        "denied_operations": denied,
        "warehouse_size": "XSMALL as provisioned by bootstrap; actual runtime size is account-controlled",
        "note": "No throughput, cost saving or production-scale claim. Synthetic dedicated demo database.",
    }
    (workspace / "verification.json").write_text(canonical(result), encoding="utf-8")
    print(canonical(result))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT / ".local/snowflake")
    parser.add_argument("--require-cloud", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument(
        "--profile", choices=["enterprise", "fixture", "stress"], default="enterprise"
    )
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    try:
        return verify(
            args.workspace, args.require_cloud, args.status_only, args.profile, args.source_dir
        )
    except Exception as error:
        # Avoid serializing connection details or key material from SDK exceptions.
        print(
            f"Snowflake verification failed ({type(error).__name__}); inspect the local dbt logs if created."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
