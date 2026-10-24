"""Opt-in local rebuilds with exact source comparison and conservative fallback."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time

import duckdb

from revenue_platform.runtime import (
    MODELS,
    ROOT,
    assert_enterprise,
    assert_expected,
    canonical,
    local_snapshot,
    make_payload,
    publish,
    run_dbt,
    source_fingerprint,
)


def connect(database):
    con = duckdb.connect(str(database))
    con.execute("set threads=" + str(int(os.environ.get("RMP_DUCKDB_THREADS", "1"))))
    con.execute("set memory_limit='2GB'; set preserve_insertion_order=false")
    return con


def columns(con, schema, table):
    return con.execute(
        "select column_name,data_type from information_schema.columns "
        "where table_schema=? and table_name=? order by ordinal_position",
        [schema, table],
    ).fetchall()


def same(con, left, right):
    # Exact multiset comparison, not a hash-based change detector.
    return not con.execute(
        f"select exists((select * from {left} except all select * from {right}) "
        f"union all (select * from {right} except all select * from {left}))"
    ).fetchone()[0]


def implementation(policy):
    return canonical(
        {
            "models": source_fingerprint(),
            "controller": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runtime": hashlib.sha256(
                (ROOT / "src/revenue_platform/runtime.py").read_bytes()
            ).hexdigest(),
            "catalog": hashlib.sha256((ROOT / "metrics/catalog.json").read_bytes()).hexdigest(),
            "policy": policy,
            "duckdb": duckdb.__version__,
        }
    )


@contextmanager
def exclusive(database):
    lock = Path(str(database) + ".rebuild-lock")
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink()


def rebuild(database, workspace, *, policy="1.0", publication=None):
    """Requires quiesced inputs and exclusive ownership of a local warehouse.

    The lock serializes this controller, not arbitrary SQL writers. Source snapshots
    and the implementation/policy checkpoint advance only after every dbt test and
    a second exact source comparison. Failed builds never publish.
    """
    with exclusive(database):
        return _rebuild(database, Path(workspace), policy, publication)


def _rebuild(database, workspace, policy, publication):
    started = time.perf_counter()
    identity = implementation(policy) + canonical({"catalog": Path(database).stem})
    with connect(database) as con:
        names = [
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema='raw' "
                "and table_type='BASE TABLE' order by table_name"
            ).fetchall()
        ]
        if not names or any(not re.fullmatch("[a-z_]+", n) for n in names):
            raise ValueError("Unsupported source inventory")
        con.execute("create schema if not exists assurance_control")
        con.execute("create table if not exists assurance_control.checkpoint(identity varchar)")
        previous = con.execute("select identity from assurance_control.checkpoint").fetchall()
        con.execute("create schema if not exists assurance_sources")
        old_names = [
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables "
                "where table_schema='assurance_sources' order by table_name"
            ).fetchall()
        ]
        initialized = previous == [(identity,)] and old_names == names
        initialized = initialized and all(
            columns(con, "raw", n) == columns(con, "assurance_sources", n) for n in names
        )
        initialized = initialized and all(columns(con, "analytics", n) for n in MODELS)
        changed = (
            [n for n in names if not same(con, f"raw.{n}", f"assurance_sources.{n}")]
            if initialized
            else names
        )
        mode = "noop" if not changed else "full"
        reason = (
            "no source change"
            if not changed
            else "initialization, broader change or invalidated state"
        )
        affected = 0
        if initialized and changed == ["opportunity_events"]:
            old, new = "assurance_sources.opportunity_events", "raw.opportunity_events"
            delta = f"(select * from {new} except all select * from {old})"
            removed = con.execute(
                f"select exists(select * from {old} except all select * from {new})"
            ).fetchone()[0]
            nonmonotonic = con.execute(
                f"select count(*) from {delta} d where ingest_seq <= "
                f"(select coalesce(max(ingest_seq),0) from {old})"
            ).fetchone()[0]
            moved = con.execute(
                f"select count(*) from {delta} d join {old} o using(event_id) "
                "where d.account_id<>o.account_id or d.opportunity_id<>o.opportunity_id"
            ).fetchone()[0]
            if not (removed or nonmonotonic or moved):
                mode, reason = (
                    "opportunity_accounts",
                    "append-only monotonic revisions with stable canonical identity",
                )
                con.execute(
                    "create or replace table assurance_control.affected_accounts as "
                    f"select distinct account_id from {delta}"
                )
                affected = con.execute(
                    "select count(*) from assurance_control.affected_accounts"
                ).fetchone()[0]
            else:
                reason = "revision contract not eligible for selective replacement"
        con.execute(
            "drop schema if exists assurance_pending cascade; create schema assurance_pending"
        )
        for name in names:
            con.execute(f"create table assurance_pending.{name} as select * from raw.{name}")
    detection_seconds = time.perf_counter() - started
    run_dbt(
        database,
        workspace,
        policy=policy,
        full_refresh=mode == "full",
        test_only="resource_type:test" if mode == "noop" else None,
        select=["stg_opportunities+", "resource_type:test"]
        if mode == "opportunity_accounts"
        else None,
        extra_vars={"selective_pipeline": True},
    )
    result = json.loads((workspace / "dbt-target/run_results.json").read_text(encoding="utf-8"))
    graph = json.loads((workspace / "dbt-target/manifest.json").read_text(encoding="utf-8"))
    expected = {k for k, v in graph["nodes"].items() if v["resource_type"] == "test"}
    actual = {r["unique_id"] for r in result["results"] if r["unique_id"].startswith("test.")}
    if expected != actual or any(r["status"] not in ("pass", "success") for r in result["results"]):
        raise ValueError("Every dbt quality gate must pass")
    with connect(database) as con:
        if any(not same(con, f"raw.{n}", f"assurance_pending.{n}") for n in names):
            raise ValueError("Sources changed during build; checkpoint and publication refused")
        con.execute("begin")
        con.execute("drop schema assurance_sources cascade; create schema assurance_sources")
        for name in names:
            con.execute(
                f"create table assurance_sources.{name} as select * from assurance_pending.{name}"
            )
        con.execute("delete from assurance_control.checkpoint")
        con.execute("insert into assurance_control.checkpoint values (?)", [identity])
        con.execute("drop schema assurance_pending cascade; commit")
    record = {
        "mode": mode,
        "reason": reason,
        "changed_sources": changed,
        "affected_accounts": affected,
        "detection_and_snapshot_seconds": detection_seconds,
        "total_seconds": time.perf_counter() - started,
        "models_executed": [
            r["unique_id"].split(".")[-1]
            for r in result["results"]
            if r["unique_id"].startswith("model.")
        ],
        "quality_gates": len(actual),
    }
    if publication is not None:
        snapshot = local_snapshot(database)
        if "dataset" in snapshot:
            assert_enterprise(snapshot, policy)
        else:
            assert_expected(snapshot, policy)
        record["release_id"] = publish(make_payload(snapshot, policy, "duckdb"), Path(publication))
    return record
