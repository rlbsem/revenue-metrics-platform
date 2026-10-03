"""Executed scale evidence; source files/databases remain under ignored .local only."""

import json
import os
import platform
import shutil
import time
from uuid import uuid4

import duckdb
from streamlit.testing.v1 import AppTest

from revenue_platform.consumer import analyst_csv, review
from revenue_platform.generator import generate
from revenue_platform.runtime import (
    MODELS,
    ROOT,
    add_correction,
    assert_enterprise,
    canonical,
    load_local,
    local_snapshot,
    make_payload,
    publish,
    run_dbt,
)


def signatures(db):
    """Order-independent row count + two hash aggregates, not bytewise identity proof."""
    with duckdb.connect(str(db), read_only=True) as con:
        return {
            name: list(
                con.execute(
                    f"select count(*),bit_xor(hash(t)),sum(cast(hash(t) as hugeint)) from analytics.{name} t"
                ).fetchone()
            )
            for name in MODELS
        }


def update_readme(result):
    """Refresh the bounded results block from the actual validated publication."""
    rows = {r["metric_id"]: r for r in result["metrics"] if r["region"] == "ALL"}
    info = result["dataset"]
    counts = info["source_counts"]

    def money(key):
        return float(rows[key]["value"])

    report = (
        f"The established enterprise Q1 review produces **CAD{money('net_bookings') / 1e6:,.2f}M in net bookings, "
        f"CAD{money('net_invoiced') / 1e6:,.2f}M net invoiced and CAD{money('cash_received') / 1e6:,.2f}M allocated cash**. "
        f"Period-end installed ARR is CAD{money('period_end_arr') / 1e6:,.2f}M; open pipeline is CAD{money('open_pipeline') / 1e6:,.2f}M. "
        f"The mature 30-day current-quarter cohort win rate is {money('cohort_win_rate'):.2%}.\n\n"
        "**Installed ARR ≠ quarterly bookings ≠ quarterly invoicing ≠ quarterly cash.** "
        "Customers acquired over 2020–2025 continue to contribute ARR. Only signed Q1 new business, renewals, expansions, upgrades, contractions and cancellations enter Q1 bookings. Billing continues for existing contracts; some collections settle opening invoices.\n\n"
        "| Generated enterprise profile | Actual value |\n|---|---:|\n"
        f"| Customer accounts | {info['accounts']:,} |\n"
        f"| Opportunities, including retained acquisition/renewal history | {info['opportunities']:,} |\n"
        f"| Active core subscriptions at March 31 | {info['active_subscriptions']:,} |\n"
        f"| Booking/amendment components | {counts['booking_events']:,} |\n"
        f"| Signed Q1 commercial events behind those components | {counts['commercial_events']:,} |\n"
        f"| Invoice/credit lines, including supporting opening invoices | {counts['invoice_lines']:,} |\n"
        f"| Payment allocations, including retained historical allocations | {counts['payment_allocations']:,} |\n"
        f"| Period-end ARR | CAD{money('period_end_arr'):,.2f} |\n\n"
        f"{info['timeline']['active_accounts_without_q1_bookings']:,} active accounts have no Q1 booking. "
        "[Generated scale summary](docs/evidence/scale-summary.json) and [acquisition/renewal timeline](docs/evidence/business-timeline.json). "
        "Each signed event has twelve service-period commitment components; these are not twelve separately won deals. "
        "ARR emerges from dated contracts and explicit fees. Runtime-generated source rows stay outside GitHub and the delivery ZIP."
    )
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    start = (
        text.index("The enterprise Q1 review")
        if "The enterprise Q1 review" in text
        else text.index("The established enterprise Q1 review")
    )
    finish = text.index("\n\n**All businesses", start)
    path.write_text(text[:start] + report + text[finish:], encoding="utf-8")


def verify():
    workspace = ROOT / ".local/enterprise-verification" / uuid4().hex
    workspace.mkdir(parents=True)
    evidence = ROOT / "docs/evidence"
    timings = {}

    def measured(label, action):
        started = time.perf_counter()
        value = action()
        timings[label] = round(time.perf_counter() - started, 3)
        print(f"{label}: {timings[label]}s", flush=True)
        return value

    manifest = measured("source_generation", lambda: generate(workspace / "sources"))
    repeated = measured("repeat_source_generation", lambda: generate(workspace / "repeat-sources"))
    assert repeated == manifest, "Seeded source bytes differ"
    db = workspace / "warehouse.duckdb"
    measured("bulk_load", lambda: load_local(db, source_dir=workspace / "sources"))
    measured("initial_dbt_build", lambda: run_dbt(db, workspace / "base", full_refresh=True))
    from revenue_platform.scenario_checks import validate_scenario

    with duckdb.connect(str(db), read_only=True) as con:
        economics = validate_scenario(con)
    before = signatures(db)
    add_correction(db, workspace / "sources")
    measured("corrected_incremental_dbt_build", lambda: run_dbt(db, workspace / "incremental"))
    incremental = signatures(db)
    assert before["fct_opportunity_daily"] != incremental["fct_opportunity_daily"]
    snapshot = measured("bounded_snapshot_and_source_hashes", lambda: local_snapshot(db))
    assert_enterprise(snapshot)
    initial_release = publish(make_payload(snapshot, "1.0", "duckdb"), workspace / "published")
    measured(
        "corrected_full_refresh_dbt_build",
        lambda: run_dbt(db, workspace / "full", full_refresh=True),
    )
    rebuilt = signatures(db)
    assert incremental == rebuilt, "Incremental/full model multiset signatures differ"
    final_snapshot = local_snapshot(db)
    assert_enterprise(final_snapshot)
    final_release = publish(make_payload(final_snapshot, "1.0", "duckdb"), workspace / "published")
    assert final_release == initial_release, "Published metric/source/profile evidence differs"
    result = review(workspace / "published")
    build_results = json.loads((workspace / "full/dbt-target/run_results.json").read_text())
    statuses = [
        {"node": r["unique_id"], "status": r["status"], "seconds": r["execution_time"]}
        for r in build_results["results"]
    ]
    assert all(r["status"] in ("success", "pass") for r in statuses)
    measured("dbt_docs", lambda: run_dbt(db, workspace / "docs", docs=True))
    graph = json.loads((workspace / "docs/dbt-target/manifest.json").read_text())
    lineage = {
        k: {
            "resource_type": v["resource_type"],
            "depends_on": v.get("depends_on", {}).get("nodes", []),
            "description": v.get("description", ""),
        }
        for k, v in graph["nodes"].items()
    }
    os.environ["REVENUE_PUBLICATION"] = str(workspace / "published")
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not app.exception
    assert len(app.metric) == 6
    expected = {r["metric_id"]: r for r in result["metrics"] if r["region"] == "ALL"}
    assert app.metric[4].value == f"${float(expected['period_end_arr']['value']):,.0f}"
    app.selectbox[0].select("EMEA").run()
    assert not app.exception
    emea = next(
        r for r in result["metrics"] if r["metric_id"] == "period_end_arr" and r["region"] == "EMEA"
    )
    assert app.metric[4].value == f"${float(emea['value']):,.0f}"
    app.selectbox[1].select("Enterprise").run()
    app.selectbox[2].select("Suite").run()
    assert not app.exception
    from verify_snowflake import verify as verify_cloud

    assert verify_cloud(workspace / "snowflake", source_dir=workspace / "sources") == 0
    cloud = json.loads((workspace / "snowflake/verification.json").read_text())
    publication = evidence / "published"
    publication.mkdir(exist_ok=True)
    for old in publication.glob("*.json"):
        old.unlink()
    for path in (workspace / "published").glob("*.json"):
        shutil.copyfile(path, publication / path.name)
    (evidence / "analyst-export.csv").write_text(analyst_csv(result), encoding="utf-8")
    values = {k: r["value"] for k, r in expected.items()}
    outputs = {
        "dataset-manifest.json": manifest,
        "source-economics.json": {"status": "passed", "error_counts": economics},
        "scale-summary.json": {**result["dataset"], "metrics": values},
        "business-timeline.json": {
            **result["dataset"]["timeline"],
            "metrics": values,
            "cohort": expected["cohort_win_rate"],
        },
        "dbt-results.json": statuses,
        "dbt-lineage.json": lineage,
        "snowflake-status.json": cloud,
        "modeling-cases.json": {
            "profile": "enterprise",
            "bounded_discrepancy_examples": result["bridge"],
            "correction_rows": manifest["row_counts"]["late_opportunity_events"],
            "daily_fact_before": before["fct_opportunity_daily"],
            "daily_fact_corrected": incremental["fct_opportunity_daily"],
            "incremental_full_model_signatures_equal": True,
            "signature_method": "row count, XOR and sum of DuckDB row hashes; probabilistic comparison. Exact rowsets also tested on canonical fixture.",
            "all_corrected_model_signatures": rebuilt,
            "published_release_equal": initial_release == final_release,
        },
        "enterprise-verification.json": {
            "status": "passed",
            "profile": "enterprise",
            "source_economics": "passed; all source-level economic and booking-authority checks",
            "source_repeat_byte_hashes_equal": True,
            "incremental_full_model_signatures_equal": True,
            "published_release_equal": True,
            "streamlit": "six cards, regional ARR, segment and offering selection passed AppTest",
            "dbt_models": len(MODELS),
            "dbt_tests": sum(r["node"].startswith("test.") for r in statuses),
            "timings_seconds": timings,
            "python": platform.python_version(),
            "os": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "duckdb_settings": {"threads": 2, "memory_limit": "2GB", "dbt_threads": 1},
            "timing_caveat": "Single local execution on a shared Windows workstation; not a controlled benchmark or production capacity claim. Incremental command rebuilds all non-incremental models.",
            "stress": "configured; not executed, no measured performance claim",
            "release_id": final_release,
        },
    }
    for name, value in outputs.items():
        (evidence / name).write_text(canonical(value), encoding="utf-8")
    report = [
        "# Enterprise commercial review",
        "",
        "Synthetic Q1 2026 B2B SaaS business. CAD. This is a scenario, not an accounting or production-scale claim.",
        "",
        "| Metric | Value | Version |",
        "|---|---:|---|",
    ]
    report += [f"| {k} | {r['value']} | {r['definition_version']} |" for k, r in expected.items()]
    report += [
        "",
        "Installed ARR is not quarterly bookings, invoicing or cash. Historical acquisitions and staggered renewals sustain the installed balance independently of this quarter’s signed activity. Pipeline is unsold opportunity value; bookings are signed commitments and amendments; invoice lines bill the fee; cash reflects received allocations; ARR annualizes eligible recurring contract terms. The win rate uses mature creation cohorts, not all open opportunities.",
        "",
        "Cash includes collections on opening invoices, so invoice less cash is a flow difference, not an accounts-receivable balance. Annual billing, monthly billing, credits, cancellations and partial/delayed settlement explain why these figures differ. The warehouse retains the full account bridge; the publication contains the first 20 account IDs as bounded examples, not a representative statistical sample.",
        "",
        "See [installed-base timeline](business-timeline.json), [source economics](source-economics.json), [scale and portfolio](scale-summary.json), [selected discrepancies and incremental correction](modeling-cases.json), [analyst CSV](analyst-export.csv), [measured execution](enterprise-verification.json), [catalog](../../metrics/catalog.json), and the separate [exact fixture review](fixture/commercial-review.md).",
        "",
    ]
    (evidence / "commercial-review.md").write_text("\n".join(report), encoding="utf-8")
    update_readme(result)
    print("Enterprise release:", final_release, flush=True)
    return outputs["enterprise-verification.json"]


if __name__ == "__main__":
    verify()
