"""Derive the performance report and verify every recorded comparison."""

import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def report():
    folder = ROOT / "docs/evidence/performance"
    rows = [
        "# Controlled selective rebuild measurements",
        "",
        "Local synthetic DuckDB/dbt measurements. No Snowflake execution or commercial cost-saving claim.",
        "",
        "The original opportunity fact already replaces changed opportunities. The new opt-in controller "
        "compares complete raw multisets against its last successful source snapshot, rebuilds only opportunity "
        "descendants for eligible revisions, and replaces the pipeline's affected **account histories**. "
        "Other source/identity/policy changes conservatively trigger a full rebuild. Every invocation runs all 30 dbt gates.",
        "",
        "## Exact-head baseline",
        "",
        "Unchanged main `a39d603cc6d16de8f1d8430abf97ad3db4aa2cbe` passed 71 Python tests, 15 models, "
        "30 dbt tests and the established enterprise verifier locally and in the "
        "[manually dispatched Windows/Linux workflow](https://github.com/rlbsem/revenue-metrics-platform/actions/runs/40141230095). "
        "The complete archived result is in [exact-main-verification.json](exact-main-verification.json).",
        "",
        "## Why this bottleneck",
        "",
        "The pilot's corrected build spent 3.837 seconds in `mart_pipeline`, 49.6% of model execution time. "
        "It reaggregated 12,470,664 daily opportunity rows into 5,395,893 account-day rows while only 600 "
        "of 195,927 opportunities had changed. The [pilot plan](pilot-pipeline-plan.txt) records the actual "
        "join/aggregation. This is downstream recomputation, not a new claim about the existing opportunity replacement.",
        "",
    ]
    for profile in ("enterprise", "stress"):
        path = folder / profile
        if not (path / "completed.json").exists():
            rows += [
                f"## {profile.title()}",
                "",
                "Not completed. See retained blocker or failed-run diagnostics; no performance claim.",
                "",
            ]
            continue
        protocol = json.loads((path / "execution-protocol.json").read_text())
        pairs = [json.loads(p.read_text()) for p in sorted(path.glob("pair-*.json"))]
        assert len(pairs) >= 3
        for pair in pairs:
            for key in ("baseline_optimized_parity", "optimized_full_parity", "noop_parity"):
                comparisons = pair[key]["models"]
                assert len(comparisons) == 15
                assert all(c["exact_multiset_equal"] and c["differences"] == 0 for c in comparisons)
            assert pair["consumer_payload_equal"]
        rows += [
            f"## {profile.title()}",
            "",
            f"Executed source `{protocol['source_commit']}`. "
            f"{protocol['dependencies']}; {protocol['python']}; {protocol['os']}. "
            "One DuckDB thread, one dbt thread and 2GB engine memory limit in both arms. "
            "Fresh databases, alternating pair order, no concurrent local test load. OS caches were not flushed; "
            "these are filesystem-warm paired observations, not cold-cache benchmarks.",
            "",
            "| Pair | Original incremental s | Selective total s | Original no-op s | Selective no-op s |",
            "|---|---:|---:|---:|---:|",
        ]
        for pair in pairs:
            s = pair["stages"]
            rows.append(
                f"| {pair['repetition']} | {s['baseline_incremental']['seconds']:.3f} | {s['optimized_incremental']['seconds']:.3f} | {s['baseline_noop']['seconds']:.3f} | {s['optimized_noop']['seconds']:.3f} |"
            )
        medians = {
            k: statistics.median(p["stages"][k]["seconds"] for p in pairs)
            for k in pairs[0]["stages"]
        }
        improvement = 100 * (1 - medians["optimized_incremental"] / medians["baseline_incremental"])
        model_rows = []
        for model in ("fct_opportunity_daily", "mart_pipeline", "fct_invoice_lines", "mart_flows"):
            values = []
            for arm in ("baseline", "optimized"):
                values.append(
                    statistics.median(
                        sum(
                            n["seconds"]
                            for n in p["stages"][arm + "_incremental"]["result"]["nodes"]
                            if n["node"].endswith("." + model)
                        )
                        for p in pairs
                    )
                )
            model_rows.append(f"| {model} | {values[0]:.3f} | {values[1]:.3f} |")
        rows += [
            "",
            "| Model | Original median s | Selective median s (zero = not rebuilt) |",
            "|---|---:|---:|",
            *model_rows,
        ]
        rows += [
            "",
            f"Median corrected rebuild: **{medians['baseline_incremental']:.3f}s → {medians['optimized_incremental']:.3f}s ({improvement:.1f}% reduction)**. "
            f"The predeclared 15% threshold was {'met' if improvement >= 15 else 'not met'}. "
            f"Median initial builds: original {medians['baseline_initial']:.3f}s, selective {medians['optimized_initial']:.3f}s. "
            f"Independent corrected full refresh: {medians['independent_full']:.3f}s. "
            "Selective totals include exact change detection, source snapshot copying, quality gates and checkpointing; "
            "source generation/loading, full rowset comparison and consumer hashing are separately timed in the raw records.",
            "",
            "All 15 model column contracts and complete multisets matched both original incremental and an independent "
            "clean full rebuild in every repetition. Hash buckets only partition the exact bidirectional `EXCEPT ALL`; "
            "views use independently bound, sorted row streams. No probabilistic signatures establish this result. "
            "Validated consumer payloads were identical. All amounts retain the existing synthetic USD contract.",
            "",
            f"[Raw repetitions]({profile}/pair-1.json) · [Protocol/source hashes]({profile}/execution-protocol.json) · "
            f"[Generation/load evidence]({profile}/generation.json) · [Full plan]({profile}/baseline-plan.txt) · "
            f"[Affected-account plan]({profile}/optimized-plan.txt)",
            "",
        ]
    rows += [
        "## Cost and safety tradeoffs",
        "",
        "Source detection scans exact raw multisets. The controller retains a successful raw snapshot plus a pending "
        "snapshot during execution; it adds disk usage and full-build work. It does not eliminate quality-gate scans, "
        "the opportunity staging window, source loading or independent reconciliation. Plans can show full physical scans "
        "even when fewer rows enter the join/aggregation; no physical partition-pruning guarantee is claimed.",
        "",
        "Sampled process-tree RSS may double-count shared pages and miss short peaks; process CPU is sampled, "
        "temporary-file measurements miss files created/deleted between samples, and machine load/cache effects remain. "
        "The 2GB DuckDB setting is not a process memory cap. Absolute measurements, model timings, resource observations "
        "and database sizes are retained rather than extrapolated to Snowflake or production capacity.",
        "",
        "The controller requires quiesced raw input and exclusive warehouse ownership. The advisory file serializes "
        "controller calls, not arbitrary ingestion writers. A stale lock requires explicit operator verification/removal; "
        "it is never silently stolen. This is not a new concurrent ingestion or physical-delete contract. "
        "A failed transformation/quality gate leaves the previous published pointer intact; failed pending state cannot "
        "authorize skipping corrections. Re-run after repair or rebuild fully. Publication remains the existing validated "
        "immutable payload and atomic pointer mechanism.",
        "",
        "[Reproduction and contracts](../../performance.md) · [Predeclared conditions](protocol.json) · "
        "[Historical preservation hashes](preserved-files.json). Original fixture, scenario, metrics and Snowflake "
        "evidence are preserved; no new vendor or cloud run was performed.",
        "",
    ]
    (folder / "report.md").write_text("\n".join(rows), encoding="utf-8")


if __name__ == "__main__":
    report()
