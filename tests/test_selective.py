import json
import shutil
import subprocess
import sys
import time

import duckdb
import pytest

from revenue_platform.performance_checks import compare
from revenue_platform.runtime import add_correction, load_local, run_dbt
from revenue_platform.selective import rebuild


@pytest.fixture(scope="module")
def selective_base(tmp_path_factory):
    folder = tmp_path_factory.mktemp("selective-base")
    db = folder / "warehouse.duckdb"
    load_local(db)
    assert rebuild(db, folder / "initial")["quality_gates"] == 30
    return db


CASES = [
    ("generated_correction", None, "opportunity_accounts"),
    (
        "larger_batch",
        "insert into raw.opportunity_events select event_id,opportunity_id,account_id,created_date,effective_date,stage,amount_cents+700,origin,3 from raw.opportunity_events where stage='open'",
        "opportunity_accounts",
    ),
    (
        "forward_beyond_calendar",
        "insert into raw.opportunity_events select event_id,opportunity_id,account_id,created_date,cast('2026-04-20' as date),stage,amount_cents,origin,3 from raw.opportunity_events where opportunity_id='O3'",
        "opportunity_accounts",
    ),
    (
        "historical_owner",
        "update raw.account_history set owner='Corrected',region='West' where account_version_id='A1_1'",
        "full",
    ),
    (
        "late_booking",
        "insert into raw.booking_events values ('LATE_B','S1','A1','2026-02-01',-10000,'amendment')",
        "full",
    ),
    (
        "late_invoice",
        "insert into raw.invoice_lines values ('LATE_I','LATE_INV','S1','A1','2026-02-01',10000,'invoice')",
        "full",
    ),
    (
        "late_payment",
        "insert into raw.payment_allocations values ('LATE_P','LATE_PAY','I1','A1','2026-03-01',10000)",
        "full",
    ),
    (
        "subscription",
        "update raw.subscription_contracts set fee_cents=fee_cents+100 where contract_id='S1'",
        "full",
    ),
    (
        "nonmonotonic",
        "insert into raw.opportunity_events values ('LATE_OLD','O_NEW','A2','2026-01-01','2026-03-30','open',99,'inbound',1)",
        "full",
    ),
]


@pytest.mark.parametrize("name,mutation,mode", CASES, ids=[c[0] for c in CASES])
def test_supported_corrections_match_independent_full(
    selective_base, tmp_path, name, mutation, mode
):
    opt, full = tmp_path / "warehouse.duckdb", tmp_path / "full.duckdb"
    shutil.copyfile(selective_base, opt)
    load_local(full)
    for db in (opt, full):
        if mutation is None:
            add_correction(db)
        else:
            with duckdb.connect(str(db)) as con:
                con.execute(mutation)
    result = rebuild(opt, tmp_path / "optimized")
    assert result["mode"] == mode and result["quality_gates"] == 30
    run_dbt(full, tmp_path / "independent", full_refresh=True)
    assert len(compare(opt, full)["models"]) == 15
    if name == "forward_beyond_calendar":
        with duckdb.connect(str(opt)) as con:
            assert (
                con.execute(
                    "select count(*) from analytics.fct_opportunity_daily where opportunity_id='O3'"
                ).fetchone()[0]
                == 0
            )


def test_noop_runs_every_quality_gate_and_no_models(selective_base, tmp_path):
    db = tmp_path / "warehouse.duckdb"
    shutil.copyfile(selective_base, db)
    result = rebuild(db, tmp_path / "noop")
    assert result["mode"] == "noop" and result["models_executed"] == []
    assert result["quality_gates"] == 30
    compare(db, selective_base)


@pytest.mark.parametrize(
    "mutation",
    [
        "delete from raw.coverage where source_name='booking_events'",
        "drop table raw.invoice_lines",
    ],
)
def test_failed_build_never_advances_source_checkpoint_or_publication(
    selective_base, tmp_path, mutation
):
    db = tmp_path / "warehouse.duckdb"
    shutil.copyfile(selective_base, db)
    add_correction(db)
    pub = tmp_path / "published"
    release = rebuild(db, tmp_path / "valid", publication=pub)["release_id"]
    before = (pub / "current.json").read_bytes()
    with duckdb.connect(str(db)) as con:
        old = con.execute("select identity from assurance_control.checkpoint").fetchall()
        con.execute(mutation)
    with pytest.raises(RuntimeError, match="dbt failed"):
        rebuild(db, tmp_path / "failed", publication=pub)
    assert (pub / "current.json").read_bytes() == before
    assert (pub / f"{release}.json").exists()
    with duckdb.connect(str(db)) as con:
        assert con.execute("select identity from assurance_control.checkpoint").fetchall() == old


def test_exact_comparison_detects_equal_count_semantic_corruption(selective_base, tmp_path):
    db = tmp_path / "warehouse.duckdb"
    shutil.copyfile(selective_base, db)
    with duckdb.connect(str(db)) as con:
        con.execute(
            "update analytics.mart_pipeline set pipeline_cents=pipeline_cents+1 where account_id='A1'"
        )
    with pytest.raises(AssertionError, match="multiset differs"):
        compare(db, selective_base)


def test_stale_build_lock_fails_closed(selective_base, tmp_path):
    db = tmp_path / "warehouse.duckdb"
    shutil.copyfile(selective_base, db)
    db.with_suffix(".duckdb.rebuild-lock").write_text(json.dumps({"owner": "unverified"}))
    with pytest.raises(FileExistsError):
        rebuild(db, tmp_path / "never")


def test_real_interruption_after_transform_before_checkpoint(selective_base, built, tmp_path):
    from revenue_platform.runtime import assert_expected, local_snapshot, make_payload, publish

    db = tmp_path / "warehouse.duckdb"
    shutil.copyfile(selective_base, db)
    publication = tmp_path / "published"
    snap = local_snapshot(built)
    assert_expected(snap)
    release = publish(make_payload(snap, "1.0", "duckdb"), publication)
    before = (publication / "current.json").read_bytes()
    add_correction(db)
    marker = tmp_path / "after-transform"
    script = """
import sys,time
from pathlib import Path
import revenue_platform.selective as s
real=s.run_dbt
def pause(*args,**kwargs):
    result=real(*args,**kwargs)
    Path(sys.argv[3]).write_text('transformation complete; checkpoint pending')
    time.sleep(60)
    return result
s.run_dbt=pause
s.rebuild(Path(sys.argv[1]),Path(sys.argv[2]),publication=Path(sys.argv[4]))
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(db),
            str(tmp_path / "interrupted"),
            str(marker),
            str(publication),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 45
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "Process did not reach the intended interruption boundary"
        process.kill()
        process.wait(timeout=10)
        assert process.returncode != 0
        assert (publication / "current.json").read_bytes() == before
        lock = db.with_suffix(".duckdb.rebuild-lock")
        assert lock.exists()
        # Explicit verified-dead operator action, never automatic lock stealing.
        assert process.poll() is not None
        lock.unlink()
        result = rebuild(db, tmp_path / "restarted", publication=publication)
        assert result["mode"] == "opportunity_accounts" and result["release_id"] == release
        compare(db, built)
        (tmp_path / "interruption-result.json").write_text(
            json.dumps(
                {
                    "actual_killed_pid": process.pid,
                    "exit": process.returncode,
                    "boundary": "after real dbt transform, before source checkpoint and publication",
                    "previous_publication_preserved": True,
                    "restart_exact_parity": True,
                }
            )
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_export_equality_preserves_null_text_unicode_duplicates_and_precise_numbers(
    tmp_path, monkeypatch
):
    import revenue_platform.performance_checks as checks

    monkeypatch.setattr(checks, "MODELS", ["probe"])
    a, b = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    for db in (a, b):
        with duckdb.connect(str(db)) as con:
            con.execute(
                "create schema analytics; create table analytics.probe(s varchar,n decimal(18,8),d double)"
            )
            con.executemany(
                "insert into analytics.probe values (?,123456789.12345678,1.2345678901234567)",
                [(None,), ("",), ("__NULL__",), ('Unicode é,"\n',), ("duplicate",), ("duplicate",)],
            )
    assert compare(a, b)["models"][0]["rows"] == 6
    with duckdb.connect(str(b)) as con:
        con.execute("update analytics.probe set s='' where s is null")
    with pytest.raises(AssertionError, match="multiset differs"):
        compare(a, b)
