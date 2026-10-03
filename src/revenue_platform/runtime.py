"""Run dbt and publish a small, immutable commercial review artifact."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import duckdb

ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "account_history": "account_version_id varchar,account_id varchar,account_name varchar,owner varchar,region varchar,valid_from date,valid_to date",
    "opportunity_events": "event_id varchar,opportunity_id varchar,account_id varchar,created_date date,effective_date date,stage varchar,amount_cents bigint,origin varchar,ingest_seq bigint",
    "booking_events": "booking_event_id varchar,contract_id varchar,account_id varchar,event_date date,amount_cents bigint,event_type varchar",
    "invoice_lines": "line_id varchar,invoice_id varchar,contract_id varchar,account_id varchar,event_date date,amount_cents bigint,line_type varchar",
    "payment_allocations": "allocation_id varchar,payment_id varchar,invoice_id varchar,account_id varchar,event_date date,amount_cents bigint",
    "subscription_contracts": "contract_id varchar,account_id varchar,start_date date,end_date date,fee_cents bigint,billing_months integer,contract_type varchar,is_trial integer,past_due integer",
    "calendar": "date_day date,month_start date,is_month_end integer",
    "coverage": "source_name varchar,complete_from date,complete_through date",
}
MODELS = [p.stem for p in sorted((ROOT / "dbt/models").rglob("*.sql"))]


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalized(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def records(cursor):
    names = [x[0].lower() for x in cursor.description]
    return [dict(zip(names, map(normalized, row), strict=True)) for row in cursor.fetchall()]


def load_local(database: Path, late=False, source_dir=None):
    source_dir = ROOT / "data" if source_dir is None else Path(source_dir)
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        raise FileExistsError("Use a new workspace; existing source databases are never reset.")
    from revenue_platform.generator import ATTRIBUTES

    manifest_path = source_dir / "dataset-manifest.json"
    generated = manifest_path.exists()
    with duckdb.connect(str(database)) as conn:
        conn.execute("create schema raw")
        for table, ddl in {**TABLES, **(ATTRIBUTES if generated else {})}.items():
            conn.execute(f"create table raw.{table} ({ddl})")
            path = str((source_dir / f"{table}.csv").resolve()).replace("'", "''")
            conn.execute(f"COPY raw.{table} FROM '{path}' (HEADER, FORMAT CSV)")
        if generated:
            conn.execute("create table raw.dataset_metadata (payload varchar)")
            conn.execute(
                "insert into raw.dataset_metadata values (?)",
                [manifest_path.read_text(encoding="utf-8")],
            )
            from revenue_platform.scenario_checks import validate_scenario

            validate_scenario(conn)
    if late:
        add_correction(database, source_dir)


def add_correction(database, source_dir=None):
    source_dir = ROOT / "data" if source_dir is None else Path(source_dir)
    with duckdb.connect(str(database)) as conn:
        if conn.execute(
            "select count(*) from raw.opportunity_events where ingest_seq=2"
        ).fetchone()[0]:
            raise ValueError("Correction batch already loaded")
        path = str((source_dir / "late_opportunity_events.csv").resolve()).replace("'", "''")
        conn.execute(f"COPY raw.opportunity_events FROM '{path}' (HEADER, FORMAT CSV)")


def cursor_fingerprint(cursor):
    """Canonical row stream; bounded memory and independent of fetch batch boundaries."""
    sha = hashlib.sha256()
    while rows := cursor.fetchmany(10000):
        for row in rows:
            sha.update(
                (
                    json.dumps(
                        [normalized(v) for v in row], ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                ).encode()
            )
    return sha.hexdigest()


def run_dbt(
    database: Path,
    workspace: Path,
    *,
    full_refresh=False,
    policy="1.0",
    target="local",
    docs=False,
    test_only=None,
):
    if policy not in ("1.0", "2.0"):
        raise ValueError("Unsupported ARR policy")
    workspace.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        REVENUE_DB=str(database.resolve()),
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        PYTHONIOENCODING="utf-8",
    )
    executable = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    command = [
        str(executable),
        "--no-use-colors",
        "test" if test_only else ("docs" if docs else "build"),
    ]
    if docs:
        command += ["generate"]
    command += [
        "--project-dir",
        str(ROOT / "dbt"),
        "--profiles-dir",
        str(ROOT / "dbt"),
        "--target",
        target,
        "--target-path",
        str((workspace / "dbt-target").resolve()),
        "--log-path",
        str((workspace / "dbt-logs").resolve()),
        "--vars",
        json.dumps({"arr_policy": policy}),
    ]
    if full_refresh and not docs:
        command += ["--full-refresh"]
    if test_only:
        command += ["--select", test_only]
    completed = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8")
    log = workspace / ("dbt-docs.log" if docs else "dbt-build.log")
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        # Detailed logs are local; never print a cloud connection exception with account details.
        raise RuntimeError(f"dbt failed; inspect {log}")
    return workspace / "dbt-target"


def local_snapshot(database):
    with duckdb.connect(str(database), read_only=True) as conn:
        generated = bool(
            conn.execute(
                "select count(*) from information_schema.tables where table_schema='raw' and table_name='dataset_metadata'"
            ).fetchone()[0]
        )
        snapshot = {
            "metrics": records(
                conn.execute("select * from analytics.mart_metric_values order by metric_id,region")
            ),
            "bridge": records(
                conn.execute(
                    "select * from analytics.mart_discrepancy_bridge order by account_id"
                    + (" limit 20" if generated else "")
                )
            ),
            "cohorts": records(
                conn.execute("select * from analytics.mart_cohorts order by cohort_month,region")
            ),
            "coverage": records(conn.execute("select * from raw.coverage order by source_name")),
        }
        if not generated:
            snapshot["source_rows"] = {
                t: records(conn.execute(f"select * from raw.{t} order by all")) for t in TABLES
            }
            return snapshot
        from revenue_platform.generator import ATTRIBUTES

        manifest = json.loads(
            conn.execute("select payload from raw.dataset_metadata").fetchone()[0]
        )
        snapshot["dataset"] = dataset_summary(conn, "analytics", manifest)
        snapshot["source_hashes"] = {
            t: cursor_fingerprint(conn.execute(f"select * from raw.{t} order by all"))
            for t in {**TABLES, **ATTRIBUTES}
        }
        return snapshot


def dataset_summary(conn, schema, manifest):
    from revenue_platform.generator import ATTRIBUTES

    return {
        "profile": manifest["config"]["profile"],
        "seed": manifest["config"]["seed"],
        "config": manifest["config"],
        "source_counts": {
            t: conn.execute(f"select count(*) from raw.{t}").fetchone()[0]
            for t in {**TABLES, **ATTRIBUTES}
        },
        "accounts": conn.execute(
            "select count(distinct account_id) from raw.account_history"
        ).fetchone()[0],
        "opportunities": conn.execute(
            "select count(distinct opportunity_id) from raw.opportunity_events"
        ).fetchone()[0],
        "active_subscriptions": conn.execute(
            "select count(*) from raw.subscription_contracts where start_date<='2026-03-31' and end_date>'2026-03-31' and contract_type='recurring' and is_trial=0"
        ).fetchone()[0],
        "bridge_scope": "First 20 account IDs; company-wide full bridge remains in warehouse",
        "timeline": {
            "acquisition_years": records(
                conn.execute(
                    "select extract(year from acquired_on) as acquired_year,count(distinct c.account_id) as accounts from raw.contract_attributes a join raw.subscription_contracts c using(contract_id) where c.end_date>'2026-03-31' group by 1 order by 1"
                )
            ),
            "q1_commercial_events": records(
                conn.execute(
                    "select event_type,count(*) as events,cast(sum(amount_cents)/100.0 as decimal(18,2)) as net_bookings from raw.commercial_events group by event_type order by event_type"
                )
            ),
            "active_accounts_without_q1_bookings": conn.execute(
                "select count(distinct c.account_id) from raw.subscription_contracts c where c.end_date>'2026-03-31' and not exists (select 1 from raw.booking_events b where b.account_id=c.account_id and b.event_date between '2026-01-01' and '2026-03-31')"
            ).fetchone()[0],
            "renewal_months": records(
                conn.execute(
                    "select extract(month from a.renewal_date) as renewal_month,count(*) as contracts from raw.contract_attributes a join raw.subscription_contracts c using(contract_id) where c.end_date>'2026-03-31' group by 1 order by 1"
                )
            ),
        },
        "portfolio": records(
            conn.execute(f"""select a.segment,c.product,count(*) as contracts,
            cast(sum(f.arr_cents)/100.0 as decimal(18,2)) as arr
            from {schema}.fct_subscription_period f
            join raw.account_attributes a using(account_id)
            join raw.contract_attributes c using(contract_id)
            where f.date_day='2026-03-31' and f.arr_cents>0 group by all order by all""")
        ),
    }


def assert_enterprise(snapshot, policy="1.0"):
    info = snapshot["dataset"]
    count = info["config"]["accounts"]
    assert info["accounts"] == count
    assert count * 3 <= info["opportunities"] <= count * 4
    assert info["active_subscriptions"] == count
    values = {
        r["metric_id"]: Decimal(r["value"]) for r in snapshot["metrics"] if r["region"] == "ALL"
    }
    assert Decimal("11000") * count < values["period_end_arr"] < Decimal("14000") * count
    assert 0 < values["net_bookings"] < values["period_end_arr"] * Decimal("0.6")
    assert values["net_invoiced"] > 0 and values["cash_received"] > 0
    assert 0 < values["cohort_win_rate"] < 1
    assert sum(Decimal(r["arr"]) for r in info["portfolio"]) == values["period_end_arr"]
    if info["profile"] == "enterprise" and count == 60000:
        if policy == "1.0":
            assert Decimal("750000000") <= values["period_end_arr"] <= Decimal("800000000")
        assert Decimal("0.05") < values["cohort_win_rate"] < Decimal("0.40")
        assert info["timeline"]["active_accounts_without_q1_bookings"] > count * 0.3
        renewals = next(
            r["events"]
            for r in info["timeline"]["q1_commercial_events"]
            if r["event_type"] == "renewal"
        )
        assert count * 0.15 < renewals < count * 0.30
        assert 200000 <= info["source_counts"]["booking_events"] <= 650000
        assert 500000 <= info["source_counts"]["invoice_lines"] <= 1000000
        assert 200000 <= info["source_counts"]["payment_allocations"] <= 500000


def source_fingerprint():
    files = sorted(
        [
            *(ROOT / "dbt/models").rglob("*"),
            *(ROOT / "dbt/macros").rglob("*"),
            *(ROOT / "dbt/tests").rglob("*"),
            ROOT / "dbt/dbt_project.yml",
        ]
    )
    return digest(
        {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
            if p.is_file()
        }
    )


def make_payload(snapshot, policy, target):
    definitions = json.loads((ROOT / "metrics/catalog.json").read_text(encoding="utf-8"))
    return {
        "format_version": 1,
        "synthetic": True,
        "target": target,
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "arr_policy": policy,
        "definitions": definitions,
        "model_fingerprint": source_fingerprint(),
        "source_fingerprint": digest(
            snapshot.pop("source_rows", snapshot.pop("source_hashes", {}))
        ),
        **snapshot,
    }


def assert_expected(snapshot, policy="1.0"):
    expected = json.loads((ROOT / "data/expected.json").read_text())
    values = {r["metric_id"]: r["value"] for r in snapshot["metrics"] if r["region"] == "ALL"}
    wanted = dict(expected["metrics_v1"])
    if policy == "2.0":
        wanted["period_end_arr"] = expected["arr_v2"]
    if values != wanted:
        raise AssertionError(f"Independent metric expectations differ: {values}")
    for row in snapshot["bridge"]:
        actual = [
            Decimal(str(row[k]))
            for k in (
                "bookings",
                "invoiced",
                "cash",
                "bookings_less_invoices",
                "invoices_less_cash",
            )
        ]
        if actual != list(map(Decimal, expected["bridge"][row["account_id"]])):
            raise AssertionError("Independent account bridge differs")


def publish(payload, publication):
    """Only callers with successful validation may publish; atomic pointer, immutable content."""
    publication.mkdir(parents=True, exist_ok=True)
    release_id = digest(payload)
    release = publication / f"{release_id}.json"
    content = canonical(payload)
    if release.exists() and release.read_text(encoding="utf-8") != content:
        raise ValueError("Existing immutable release differs")
    release.write_text(content, encoding="utf-8")
    pointer = publication / f".{uuid4().hex}.tmp"
    pointer.write_text(
        canonical({"release_id": release_id, "file": release.name}), encoding="utf-8"
    )
    os.replace(pointer, publication / "current.json")
    return release_id


def validate_and_publish(database, workspace, publication, policy="1.0"):
    run_dbt(database, workspace, policy=policy)
    snapshot = local_snapshot(database)
    if "dataset" in snapshot:
        assert_enterprise(snapshot, policy)
    else:
        assert_expected(snapshot, policy)
    return publish(make_payload(snapshot, policy, "duckdb"), publication)


def demo(workspace, profile="enterprise"):
    from revenue_platform.generator import generate

    source_dir = ROOT / "data"
    if profile != "fixture":
        source_dir = workspace / "sources"
        generate(source_dir, profile)
    database = workspace / "warehouse.duckdb"
    load_local(database, late=True, source_dir=source_dir)
    release = validate_and_publish(database, workspace, workspace / "published")
    run_dbt(database, workspace, docs=True)
    return release
