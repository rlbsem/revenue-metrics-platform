"""Full local verification and generated evidence, always in a new workspace."""

import json
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from uuid import uuid4

import duckdb

from revenue_platform.consumer import analyst_csv, metric_rows, review
from revenue_platform.runtime import (
    ROOT,
    add_correction,
    assert_expected,
    canonical,
    demo,
    load_local,
    local_snapshot,
    make_payload,
    publish,
    run_dbt,
)


def command(args, cwd=ROOT):
    result = subprocess.run(
        [sys.executable, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError("Verification command failed: " + " ".join(args))
    return result.stdout


def main():
    workspace = ROOT / ".local/verification" / uuid4().hex
    workspace.mkdir(parents=True)
    evidence = ROOT / "docs/evidence/fixture"
    evidence.mkdir(parents=True, exist_ok=True)
    command(["-m", "pip", "check"])
    command(["-m", "ruff", "check", "src", "tests", "scripts", "app.py"])
    command(["-m", "ruff", "format", "--check", "src", "tests", "scripts", "app.py"])
    tests = command(
        [
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(workspace / "pytest"),
            "-p",
            "no:cacheprovider",
            "--junitxml",
            str(workspace / "pytest.xml"),
        ]
    )
    junit = ET.parse(workspace / "pytest.xml")
    case_results = [
        {
            "test": case.attrib["name"],
            "status": "passed"
            if not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
            else "not_passed",
        }
        for case in junit.iter("testcase")
    ]
    assert all(case["status"] == "passed" for case in case_results)
    (evidence / "test-results.json").write_text(canonical(case_results), encoding="utf-8")
    base = workspace / "incremental"
    db = base / "warehouse.duckdb"
    load_local(db)
    run_dbt(db, base)
    with duckdb.connect(str(db)) as con:
        before = con.execute(
            "select sum(pipeline_cents) from analytics.mart_pipeline where date_day='2026-02-04'"
        ).fetchone()[0]
    add_correction(db)
    run_dbt(db, base)
    snapshot = local_snapshot(db)
    assert_expected(snapshot)
    with duckdb.connect(str(db)) as con:
        after = con.execute(
            "select sum(pipeline_cents) from analytics.mart_pipeline where date_day='2026-02-04'"
        ).fetchone()[0]
        fanout = con.execute(
            "select sum(i.amount_cents) from analytics.fct_invoice_lines i join analytics.fct_payment_allocations p using(invoice_id)"
        ).fetchone()[0]
    first = publish(make_payload(snapshot, "1.0", "duckdb"), base / "published")
    second = demo(workspace / "clean-replay", profile="fixture")
    assert first == second, "Clean replay release differs"
    result = review(base / "published")
    sample = evidence / "published"
    sample.mkdir(exist_ok=True)
    # Keep exactly the current generated example, leaving old local workspaces untouched.
    for old in sample.glob("*.json"):
        old.unlink()
    shutil.copyfile(base / "published/current.json", sample / "current.json")
    shutil.copyfile(base / "published" / f"{first}.json", sample / f"{first}.json")
    (evidence / "analyst-export.csv").write_text(analyst_csv(result), encoding="utf-8")
    v2 = workspace / "policy-v2"
    v2.mkdir()
    shutil.copyfile(db, v2 / "warehouse.duckdb")
    run_dbt(v2 / "warehouse.duckdb", v2, policy="2.0")
    v2snap = local_snapshot(v2 / "warehouse.duckdb")
    assert_expected(v2snap, "2.0")
    differences = [
        {"metric_id": a["metric_id"], "region": a["region"], "v1": a["value"], "v2": b["value"]}
        for a, b in zip(result["metrics"], v2snap["metrics"], strict=True)
        if a["value"] != b["value"]
    ]
    (evidence / "definition-change.json").write_text(
        canonical(
            {
                "reason": "ARR policy 2 excludes past-due contracts; source facts unchanged.",
                "differences": differences,
            }
        ),
        encoding="utf-8",
    )
    (evidence / "modeling-cases.json").write_text(
        canonical(
            {
                "historical_pipeline_correction": {
                    "date": "2026-02-04",
                    "base_cents": before,
                    "corrected_cents": after,
                },
                "invoice_join_fanout": {"naive_join_cents": fanout, "correct_cents": 1600000},
                "incremental_clean_release_equal": True,
            }
        ),
        encoding="utf-8",
    )
    target = workspace / "clean-replay/dbt-target"
    manifest = json.loads((target / "manifest.json").read_text())
    lineage = {
        k: {
            "resource_type": v["resource_type"],
            "depends_on": v.get("depends_on", {}).get("nodes", []),
            "description": v.get("description", ""),
        }
        for k, v in manifest["nodes"].items()
    }
    (evidence / "dbt-lineage.json").write_text(canonical(lineage), encoding="utf-8")
    # docs generate can replace run_results; use actual build results from the incremental build.
    build_results = json.loads((base / "dbt-target/run_results.json").read_text())
    statuses = [{"node": r["unique_id"], "status": r["status"]} for r in build_results["results"]]
    assert all(r["status"] in ("success", "pass") for r in statuses)
    (evidence / "dbt-results.json").write_text(canonical(statuses), encoding="utf-8")
    command(
        [
            "scripts/verify_snowflake.py",
            "--status-only",
            "--workspace",
            str(workspace / "snowflake"),
        ]
    )
    cloud = json.loads((workspace / "snowflake/verification.json").read_text())
    (evidence / "snowflake-status.json").write_text(canonical(cloud), encoding="utf-8")
    report = [
        "# Q1 2026 commercial review",
        "",
        "All values and source systems are synthetic.",
        "",
        "| Metric | Value | Definition |",
        "|---|---:|---|",
    ]
    report += [
        f"| {r['metric_id']} | {r['value']} | {r['definition_version']} |"
        for r in metric_rows(result)
    ]
    report += [
        "",
        "The 20% win rate is 1 win / 5 mature opportunities; one immature opportunity is excluded.",
        "",
        "## Explain the gaps",
        "",
        "| Account | Bookings | Net invoices | Cash | Bookings less invoices | Invoices less cash |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in result["bridge"]:
        report.append(
            "| "
            + " | ".join(
                str(r[k])
                for k in (
                    "account_id",
                    "bookings",
                    "invoiced",
                    "cash",
                    "bookings_less_invoices",
                    "invoices_less_cash",
                )
            )
            + " |"
        )
    report += [
        "",
        "The gaps are timing and commercial-definition differences. They are not forced to zero. A1 has a signed amendment and partial credit; A2/A4 have uninvoiced commitments. Allocated cash leaves a net $5,000 invoice gap. This sample has no opening balances; this difference is not a general accounts-receivable balance.",
        "",
        f"The deliberately incorrect invoice/payment join produces ${fanout / 100:,.0f}, instead of $16,000. The conservation test rejects the inflated mart.",
        "",
        "The backdated CRM correction changes February 4 pipeline from $12,000 to $21,000. The final quarter-end pipeline remains $4,000. Incremental processing matches a clean rebuild.",
        "",
        "ARR policy 2 excludes past-due contracts and changes quarter-end ARR from $18,000 to $12,000. That is a definition change, not a change to source facts.",
        "",
        f"Release: `{first}`. Read the [analyst export](analyst-export.csv), [modeling cases](modeling-cases.json), [definition change](definition-change.json) and [dbt results](dbt-results.json).",
        "",
    ]
    (evidence / "commercial-review.md").write_text("\n".join(report), encoding="utf-8")
    # Materialize this link target before checking all documentation links; no pass claim yet.
    (evidence / "verification.json").write_text(
        canonical({"local_execution": "checks_in_progress"}), encoding="utf-8"
    )
    broken = []
    for md in evidence.rglob("*.md"):
        if any(part.startswith(".") for part in md.relative_to(ROOT).parts):
            continue
        for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", md.read_text(encoding="utf-8")):
            if link.startswith(("http:", "https:", "#", "mailto:")):
                continue
            path = link.split("#")[0]
            if path and not (md.parent / path).exists():
                broken.append(str(md.relative_to(ROOT)) + ": " + link)
    assert not broken, broken
    summary = {
        "local_execution": "passed",
        "python": platform.python_version(),
        "platform": platform.system(),
        "pytest_summary": tests.strip().splitlines()[-1],
        "dbt_models": sum(r["node"].startswith("model.") for r in statuses),
        "dbt_data_tests": sum(r["node"].startswith("test.") for r in statuses),
        "deterministic_replay": True,
        "streamlit": "AppTest exercised all six cards and region selection",
        "markdown_links": "passed",
        "snowflake": cloud["status"],
        "github_actions": "configured, not executed by this local verification",
        "release_id": first,
    }
    (evidence / "verification.json").write_text(canonical(summary), encoding="utf-8")
    print(canonical(summary))


if __name__ == "__main__":
    main()
