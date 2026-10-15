"""Export only validated, sanitized native fixture evidence; never connect to Snowflake."""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil

from streamlit.testing.v1 import AppTest

from revenue_platform.consumer import analyst_csv, metric_rows, review
from revenue_platform.runtime import ROOT, canonical, source_fingerprint


def export(workspace, destination):
    workspace, destination = Path(workspace), Path(destination)
    verification = json.loads((workspace / "verification.json").read_text())
    assert verification["status"] == "passed" and verification["executed_cloud"] is True
    assert verification["acceptance_passed"] is True and verification["profile"] == "fixture"
    assert verification["warehouse_final_state"] == "SUSPENDED"
    existing = destination / "verification.json"
    if (
        existing.exists()
        and json.loads(existing.read_text())["query_tag"] != verification["query_tag"]
    ):
        raise FileExistsError("Use a new evidence destination for a different native run")
    for name, expected in verification["implementation_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    comparisons = verification["model_comparison"]
    assert len(comparisons) == 15 and all(
        r["exact_multiset_equal"] and r["column_contract_equal"] for r in comparisons
    )
    names = [
        "verification.json",
        "model-comparisons.json",
        "query-history.json",
        "initial_full_build-results.json",
        "corrected_incremental_build-results.json",
        "independent_corrected_full_build-results.json",
        "rejected-coverage-results.json",
        "restored-coverage-results.json",
    ]
    for name in names[3:6]:
        nodes = json.loads((workspace / name).read_text())
        assert sum(r["node"].startswith("model.") for r in nodes) == 15
        assert sum(r["node"].startswith("test.") for r in nodes) == 30
        assert all(r["status"] in {"success", "pass"} and r["query_id"] for r in nodes)
    rejected = json.loads((workspace / names[6]).read_text())
    restored = json.loads((workspace / names[7]).read_text())
    assert len(rejected) == len(restored) == 1
    assert rejected[0]["status"] == "fail" and restored[0]["status"] == "pass"
    assert (
        rejected[0]["node"] == restored[0]["node"] == "test.revenue_metrics.assert_source_coverage"
    )
    payload = review(workspace / "published", verification["release_id"])
    assert payload["target"] == "snowflake" and payload["model_fingerprint"] == source_fingerprint()
    exported = analyst_csv(payload)
    csv_rows = list(csv.DictReader(io.StringIO(exported)))
    assert {r["metric_id"]: r["value"] for r in csv_rows} == {
        r["metric_id"]: r["value"] or "" for r in metric_rows(payload)
    }
    previous = os.environ.get("REVENUE_PUBLICATION")
    try:
        os.environ["REVENUE_PUBLICATION"] = str((workspace / "published").resolve())
        app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
        assert not app.exception
        values = [m.value for m in app.metric]
        assert values == ["$4,000", "$18,000", "$16,000", "$11,000", "$18,000", "20.0%"]
        app.selectbox[0].select("East").run()
        assert not app.exception and app.metric[0].value == "$4,000"
        assert app.metric[5].value == "Not mature"
    finally:
        if previous is None:
            os.environ.pop("REVENUE_PUBLICATION", None)
        else:
            os.environ["REVENUE_PUBLICATION"] = previous
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(workspace / name, destination / name)
    publication = destination / "published"
    publication.mkdir(exist_ok=True)
    for name in ("current.json", verification["release_id"] + ".json"):
        shutil.copyfile(workspace / "published" / name, publication / name)
    (destination / "analyst-export.csv").write_text(exported, encoding="utf-8")
    (destination / "consumer-results.json").write_text(
        canonical(
            {
                "status": "passed",
                "release_id": verification["release_id"],
                "source": "Actual validated Snowflake fixture publication",
                "scope": "Offline CSV and Streamlit AppTest of the native publication; no new warehouse execution",
                "csv_metric_parity": True,
                "all_region_cards": values,
                "east_pipeline": "$4,000",
                "east_cohort": "Not mature",
            }
        ),
        encoding="utf-8",
    )
    print(canonical({"status": "passed", "destination": str(destination), "files": len(names) + 4}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--destination", type=Path, default=ROOT / "docs/evidence/snowflake/fixture"
    )
    args = parser.parse_args()
    export(args.workspace, args.destination)
