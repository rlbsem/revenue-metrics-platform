"""Controlled paired native DuckDB/dbt measurements. No cloud execution."""

import argparse
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import time

BASELINE = "a39d603cc6d16de8f1d8430abf97ad3db4aa2cbe"
ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def worker(args):
    from revenue_platform.runtime import load_local, run_dbt

    workspace, db = args.workspace, args.database
    if args.worker == "generate":
        from revenue_platform.generator import generate

        result = generate(workspace, args.profile)
    elif args.worker == "load":
        load_local(db, source_dir=workspace, late=args.late)
        import duckdb

        with duckdb.connect(str(db)) as con:
            result = {
                "loaded": True,
                "loader_default_threads": con.execute(
                    "select current_setting('threads')"
                ).fetchone()[0],
                "loader_default_memory_limit": con.execute(
                    "select current_setting('memory_limit')"
                ).fetchone()[0],
            }
    elif args.worker == "correction":
        from revenue_platform.runtime import add_correction

        add_correction(db, workspace)
        result = {"correction_loaded": True}
    elif args.worker == "large":
        import duckdb

        with duckdb.connect(str(db)) as con:
            con.execute(
                "create temp table chosen as select opportunity_id from raw.opportunity_events where opportunity_id like '%P%' group by opportunity_id having arg_max(stage,effective_date)='open' order by opportunity_id limit 10000"
            )
            con.execute(
                "insert into raw.opportunity_events select event_id,opportunity_id,account_id,created_date,effective_date,stage,amount_cents+50000,origin,3 from (select * from raw.opportunity_events qualify row_number() over(partition by event_id order by ingest_seq desc)=1) e where opportunity_id in (select opportunity_id from chosen)"
            )
            result = {
                "opportunities": con.execute("select count(*) from chosen").fetchone()[0],
                "scope": "Adversarial appended amount corrections to open prospects; separate from canonical economics",
            }
    elif args.worker == "optimized":
        from revenue_platform.selective import rebuild

        result = rebuild(db, workspace)
    else:
        run_dbt(db, workspace, full_refresh=args.worker == "full")
        result = {"mode": args.worker}
    if args.worker in ("optimized", "full", "incremental"):
        raw = json.loads((workspace / "dbt-target/run_results.json").read_text(encoding="utf-8"))
        result["nodes"] = [
            {"node": r["unique_id"], "status": r["status"], "seconds": r["execution_time"]}
            for r in raw["results"]
        ]
        if any(r["status"] not in ("pass", "success") for r in result["nodes"]):
            raise RuntimeError("dbt result did not pass")
    write(args.result, result)


def measured(args, label, command, *, env=None):
    import psutil

    started = time.perf_counter()
    log = args.workspace / (label + ".log")
    peak, cpu, samples, temp_peak = 0, {}, 0, 0
    minimum_available = psutil.virtual_memory().available
    with log.open("w", encoding="utf-8") as out:
        process = subprocess.Popen(command, env=env, stdout=out, stderr=subprocess.STDOUT)
        root = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                rss = 0
                try:
                    tree = [root, *root.children(recursive=True)]
                except psutil.NoSuchProcess:
                    tree = []
                for member in tree:
                    try:
                        rss += member.memory_info().rss
                        times = member.cpu_times()
                        cpu[member.pid] = max(cpu.get(member.pid, 0), times.user + times.system)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                peak, samples = max(peak, rss), samples + 1
                minimum_available = min(minimum_available, psutil.virtual_memory().available)
                temp_peak = max(
                    temp_peak,
                    sum(
                        p.stat().st_size
                        for d in args.workspace.rglob("*.tmp")
                        if d.is_dir()
                        for p in d.rglob("*")
                        if p.is_file()
                    ),
                )
                if (
                    time.perf_counter() - started > 1200
                    or rss > 6 * 1024**3
                    or psutil.virtual_memory().available < 1024**3
                ):
                    raise RuntimeError("Resource/time guard reached")
                time.sleep(0.2)
            if process.returncode:
                raise RuntimeError(f"Stage failed: {label}; retained private log {log.name}")
        finally:
            if process.poll() is None:
                for child in root.children(recursive=True):
                    child.kill()
                process.kill()
                process.wait()
    return {
        "seconds": time.perf_counter() - started,
        "sampled_tree_rss_max_bytes": peak,
        "sampled_process_cpu_seconds": sum(cpu.values()),
        "samples": samples,
        "available_memory_min_bytes": minimum_available,
        "sampled_temp_peak_bytes": temp_peak,
    }


def run(args):
    import psutil
    from revenue_platform.performance_checks import compare
    from revenue_platform.runtime import assert_enterprise, local_snapshot, make_payload

    if args.workspace.exists() or args.output.exists():
        raise ValueError("Fresh workspace and evidence directory required")
    args.workspace.mkdir(parents=True)
    args.output.mkdir(parents=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT):
        raise ValueError("Commit the implementation before qualifying measurements")
    reference = args.workspace / "reference"
    reference.mkdir()
    raw = subprocess.check_output(["git", "archive", BASELINE], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(reference, filter="data")
    source_seal = {
        n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
        for n in subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        if n.startswith(("src/", "dbt/", "scripts/", "profiles/", "metrics/", "requirements"))
    }
    environment = {**os.environ, "RMP_DUCKDB_THREADS": "1", "PYTHONIOENCODING": "utf-8"}
    private_temp = args.workspace / "temp"
    private_temp.mkdir()
    environment.update(TEMP=str(private_temp.resolve()), TMP=str(private_temp.resolve()))
    protocol = {
        "source_commit": revision,
        "baseline_commit": BASELINE,
        "source_sha256": source_seal,
        "started_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "os": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "physical_memory_bytes": psutil.virtual_memory().total,
        "disk_free_bytes": shutil.disk_usage(args.workspace).free,
        "dependencies": {n: version(n) for n in ("duckdb", "dbt-core", "dbt-duckdb", "psutil")},
        "repetitions": args.repetitions,
        "duckdb_threads": 1,
        "dbt_threads": 1,
        "memory_limit": "2GB",
        "sampling_seconds": 0.2,
        "cache": "Fresh warehouse files; OS cache not flushed; paired order alternates; not cold-cache claims",
    }
    write(args.output / "execution-protocol.json", protocol)

    def stage(label, operation, db, folder, baseline=False, late=False):
        result = args.workspace / (label + "-result.json")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            operation,
            "--workspace",
            str(folder.resolve()),
            "--database",
            str(db.resolve()),
            "--result",
            str(result.resolve()),
            "--profile",
            args.profile,
        ]
        if late:
            command += ["--late"]
        env = {**environment, "PYTHONPATH": str((reference if baseline else ROOT) / "src")}
        timing = measured(args, label, command, env=env)
        return {**timing, "result": json.loads(result.read_text(encoding="utf-8"))}

    if (
        psutil.virtual_memory().available < 4 * 1024**3
        or shutil.disk_usage(args.workspace).free < 50 * 1024**3
    ):
        write(
            args.output / "blocked.json",
            {
                "reason": "predeclared resource guard",
                "available_memory_bytes": psutil.virtual_memory().available,
            },
        )
        return
    sources = args.workspace / "sources"
    generation = stage("generation", "generate", args.workspace / "unused", sources)
    write(args.output / "generation.json", generation)
    for repetition in range(1, args.repetitions + 1):
        folder = args.workspace / f"pair-{repetition}"
        folder.mkdir()
        base, opt, full = [
            folder / (n + ".duckdb") for n in ("baseline", "optimized", "independent")
        ]
        record = {"profile": args.profile, "repetition": repetition, "stages": {}}
        stages = record["stages"]
        for name, db in (("baseline", base), ("optimized", opt)):
            stages[name + "_load"] = stage(f"{repetition}-{name}-load", "load", db, sources)
        order = [("baseline", base), ("optimized", opt)]
        if repetition % 2 == 0:
            order.reverse()
        for name, db in order:
            stages[name + "_initial"] = stage(
                f"{repetition}-{name}-initial",
                "full" if name == "baseline" else "optimized",
                db,
                folder / (name + "-initial"),
                baseline=name == "baseline",
            )
        for name, db in order:
            stages[name + "_correction_load"] = stage(
                f"{repetition}-{name}-correction-load", "correction", db, sources
            )
            stages[name + "_incremental"] = stage(
                f"{repetition}-{name}-incremental",
                "incremental" if name == "baseline" else "optimized",
                db,
                folder / (name + "-incremental"),
                baseline=name == "baseline",
            )
        record["baseline_optimized_parity"] = compare(opt, base)
        stages["independent_load"] = stage(
            f"{repetition}-independent-load", "load", full, sources, late=True
        )
        stages["independent_full"] = stage(
            f"{repetition}-independent-full", "full", full, folder / "independent-full"
        )
        record["optimized_full_parity"] = compare(opt, full)
        checked_at = time.perf_counter()
        snapshots = [local_snapshot(d) for d in (opt, full)]
        for snap in snapshots:
            assert_enterprise(snap)
        payloads = [make_payload(s, "1.0", "duckdb") for s in snapshots]
        if payloads[0] != payloads[1]:
            raise AssertionError("Validated consumer payloads differ")
        record["consumer_payload_equal"] = True
        if args.profile == "enterprise":
            from decimal import Decimal

            expected = json.loads((ROOT / "docs/evidence/scale-summary.json").read_text())[
                "metrics"
            ]
            actual = {
                r["metric_id"]: r["value"] for r in payloads[0]["metrics"] if r["region"] == "ALL"
            }
            if {k: Decimal(str(v)) for k, v in expected.items()} != {
                k: Decimal(str(v)) for k, v in actual.items()
            }:
                raise AssertionError("Canonical published enterprise economics changed")
            record["canonical_usd_metrics_unchanged"] = True
        record["consumer_check_seconds"] = time.perf_counter() - checked_at
        record["metrics"] = payloads[0]["metrics"]
        with __import__("duckdb").connect(str(opt), read_only=True) as con:
            record["affected_opportunities"] = con.execute(
                "select count(distinct opportunity_id) from raw.opportunity_events where ingest_seq=2"
            ).fetchone()[0]
            record["total_opportunities"] = con.execute(
                "select count(distinct opportunity_id) from raw.opportunity_events"
            ).fetchone()[0]
            record["affected_fact_rows"] = con.execute(
                "select count(*) from analytics.fct_opportunity_daily where account_id in (select account_id from assurance_control.affected_accounts)"
            ).fetchone()[0]
            record["source_counts"] = {
                n: con.execute(f"select count(*) from raw.{n}").fetchone()[0]
                for n in payloads[0]["dataset"]["source_counts"]
            }
        for name, db in order:
            stages[name + "_noop"] = stage(
                f"{repetition}-{name}-noop",
                "incremental" if name == "baseline" else "optimized",
                db,
                folder / (name + "-noop"),
                baseline=name == "baseline",
            )
        record["noop_parity"] = compare(opt, base)
        record["canonical_warehouse_bytes"] = {
            n: d.stat().st_size
            for n, d in (("baseline", base), ("optimized", opt), ("independent", full))
        }
        if args.profile == "enterprise" and repetition == args.repetitions:
            large_full = folder / "large-independent.duckdb"
            stages["large_independent_load"] = stage(
                f"{repetition}-large-independent-load", "load", large_full, sources, late=True
            )
            for name, db in (("baseline", base), ("optimized", opt), ("independent", large_full)):
                stages["large_" + name + "_source"] = stage(
                    f"{repetition}-large-{name}-source", "large", db, sources
                )
                stages["large_" + name] = stage(
                    f"{repetition}-large-{name}",
                    "optimized"
                    if name == "optimized"
                    else ("full" if name == "independent" else "incremental"),
                    db,
                    folder / ("large-" + name),
                    baseline=name == "baseline",
                )
            record["large_batch_baseline_parity"] = compare(opt, base)
            record["large_batch_full_parity"] = compare(opt, large_full)
        record["warehouse_bytes"] = {
            n: d.stat().st_size
            for n, d in (("baseline", base), ("optimized", opt), ("independent", full))
        }
        if repetition == 1:
            from revenue_platform.selective import connect

            with connect(opt) as con:
                sql = "select f.date_day,f.account_id,a.region,a.owner,sum(case when f.stage='open' then f.amount_cents else 0 end) as pipeline_cents from analytics.fct_opportunity_daily f join analytics.dim_account a on f.account_id=a.account_id and f.date_day>=a.valid_from and f.date_day<a.valid_to {} group by f.date_day,f.account_id,a.region,a.owner"
                for name, condition in (
                    ("baseline", ""),
                    (
                        "optimized",
                        "where f.account_id in (select account_id from assurance_control.affected_accounts)",
                    ),
                ):
                    plan = con.execute("explain analyze " + sql.format(condition)).fetchall()
                    (args.output / (name + "-plan.txt")).write_text(
                        "\n".join(r[1] for r in plan), encoding="utf-8"
                    )
        write(args.output / f"pair-{repetition}.json", record)
        print(
            json.dumps(
                {
                    "pair": repetition,
                    "profile": args.profile,
                    "baseline_incremental": stages["baseline_incremental"]["seconds"],
                    "optimized_incremental": stages["optimized_incremental"]["seconds"],
                    "exact_models": 15,
                }
            ),
            flush=True,
        )
    if any(
        hashlib.sha256((ROOT / n).read_bytes()).hexdigest() != h for n, h in source_seal.items()
    ):
        raise ValueError("Source changed during measurements")
    write(
        args.output / "completed.json",
        {"source_unchanged": True, "pairs": args.repetitions, "profile": args.profile},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", choices=["enterprise", "stress"], default="enterprise")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--worker",
        choices=["generate", "load", "correction", "large", "full", "incremental", "optimized"],
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--late", action="store_true")
    arguments = parser.parse_args()
    worker(arguments) if arguments.worker else run(arguments)
