"""Summarize checked experiments and package a review copy without overwriting old deliveries."""

import argparse
import hashlib
import json
import statistics
import zipfile

from check_performance import check
from package import MANIFEST, delivered_files, scan
from report_performance import report
from revenue_platform.runtime import ROOT, canonical

FOLDER = ROOT / "docs/evidence/performance"


def main(package):
    report()
    data = {}
    details = [
        "## Resource, verification and larger-batch observations",
        "",
        "Wall times include child startup and up to one 0.2s polling interval. Process CPU/RSS are sampled; "
        "RSS sums can double-count shared pages. Loading uses the unchanged loader's engine defaults "
        "(recorded separately); the paired transformations use one thread and 2GB in both arms. "
        "No exclusive host CPU isolation is claimed. Exact comparisons run in the parent and are separately timed; their CPU/RSS peaks were not sampled.",
        "",
    ]
    for profile in ("enterprise", "stress"):
        path = FOLDER / profile
        if not (path / "completed.json").exists():
            continue
        pairs = [json.loads(p.read_text()) for p in sorted(path.glob("pair-*.json"))]
        original = statistics.median(p["stages"]["baseline_incremental"]["seconds"] for p in pairs)
        candidate = statistics.median(
            p["stages"]["optimized_incremental"]["seconds"] for p in pairs
        )
        data[profile] = {
            "pairs": len(pairs),
            "baseline_incremental_median_seconds": original,
            "candidate_incremental_median_seconds": candidate,
            "candidate_wall_change_percent": 100 * (candidate / original - 1),
            "predeclared_threshold_met": candidate <= original * 0.85,
        }
        details += [
            f"### {profile.title()}",
            "",
            "| Pair | Original CPU s | Candidate CPU s | Original sampled RSS MB | Candidate sampled RSS MB | Original file MB | Candidate file MB | Exact checks s | Consumer check s |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for p in pairs:
            s = p["stages"]
            checks = sum(
                p[k]["seconds"]
                for k in ("baseline_optimized_parity", "optimized_full_parity", "noop_parity")
            )
            details.append(
                f"| {p['repetition']} | {s['baseline_incremental']['sampled_process_cpu_seconds']:.3f} | {s['optimized_incremental']['sampled_process_cpu_seconds']:.3f} | {s['baseline_incremental']['sampled_tree_rss_max_bytes'] / 1e6:.1f} | {s['optimized_incremental']['sampled_tree_rss_max_bytes'] / 1e6:.1f} | {p['canonical_warehouse_bytes']['baseline'] / 1e6:.1f} | {p['canonical_warehouse_bytes']['optimized'] / 1e6:.1f} | {checks:.3f} | {p['consumer_check_seconds']:.3f} |"
            )
        p = pairs[0]
        details += [
            "",
            f"Source correction: {p['affected_opportunities']:,} / {p['total_opportunities']:,} opportunities; "
            f"{p['stages']['optimized_incremental']['result']['affected_accounts']:,} affected accounts; "
            f"{p['affected_fact_rows']:,} fact rows belong to those account histories. "
            "Plans are diagnostic executions separate from timing. Sampled temporary storage can miss brief files; "
            "warehouse size is allocated file size, including fragmentation and retained state, not logical bytes or cloud cost.",
            "",
        ]
        for p in pairs:
            if "large_batch_full_parity" in p:
                for key in ("large_batch_baseline_parity", "large_batch_full_parity"):
                    assert len(p[key]["models"]) == 15 and all(
                        r["exact_multiset_equal"] for r in p[key]["models"]
                    )
                s = p["stages"]
                details += [
                    f"Larger batch: **{s['large_optimized_source']['result']['opportunities']:,} open-prospect opportunity revisions**. "
                    f"Original incremental {s['large_baseline']['seconds']:.3f}s; candidate {s['large_optimized']['seconds']:.3f}s; "
                    f"independent full {s['large_independent']['seconds']:.3f}s. "
                    "All 15 complete model rowsets matched both comparisons. These appended amount corrections "
                    "are a separate adversarial case, not a replacement for canonical published enterprise economics.",
                    "",
                ]
    rejected = "enterprise" in data and not data["enterprise"]["predeclared_threshold_met"]
    recommendation = (
        "**Decision: retain the original default build.** The candidate reduces pipeline aggregation work "
        "but does not meet the predeclared end-to-end performance threshold. Its exact change detection "
        "and retained snapshots cost more than the aggregation saves. It remains an explicitly opt-in "
        "experimental path for inspection, not a recommended production optimization. "
        "No financial or native Snowflake performance claim follows from these measurements."
        if rejected
        else "The predeclared result is recorded below; no automatic production promotion is performed."
    )
    text = (FOLDER / "report.md").read_text(encoding="utf-8")
    (FOLDER / "report.md").write_text(
        text.replace("\n\n", "\n\n" + recommendation + "\n\n", 1) + "\n".join(details) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (FOLDER / "summary.json").write_text(
        canonical({"profiles": data, "default_changed": False, "candidate_promoted": False}),
        encoding="utf-8",
        newline="\n",
    )
    manifest = FOLDER / "manifest.json"
    record = {
        "files": {
            str(p.relative_to(FOLDER)).replace("\\", "/"): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted(FOLDER.rglob("*"))
            if p.is_file() and p != manifest
        }
    }
    manifest.write_text(canonical(record), encoding="utf-8", newline="\n")
    check()
    if package:
        files = delivered_files()
        scan(files)
        if any(p.stat().st_size > 2_000_000 for p in files):
            raise ValueError("Unexpected large public file")
        inventory = {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
            if p.relative_to(ROOT).as_posix() != MANIFEST
        }
        (ROOT / MANIFEST).write_text(
            canonical(
                {
                    "algorithm": "SHA-256",
                    "scope": "Every delivered file except this manifest",
                    "files": inventory,
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        output = ROOT / ".local/delivery/Revenue_Metrics_Performance_Review.zip"
        output.parent.mkdir(exist_ok=True, parents=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in delivered_files():
                archive.write(path, "revenue-metrics-platform/" + path.relative_to(ROOT).as_posix())
        with zipfile.ZipFile(output) as archive:
            assert archive.testzip() is None
            for name, expected in inventory.items():
                assert (
                    hashlib.sha256(archive.read("revenue-metrics-platform/" + name)).hexdigest()
                    == expected
                )
        print(
            canonical(
                {
                    "package": str(output.relative_to(ROOT)),
                    "files": len(inventory) + 1,
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "content_scan": "passed narrow credential-pattern scan; reviewed paths",
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", action="store_true")
    main(parser.parse_args().package)
